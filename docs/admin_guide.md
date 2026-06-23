# 옛바 라이선스 운영 가이드 (관리자용)

관리 UI는 따로 없다. **Supabase 대시보드 → SQL Editor**에서 아래 SQL로 직접 관리한다.
모든 검증은 서버 RPC(`app_login`/`app_heartbeat`/`app_logout`)가 한다 — 자세한 정의는 `license_schema.sql`.

## 0. 최초 1회 설치
1. Supabase → SQL Editor 에 `docs/license_schema.sql` 전문 붙여넣고 **RUN**.
   - `app_users` / `devices` / `app_config` 테이블 + RPC 3개 + RLS + `releases.build_version` 컬럼 생성.
2. 관리자 계정 발급:
   ```sql
   insert into app_users(username, password_hash, is_admin, max_devices, max_concurrent)
   values ('admin', crypt('관리자비번', gen_salt('bf')), true, 99, 99);
   ```
   (관리자는 킬스위치·강제버전 면제)

## 1. 계정 발급
```sql
-- 기기 3대 등록 / 동시 1대 / 30일
insert into app_users(username, password_hash, max_devices, max_concurrent, expires_at)
values ('tester1', crypt('test1234', gen_salt('bf')), 3, 1, now() + interval '30 days');
```
- `max_devices` = 한 계정에 **등록 가능한 기기 수**(영구). `max_concurrent` = **동시에 켜는 기기 수**.
- 둘 중 하나라도 0 이면 실행 불가.
- `expires_at` 을 비우면(null) 무제한.

## 2. 일상 운영
| 작업 | SQL |
|---|---|
| 비번 변경 | `update app_users set password_hash=crypt('새비번',gen_salt('bf')) where username='tester1';` |
| 기간 연장 | `update app_users set expires_at=now()+interval '90 days' where username='tester1';` |
| 개별 차단 | `update app_users set enabled=false where username='tester1';` |
| 개별 해제 | `update app_users set enabled=true where username='tester1';` |
| 등록/동시 한도 변경 | `update app_users set max_devices=5, max_concurrent=2 where username='tester1';` |
| 기기 슬롯 비우기(PC 교체) | `delete from devices where username='tester1';` |

## 3. 전체 ON/OFF (킬스위치 — 관리자 제외 전원)
```sql
update app_config set value='false'::jsonb where key='global_enabled';  -- 전원 OFF (실행/하트비트 차단)
update app_config set value='true'::jsonb  where key='global_enabled';  -- 복구
```
- OFF 시: 실행 중인 사용자도 **다음 하트비트(최대 30초) 내 자동 종료**.

## 4. 강제 업데이트
배포 버전이 `min_build` 미만이면 로그인이 막힌다(관리자 면제).
```sql
update app_config set value='"0.2.0"'::jsonb where key='min_build';  -- 0.2.0 미만 실행 차단
```

## 5. 배포 (D머신 = 배포자)
```bash
py -m src.tools.cloud_uploader --build 0.1.0 --changelog "변경 내역"
```
- `--build` 생략 시 `src/version.py` 의 `BUILD_VERSION` 사용.
- 사용자 앱은 시작 시 자동 업데이트(증분) 후, 로그인에서 `min_build` 검사.
- 새 배포 버전 올릴 때: `src/version.py` 의 `BUILD_VERSION` 상향 → 빌드/업로드 → 필요 시 `min_build` 상향.

## 6. 모니터링
```sql
-- 사용자별 현재 접속(최근 90초) / 등록 기기 수
select username,
       count(*) filter (where last_seen > now()-interval '90s') as online,
       count(*) as registered
  from devices group by username order by online desc;

-- 만료 임박 / 만료된 계정
select username, expires_at, enabled from app_users
 where expires_at is not null order by expires_at;
```

## 로그 수집
- 앱이 **5분 주기 + 종료 시** 로그를 `sunbi-logs/{role}-{hwid앞8}/` 에 업로드(파일명에 로그인 아이디 포함).
- D머신에서 회수: `py -m src.tools.cloud_logs --pull`.
