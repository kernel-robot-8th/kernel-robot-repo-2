# 관제 물량·재계획 경로·느린 재생 검증

[ADR0011](../adr/0011-operations-replan-explanation-and-playback.md), [설계23](../design/23-operations-replan-playback.md)을 따른다. 실제 Edge 웹 화면이며 화물·관측 자료는 합성이다.

## 확인 결과

- Django183개 통과. 다칸 개체/점유 칸 구분, 관측 pin·미관측·오래됨/새 원본, 요청별 최신 Plan 중복 방지, Command 원본 별도 집계, 실제 완료 미집계, 읽기 무부작용과 role을 확인했다.
- 도메인148개 중146개 통과·기존 통합2개 skip, 기존 Node 가상 배치·지지·회전·불변성 시험 통과. 계산·실행 API와 DB schema를 변경하지 않았다.
- 화물별 출발·임시·반출/복귀, 단계·팔·크기 원본과 이전/현재 경로의 추가·제외·변경·유지를 legacy/공간/입고 추천에서 검증했다. HOLD는 부분 이동을 표시하지 않고 다른 요청의 이전 Plan을 비교하지 않는다.
- 실제 브라우저로4.5초 이동의 중간 렌더 mesh 위치(들어 올림·이동·내려놓기), 속도 변경, 가상 편집 이력의 순서·입력 잠금·중지 후 최종 draft 복원을 확인했다. 원본 JSON과 서버는 바뀌지 않았다.
- 기존 WebGL 실제 핸들 이동, 화면 이동/각도/확대·전체화면·두 손가락·context loss 복구·reduced motion·JS 없는 평면도 회귀가 통과했다.1440/1280/768/390에서 overflow 없음·콘솔 오류 없음.
- 전체 제품 브라우저 회귀 통과. 별도 캡처에서 관제·경로·전후 비교·선택 화물·느린 중간 프레임·재적재 평면·태블릿/모바일8장과 실제 브라우저 WebM 영상을 남겼다. 캡처 전후 Plan·Observation·Command 개수 불변, JS 예외 없음.

로컬 증거(접근 파일·DB·원본 영상은 공개 Git에 넣지 않음):

```
runs/operations-django-final.log
runs/operations-playback-fixed/run-49e01c6701c34160974f02c8b1c5a890/report.json
runs/operations-product-ready/run-bc0a3c51800345589de37e3a2e6ff808/report.json
runs/operations-evidence-2026-10-26/report.json
runs/operations-evidence-2026-10-26/slow-plan-playback.webm
```

## 실제 실행에서 찾은 보정과 한계

재생의 새 구간이 시작될 때 직전 진행률이 잠깐 남는 문제가 실제 브라우저에서 발견되어 새 구간0%를 즉시 표시하도록 고쳤다. 입력 잠금은 fieldset의 DOM disabled와 실제 input/submit의 비활성 상태로 확인했다(Playwright의 fieldset.is_disabled 판정과 구분).

디자인 탐지기는 Django static 템플릿 해석에 제한이 있어 실제 렌더를 직접 확인했다. 방향만 바꾸는 비교와 관측 출발이 없는 입고 추천은 즉시 비교로 명시한다. 실제 로봇 경로·시간·물리 안전을 검증하지 않았다. 처리 실적·TEU·ton·시간당 물동량을 추정해 만들지 않았다.

CE: 해당 없음 — 발견한 진행률·검사 의미의 차이를 코드 주석과 실제 브라우저 회귀 검사에 보존했다.
