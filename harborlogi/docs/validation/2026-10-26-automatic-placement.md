# 자동 배치·변경 기반 재계획 검증

기준: [ADR0010](../adr/0010-automatic-placement-and-replanning.md), [설계22](../design/22-automatic-placement.md). 소프트웨어·합성 자료 시험이며 실제 장치·Fleet/업무 수용과 구분한다.

## 결과

- Django168개 통과. 자동 등록·해제·16요청 상한·실제 actor 권한/감사, 지시·관측·크기 변경, 원본/이전 Plan 불변, 입력 중복 방지, 다른 source 무시·Condition 변경 HOLD, Command 고정, 복수 입고 예약/변경 전파, 조회 무부작용을 확인했다.
- 도메인 unittest148개 중146개 통과, 기존 통합2개 skip. 반출 순서 역전 시 추천 칸 변경, 다칸·회전·전체 지지·전체 목적 위치의 동일 팔 도달·미확인 순서·만석·오래됨·후보 상한·예약의 가상 지지 거절을 검증했다.
- migration drift 없음. 새 `0010_order_auto_replan`은 기존 요청을 기본 해제하고 nullable 관측 binding을 추가한다.
- 실제 Edge의 원래 입고 폼에서 자동 계산을 켜고 추천이2열에 생김을 확인했다. 반출 우선순위를 UI로 바꾸자1열로 자동 변경됐다. 관리자 관측 등록 UI에서 두 화물의 관측 배치를 바꾸자2열1층으로 다시 계산됐다. 이전 Plan·관측은 보존됐고 Command는 생성되지 않았다.
- 실제 관측 등록 UI는 수동 DECLARED 자료만 제공한다. 브라우저 시험도 이 경로를 사용했으며 초기 합성 fixture와 수동 입력 모두 운영 producer로 주장하지 않는다.
- 입고 추천의3D/평면 원본·예상 분리, 자동 해제,1440/1280/768/390 overflow 없음·JS 예외 없음. 기존 전체 제품 브라우저 회귀도 통과했다.

로컬 증거:

```
runs/automatic-planning-django.log
runs/automatic-planning-browser/run-961b5eff52314ccf9a1b33ff8d97dd2f/report.json
runs/automatic-planning-product/run-5dd267220dec4eec82b9f776c57e4496/report.json
```

## 한계

점수는 선출고 방해·순서 미확인·높이에 대한 결정적 휴리스틱이다. 전체 일정 최적해·실측 시간 절감·미래 ETA를 보장하지 않는다. 예약은 실제 화물이나 지지가 아니다. 기존 Command는 자동 교체하지 않으며 다른 Command 이력은 인증된 업무 종료·공간 해제를 입증할 수 없어 자동 계산을 보수적으로 보류한다. 실시간 장비 이벤트·실행 중 요청의 나머지 단계 변경·시간 기반 최적화는 후속 계약/수용이 필요하다.

CE: 해당 없음 — 알고리즘·불변성·예약 경계와 제한이 ADR·코드·회귀 시험에 직접 남아 있다.
