# ADR 0007 — 계정과 영속 업무를 갖춘 Harbor 웹

상태: 채택, 2026-10-25. 구현·검증 진행. [제품 완성 계획](../design/12-web-product-completion.md) v0.2는 독립 architecture/critic 두 검토의 round 2에서 승인했다. 계획 승인은 현장 수용이 아니다.

## 문제

기존 표준 WSGI 조회 앱은 업무 관측과 ROSY 원본을 읽지만 계정·역할, 업무 입력, 데이터 변경 이력을 제공하지 않는다. 이 앱에 수동 세션/비밀번호/DB migration을 덧붙이면 업무와 인증·실행 경계가 섞인다. 사용자 기준은 PC 중심+현장 태블릿 보조, 브라우저 웹, 조치 필요 첫 화면, 팀 자체 계정과 역할별 권한이다.

## 결정

Harbor 안에 선택 설치하는 Django 5.2 LTS 단일 웹 계층을 둔다. 서버 템플릿과 작은 탐색 JavaScript를 사용하며 기존 Python domain/CLI는 Django 없이 유지한다. Django auth·session·CSRF·password validator, DB-backed django-axes를 사용한다. 초기 단일 사이트/프로세스는 SQLite와 Waitress, 정적 자산은 WhiteNoise, 외부 연결은 TLS proxy를 사용한다. 실제 배포는 별도 검증한다.

역할은 명시적인 `viewer`, `operator`, `field`, `admin` membership이다. superuser나 관리 역할이 업무 실행 역할을 암묵적으로 얻지 않는다. Fleet의 named operator 승인·안전·재무장 권한은 별개다. TRIP 생성은 실행을 시작하므로 Harbor 서비스 계정으로 사람을 대신하지 않고 Fleet로 인계한다.

새 제품 DB는 화물·요청·관측 원본·불변 계획·현장 회차·감사를 소유한다. 기존 업무 장부는 현재 단계와 완료 판정을 소유한다. 두 DB 사이의 원자성을 가정하지 않는다. 외부 제출 전 durable intent와 dispatch marker를 commit하고, 불명/재시작은 원래 key GET으로만 대조한다. OS writer lease를 생성자/복구 전에 획득하고 한 registry가 장부를 소유한다. 복원된 실행은 외부 대조·운영 검토가 끝나기 전 새 제출을 보류한다.

물리 참조는 설치된 binding으로만 변환한다. 수동/합성 관측을 인증된 운영 사실로 승격하지 않는다. 물리 receipt의 grant digest와 제안 body digest는 의미가 다르다([ADR0006](0006-neutral-arm-transfer-binding.md)). receipt 하나로 배치·복귀·관련 팔 모두의 업무 완료를 만들지 않는다.

전역 화면과 동작 권한은 [화면 계약](../design/13-product-web-surface.md), 지원된 계약과 결손은 [완성 계획 §9](../design/12-web-product-completion.md#9-동결된-구현-결정과-계약-게이트)를 따른다. 기존 조회 preview는 별도 호환 도구로 유지하며 두 번째 운영 writer가 되지 않는다.

## 대안과 비용

표준 WSGI 확장은 현재 코드 변경이 작지만 인증/세션/관리/이력/마이그레이션을 직접 유지해야 한다. FastAPI+별도 SPA는 화면 상호작용을 분리하기 쉽지만 인증·권한 조립과 두 빌드/배포가 늘어난다. Django는 관리 기능과 Python 업무 로직을 재사용할 수 있지만 ORM과 기존 장부 사이의 복구 프로토콜이 필요하다.

단일 DB로 장부까지 이관하면 원자성은 단순해지지만 기존 장부/CLI 호환과 데이터 이관 범위가 커진다. 이번에는 기존 장부를 보존하고 명시적 intent·대조·짝 백업으로 운영한다. HA/복제·다중 writer는 지원 범위 밖이다.

## 수용

역할·CSRF·불변 이력·원래 key·응답 유실·취소와 설치 대응 변경·복원 경계를 시험한다. 네 viewport의 실제 로그인/입력/계획/현장 확인과 설치 wheel/static, 고정 commit의 실제 Fleet HTTP를 따로 검증한다. 운영 관측 producer, 모든 이동 binding, 전체 공정/실물 장치 수용이 없으면 해당 운영 기능은 미완료로 남긴다.
