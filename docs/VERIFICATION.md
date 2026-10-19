# ROSY 제출본 검증 기록

확인일: 2026-10-19(UTC). 실제 로봇은 접근 불가이며, 아래 빌드·Gazebo 결과는 원격 시험 PC에서 얻었습니다.

## 검증 대상

- 팀 저장소 병합 커밋: `f126acd2762c41706b392e9bed000dc3dab9b402`
- 포함된 ROSY 소스 커밋: `38f0c452e410fca6c2c8fc72f45c3c250751d58f`
- 동일성을 확인한 소스 트리: `3a743f1fe396370f419be72daf2f5a7a0b008bec`

팀 저장소 병합 커밋의 `rosy/` 트리와 ROSY 소스 커밋의 루트 트리가 정확히 같습니다. 원격 실행기에는 이 소스 커밋을 Git bundle로 전달해 독립 작업 디렉터리에서 실행했습니다. 이어서 팀 저장소를 별도로 받아 병합 커밋을 고정하고, 실제 `rosy/` 디렉터리에서도 문서의 전체 빌드 절차를 확인했습니다. 이 기록은 원격 PC 시험 결과이며 제출 저장소의 GitHub Actions 실행 결과는 아닙니다.

## 결과

| 항목 | 결과 | 증거 |
|---|---|---|
| 소스 동일성 | 통과 | 두 Git 트리 해시 일치, 8,562개 파일 |
| 전체 패키지 빌드 | 통과 | AI PC, 소스 루트와 실제 제출본에서 각각 27개 패키지 완료, 종료 코드 0 |
| Gazebo와 Fleet 통합 | 통과 | 모델 PC, 로봇 2대 목표 도착, 종료 코드 0 |
| 전체 pytest | 실패 1개 | 22개 호출 완료: 28,895 passed / 1 failed / 1,919 skipped, 실행기 종료 코드 1 |
| 실제 Chromium 회귀 | 실패 5개 | 2개 호출 완료: 1,206 passed / 5 failed / 0 skipped, 실행기 종료 코드 1 |
| 실제 Pinky Pro | 미실행 | 설정된 대상의 SSH 연결 시간 초과, 사용자가 현재 실물 접근 불가 확인 |

전체 빌드는 `colcon_roots.py`가 찾은 패키지에 대해 `--packages-up-to` 제한 없이 수행했습니다. 실제 제출본 빌드는 `--symlink-install`을 사용했으며 설치 후 `ros2 pkg prefix core`로 이번 작업의 설치 경로를 확인했습니다. 소스 루트 빌드는 10개, 실제 제출본 빌드는 11개 패키지에서 setuptools의 `tests_require`·패키지 탐색 설정 또는 CMake 경고가 있었으며 빌드 실패는 없었습니다. 초기 시뮬레이션 의존 패키지 빌드는 별도로 17개가 완료됐습니다.

Gazebo에서는 CORE 준비, 위치 추정, 카메라가 모두 확인됐습니다. `rosy_01`은 0.425m 이동해 목표까지 0.023m, `rosy_02`는 0.401m 이동해 목표까지 0.019m 지점에 도착했습니다. 두 로봇의 상태는 `started → running → arrived`, 결과의 `pass`는 `true`, 종료 후 남은 실행은 없습니다.

### 발견된 실패

`pytest/run-9.txt`에서 `middleware/core/gateway/test/test_station_camera_pipeline.py::test_station_camera_http_pipeline_aligns_then_requires_contact_and_measured_stop[lifespan]`가 실패했습니다. 충전소에서 출발한 뒤 일반 주행 작업이 완료되어야 하는 단계에서 작업이 `QUEUED` 상태에 남아 `ordinary work did not complete` 단언에 걸렸습니다(시험 파일 593행). 이 호출의 결과는 `1 failed, 7134 passed, 64 skipped`이며 원본 `known_failures.py`는 `1 new, 0 known, 0 flaky`로 분류했습니다.

같은 소스 커밋의 해당 항목만 AI PC에서 별도로 실행한 결과는 `1 passed`(125.28초), 최초 실패가 난 모델 PC에서도 `1 passed`(127.32초)이며 두 실행의 종료 코드는 0입니다. 통합 호출에서의 최초 실패와 두 단독 통과만으로 실행 순서나 비동기 타이밍 중 원인을 확정할 수 없습니다. 최초 실패를 기존 실패나 등록된 flaky로 바꾸지 않았으며 전체 시험의 실패 결과를 유지합니다.

전체 시험의 `run-1.txt`부터 `run-22.txt`까지 원본 `known_failures.py`로 각각 비교했습니다. 최초 CORE 실패 1개가 `NEW`이며 다른 21개 로그에서는 실패가 발견되지 않았습니다. 집계는 호출별 실행 횟수의 합으로 중복 실행되는 항목을 포함하며, 단독 재현 결과는 별도입니다. 건너뛴 시험은 통과 수에 포함하지 않습니다.

브라우저 시험은 현장 PC의 격리된 시험 스코프에서 Chromium을 실제 실행했습니다. 첫 호출은 `1157 passed / 4 failed`, 두 번째는 `49 passed / 1 failed`이며 건너뛴 항목은 없습니다. 각 로그의 기존 실패 비교는 다음과 같습니다.

| 분류 | 항목 |
|---|---|
| NEW | `test/learning/perception/test_review_ai_browser.py::test_pixel_unsaved_gesture_blocks_ai_and_result_reload` |
| known | `test/learning/perception/test_pixel_review_browser.py::test_short_desktop_view_keeps_entire_photo_in_view` |
| known | `test/learning/perception/test_review_flow_browser.py::test_review_photo_and_result_are_in_first_view` |
| known | `test/test_dashboard_browser.py::test_an_expired_remembered_token_is_dropped_on_load` |
| known | `test/test_surface_shell_browser.py::test_the_shell_estop_reaches_the_safety_route` |

픽셀 검수의 NEW 항목은 `t`, `Space`, `Alt+ArrowRight` 입력 뒤 초안 상태에 `1획`이 표시되기를 기대했지만 `먼저 픽셀 클래스를 선택하세요.`가 표시돼 91행에서 실패했습니다. 같은 소스 커밋을 AI PC에서 단독 실행해도 `1 failed`(8.24초), 종료 코드 1로 같은 단언이 재현됐습니다. 시험은 `tmp_path`의 합성 workspace를 사용하며 운영 중인 사람 라벨과 검수 상태는 사용하지 않았습니다. 이 결과는 편집 UI 회귀이며 실제 사람 검수, 학습 자료 승인, 모델 정확도의 증거는 아닙니다.

전체 시험과 브라우저 시험에서 등록되지 않은 실패는 합계 2개, 기존 목록의 실패는 4개입니다. `NEW`는 원본의 등록된 실패 목록에 없다는 분류이며 원본과 동일한 소스에서 관측한 결과입니다. 원인 수정이나 실패 목록 변경은 하지 않았고 전체 통과로 판정하지 않습니다.

## 실행과 로그

제출 저장소 루트에서 포함된 `rosy/tools/remote/remote_pytest.py`와 `remote_sim.py`를 실행했습니다. `ROSY_LAB_HOSTS`는 작업 PC의 기존 비공개 시험 호스트 설정을 참조했습니다. 실제 주소·인증 정보는 이 기록에 포함하지 않습니다.

- 전체 빌드: `remote_sim.py --sha <ROSY-source-sha> cmd`를 사용해 격리된 시험 환경 안에서 전체 `colcon build` 실행
- 통합 시뮬레이션: `remote_sim.py --sha <ROSY-source-sha> fleet_trips -- --render gpu`
- 전체 시험: 원본 `lab_full_invocations` 선택 전체를 `remote_pytest.py --sha <ROSY-source-sha> --affected-json -`로 실행
- 브라우저: 중복 파일명을 원본 `pack`으로 분리하고 각 호출에 `ROSY_RUN_BROWSER_TESTS=1` 지정
- 실패 분류: 완료된 각 로그에 원본 `test/known_failures.py` 적용

원본 로그는 작업 PC의 `%TEMP%/rosy-submission-verification/`에 있습니다.

| 경로 | 내용 |
|---|---|
| `build/out/full-build.txt` | 전체 27개 패키지 빌드 출력 |
| `build/out/full-colcon-log/` | 패키지별 빌드 로그 |
| `submitted-build/out/submitted-checkout.txt` | 팀 저장소 병합 커밋을 직접 받은 기록 |
| `submitted-build/out/submitted-build.txt` | 실제 제출본의 전체 27개 패키지 빌드 출력 |
| `submitted-build/out/submitted-core-prefix.txt` | 이번 제출본 빌드의 CORE 설치 경로 확인 |
| `sim/out/colcon.txt` | 시뮬레이션 의존 패키지 빌드 출력 |
| `sim/out/trips/result.json` | 로봇별 도착 상태와 거리, 통합 판정 |
| `pytest/run-*.txt` | 전체 시험 호출별 원본 출력 |
| `browser/run-*.txt` | 실제 Chromium 호출별 원본 출력 |
| `station-repro/run-1.txt` | 충전소 통합 시험 실패 항목의 단독 재현 출력 |
| `station-repro-model/run-1.txt` | 최초 실패가 난 모델 PC에서의 같은 항목 단독 재현 출력 |
| `review-ai-repro/run-1.txt` | 픽셀 검수의 NEW 항목을 AI PC에서 단독 재현한 출력 |

## 남은 범위

실제 로봇의 설치 릴리스, 하드웨어 상태, 센서, 물리 주행은 검증하지 못했습니다. ARM64 릴리스 이미지 생성과 현장 인수 시험도 이번 PC 검증 결과에 포함되지 않습니다. 실물 확인에는 로봇 연결 복구와 현장 관찰이 필요합니다.
