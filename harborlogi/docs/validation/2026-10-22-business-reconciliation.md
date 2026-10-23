# 실제 Fleet와 Port 업무 대조 검증

검증 코드: Port **`e39d62f7406294dc97455f9d73256dd83deff4d1`**, 플랫폼 **`584251615d5609a09ebcb52e18af0cbc0c9beaec`**, Vault 시험 하네스 **`ad7030b319d5796ba45e7287f7dfe170470283b8`**. 세 소스를 실행 전 전체 SHA와 clean 상태로 확인했다. 최종 추가 문서는 이 코드 결과를 기록하며, 다른 SHA를 시험한 것으로 표시하지 않는다.

승인된 비현장 AI 시험 PC의 기존 플랫폼 시험 의존성 환경을 사용했다. 플랫폼은 기존 test cache에서 고정 clone을 만들고 Port·Vault는 Git bundle로 전달해 고정 checkout했다. 서버 조립 프로세스만 Vault/플랫폼 시험 코드를 import했다. Port 시험은 별도 프로세스의 공개 HTTP 소비자이며 런타임 wheel에 하네스가 포함되지 않는다. 서버는 loopback, 자격은 임시 합성 값, 장부는 별도 임시 SQLite다. 실제 장치·운영 원장·사람 검토 DB를 사용하지 않았다.

## 결과

명시 HTTP 통합 suite는 **3 tests / exit 0 / skip 0** (1.887초)이다.

| 검증 | 결과 |
|---|---|
| 설치 문서 compile → proposal → cell job 조회 | 통과 |
| 후보 동일 key 재접수·본문 충돌·busy·viewer 거절 | 통과 |
| 실제 제안 → 별도 승인자 승인 → Fleet GOAL_CONFIRMED | 통과 |
| 목표 확인 뒤에도 업무 관측 전 WAITING 유지 | 통과 |
| 잘못된 출처·시도·화물, 10초 전 관측, 퇴피 미확인 관측 거절 | 통과; 업무 단계 유지 |
| 장부 재시작 뒤 새 Fleet 조회 전 관측 거절 | 통과 |
| 유효한 합성 인계 관측 후 AWAITING_UNLOAD | 통과 |
| 하역 미확인 완료 거절, 별도 합성 하역 관측 후 COMPLETED | 통과 |

별도 승인 HTTP는 시험 actor가 사용하며 Port 런타임의 승인 API를 추가하지 않았다. 시험 설정에는 이 임시 승인자만 추가했다. RED `fd35ff218bde607a009d4fdb0c4d31068c7f388c`에서 해당 설정 누락으로 새 업무 시험이 실패하고 기존 HTTP 시험 2개는 통과했다. 설정 연결 후 위 3개가 모두 통과했다.

일반 앱 unittest는 **47 tests / exit 0 / integration 미설정 skip 1**이다. 이 기본 skip을 실제 연결 성공으로 세지 않았고 위 명시 실행으로 구분했다. 플랫폼의 기존 `test_cell_goal_evidence_api.py`도 같은 SHA의 공식 원격 runner에서 **14 passed / exit 0**였다. 플랫폼 전체 FULL 또는 다른 통합 경로를 실행했다고 주장하지 않는다.

## 증거와 한계

원본 로그는 작업자의 Git 밖 외부 scratch `rosy-port-business-20261022/integration-e39d62f74062.txt`와 `platform-smoke/run-1.txt`에 있다. source SHA·exit code를 함께 보존하며 임시 자격·주소 등 원본 로그를 조직 저장소에 복사하지 않는다.

양성 업무 관측은 시험이 구성한 `synthetic-business-observer`의 명시적 값이다. Fleet의 합성 목표 근거에서 실제 화물 식별·팔 퇴피·하역 사실을 추론하지 않았다. 현재 인증된 현장 관측원에서 원본 revision·시각·cargo/mission/attempt 대응을 읽는 운영 어댑터는 없다. 관측원을 정하고 실제 입력 형식·인증을 제공해야 그 연결과 실물 수용을 검증할 수 있다. 이번 결과는 소프트웨어 HTTP/상태/거절 경계의 증거다.
