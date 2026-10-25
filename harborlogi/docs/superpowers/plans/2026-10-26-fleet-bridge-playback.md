# Fleet 연동과 계획 재생 구현 계획

> **For agentic workers:** Use superpowers:executing-plans for each owned task. Independent playback work may use superpowers:dispatching-parallel-agents. Preserve other workers' edits.

**Goal:** 재생 즉시 종료를 해결하고 설정에서 Fleet의 실제 신원·ARM API를 확인해 기존 제출/대조 경로와 연결한다.

**Architecture:** ADR0013의 Harbor 업무/ROSY 실행 소유를 유지한다. 관리자 설정은 서버가 허용한 origin과 서비스 신원만 관리하고, 토큰·ledger·설치 binding은 배포 서버 환경에서 관리한다. 공개 API의 read-only probe를 명시적 POST 조작으로 실행한다. 상시 업무 실행기와 미수용 물리 계약을 이번 연결 확인으로 활성화하지 않는다.

**Scope:** `fleet_connection.py`(설정·검사), FleetConnection 모델/migration, guide/settings/integration 화면, 기존 commands/pipeline/runtime의 설정 소비. 재생은 yard-3d.js/yard_3d.html과 실제 브라우저 회귀. 기존 domain client/API를 재사용하고 새 ROSY endpoint나 플랫폼 내부 import를 추가하지 않는다.

## 작업

- [ ] 재생을 실제 PC/태블릿·reduced-motion 조건에서 재현한다. 속도·단계·실제 mesh 중간 위치·재생 시간·재생 후 반복을 회귀 검사한다. 출발이 없는 입고 추천은 경로를 꾸미지 않고 배치 비교임을 유지한다.
- [ ] 연결 설정·명시적 검사에 실패 시험을 먼저 추가한다. GET 무통신·무쓰기, 관리자 역할·CSRF, origin allowlist, 토큰 비노출, stale 설정, 기존 Command identity 보존을 확인한다.
- [ ] 서버 환경이 고정한 origin은 읽기 전용으로 유지한다. UI 설정은 배포 allowlist 안에서만 허용하고, 기존 실행이 있는 상태에서 연결 신원을 바꾸지 않는다. 버전과 감사를 보존한다. 토큰은 서버 환경에만 둔다.
- [ ] `GET /api/fleet/session`의 principal/role을 검증하고, 새로운 읽기 전용 probe key의 ARM 조회에서 구조화된 NOT_FOUND를 확인한다. 미설치503·일반404·권한 오류·timeout·잘못된 schema를 구별한다. 성공은 조회 API 지원이며 제출 권한/실물 준비 완료가 아님을 표시한다.
- [ ] 같은 설정 resolver를 현재 요청 제출·조회·콘솔 링크·백업 식별에 연결한다. 토큰 교체·설정 변경 후 예전 검사 성공을 재사용하지 않는다. 기존 원래 key/identity/receipt 검증을 보존한다.
- [ ] 격리 HTTP 서버와 실제 브라우저로 설정→검사→원래 작업 제출/대조를 검증한다. 가능한 고정 실제 Fleet 통합은 별도로 수행하고 환경 부재·skip·실물 미수용을 구분한다.
- [ ] 설계/검증/Confluence 갱신, 독립 검토, 커밋·PR·CI·병합·fork 동기화 후 로컬8060 반영과 최종 smoke를 한다.

완료 기준: 환경 설정만 읽던 화면에서 실제 검사 결과·시각·부족한 조건을 확인하고, 같은 신원이 기존 공개 API 실행 경로에 사용된다. 합성 미리보기는 운영 자격을 상속하지 않는다. 미검증 Fleet·운영 근거·ARM 승인 정책을 완료로 표시하지 않는다.
