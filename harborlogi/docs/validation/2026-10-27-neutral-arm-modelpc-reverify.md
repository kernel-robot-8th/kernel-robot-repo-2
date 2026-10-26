# 중립 ARM·웹 경로의 재검증 (모델 시험 PC)

2026-10-27. 설계 [27](../design/27-business-executor-implementation-plan.md) P0②가 "팀 시험 PC(Linux)에서 재실행 예정"으로 남긴 중립 ARM `--arm-only`와 `--web-arm-only`를 실제로 실행한 기록이다. 실행 대상은 Harbor 스택의 끝 `a4465b81f973a8d50506eb374f50c8ddcf8ec228`(실행기·근거·이력·다칸·팔별 대응, PR #44 head)와 플랫폼 고정 commit `bb38fdcdd0140873e4e9d0133662982c1d6a30e0`(API v1.312)이다.

[2026-10-24 기록](2026-10-24-neutral-arm-http.md)·[2026-10-25 기록](2026-10-25-operator-pipeline.md)과 플랫폼 고정은 같고 Harbor 쪽이 실행기·근거·이력·다칸·팔별 대응까지 쌓인 지금의 tip이라는 점이 다르다.

## 실행 환경

| 항목 | 값 |
|---|---|
| 호스트 | 공식 model-role 시험 PC(OMEN). site PC 제외 |
| 프로브 | 24코어, load1 1.60, MemAvailable 약 21GiB, `/home` 여유 약 113GiB |
| 예약 | `~/rosy-jobs/harbor-arm-20261027.lock`(2코어/4GiB), 종료 후 제거 |
| 우선순위·상한 | `nice -n 15 ionice -c3`, `timeout 1800` |
| 소스 | `core.autocrlf=false` 자기완결 복제 + `git archive` 성격의 tar 운반, `verify_checkout`이 SHA·깨끗함·toplevel 확인 |
| 의존성 | 하네스용·웹용 `venv --without-pip` + 시스템 pip `--python` 설치, `pip check` 통과 |

두 checkout 모두 실행 전 `git status --porcelain` 결과가 0줄이었다. 플랫폼 핀은 `origin/main`의 조상이 아니라 별도 계열에 있어 증분 bundle이 만들어지지 않았고, Windows 체크아웃을 그대로 tar로 옮기면 CRLF가 `verify_checkout`을 깨뜨렸다. 이 운반 절차와 실패 지점은 [Linux 시험 PC 운반 절차](../solutions/2026-10-27-linux-testpc-shipping.md)에 따로 남겼다.

## 결과

| 명시 실행 | 결과 |
|---|---|
| `real_fleet_http --platform-root <핀> --platform-sha bb38fdcd… --arm-only` | 1실행 통과, 실패0, skip0, 1.171초 |
| 같은 핀 `--web-arm-only --app-python <웹 venv>` | 1실행 통과, 실패0, skip0, 4.170초 |

두 실행 모두 합성 UDS Submit 1회·Get 1회, 물리 근거 `PRE_DISPATCH`·`POST_ACTION` 각 1건을 남겼다. `--web-arm-only`는 설치된 Django 앱의 실제 세션·역할을 거쳐 같은 Fleet HTTP 경로를 통과했다.

업무 원장은 이번에도 `GOAL_CONFIRMED`에서 근거 없는 confirm을 거절했다. 장치 영수증 1건만으로 업무가 완료되지 않는다는 2026-10-24 시나리오가 새 스택에서 그대로 성립한다.

## 이 기록이 세지 않는 것

장치 대역은 synthetic owner이고 관측값은 합성 fixture다. 이 결과는 인증·HTTP/UDS framing·원장·소비자 분리·웹 인증 경로의 재확인이며, 물리 시뮬레이터·센서 보정·native actuator·실물·현장 수용의 근거가 아니다. 플랫폼 전체 pytest를 실행하지 않았고 플랫폼 수용 pin도 갱신하지 않는다. 로컬 기본 unittest·Django suite(각 163·230)는 별개 증거이며 이 표에 합산하지 않는다.

ARM 정책 admission(ROSY D-865), preflight `catalog_digest` 발급·갱신 절차, 운영 서비스 principal, 실제 사람의 현장 승인은 여전히 남아 있다. 이 실행은 그 항목들의 완료를 대신하지 않는다.

CE: 새 교훈 기록 — Linux 시험 PC로 Windows checkout을 옮기는 절차와 다섯 번의 실패 원인을 `docs/solutions/2026-10-27-linux-testpc-shipping.md`에 남겼다.
