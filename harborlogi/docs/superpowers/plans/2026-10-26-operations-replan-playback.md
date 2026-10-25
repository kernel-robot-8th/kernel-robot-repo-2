# 운영 요약·재계획 설명·느린 재생

[ADR0011](../../adr/0011-operations-replan-explanation-and-playback.md) 기준.

1. 읽기 운영 모델·관제 요약: 선택 원본의 재고/점유, 전체 지시, 최신/인계 Plan의 예상 재취급과 근거 없는 완료 미집계. 의미/원본 pin·중복 회귀 검증.
2. 읽기 화물 경로·계획 차이: 화물별 분리→임시→반출/재적재, 이전/현재 경로와 변경. 공간/legacy와HOLD·다칸·크기 pin 시험.
3. 뷰어: 느린 단계별 이동, 속도/진행·가상 편집 이력 재생, reduced motion·중지·원본 보존. 기존 Three.js 모듈 확장.
4. 주 작업자는 overview/workspace/placement 화면 통합·정확한 단위/동선·반응형·브라우저 증거·Confluence를 담당한다. 세 구현의 파일 소유권을 분리한다.
5. 검토·Django/Node/브라우저 회귀·문서·CI, 허가된 커밋/병합/로컬8060 배포.

소프트웨어 구현과 검증 결과는 [검증 기록](../../validation/2026-10-26-operations-replan-playback.md)에 있다. 실제 물동량/실물 수용과 구분한다.
