# HarborLogi 로고 기반 색상 조정

2026-10-25. 사용자 요청은 기존 로고 색에서 팔레트를 확장하고 이름을 HarborLogi로 표시하는 것이다. 레이아웃·간격·업무 동작은 변경하지 않았다. 원본 아이콘은 그대로 사용한다.

원본 `assets/brand/harbor-app-icon-20261023.png`의 네이비 배경, 청록 판/홈, 오렌지 판, 크림 판 내부 11×11 pixel 평균 대표색은 각각 #112C3F, #2CBBB7/#087C80, #FE6D08, #FCF1D9다. 질감이 있는 래스터의 대표색이며 벡터 원색을 복원했다고 주장하지 않는다.

로고 청록은 메뉴 선택, 짙은 청록은 버튼/링크, 연한 청록은 선택/행 hover에 사용한다. 크림은 보조 원색에 보존하고 넓은 베이지 바탕은 쿨화이트 파생 면으로 바꿨다. 오렌지의 밝은 원색과 대비가 필요한 짙은 톤을 분리했다. 성공/오류의 기능 의미는 보존했다.

토큰은 primitive68/semantic39/component40, 총147개다. 버튼 기본·hover·disabled·focus, 메뉴 선택, 표 header/row-hover/border, 입력 배경/글자/placeholder/border/focus를 별도 alias로 연결했다. 생성 CSS 일치 및 잘못된 참조·순환·타입·이름 충돌 시험2개를 통과했다.

텍스트/배경 역할10쌍 대비는 11.81,5.47,4.99,6.98,5.34,6.09,6.09,7.03,5.48,5.85:1로 AA4.5:1 이상이다. 단순 원색 모두가 흰 글자와 대비를 만족한다는 뜻은 아니며, 실제 용도 alias로 검증했다.

Edge browser `runs/brand-palette-final/run-542a42e0b9f34f77951b046a468606c2`에서1440/1280/768/390px의 실제 계정·요청·계획·현장 확인·관리 화면 동작이 통과했다. 실제 페이지에서도 HarborLogi 표시와 아이콘, 네이비/청록 렌더값, 페이지 오류0·모바일 가로 넘침0을 확인했다. 캡처는 gitignored `.impeccable/review/palette-*.png`다. 독립 팔레트 검토는 이 범위에서 ship이었다.

업무/ROSY API 로직을 변경하지 않아 장치·실제 Fleet 시험은 재실행하지 않았다. 참고한 외부 자료는 [색상 역할 분리](https://atlassian.design/foundations/color/)이며 실제 원색은 HarborLogi 로고를 기준으로 했다.

CE: 해당 없음 — 색상·alias 조정과 검증 기록으로 이유를 보존했다.
