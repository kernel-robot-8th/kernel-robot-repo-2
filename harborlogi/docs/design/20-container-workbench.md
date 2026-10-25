# 컨테이너 모델 뷰어와 가상 배치 편집

mode: Operate. 사용자 확정: 카메라 이동과 박스 배치 가상 편집 모두 제공한다. 기존 브랜드와 Django·관측/계획/실행 경계는 유지한다.

Three.js의 로컬 고정 배포본으로 WebGL 모델·면/골·조명·그림자를 표현한다. OrbitControls는 회전/팬/확대, TransformControls는 선택 화물의 격자 이동 도구다. 외부 CDN을 실행 시 호출하지 않는다. 기본은 전체 맞춤이며 선택 맞춤·선택만 보기·각도 프리셋·전체화면·터치/키보드 대안을 제공한다. 카메라 이동은 재생 중 자동 framing에 의해 취소되지 않는다.

모듈은 DOM/업무를 모르는 scene/draft 모델, WebGL/카메라/선택 도구 렌더러, 페이지별 stage와 UI를 연결하는 controller로 나눈다. source scene은 불변 입력이고 renderer는 현재 boxes만 받는다. 숨김/재사용/종료 시 이벤트·GPU 자원을 정리한다. WebGL을 사용할 수 없으면 평면도와 원본 조회를 유지한다.

가상 편집은 선택한 stage를 브라우저 메모리로 복사해 시작한다. 박스 이동 핸들이나 구역·행·열·층·명시한 방향 입력을 사용한다. 모든 점유 면적에 대해 경계·겹침·아래층 전체 지지를 검사하고 실패하면 원래 가상 위치로 복원한다. 아래 화물을 옮겨 위 화물이 뜨는 것도 거절한다. 이는 논리 검사이며 하중·물리 안정성·실제 이동 경로 검증이 아니다.

가상 편집 중 배지·원본 위치/가상 위치·변경 수를 표시하고 계획 재생/단계 변경/평면 전환을 잠근다. 원본 복원으로 로컬 변경을 버리고 관측/계획 조회로 돌아간다. 서버 저장·관측 revision 변경·새 계획 생성·Fleet 명령·완료 판정은 추가하지 않는다.

수용: 카메라 팬/확대·선택 맞춤/격리·실제 핸들 이동·입력 이동·90도 점유·경계/겹침/지지 거절·복원·재생 동기화·원본 불변,4개 화면 폭·키보드·reduced-motion·WebGL 실패·공통 회귀를 확인한다.

## 모듈 인터페이스

| 모듈 | 호출자에게 제공하는 기능 | 소유하지 않는 것 |
|---|---|---|
| `yard-model.mjs` | `cloneBoxes`, `validatePlacement`, `moveBox`, `placementFromWorld`, `transit` | DOM·WebGL·업무 계획·네트워크 |
| `yard-renderer.js` | `createRenderer(canvas, areas, callbacks)` → `setBoxes/select/setMode/setEditable/fit/view/zoom/isolate/resize/dispose` | 페이지 route·계획 단계·가상 배치 승인 |
| `yard-3d.js` | `mountYardViewer(figure)` → Promise의`destroy`; 공통 template 자동 mount, 단계·재생·로컬 편집 연결 | 저장·관측 producer·Fleet 실행 |

`areas`와 `boxes`는 기존 `yard_scene.py`의 값이다. 논리 x는 열, y는 행, z는 층의0기준 anchor이며 Three 좌표로는 X/Y/Z=(x,z,y)로 표시한다. 실제 단위/좌표가 아니다. model은 source/반환값을 분리하며 방향과 width/depth·절대 anchor의 일관성을 검사한다. Renderer의 `onSelect(id)`는 선택만, `onTransform(id,{x,y,z})`는 핸들 이동의 raw anchor만 돌려준다. controller가 이를 격자로 변환해 model에 검증시킨 뒤 성공/복구된 boxes를 다시 그린다.

각 뷰어는 독립된 `data-yard-preview`와 공통 `yard_3d.html`을 사용한다. 같은 페이지에 여러 개를 놓으면 include에 서로 다른 `yard_scene_id`를 준다. `destroy`는 해당 DOM 인스턴스 종료용이다. 정상 BFcache 복귀에서는 인스턴스를 보존하고 크기만 갱신한다. WebGL2를 만들 수 없거나 컨텍스트가 끊기면 renderer를 종료하고 원래 평면도로 복구하며 전체 화면도 종료한다. 입력은 source로부터 만든 메모리 사본이며 새로고침/원본 복원으로 버린다.

엔진은 npm `three@0.186.1`을 SHA512로 확인한 로컬 사본이다. MIT license와 출처/수정한 import 두 줄을 vendor README에 보존한다. [OrbitControls 공식 문서](https://threejs.org/docs/pages/OrbitControls.html)와 [TransformControls 공식 문서](https://threejs.org/docs/pages/TransformControls.html)의 인터페이스를 사용한다. 모델이 있는 페이지에서만 renderer/engine을 동적으로 읽으며 CDN이나 실행 시 npm 설치를 요구하지 않는다.
