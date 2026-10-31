# 독립 서비스 신원과 공개 API 협력 검증

2026-10-27. 구현 SHA `4713a925387be88ceb43581c7cbdfc8f8e0a2b3f`. 후속3d7469d는 legacy 이관 runbook만 추가했다. ADR0015·설계27과 기존 플랫폼 수용 pin을 구분한다.

## 검증한 변화

기존 어댑터는 응답의 creator를 검사했지만 일부 요청 전 신원을 고정하지 않았다. 원문 자격이 설정한 principal과 다르면 요청이 먼저 생성될 수 있었다. RED13개 실패 subcase를 확인하고 named Registry client의 각 공개 API 호출 전에 같은 자격의 `GET /api/fleet/session`으로 principal·service 역할을 확인하도록 고쳤다. 기존 응답 결속·무 redirect·TLS·크기 제한·원래 key 대조를 유지한다.

Harbor 웹 연결은 사람 operator 역할을 거절한다. Registry는 principal과 role을 모두 고정한다. 신원 미고정 legacy 단독 client는 기존 동작이며 강화된 웹 서비스 인증으로 세지 않는다. 기존 operator 배포의 미해결 작업은 원래 소유 경로에서 복구한 뒤 이관해야 한다. 실제 요청별 최종 인증·admission은 Fleet이 소유하며 사전 GET과 요청을 분산 transaction으로 주장하지 않는다.

## 단독·화면 근거

- Django 전체207 PASS. 도메인 unittest161 실행 OK, opt-in 실제 Fleet 클래스1개 skip. 새5개 시험에서 다른 principal·사람 역할·malformed identity·변경된 신원·보호된 조회의 후속 요청0, 올바른 service 취소를 확인했다. 실제 stdlib HTTP 전송에서도 service→operator 변경 뒤 POST가 추가되지 않았다.
- Edge 합성 authenticated HTTP double에서1440/1280/768/390px PASS. GET12개 전부 인증됨, Command0, 잘못된 principal·동일 principal의 operator 역할·일반404 거절, 저장/GET 무통신·역할/비밀 비노출을 확인했다. operator 역할은 ARM 조회 전에 거절된다. 마지막 `runs/service-trust-browser-final/run-bbd8615dcc75471c9c3db19a235937e3/report.json`은 로컬 근거이며 실제 Fleet 실행으로 세지 않는다.
- 독립 Harbor 검토는 호출 전 결속·legacy opt-in 경계·service 역할·문서의 race/권한 소유를 확인했고 P1/P2 없음이다. 플랫폼 reviewer는 실제 session·creator/key scope·별도 operator·observer 계약을 소스 대조했다. reviewer가 직접 시험을 실행한 것으로 표시하지 않는다.

## 고정 양 끝 실제 Fleet HTTP

| 항목 | 값 |
|---|---|
| Harbor | `4713a925387be88ceb43581c7cbdfc8f8e0a2b3f` |
| ROSY | `135035458174c57075c83ea9fd93dd14cf1a0957` |
| 환경 | 공식 model-role 시험 PC, site 제외, 기존 전용 checkout 재사용 |
| 의존성 | native identity `0f1748afc7c48f66` 정상 복구·marker 검증·양쪽 pip check PASS |
| 장치 | 실제 Fleet HTTP·인증·원장, 합성 UDS 장치/관측 |
| 제품 웹 ARM 시나리오 | `--web-arm-only`: 1 PASS / 실패0 / skip0,3.898초 |
| ARM-only 시나리오 | `--arm-only`: 1 PASS / 실패0 / skip0,1.304초 |

`tools.integration.real_fleet_http`에 platform SHA를 명시했다. service 역할의 실제 session, 다른 서비스의 receipt 조회403, 멱등 key·본문/영수증 결속·취소·HOLD 대조와 receipt만으로 업무 완료가 되지 않음을 확인했다. 별도 operator-role 하네스 principal이 승인을 모델링했으며 실제 사람이 현장에서 승인한 시험은 아니다. 앱에 승인/observer 자격을 합치지 않았다. 개별 HTTP GET 총수를 이 두 시험에서 추가 계측한 것으로 주장하지 않는다.

시험 전후 소스는 각각 해당 SHA의 clean 상태였고 공용 소스 cache·동료 파일·운영 서비스·장치를 바꾸지 않았다. 새 clone/worktree 없이 기존 전용 checkout을 재사용했고6GiB 예약 범위에서 실행했다. 플랫폼 전체 pytest를 노트북에서 실행하지 않았다.

## 완료로 세지 않는 것

현재 ROSY local main에 후보가 포함된 것과 PR210 공개 게시·서명된 운영 설치는 다르다. 플랫폼 수용 pin을 이 시험만으로 갱신하지 않는다. 2026-10-27 현재 Windows 기본 DNS에서 운영 Fleet 이름이 해석되지 않고 관리자 권한도 없다. 검토된 단일 hosts 적용의 사용자 선택을 기다리며 우회 TLS·임시 운영 binding·공유 사람 토큰으로 연결을 꾸미지 않았다.

운영 서비스 principal 발급, production ARM 설치/다칸 binding, 인증된 업무 근거 producer, 설계24의 지속 실행기·confirm/finish 연결과 실물/현장 수용은 남아 있다. 사용자 결정의 후속 진행을 UI polling·가상 배치로 대체하지 않는다.

CE: 해당 없음 — 원인·해결·재발 방지가 공통 client 검사·양성/음성 시험·실제 HTTP·ADR/설계에 보존된다. 운영 연결 수용 미완료는 별도로 추적한다.
