# WebGL 모델 뷰어·가상 배치·모듈화 검증

2026-10-25. 기본 구현 `20b9f6235c5657d3bcfc1967b514b82ccd9e5650`, 최종 두 손가락 선택 회귀 수정 포함 코드 `1746d6e3d577b787feda32632c737a00fb522e10`. [설계와 모듈 인터페이스](../design/20-container-workbench.md). 관측/계획/ROSY 실행을 바꾸는 기능은 추가하지 않았다.

## 구현

기존 자체 Canvas 투영을 로컬 고정 `three@0.186.1`의 WebGL 모델로 교체했다. 골·모서리·앞/뒤/지붕 ID·조명·그림자와 바닥 구역을 표시한다. 회전/팬/휠·핀치 확대, 키보드 대안, 각도 프리셋, 전체/선택 맞춤, 선택 격리, 전체 화면을 제공한다. 카메라는 단계 재생에서 자동 재설정되지 않는다.

순수 model, GPU/카메라 renderer, 페이지 controller로 분리했다. Renderer는 현재 boxes만 받고 stage/업무/Fleet을 알지 않는다. model은 source를 deep copy하고 경계·겹침·전체 지지·방향과 크기·anchor 일관성을 검사한다. controller는 원본과 별도의 브라우저 메모리 편집본을 만들며 실제 핸들 이동과 구역/행/열/층 입력을 같은 validator로 검사한다. 실패하면 원래 가상 위치로 복원한다. 편집 중 계획 단계/재생/평면 전환은 잠그고 원본 복원으로 풀린다. 저장/관측 수정/계획 생성/장치 요청은 없다.

## 실행한 검사

| 검사 | 실제 결과 |
|---|---|
| Django 전체 |131개 통과; 기존 역할/원본/계획/현장/3D source 회귀 유지 |
| 도메인 기본 discovery |131수집,129실행 통과/2명시적 통합skip; skip을 Fleet 실행으로 표시하지 않음 |
| 새 순수 model |`node tests/yard-model.test.mjs` 통과. 정상·90도·3칸·경계·겹침·부분 지지·아래 화물 제거·누락 ID·비정수·비유한 수·잘못된 anchor·방향/크기 불일치·불변 사본·애니메이션 endpoint 확인. 새 anchor 거절 검사 RED 후 GREEN 확인 |
| 기존 제품 브라우저 |`product_web_browser_check.py --output runs/model-workbench-product`,1440/1280/768/390 통과. 기존 등록/현장/계정/비교·CSRF/권한·복귀·키보드 유지. JS/console/HTTP/실패 요청/넘침0 |
| 모델/가상 편집 브라우저 |실제 WebGL에서 카메라 이동·프리셋·확대·선택 맞춤/격리·전체 화면, 입력 이동·실제 빨간 translation 핸들 드래그,90도 성공/경계 거절·겹침 거절·지원 화물 제거 거절·원본 복원을 확인 |
| 소스 보존 |편집 전후 원본 JSON 동일, 편집 동안 non-GET 요청0, 원래 관측/계획 불변과 실행 intent 없음 확인 |
| 접근/복구 |동작 줄이기·JS 없음·WebGL 미지원·컨텍스트 손실 때 원래 평면도 유지. 실제 `WEBGL_lose_context`로 전체 화면 종료/renderer 해제/평면 복구 확인 |
| 정적/패키지 |ESM 구문 검사,147개 token CSS 일치, migration drift 없음, 공백 검사 통과. wheel 생성 및 세 viewer 모듈·엔진/controls·MIT license 포함 확인 |

PC/모바일 캡처와 앞면 ID를 직접 열어 확인했다. 초기 정면에서 ID가 뒤쪽 면에만 보이는 문제는 앞면/뒷면/지붕의 공통 label texture와 골 바깥 면 위치로 수정했다. 카메라 맞춤은 구형 경계 대신 투영된 모서리의 perspective 제약으로 계산하며 portrait/standard/wide×iso/front/top의9개 조합에서 clip 내부/12% 여백을 확인했다.

기본 성공 원본 report는 Git 제외 `runs/model-workbench-release/run-4fd388efc7934a098fe594c42ccda982/report.json`, 기존 제품 report는 `runs/model-workbench-product/run-96633a440c5c4bf9a096d6173892e881/report.json`이다. 핸들 조작은 renderer 상태를 주입하거나 callback을 직접 호출하지 않았다. 스크롤/캡처 후 실제 화면의 빨간 픽셀로 핸들 위치를 읽고 mouse input을 보내 이동·snap·변경1개를 확인했다. async ESM/demand draw는 완료된 render나 fallback 상태를 기다리며 CSP를 완화하지 않았다.

## 터치 회귀

두 손가락 중 첫 손가락만 이동시키고 두 번째는 정지한 Chromium 터치 입력(CDP 에뮬레이션)에서, 카메라 조작 뒤 정지한 손가락의 박스가 선택되는 RED를 재현했다. 단일 pointer 후보가 두 번째 손가락으로 교체되는 원인이었다. renderer가 active pointer ID와 multi-pointer latch를 유지해 마지막 손가락이 끝날 때까지 클릭 선택을 억제하도록 수정했다. 같은 CDP 입력 회귀와 기존 mouse/단일 pointer 선택·핸들 이동을 함께 재실행해 GREEN을 확인했다. 최종 `runs/model-workbench-touch-green/run-052ff22aed14413fb08b43881bfbdf9b/report.json`은 전체 가상 편집/재생/복구·4개 폭을 포함하며 오류/넘침0이다. 물리 태블릿의 손가락 입력을 측정한 것은 아니다.

## 배포/소유와 제한

npm tarball SHA512를 검증하고 MIT 원문을 보존했다. controls의 bare import2개를 로컬 상대 경로로 바꾸고 upstream core의 indent1개만 정규화했다. 이 외 upstream source/license 동일함을 대조했다. 런타임 CDN을 요구하지 않으며 모델 페이지에서만 엔진을 lazy import한다. 실제 실행 서비스에는 npm/Node를 추가하지 않는다.

논리 격자의 가상 편집이다. 하중/물리 안정성·충돌 회피 경로·실제 팔/로봇 실행·실물 네트워크 수용은 확인하지 않았다. 화면의 보조 구역 offset을 현장 좌표로 사용하지 않는다. 기존 다칸 실행 binding/인증된 업무 근거 게이트는 유지한다.

CE: **해당 없음** — 원인/수정은 renderer의 pointer 추적·순수 validator·실제 gesture 회귀와 현재 모듈 문서로 설명된다. 별도 숨은 장치/운영 지식의 해결 교훈으로 늘리지 않는다.
