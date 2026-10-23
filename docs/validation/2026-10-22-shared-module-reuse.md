# 2026-10-22 — 실제 Fleet HTTP 계약 검증

검증 소스: Port `5d6c4ba39c297e30bed3fef82d32a3b94b544a8d`, 플랫폼 `238af6c8990fd282995a32996f504af2239c9b95`, 시험용 Vault 하네스 `ad7030b319d5796ba45e7287f7dfe170470283b8`. 플랫폼 D-841과 Port ADR 0002에 따른 개발·simulation 범위다.

승인된 AI 시험 PC로 Port·Vault Git bundle을 전달해 검증하고, 플랫폼은 해당 PC의 기존 Git object 저장소에서 고정 SHA로 checkout했다. 세 소스는 실행 전 전체 SHA와 깨끗한 상태를 확인했다. 실제 운영 서버·원장·장치는 사용하지 않았다.

`python -m tools.integration.real_fleet_http --platform-root <고정-root> --platform-sha <전체-SHA> --vault-root <고정-Vault-root> --vault-sha <전체-SHA>`를 플랫폼 시험 의존성 환경에서 실행했다. 하네스 조립 프로세스만 플랫폼·Vault 시험 코드를 import하고, 별도 Port unittest 프로세스는 기존 RosyClient로 loopback HTTP를 소비했다. 임시 합성 operator/viewer 자격과 임시 장부는 실행 종료 후 정리됐다.

| 시험 | 결과 | 한계 |
|---|---|---|
| Port 전체 `unittest discover -s tests -v` | 47 tests, exit 0; 통합 미설정 skip 1 | 기본 suite만으로 실제 연동을 주장하지 않는다 |
| 설치 문서 compile → proposal → cell job 조회 | 통과 | 실제 Fleet compiler·route·장부, 장치 transport는 fake |
| 후보 요청의 동일 key 재접수·본문 충돌·busy·viewer 거절 | 통과 | 같은 mission 재사용, conflict/busy 409, viewer 403 |
| 명시 HTTP 통합 suite | 2 tests, exit 0, skip 0 | 고정 simulation 프로필만 검증 |
| 명시 모드의 설정 누락 | 예상 exit 5 실패 확인 | skip으로 성공 처리하지 않는다 |

원본 로그는 실행 PC의 외부 scratch `%TEMP%/rosy-shared-reuse/apps/run-1.txt`에 보존한다. 이 로그는 Port 성공과 최초 Vault 환경 시도의 실패도 함께 보존한다. 원본의 비밀·네트워크 정보를 공개 저장소에 복사하지 않는다.

로그 SHA-256: `272b4fbe0574d6d5e80f5d10f6d7173943aa144f05dded26b5c221374362d76b`.

Port 업무 관측에 필요한 인증된 producer → MoveEvidence 운영 어댑터는 여전히 미구현이다. 합성 목표 관측·가짜 팔의 성공은 실물 화물 인계·퇴피·하역 완료 증거가 아니다. 런타임 의존성·공개 API·운영 배치·장치 활성화는 변경하지 않았다.
