# 2026-10-21 공정 제안·후보 배정·업무 완료 검증

## 소스와 범위

- 앱 기능: `63dc8fb`, 독립 최종 검토의 두 Important 수정: `9be6f89`.
- 플랫폼 ADR D-836, API reference v1.292. 원격 새 시험 RED: `0122aabecc`; 초기 후보 회귀 GREEN: `e07e75a628`; 지도 소실 뒤 재접수 RED: `c0025a51ba`; 수정 회귀 GREEN: `10cf063bc6`.
- D-766의 기존 schema 크기 한계를 지키기 위해 원래 unpublished 이력을 보존하고 `feat/app-execution-ready`에 계약 커밋을 재구성했다. 기존 표시 요청 타입은 동작·canonical import를 유지하며 분리했고 크기 ratchet을 1234→1230으로 낮췄다. 전체 검사는 최신 main을 합친 `058527723f`로 시작했다.

## 실행 근거

- 앱: `python -m unittest discover -s tests -q` **46/46 통과**, skip 없음. 실제 local HTTP socket과 합성 Fleet responder로 compile→proposal→job 조회→이동 근거 대조→최종 하역 확인을 시험했다. redirect/auth 실패, 중복·재시작·불명 POST·문서/공정 digest 변경·기존 장부 보존도 확인했다.
- 플랫폼: 공식 `tools/remote/remote_pytest.py`로 후보 배정·기존 TRIP 작업/lease 시험 **113개 성공**, exit0. 같은 key·본문 충돌·동시 요청·재시작·정지·DB 실패 뒤 lease 정리·profile/권한·지도 소실 뒤 재접수를 포함한다. known failure 비교는 0 new/0 known/0 flaky였다.
- API 소비 호환 시험에서 live-version 변경 이력 행 누락을 발견해 보완했다. 기존 램프 요청·공정 API·version pin도 전체 검증에 포함된다.
- 첫 FULL(`058527723f`): 26개 묶음 중 25개 보고서를 확보했다. 합계 30,693 passed / 1,958 skipped / 2 failed이며 브라우저 한 묶음은 통신 문제로 보고서가 없어 미확인이다. 실패는 신규 TRIP 오류 두 개의 콘솔 문구 누락과 기존 coordinator liveness 타이밍(1.511초, 기대 1.25초 이내)이다. 전체 통과로 간주하지 않는다.
- 콘솔 문구 누락은 단독 실행에서도 실패하고 unchanged main에서는 통과했다. `e1f64b5f87`에 문구를 추가한 뒤 해당 공식 원격 시험이 통과했다. 타이밍 시험은 **동일 `058527723f`의 단독 원격 실행에서 통과**했으며 기준선에 새 실패를 추가하지 않았다.
- 콘솔 문구 커밋도 기존 JS 파일의 크기 제한에 걸려 이력을 보존하고 재구성했다. 경로 편차 표시 순수 함수를 별도 파일로 옮기되 기존 export와 웹 제공 경로를 유지했다. ratchet은 800→788로 낮췄고 `923661701f`에서 9개 커밋의 크기 검사가 통과했다.
- 최신 main의 웹 asset registry와 병합하면서 새 순수 함수도 그 registry에 등록했다. 수정 뒤 FULL은 `10c9c87f565e2262f2773606ddbe42a5a1bdff6f`로 실행했고 **26/26 보고서, 30,959 passed / 1,993 skipped / 0 failed**를 확인했다. 묶음별 합계이므로 일부 중복 실행이 있으며 subtest 수는 별도다. 공식 분류는 모든 묶음에서 **0 new / 0 known / 0 flaky**였다. 브라우저 네 묶음은 137+90+187+151 = **565 passed**, skip 없음. 콘솔 문구와 coordinator liveness도 이번 전체 실행에서 통과했다. lint는 0 errors / 33 warnings다.
- 병합 직전 아키텍처·문서 계약 검사 **250 passed**, 최종 버전·문서 배치·공개 출처 경계 검사 **272 passed**. 각각 0 new / 0 known / 0 flaky다. 공식 `tools/land.py --tests auto`는 exit0으로 완료했으며 검증한 `10c9c87f565e2262f2773606ddbe42a5a1bdff6f`의 main 포함 여부를 별도로 확인했다. 첫 FULL의 실패·미확인 기록은 이력으로 남기며 최종 결과와 섞지 않는다.

로그는 작업 PC 외부 scratch의 `rosy-app-execution-20261021/` 아래에 있으며 제품 저장소·워크트리에 시험 로그를 만들지 않았다. 플랫폼 pytest는 이 PC에서 실행하지 않았다.

플랫폼과 앱 모두 로컬 main에 반영했다. 커밋 재구성으로 빠진 작업 공간의 소유 metadata는 원래 작업 브랜치의 값을 복원했고, main 포함·깨끗한 상태 확인 후 공식 `worktrees.py prune --apply`로 `app-execution`을 회수했다. 보존용 원래 이력과 외부 시험 보고서는 유지한다. 일회성 추가 슬롯의 회수 조건을 충족했으며 push·배포·실제 장치 활성화는 수행하지 않았다.

## 독립 최종 검토와 수정

새 검토자 한 명이 전체 변경을 검토했다. Critical/Minor 없음, Important 두 건이다. 수정 후 재검토 대신 재현 시험의 RED→GREEN과 전체 앱 suite로 확인했다.

1. 늦은 refresh가 다음 단계·COMPLETED·새 attempt를 덮는 문제: `test_race_late_refresh_cannot_overwrite_next_stage`, `test_race_late_refresh_cannot_overwrite_completed_business`, `test_race_old_refresh_cannot_replace_another_readers_new_attempt`가 먼저 실패한 뒤 통과했다.
2. 늦은 compile이 이전 단계/request key를 재제안하는 문제: `test_race_delayed_compile_never_submits_a_previous_stage`가 먼저 실패한 뒤 통과했다.

업무 행의 증가하는 row version을 DB transaction에서 대조하고, 적용된 응답만 조회 cache에 반영한다. 최종 suite는 **46/46**이다. 유보한 Minor는 없다.

## 구현 판단과 남은 수용

- 새 DB unique-index migration 대신 기존 `BEGIN IMMEDIATE` 안의 principal/key 재검사를 사용했다. 같은 namespace의 모든 작성자가 이 규약을 따라야 하며, 위반하면 중복 의도가 생길 수 있다.
- 크기 제한을 높이지 않고 기존 표시 요청과 경로 편차 표시 함수를 분리했다. canonical import/검증 및 웹 제공 경로가 깨질 위험은 기존 API·웹 시험과 전체 공유 계약 검사로 확인한다.
- `MoveEvidence`는 앱 내부의 신뢰된 입력 값이다. 실제 producer 인증이나 ROSY 공개 evidence endpoint를 구현한 것으로 간주하지 않는다. 이 구분이 무너지면 합성 출처 이름을 물리 증거로 오해할 수 있다.
- 검토자가 판단하지 않은 실제 관측 생산자·설치 binding의 물리 정합·운동/충돌/퇴피/하역/충전·배포·실제 양 저장소 통합·규모별 처리량은 별도 수용 대상이다. 운영 수용을 주장하지 않는다.
- 최종 원격 결과는 구현자가 직접 확인해야 한다. 공유 계약 회귀가 남으면 완료·착지로 표시하지 않는다.
