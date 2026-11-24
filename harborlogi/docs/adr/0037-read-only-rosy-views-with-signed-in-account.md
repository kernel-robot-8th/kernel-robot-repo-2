# ADR 0037: ROSY 읽기 전용 화면은 로그인한 사람의 Rosy 계정 토큰으로 읽는다

날짜: 2026-11-23

상태: 수락(2026-11-23, 사용자 결정 "그 방향으로 진행해" — 탑뷰를 Harbor에 접속한 사람의 권한으로 받아온다). 탑뷰 프록시에 구현했다. 운영 R에서의 실사용 확인 전이다.

- 고치는 결정: [ADR 0027](0027-rosy-account-sign-in.md) 결정 6 「서버 간 연결은 Fleet이 발급한 named service 신원이다. 사람 Rosy 세션을 서버 호출에 쓰지 않는다」에 예외 하나를 둔다. 업무 제안·실행·관측 원본 같은 쓰기와 업무 근거는 그대로 service 신원이다([ADR 0015](0015-service-identity-and-independent-integration.md)).
- 플랫폼 쪽 근거: rosy-platform `operations/fleet/fleet/server/auth/site_auth.py`의 `authorize`가 Bearer JWT를 `account_receiver.authenticate_bearer`로 확인한다. 토큰의 `aud`가 Fleet의 site resource와 같고 허브 introspect가 활성이면 사람 계정 주체로 받는다(`fleet.*` 권한이 없으면 `viewer`). 카메라 이용권(`POST /api/fleet/vision/lease`)은 `require_camera_viewer`이므로 `viewer`로 충분하다.

## 배경

Harbor 탑뷰 프록시는 `HARBOR_FLEET_TOKEN`(서비스 신원) 하나로 Fleet 카메라를 읽었다. 그 토큰은 Fleet 담당이 R 호스트의 `site-users.yaml`에 등록해야 하고 아직 없다. 한편 사람이 Rosy 계정으로 Harbor에 로그인하면 허브가 접근 토큰을 발급하고 Harbor 서버가 세션에 보관한다(ADR 0027). 사용자는 탑뷰가 접속한 사람의 권한으로 보이기를 원했다.

## 결정

1. **읽기 전용 ROSY 화면**(지금은 탑뷰: 카메라 목록 조회, 이용권, 프레임)은 Harbor 서버가 **로그인한 사람의 Rosy 접근 토큰**으로 Fleet을 부른다. 토큰은 서버에만 있고 브라우저로 보내지 않는다(응답 헤더·본문 포함).
2. 그 사람이 Rosy로 로그인하지 않았거나(로컬 계정) 토큰이 만료됐으면 `HARBOR_FLEET_TOKEN`이 있을 때만 그것을 쓴다. 둘 다 없으면 「ROSY 로그인 필요」로 표시하고 Fleet을 부르지 않는다. `HARBOR_FLEET_ORIGIN`이 없으면 지금처럼 「ROSY 연동 전」이다.
3. 누가 탑뷰를 보는지는 Harbor 역할(`require_roles`)이 먼저 정하고, Fleet은 그 사람의 계정 권한으로 한 번 더 판단한다. 계정에서 권한이 사라지면 ADR 0027의 10초 재확인으로 Harbor 세션이 끝난다.
4. 업무 제안·실행·대조·관측 원본 등록은 이 결정의 대상이 아니다. 사람 세션 만료가 업무 진행을 끊지 않도록 service 신원을 유지한다.

## 운영 전제

- R 허브에 Harbor client가 등록돼 있고 Harbor에 `HARBOR_ROSY_ACCOUNT_FILE`이 있다(ADR 0027).
- 그 파일의 `resource`가 Fleet의 site resource와 **같은** `site:<id>`여야 Fleet이 토큰을 받는다. 다르면 Fleet이 401을 주고 탑뷰는 「ROSY 연결 안 됨」으로 남는다.
- `HARBOR_FLEET_ORIGIN`, `HARBOR_FLEET_CA_FILE`은 여전히 필요하다.
- 탑뷰 카메라는 `workcell-observations`의 카메라 목록과 Fleet vision source 목록에 모두 있어야 한다.

## 결과

- 서비스 토큰 없이도 Rosy 계정으로 로그인한 사람은 탑뷰를 볼 수 있다. Fleet 감사 기록에는 그 사람의 계정 주체가 남는다.
- 탑뷰 이미지 갱신마다 그 사람의 토큰으로 Fleet을 부른다. Fleet은 같은 토큰의 introspect를 10초 동안 재사용한다.

## 검증

`harbor_web/tests/test_topview_proxy.py`의 `TopviewUserTokenTests`: 로그인한 사람의 토큰이 카메라 조회와 이용권 호출에 쓰이고 응답에 나가지 않음, 만료 토큰·세션 없음은 「ROSY 로그인 필요」(Fleet 미호출), 로컬 계정은 서비스 토큰 유지. 운영 R 대조는 하지 않았다.
