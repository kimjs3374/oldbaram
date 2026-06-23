-- ============================================================================
-- 옛바 매크로 라이선스 시스템 — Supabase 스키마 + RPC + RLS
-- 실행: Supabase 대시보드 → SQL Editor 에 전문 붙여넣고 RUN (1회).
-- 보안 원칙: 클라이언트(anon key 노출)는 검증을 하지 않는다. 로그인/라이선스/
--   기기/킬스위치 판정을 전부 아래 SECURITY DEFINER 함수로 서버에서 처리하고,
--   테이블은 RLS 로 anon 직접 접근을 전면 차단한다. 앱은 RPC 결과만 신뢰.
-- ============================================================================

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- 1) 테이블
-- ---------------------------------------------------------------------------
create table if not exists app_users (
    username       text primary key,
    password_hash  text not null,                 -- extensions.crypt(비번, extensions.gen_salt('bf'))
    is_admin       boolean not null default false,-- 관리자=킬스위치 영향 안 받음
    max_devices    integer not null default 10,   -- 등록 가능 기기 수(0=실행불가)
    max_concurrent integer not null default 1,    -- 동시 실행 기기 수(0=실행불가)
    expires_at     timestamptz,                   -- 사용 만료(null=무제한)
    enabled        boolean not null default true, -- 개별 ON/OFF
    created_at     timestamptz not null default now()
);

create table if not exists devices (
    id            uuid primary key default gen_random_uuid(),
    username      text not null references app_users(username) on delete cascade,
    hwid          text not null,                  -- 클라가 보낸 MachineGuid 해시
    device_name   text,
    registered_at timestamptz not null default now(),
    last_seen     timestamptz,                    -- 하트비트 갱신(동시실행 판정)
    session_token uuid,
    unique (username, hwid)
);

create table if not exists app_config (
    key   text primary key,
    value jsonb not null
);

-- 전역 설정 시드: 킬스위치 ON(true=정상), 강제 최소 배포버전.
insert into app_config(key, value) values
    ('global_enabled', 'true'::jsonb),
    ('min_build',      '"0.0.0"'::jsonb)
on conflict (key) do nothing;

-- 기존 releases 테이블에 배포 semver 컬럼 추가(증분 version 정수와 별개, 표시/강제용).
alter table if exists releases add column if not exists build_version text;
-- exe 배포(런처 자동업데이트)용 dist 파일 manifest. 런처가 이걸 받아 증분 다운로드.
alter table if exists releases add column if not exists dist_manifest jsonb;

-- ---------------------------------------------------------------------------
-- 2) RLS — 세 테이블 모두 anon 직접 접근 전면 차단(정책 무 = 거부). RPC만 우회.
-- ---------------------------------------------------------------------------
alter table app_users  enable row level security;
alter table devices    enable row level security;
alter table app_config enable row level security;
-- (정책을 만들지 않으므로 anon 의 select/insert/update/delete 전부 거부됨)

-- ---------------------------------------------------------------------------
-- 3) semver 비교 헬퍼: 'a.b.c' → 정렬가능 정수. (각 파트 0~999 가정)
-- ---------------------------------------------------------------------------
create or replace function _semver_num(v text) returns integer
language plpgsql immutable as $$
declare p text[];
begin
    if v is null or v = '' then return 0; end if;
    p := string_to_array(v, '.');
    return  coalesce(nullif(p[1],'')::int,0)*1000000
          + coalesce(nullif(p[2],'')::int,0)*1000
          + coalesce(nullif(p[3],'')::int,0);
exception when others then return 0;
end $$;

-- ---------------------------------------------------------------------------
-- 4) app_login — 로그인 + 라이선스 + 기기 + 킬스위치 + 버전 한 번에 판정
--    반환 json: 성공 {ok:true, token, username, is_admin, expires_at,
--                     max_devices, max_concurrent}
--               실패 {ok:false, reason:'...'}
--    reason: bad_credentials | killswitch | disabled | expired |
--            update_required | device_limit | concurrent_limit
-- ---------------------------------------------------------------------------
create or replace function app_login(
    p_username    text,
    p_password    text,
    p_hwid        text,
    p_device_name text,
    p_build       text
) returns json
language plpgsql security definer set search_path = public, extensions as $$
declare
    u            app_users%rowtype;
    g_enabled    boolean;
    v_min_build  text;
    n_registered integer;
    n_active     integer;
    v_token      uuid;
    dev_exists   boolean;
begin
    select * into u from app_users where username = p_username;
    -- 비번 검증 (존재하지 않는 계정도 동일 메시지로 — 계정 열거 방지)
    if u.username is null
       or u.password_hash is null
       or extensions.crypt(p_password, u.password_hash) <> u.password_hash then
        return json_build_object('ok', false, 'reason', 'bad_credentials');
    end if;

    -- 킬스위치 (관리자는 면제)
    select (value)::text::boolean into g_enabled from app_config where key='global_enabled';
    if coalesce(g_enabled, true) = false and u.is_admin = false then
        return json_build_object('ok', false, 'reason', 'killswitch');
    end if;

    if u.enabled = false then
        return json_build_object('ok', false, 'reason', 'disabled');
    end if;
    if u.expires_at is not null and u.expires_at < now() then
        return json_build_object('ok', false, 'reason', 'expired');
    end if;

    -- 강제 업데이트 (관리자는 면제)
    select trim(both '"' from value::text) into v_min_build from app_config where key='min_build';
    if u.is_admin = false
       and _semver_num(p_build) < _semver_num(coalesce(v_min_build,'0.0.0')) then
        return json_build_object('ok', false, 'reason', 'update_required');
    end if;

    -- 기기 등록 한도 (이미 등록된 hwid 면 통과)
    select exists(select 1 from devices where username=p_username and hwid=p_hwid)
      into dev_exists;
    if not dev_exists then
        select count(*) into n_registered from devices where username=p_username;
        if n_registered >= u.max_devices then
            return json_build_object('ok', false, 'reason', 'device_limit');
        end if;
        insert into devices(username, hwid, device_name)
        values (p_username, p_hwid, p_device_name);
    end if;

    -- 동시 실행 한도 (자기 hwid 제외, 최근 90초 살아있는 기기 수)
    select count(*) into n_active
      from devices
     where username=p_username
       and hwid <> p_hwid
       and last_seen is not null
       and last_seen > now() - interval '90 seconds';
    if n_active >= u.max_concurrent then
        return json_build_object('ok', false, 'reason', 'concurrent_limit');
    end if;

    -- 통과 → 토큰 발급 + 하트비트 시작
    v_token := gen_random_uuid();
    update devices
       set session_token = v_token,
           last_seen     = now(),
           device_name   = coalesce(p_device_name, device_name)
     where username = p_username and hwid = p_hwid;

    return json_build_object(
        'ok', true,
        'token', v_token,
        'username', u.username,
        'is_admin', u.is_admin,
        'expires_at', u.expires_at,
        'max_devices', u.max_devices,
        'max_concurrent', u.max_concurrent
    );
end $$;

-- ---------------------------------------------------------------------------
-- 5) app_heartbeat — last_seen 갱신 + 만료/킬스위치/disabled 재확인
--    반환 {ok:true} | {ok:false, reason}
-- ---------------------------------------------------------------------------
create or replace function app_heartbeat(p_token text) returns json
language plpgsql security definer set search_path = public, extensions as $$
declare
    d         devices%rowtype;
    u         app_users%rowtype;
    g_enabled boolean;
begin
    select * into d from devices where session_token = p_token::uuid;
    if d.id is null then
        return json_build_object('ok', false, 'reason', 'no_session');
    end if;
    select * into u from app_users where username = d.username;
    if u.username is null then
        return json_build_object('ok', false, 'reason', 'no_user');
    end if;

    select (value)::text::boolean into g_enabled from app_config where key='global_enabled';
    if coalesce(g_enabled, true) = false and u.is_admin = false then
        return json_build_object('ok', false, 'reason', 'killswitch');
    end if;
    if u.enabled = false then
        return json_build_object('ok', false, 'reason', 'disabled');
    end if;
    if u.expires_at is not null and u.expires_at < now() then
        return json_build_object('ok', false, 'reason', 'expired');
    end if;

    update devices set last_seen = now() where id = d.id;
    return json_build_object('ok', true);
exception when others then
    return json_build_object('ok', false, 'reason', 'error');
end $$;

-- ---------------------------------------------------------------------------
-- 6) app_logout — 동시실행 슬롯 즉시 반환
-- ---------------------------------------------------------------------------
create or replace function app_logout(p_token text) returns json
language plpgsql security definer set search_path = public, extensions as $$
begin
    update devices
       set last_seen = null, session_token = null
     where session_token = p_token::uuid;
    return json_build_object('ok', true);
exception when others then
    return json_build_object('ok', false);
end $$;

-- ---------------------------------------------------------------------------
-- 7) anon 에게 RPC 실행 권한 부여 (테이블 권한은 주지 않음 — RLS 로 차단됨)
-- ---------------------------------------------------------------------------
grant execute on function app_login(text,text,text,text,text) to anon;
grant execute on function app_heartbeat(text)                 to anon;
grant execute on function app_logout(text)                    to anon;

-- ============================================================================
-- 운영 예시 (SQL Editor 에서 그때그때 실행)
-- ----------------------------------------------------------------------------
-- ▶ 관리자 계정 발급 (킬스위치/버전 면제, 기기/동시 넉넉히)
--   insert into app_users(username, password_hash, is_admin, max_devices, max_concurrent)
--   values ('admin', extensions.crypt('관리자비번', extensions.gen_salt('bf')), true, 99, 99);
--
-- ▶ 일반 테스트 계정 발급 (기기 3대 등록, 동시 1대, 30일 사용)
--   insert into app_users(username, password_hash, max_devices, max_concurrent, expires_at)
--   values ('tester1', extensions.crypt('test1234', extensions.gen_salt('bf')), 3, 1, now() + interval '30 days');
--
-- ▶ 비번 변경
--   update app_users set password_hash = extensions.crypt('새비번', extensions.gen_salt('bf')) where username='tester1';
--
-- ▶ 사용 기간 연장
--   update app_users set expires_at = now() + interval '90 days' where username='tester1';
--
-- ▶ 개별 사용자 차단/해제
--   update app_users set enabled = false where username='tester1';   -- 차단
--   update app_users set enabled = true  where username='tester1';   -- 해제
--
-- ▶ 전역 킬스위치 (관리자 제외 전원 차단/복구)
--   update app_config set value='false'::jsonb where key='global_enabled';  -- 전원 OFF
--   update app_config set value='true'::jsonb  where key='global_enabled';  -- 복구
--
-- ▶ 강제 최소 버전 올리기 (이 미만 빌드는 실행 차단)
--   update app_config set value='"0.2.0"'::jsonb where key='min_build';
--
-- ▶ 기기 슬롯 비우기 (사용자가 PC 교체 시)
--   delete from devices where username='tester1' and hwid='...';
--   -- 또는 전부:  delete from devices where username='tester1';
--
-- ▶ 현재 접속(최근 90초) 현황
--   select username, count(*) filter (where last_seen > now()-interval '90s') as online,
--          count(*) as registered
--     from devices group by username;
-- ============================================================================
