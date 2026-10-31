# 중립 이송 의도와 실제 Fleet HTTP 대조

검증 소스는 플랫폼 commit `a8a7d66121597bec9ee5efa2d25e66e0dbfc75c1`, Harbor commit `0ee812f2ebd9aa83f160534b53d1e711ee755416`다. 이후 문서 커밋을 해당 시험 SHA로 부르지 않는다.

모델 시험 PC의 기존 플랫폼 시험 의존성 환경에서 실행했다. 양쪽 소스를 Git bundle로 전달하고 깨끗한 고정 checkout임을 확인했다. 서버·임시 자격·장부는 격리한 loopback simulation이며 실물 장치나 운영 장부를 사용하지 않았다. child Harbor는 플랫폼 Python 없이 공개 HTTP만 사용했다.

| 검증 | 결과 |
|---|---|
| 운송 전용 Fleet 시작, Cell compiler·recipe 문서 seed 없음 | 통과 |
| 의도 저장 → 명시 TRIP 시작 → 응답 유실 모사 | 통과; UNCERTAIN 유지 |
| 재시작 뒤 두 번째 POST 거절, 원본 key GET 대조 | 통과 |
| 실행 ID/원본 key의 불변 identity·digest·선택 로봇·step 대조 | 통과 |
| 다른 기명 운영자의 원본 key 조회 | 404로 은닉 |
| 실제 simulation TRIP 도착과 attempt 조회 | 통과 |
| 로봇 도착 이후에도 앱 상태는 ACKNOWLEDGED, 업무 완료 주장 없음 | 통과 |
| 기존 Cell compile/proposal/job·후보 중복/충돌·업무 관측 대조 | 기존 명시 HTTP suite 3개 통과 |

명시 운송 suite는 1 test / exit 0 / skip 0, 기존 Cell suite는 3 tests / exit 0 / skip 0이다. 로컬 Harbor 단독 시험은 89 tests / exit 0 / integration 미설정 skip 1이었다. 이 skip을 HTTP 통과로 세지 않았다.

RED Harbor commit `3a4d45978a042ad950519bc50738f7867bde2076`에서는 transport-only 시작이 빈 recipe seed를 참조해 실제 HTTP 시험 전에 KeyError로 실패했다. 빈 공정 seed 경로를 건너뛰고 기존 rearm을 유지한 뒤 위 두 suite가 통과했다. 독립 검토에서 지적한 가변 snapshot 전체 비교도 불변 identity 비교로 수정했다. 배경 tick으로 status/attempt/updated_at이 두 GET 사이에 바뀌는 것은 정상이다.

첫 범위는 화물 없는 로봇 운송이다. 공급암 S의 출발 gate·화물 내려놓기·팔 퇴피·반출 업무 완료를 대신하지 않는다. 중립 팔 동작과 도달 사전 검증·관측 producer·장치/현장 수용은 여전히 후속이다. 플랫폼 FULL 회귀는 별도 작업으로 결과를 확인하며 이 HTTP 통과가 플랫폼 전체 GREEN을 뜻하지 않는다.

CE: 해당 없음 — 새로운 운영 장애 복구 지식이 아니라 시험 하네스 분기의 수정이며 최종 코드·명시 HTTP 시험으로 판단을 복원할 수 있다.
