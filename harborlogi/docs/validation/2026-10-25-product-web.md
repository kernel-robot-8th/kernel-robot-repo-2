# Harbor 제품 웹 검증

2026-10-25. 출발 commit `fbbdc42`, 브랜치 `feat/harbor-web-product`. [ADR0007](../adr/0007-authenticated-harbor-web.md), [제품 완성 계획](../design/12-web-product-completion.md), [화면 계약](../design/13-product-web-surface.md)에 따른 구현이다. 이번 문서의 초기 기록은 커밋 전 로컬 검증이며 실제 Fleet HTTP/최종 검토는 아래 후속 결과로 보완한다.

## 구현과 독립 검토

Django 선택 웹 계층에 명시적 역할, 화물/요청/관측 원본/불변 계획, durable 실행 intent, 원래 key 복구, 현장 배정·확인·재배정/회차별 출발 전제, 감사/측정 비교, 환경/장부 writer 소유와 짝 백업/복원을 구현했다. 기존 domain/CLI와 조회 preview는 호환 유지한다.

계획은 독립 architecture/critic round 2에서 승인했다. 구현의 인증/데이터/명령/배포 경계도 각각 독립 검토했다. 발견된 이력 덮어쓰기·취소 crash gap·설치 binding 변경·복원 경로/반복 복원 결손은 회귀 시험과 함께 수정했다. 물리 receipt grant digest와 제안 body digest의 동일 비교 제안은 실제 ROSY producer 및 ADR0006과 맞지 않아 철회했고, 유효한 서로 다른 digest를 받는 시험을 보존했다.

## 로컬 실행 결과

| 검사 | 실제 결과 | 범위 |
|---|---|---|
| 표준 Python `unittest discover -s tests -v` | 117개 실행, OK, skip 2 | 선택 실 Fleet integration 및 기본 환경에 없는 Django 검사 skip. 통합 성공으로 세지 않음 |
| 선택 web venv `harbor_web.manage test harbor_web.tests --noinput` | 63개 실행, OK | 계정/CSRF/역할·불변/동시 버전·원래 key·유실/취소·복원·실제 페이지 |
| `design_tokens.py --check` | 일치 |124개 기존 토큰과 생성 CSS |
| JS syntax, wheel build | 성공 | 새 웹 package 포함. 아래 설치 smoke와 구분 |
| 설치 wheel, 소스 밖 실행 | template/계정 페이지·migration·토큰·브랜드·product CSS 탐색 성공 | wheel 전용 venv; 실제 배포/TLS 증거 아님 |
| 비밀 검사 |91개 파일 중6개 탐지 검토: 고정 시험 값/실행 시 생성·변수 참조, 실제 자격 증명 없음 | 생성된 private access/DB/로그는 검사·Git 대상에서 제외 |

원본 로그·DB·캡처는 gitignored `runs/`에 둔다. 웹 자동 검사는 격리 새 DB/난수 계정으로 실제 Edge 브라우저의 login/CSRF와 공개 웹 동작을 사용한다. 임의로 DB를 초기화하거나 운영 계정을 만들지 않는다.

## 브라우저와 디자인

`tools/integration/product_web_browser_check.py --output runs/product-browser-verdict` 결과 `run-c812297c7cc645959e89301b780efa02`는 **PASSED**다. 1440×1000,1280×800,768×1024,390×844 각각 page/console/request 오류와 페이지 가로 넘침0, 본문16px/제목28px을 확인했다.

실제 UI로 화물·반출 요청 등록/검색/페이지 이동, 현재 관측 선택→3단계 반출 계획, 원본/예상 분리, 현장 배정→본인 CLEAR→같은 회차 출발 전제 한 번 기록, 재배정/이전 탭409, 다른 현장 담당과 viewer의403, 측정 자료 입력/같은 조건 비교와 null을 확인했다. 직접 URL/뒤로 가기/새로고침/키보드 메뉴·Escape·포커스/오류 입력 보존도 포함한다. 이 browser run은 Fleet를 설정하거나 호출하지 않는다.

첫 실제 브라우저 실행은 Windows 정적 자산 prefix 문제를 발견했다. Django5.2 finder가 prefix에 OS 경로 구분자를 써서 URL의 `/`와 맞지 않았다. 고유한 기존 자산 이름으로 탐색하고 `finders` 회귀 및 실제 재실행으로 검증했다. 중복 ID 오류는 업무 용어와 복구 방법으로 바꾸고, 작은 화면의 조치 표를 행별 구성으로 보강했다. 디자인 detector는 Django `{% static %}`를 해석하지 못해 raw template의 검정색 기본값 advisory를 냈으므로 렌더된 색상 통과로 간주하지 않았다. 실제 browser 계산값/캡처로 판단했다.

독립 finish review의 첫 판정은 FIX였다. 모바일 표/오류 문구/현재 제품 문서/부정확한 seed 표기를 고쳤으며, 최종 verdict는 후속 기록한다. 전용 impeccable agent role이 없어 fresh generic reviewer/documenter로 동일 절차를 대체했다. 기존 브랜드와124개 토큰을 계승했고 새 raster는 만들지 않았다.

## 실제 Fleet HTTP와 남은 게이트

`--web-arm-only --app-python <격리 web Python>` 하네스는 준비됐으나 이 초기 기록 시점에는 실제 실행하지 않았다. 지정 test PC에서 깨끗한 양쪽 commit을 고정하고 별도 설치한 web venv로 수행한 결과만 이 절에 추가한다. 기존 조회 앱의 성공 기록을 새 제품 웹의 성공으로 재사용하지 않는다.

운영 인증 관측/배치·관련 팔 전체의 업무 근거 producer, 모든 이동의 설치 binding, 입고 planner, Fleet 위임/교차 신원 TRIP 계약, 현장 장치·LAN/TLS 수용은 남은 게이트다. 실제 단일 ARM HTTP simulation이 통과해도 이 전체를 수용한 것은 아니다. 복원된 새 제출은 보호된 상태로 남으며 외부 대조·운영 복구 절차 전 자동 개방하지 않는다.

## 전체 검토 뒤 보강

`b12589a` 전체 검토에서 같은 요청을 재계획해 불명 실행을 새 key로 우회하는 결손과 과거 지시 내용 소실을 발견했다. 한 요청에 하나의 실행 원본을 DB 제약으로 묶고, 중복된 기존 자료는 삭제하지 않고 migration을 거절한다. 변경 전후·계획 당시 요청/큐 snapshot을 불변으로 저장한다. 요청/관측 변경과 제출 판단의 공유 잠금, 새 관측 revision의 이전 계획 차단도 회귀 시험으로 검증했다. 수정 후 전체 Django **72개 수행·통과**, 독립 검토의 관련53개 및 추가 revision 검사1개도 통과했다.

첫 실제 test-PC의 `b12589a` 실행은 실제 Fleet/독립 승인·관측/합성 UDS owner를 통해 정확히 한 SubmitAction과 GetAction, PRE_DISPATCH/POST_ACTION 근거를 만들었으나 **하네스에 남아 있던 잘못된 두 digest 동일성 검사로 실패**했다. 이 결과를 통합 통과로 세지 않는다. 기존 ADR0006에 맞게 시험을 고치고 새 commit에서 다시 실행한다. 앱은 receipt까지 도달했으며 업무 완료는 여전히 WAITING이었다.

마지막 browser run은 `runs/product-browser-release/run-9dc55b164bb841ee915f60651419cc5e`에서 **PASSED**다. 기존 네 viewport 동작에 추가해 active staff+명시적 admin이 실제 관리 UI로 계정을 만들고 viewer만 배정했으며 operator는 자동 부여되지 않았다. admin CSP/console 오류도0이다. 계정 UI의 많은 자산 요청을 받도록 개발/시험도 운영과 같은 Waitress 한 프로세스를 사용하고, 기본 favicon 경로를 제공한다. GUI를 임의로 클릭해 통과한 것으로 세지 않고 저장한 실제 계정/권한을 대조했다.

독립 finish review의 fix verdict는 네 지적 모두 해결, **ship(해당 수정 범위)**이다. 이후 업무 원본/이력 보강은 별도 전체 코드 검토와 위72개 시험, 마지막 browser run을 따른다. 개발 preview 초기화도 writer lease 안에서만 수행하며 실행 중 두 번째 초기화가 장부를 쓰기 전에 거절되는 것을 확인했다.

## 고정 commit 실제 HTTP 최종 결과

| 항목 | 확인한 값 |
|---|---|
| Harbor 제품 코드 | `885a566a0fe0c32b645b1a57dc1144529d34c6a4` |
| 취소 보류 추가 검증을 포함한 Harbor source | `82e4ecf00855f989c2f3c23a60d7e45576e5114c` (제품 코드는 위와 동일, 통합 시험만 추가) |
| ROSY Platform | `bb38fdcdd0140873e4e9d0133662982c1d6a30e0` |
| 호스트/환경 | 지정 비현장 test PC, 기존 platform 환경과 별도 web venv, `pip check` 성공 |
| 실행 | `tools.integration.real_fleet_http --web-arm-only --app-python <격리 web Python>` |
| 결과 | 각 source에서 1개 수집·1개 수행·OK·skip0, 최종2.852초, exit0 |
| 합성 owner I/O | SubmitAction1, GetAction1, PRE_DISPATCH/POST_ACTION 두 근거 |

실제 Django HTTP 로그인/CSRF→요청/계획→현재 ARM 제안→원래 key 대조→receipt 표시, 중복 제출409, registry 재시작 후 원본 보존, GET 장부 무변경, 부족한 업무 근거로 완료 차단을 확인했다. 취소 요청과 후속 원본 대조 뒤에도 웹 CANCEL_PENDING/업무 HOLD가 유지된다. 이는 취소를 정지/점유 해제 근거로 바꾸지 않는 검사이며 실제 정지나 물리 퇴피의 수용을 의미하지 않는다.

부모 개발 하네스만 실제 플랫폼을 import하고 독립 승인/관측과 합성 UDS owner를 소유한다. 앱 child에는 proposer credential만 전달했고 observer/approver credential과 플랫폼 Python을 넘기지 않았다. 원본 byte 로그는 Git 밖 workspace-records의 `harbor-product-web-http-evidence/product-web-http-82e4ecf00855.txt`에 보존한다. 로그를 공개 저장소로 복사하지 않았다.

최종 wheel은 소스 밖 전용 venv에서 template/계정 경로·전체 migration·favicon·토큰·브랜드·product CSS를 확인했다. 실제 장치·인증 운영 관측·전체 공정/현장 LAN/TLS·외부 게시·GitHub CI는 이 결과에 포함하지 않는다.

CE: 해당 없음 — 새로 발견한 소프트웨어 경계/Windows 동작의 이유와 회귀는 최종 코드·시험·본 검증 기록에 보존된다. 실물 장치나 운영 장애를 해결한 사실로 기록하지 않는다.
