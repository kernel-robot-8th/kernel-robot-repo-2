# 자동 배치·재계획 구현 계획

[ADR0010](../../adr/0010-automatic-placement-and-replanning.md)을 따른다.

1. `harbor/placement.py`와 domain tests: 전체 footprint 후보 검증, 반출 순서 기반 결정적 점수, 대안·HOLD·후보 상한. 기존 관측 불변.
2. `harbor_web/models.py` Order의 자동 설정·관측 binding, migration, `autoplanning.py`와 services hooks: 명시적 자동 등록·16개 상한, 입력 digest 중복 방지, 출처/기준 pin, 새 Plan과 이력, Command 원본 보존. 단위 회귀 시험.
3. 폼·입고 요청/계획 화면·반출 작업 공간: 추천·비교·자동 설정·이력, 실제 관측/예상 장면과 이유. 기존 viewer 모듈 재사용. GET 무부작용·권한·CSRF 검증.
4. UR/SR 추적·용어·설계 지도·검증 문서, 실제 브라우저 변경 시나리오. 기존 도메인·Django·제품 화면 회귀.
5. 코드 검토, 커밋·PR·CI, 요청된 main 병합·fork 동기화와 로컬8060 반영. Confluence에 실제 구현 범위와 미수용 범위 기록.

도메인과 서버 구현은 독립 파일 소유권을 나누어 병렬 진행하고 UI·통합·증거는 주 작업자가 맡는다.

구현·소프트웨어 검증: [검증 기록](../../validation/2026-10-26-automatic-placement.md). 실제 수행·시간 기반 최적화 수용과 구분한다.
