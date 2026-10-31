# 공통 공간 보기와 계산 추천 구현 계획

> **For agentic workers:** Use superpowers:executing-plans or superpowers:dispatching-parallel-agents within explicitly owned files. Do not revert other workers' edits.

**Goal:** 같은 공간의2D/3D 선택·편집을 연결하고, 실제 유효 반출 후보를 비교하는 계산 추천을 사용자에게 제공한다.

**Architecture:** [ADR0014](../../adr/0014-linked-spatial-views-and-ranked-planning.md). 좌표/상태 공유는 Harbor 뷰어 내부, 계산/평가는 Harbor domain, 공개 실행/승인/안전은 ROSY 소유다. UI 연결·계산 domain은 독립 파일 소유로 병렬 진행하고 부모가 저장/설명 화면을 조립한다.

**Tech stack:** 기존 Django/Python·표준 라이브러리·ES modules·실제 Edge. 외부 AI/새 패키지 없음.

## 작업과 검증

- [ ] UI: yard_scene의 명시 주소, scoped 선택 bus,2D/3D 연계·빈칸 표식·다칸 점유·draft 갱신. 기존 product/yard 모듈과 pure selection helper를 사용하고 키보드·fallback·모바일·재생을 확인한다.
- [ ] Domain: spatial bounded search가 완전 후보를 평가/비교한다. 원래 복귀 구역/행열 혼란과 같은 구역 변위·층을 비교하며 경로 비용을 발명하지 않는다. oracle·전체 replay·budget·불변성·결정성을 시험한다.
- [ ] 앱: JSON 결과에 평가/대안/진단을 고정하고 설명 화면에서 점수·비교 이유·최적성 한계를 표시한다. 과거 기록에는 계산 자료가 없음을 표시하고 GET에서 재계산하지 않는다. 역할·stale·Command 원본 게이트를 보존한다.
- [ ] 문서: ADR·공간/추천 상세설계·검증 기록을 갱신한다. 계산 추천과 AI 미연결을 구별하며 운영 Fleet 선행 조건을 별도로 유지한다.
- [ ] 실제 브라우저: 관측/계획 단계·선택·전체 점유·빈 위치·로컬 편집의 양쪽 주소 일치, 화면 폭4개,0POST와 원본 보존을 검증한다. 기존 느린 재생 회귀도 실행한다.
- [ ] 검토/게시: 독립 검토, 적절한 domain/Django/브라우저 검사, 커밋·PR·CI·병합·로컬8060 반영·Confluence를 마친다.

## 병행하는 운영 Fleet 작업

사용자의 ‘처리해’에 따라 운영 연결 선행 작업도 재개한다. 생산용 ARM 설치 계약·정식 service 발급과 도구 delivery·journal 복구 검증을 준비한다. 현재 플랫폼 worktree가 운영 상한15를 초과해 신규 생성 전에 소유·해제 조건을 확인해야 한다. 이미 진행 중인 동료 작업을 삭제·reset·우회 clone해서 슬롯을 넘기지 않는다. 이 게이트가 공간/추천의 독립적인 Harbor 작업을 막지는 않는다.
