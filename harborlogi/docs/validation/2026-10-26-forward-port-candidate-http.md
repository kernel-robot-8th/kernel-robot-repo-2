# 최신 ROSY 후보와 Harbor 실제 HTTP 계약 검증

2026-10-26. Harbor의 연결 소비자와 ROSY 후보의 호환성을 검증한 기록이다. 운영 배포·실물 장치 수용이나 플랫폼 계약 pin 갱신을 뜻하지 않는다.

## 소스와 실행 환경

| 항목 | 검증한 값 |
|---|---|
| Harbor | `f806bbf0c9ce330f4571e96c74a5b78c00449850` |
| ROSY 후보 | `41f7445dd649c663c2b487b1776b843d7d62f13d` |
| 소스 상태 | 두 checkout 모두 해당 커밋의 clean 상태 |
| 배치 | 공식 비현장 AI·model 시험 PC, 자원 예약·메모리 제한 적용 |
| Fleet | 실제 HTTP 서버와 실제 인증·장부·작업 경로 |
| 장치·관측 | 격리된 합성 UDS 장치와 독립 observer |

`tools.integration.real_fleet_http`의 명시적인 `--platform-sha`로 후보를 고정했다. 기존 수용 pin의 의미를 바꾸거나 ROSY 내부 import를 Harbor에 추가하지 않았다. 이 기록 당시 후보의 정상 FULL·착지·게시 검증은 진행 중이었다.

## 결과

| 시나리오 | 결과 | 시간 |
|---|---|---|
| Django 제품 경로 → 실제 Fleet HTTP | 1 PASS, 실패0, skip0 | 6.342초 |
| 기존 ARM-only HTTP 계약 경로 | 1 PASS, 실패0, skip0 | 1.153초 |

실제 세션의 역할이 `service`임을 확인했다. 하네스의 별도 `operator` 역할 principal로 승인해 운영자의 독립 승인을 모델링했으며, 사람이 직접 승인한 현장 시험을 뜻하지 않는다. 앱에는 승인 역할을 합치지 않는다. 다른 service principal의 영수증 조회는 HTTP403으로 거절했다. 재시작에도 안정적인 멱등 키, 요청 본문·영수증 결속, 취소, 결과 대조의 HOLD를 확인했다. 물리 실행 영수증만으로 업무 완료를 만들지 않고 독립 업무 근거를 기다리는 동작을 유지했다.

두 시나리오의 합계는2 PASS/실패0/skip0이다. 런타임 의존성 검증도 통과했다. Django fixture의 static 디렉터리 부재 경고1건은 비차단 경고로 남았다. 시험 종료 후 시험 전용 예약을 해제했으며 다른 FULL 작업을 중단하지 않았다.

## 적용 한계

운영 principal 생성, 운영 Fleet/Harbor 설정, 서명된 image payload 구성·설치, 신뢰 verifier 교체, 미수용 production installation binding과 실물 실행은 수행하지 않았다. 당시 운영 Fleet과 후보의 상태를 혼동하지 않는다. [플랫폼 계약](../platform-contract.md)의 수용 pin은 이 시험만으로 변경하지 않는다.

CE: 해당 없음 — 기존 공개 계약 시나리오를 새 후보에 적용한 호환성 확인이며, 운영 연결의 미완료 조건은 별도로 추적한다.
