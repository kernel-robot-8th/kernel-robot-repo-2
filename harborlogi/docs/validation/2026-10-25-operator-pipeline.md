# 페이지별 운영 흐름과 ROSY 파이프라인 검증

2026-10-25. Harbor 구현 `89f5e7e95204647ff32534a22aa340f459ba8cd3`, 실제 HTTP fixture 보정 포함 `b7272cb6856c32c276bbedf0b169a3c4f480ce5f`. 최종 UI 코드 `61768c877edcb75667e3872752ab71b8de9964f0`는 과거 관측 dropdown 보정과126개 Django 시험을 포함한다. HTTP 실행은 아래 b7272cb에 정확히 묶으며 후속 소스를 실행한 것처럼 표시하지 않는다. 후속 문서 커밋은 실행 소스 변경을 뜻하지 않는다.

## 구현과 사용자 결과

- 관측 → 요청 목록/등록 → 작업 공간 → 계획·우선순위 저장에서 선택한 관측을 검증하고 유지한다. 과거 계획의 원본은 고정하고 재검토용 선택과 구별한다. 최근100건 밖의 명시적 원본도 실제 폼 선택에 포함한다. 잘못된 명시적 ID는404다.
- 화물 크기 입력은 최신 원본을 기본값으로 사용한다. 화물에서 요청 등록, 입고 요청에서 같은 요청의 현장 배정으로 이동한다.
- 현장 확인의0~5분 유효 시간을 서버에서 계산한다. 만료·미래 시각·불일치에서는 출발 전제 버튼 대신 새 회차 배정을 제공하며 원래 확인 시각은 보존한다. 조회 전용 계정에는 접근할 수 없는 현장 링크를 표시하지 않는다.
- ‘실행·대조’는 Harbor 제출, Fleet 독립 승인, 동작, 물리 receipt, 업무 완료를 분리하고 상태에 맞는 주 조작을 제공한다. 불명 상태는 원래 key 대조를 우선한다. 화면 조회는 네트워크나 장부 writer를 열지 않고 저장된 fact의 실제 read_at을 표시한다.
- 기록은 객체 종류/요청 범위로 묶어 같은 숫자 ID의 다른 객체가 섞이지 않는다. `/workflow/`는 역할별 안내, `/settings/integration/`은 관리자 전용 설정·수용 조건이다.
- 로고·147개 토큰·Django를 유지하고 기존 navy/teal/orange 의미 색, 원본·판단·다음 조치 계층과 반응형 파이프라인을 사용한다.

## Harbor 검증

| 검사 | 실제 결과 |
|---|---|
| Django 전체 | `django test harbor_web.tests --settings=harbor_web.settings --verbosity 0`: **126통과** |
| 도메인/기존 계약 | `python -m unittest discover -s tests -q`:131수집, **129실행 통과 / 명시적 통합2skip**. skip을 실행으로 세지 않음 |
| 원본/역할 회귀 | 새 context6 + pipeline9 + operator journey4. reviewer가 발견한 폼 action의 선택 유실, 계획 URL 검증 누락, viewer의403 링크를 수정하고 회귀 확인. 최근100건 밖의 원본이 재고 dropdown에도 selected로 유지되는 실제 HTML을 추가 확인 |
| 제품 브라우저 | `product_web_browser_check.py`,1440/1280/768/390, **통과**. JS/console/실패 요청/오류 HTTP/넘침0; 현장 회차·CSRF·직접 경로·관리 계정·뒤로 가기·메뉴 키보드 확인 |
| 공간·맥락 브라우저 | `multicell_browser_check.py`,같은4개 폭 **통과**. 실제 관측→요청→원본 선택 유지, 원자적3회 이동, colspan/rowspan, 독립 층, JS 없는 조회, 안내 화면 넘침0 |
| 토큰·migration·공백 | 생성 CSS 일치, `makemigrations --check --dry-run` 변경 없음, `git diff --check` 통과 |
| 배포 패키지 | 격리된 build 의존성으로 wheel 생성 통과. 새 pipeline/guide 모듈,3개 새 템플릿과 제품 CSS 포함 확인. 실행용 venv에는 setuptools가 없어 no-build-isolation 시도는 실패했으며 격리 빌드로 확인 |
| 미리보기 | 격리된 합성 DB를 사용하는127.0.0.1:8060의 요청/안내/연동 페이지200·넘침없음. Fleet 연결 없음 |

브라우저 원본 report는 Git 제외 `runs/operator-product-browser/run-0d828028ce8a469281b259f531d75c7f/report.json`, `runs/operator-multicell-browser/run-1db3cbc6548c4905aa2eba9772901a73/report.json`에 있다. 데스크톱 캡처를 직접 열어 정보 계층과 지도/파이프라인 표현을 확인했다. 초기 제품 브라우저의 이전 버튼명 탐색 실패는 새 회차 버튼명으로 시험을 맞춘 뒤 전체 재실행했다.

## 실제 HTTP와 Fleet 화면은 별도로 바인딩한다

Harbor **b7272cb6856c32c276bbedf0b169a3c4f480ce5f** ↔ ROSY 중립 ARM producer **bb38fdcdd0140873e4e9d0133662982c1d6a30e0**: 공식 시험 PC에서 `tools.integration.real_fleet_http --web-arm-only` **1통과**. 설치된 웹 패키지의 실제 세션·CSRF·역할을 거쳐 제안→독립 승인→합성 UDS owner→PRE_DISPATCH/POST_ACTION→검증된 `AT_TARGET_AND_CLEAR` receipt를 대조했다. 제출은 정확히1회, owner 조회1회였다. GET 뒤 장부 불변, 전체 grant digest와 transfer body digest 구분, DB 재개방, 잘못된 재제출409, 취소 뒤 업무 HOLD 유지, 앱 프로세스에 플랫폼 import 없음도 확인했다. `pip check` 통과.

첫 HTTP 실행은89f5e7에서 시험 fixture가 필수 `length_cells`를 누락해 화물을 저장하지 못했다. 필드 추가와 저장 결과 단언 후 b7272cb 전체 실행이 통과했다. 제품 검증을 우회하거나 폼을 약하게 만들지 않았다. 비공개 원시 로그는 작업 공간 기록 `2026-10-25/harbor-product-web-http-evidence/product-web-http-b7272cb6856c.txt`에 보관하고 주소·자격증명·DB를 공개하지 않는다.

새 Fleet 승인 UI는 별도 **915a46da52e14032671d3ecd4a360b60d3ea586f**다. 원격 Chromium **8통과 /0skip**, 알려진 실패 비교 **0NEW**. 7개 API fixture 화면 회귀와1개 실제 HTTP 서버 회귀를 구분한다. 실제 서버 회귀는 제안자 `harbor`와 승인자 `operator`를 유지하며 조회 때 POST 없음, 명시 확인 뒤 현재 digest/generation으로 admit하여 실제 READY/approved_by를 확인했다. `/common` 자산이 없는 API-only 시험 fixture는 실제 shared web을 제공하도록 보정했다. 이전 소스900f5e7의 초기 콘솔 auth 초기화 실패는 코드3bf85c7에서 수정했으며 실패 로그를 성공 근거로 쓰지 않는다.3bf85c7과 최종915a46의 차이는 해당 브라우저 시험 파일뿐이다.

최종 UI 코드 **bfa3872d0cb59bfba35384dce51836c065696bd2**는 로그인 복귀 query 보존·192자 ID 대응·공통 버튼/입력 규칙을 포함한다. 원격 브라우저9개(ARM8 + 기존 로그인1) **통과/0skip**, 공통 UI·구조·feature matrix87개 **통과/0skip**이며 각각 알려진 실패 비교0NEW다. Node365개도 통과했다. 최종9개 브라우저에는 실제 HTTP 서버의 독립 admit 사례가 포함돼 있으며 앞선915 실행만으로 최신 코드를 대체하지 않았다.

ROSY 최종 기록 커밋은 `d375eb4a5914971ef3fa879110c40405ddbba2cd`이며 bfa 이후 변경은 시험 등록/문서다. 정본 검증 기록은 플랫폼 `docs/validation/harbor-arm-console-2026-10-25/result.md`에 있다. 해당 최종 UI 소스로 Node365개를 다시 실행해 통과했고 작업 트리는 clean이다. ROSY push/main 착지/장치 배포는 수행하지 않았다.

넓은 영향 검사는 앞선 UI 소스3bf85c7에서 두 partition을 실행했다. 각각 **4실패/4801통과/248skip**, **4실패/6717통과/343skip**로 전역 GREEN이 아니다. 6건은 새 UI의 feature matrix·버튼/필드·모듈 크기·문서별 import 소유·확인창 호출 등록 누락이었다. 제품/등록을 수정해 bfa의87개 검사와 후속 `f3a818e997`의 소유/import·확인창4개 검사를 원격에서 **통과/0skip/0NEW**로 확인했다. 검사를 삭제하거나 기존 assertion을 약하게 바꾸지 않았다.

나머지2건은 G1의 tight-segment liveness와 실제 UDS round-trip 시간 한도다. f3a의 단독 재실행도2실패/2통과여서 병렬 부하 탓으로 단정하지 않았다. 이 작업의 G1/owner 소스 diff는 비어 있다. 현재 main `f4d06e45438de90e473dea2ef7fad37bdc857ef6`에서도 공식 원격 실행이 처음2실패/5통과였고, main에 이미 있던 quarantine 재시험에서2통과했다. main 자신의 known-failure 분류는 **0NEW/0known/2FLAKY**였다. 오래된 기능 브랜치의 원본2NEW를 main의 분류로 소급해 지우지 않는다. 시간 한도·quarantine·코어 코드를 바꾸지 않았으며, 엄격한 시간 검증의 불안정성은 미해결로 남는다. 최종 소스의 전체 영향 검사를 다시 GREEN으로 실행했다고 주장하지 않는다.

Harbor HTTP 시험이 새 Fleet UI를 조작한 것은 아니다. 반대로 Fleet UI 시험이 Harbor 전체 업무를 완료한 것도 아니다. 양쪽은 같은 기존 공개 API를 각각 소비하며 실물 장치와 물리 truth를 합성하지 않는다. Fleet 화면은 job 상태를 표시하며 상세 receipt 조회·검증은 Harbor 원본 대조에서 확인했다.

## 운영 개방과 남은 범위

조사 시점 ROSY main46f7a에는 중립 ARM API가 없었다. 이후 다른 세션이 main을 갱신했으므로 이 조사만으로 현재 main 수용을 선언하지 않는다. Fleet 기능 브랜치의 새 승인 화면과 필요한 API를 실제로 배포한 뒤에만 `HARBOR_FLEET_ARM_CONSOLE_ENABLED=1`을 선언한다. 선언 자체는 인증/API/실물 수용의 증거가 아니다. 일반 콘솔 인계는 지원하며 자동 승인·자동 재시도·직접 장치 제어는 없다.

ROSY native `tools/land.py --tests auto --dry-run --no-prune`은 실제 병합 충돌로 중단됐다. 최종 재검사는 topic `ea49206cbf`, 검사 전후 main `f4d06e45438de90e473dea2ef7fad37bdc857ef6`이 같음을 기록했으며22개 경로에서 수동 해결이 필요하다. 초기 사전 검사에서는 main SHA를 바로 기록하지 않아 그때의 기준을 추정하지 않는다. 기존 주제는 자산/UI 외 protocol·Fleet dispatch·OMX·API reference까지 얽혀 있으며 새 화면만 복사해 필요한 producer를 생략하지 않았다. 현재 새 화면은 기능 브랜치에 커밋돼 있으며 ROSY main 착지/배포는 별도 전체 주제 통합·충돌 해결·수용 게이트가 필요하다. 사전 검사는 checkout을 변경하지 않았고 다른 세션의 main 미추적 파일도 보존했다.

다칸 실행 binding, 운영 관측/업무 근거 어댑터, 실제 위치·하중·보정·관련 팔 퇴피와 실물 수용은 미완료다. `SPATIAL_EXECUTION_BINDINGS_REQUIRED`와 업무 근거 gate를 유지한다. 이번 HTTP는 기존 단일1×1 합성 ARM 인계이며3×3×3 다칸 작업의 실제 수용을 증명하지 않는다. 출발 전제는 TRIP 실행이나 차량 출발 증명이 아니다.

CE: **해당 없음** — 해결한 선택 유실·권한 링크·만료·fixture 필수값과 초기화 문제의 원인/예방을 코드와 회귀 시험·현재 계약 문서에서 바로 확인할 수 있다. G1 시간 실패는 해결 교훈으로 만들지 않고 위의 증상·비교 근거·미해결 상태로 보존한다. 후속은 같은 엄격한 시간 시험의 원인 분리이며 UI 구현 성공을 그 해결로 대신하지 않는다.
