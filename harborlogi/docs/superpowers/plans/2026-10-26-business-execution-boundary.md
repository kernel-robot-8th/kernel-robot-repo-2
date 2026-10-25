# Harbor 업무 실행 경계 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자 확정 목표를 계속 운용하는 Harbor 실행기를 만들고, ROSY 내부 구현과 항만 규칙의 상호 의존을 막는다.

**Architecture:** Harbor 업무 전이는 앱 소유 결과로 판단하고 기존 ROSY HTTP 연동은 어댑터로 제한한다. 처음에는 기존 서비스 lifecycle·단일 writer 안에서 운용하며 새 queue 서비스나 플랫폼 전용 재배치 API를 만들지 않는다.

**Tech Stack:** 기존 Python/Django, SQLite/WriterLease, unittest와 Django 시험. 새 의존성 없음.

**Spec:** [ADR0013](../../adr/0013-independent-business-execution-and-platform.md), [상세설계24](../../design/24-business-execution-boundary.md).

상태: 실행 전 계획. 이번 문서 변경은 아래 체크 항목의 완료나 ROSY 계약 활성화를 뜻하지 않는다. ROSY 쪽 계약 변경은 해당 저장소에서 별도 계획·검증한다.

## 공통 제약

결정의 정본은 ADR0013이다. 원본 key/identity·근거·restore 차단·기존 승인·단일 writer를 보존한다. 화면 GET은 읽기 전용이다. 물리 참조와 다칸 binding·인증 근거가 없는 실행은 보류한다. 불명 응답은 원래 key로 대조하며 새 key 자동 재제출을 하지 않는다.

## 검토 초점

- 응답 도착 전후 프로세스 종료: 같은 요청이 중복 실행되지 않고 복구된다. 작업2에서 시험한다.
- 늦게 도착한 이전 계획/attempt의 결과: 현재 단계에 적용되지 않는다. 작업1·3에서 시험한다.
- 정책 승인 대기·unknown 상태/schema: 실행으로 오인하지 않고 이유를 보존한다. 작업1에서 시험한다.
- 두 업무의 논리 위치 충돌: 물리 자원 admission과 별개로 보류한다. 작업3에서 시험한다.
- 복원 직후 background 실행: 기존 복구 대조 차단을 해제하지 않는다. 작업2에서 시험한다.

## 작업1: 연동 경계와 업무 전이의 분리

대상: 기존 `harbor/rosy_http.py`, `harbor/process.py`, `harbor/workflow.py`, `harbor_web/commands.py`; 필요 최소한의 새 `harbor/execution_port.py`(앱 결과 타입), `harbor/execution_adapter.py`(기존 client 조립). 두 새 파일은 제안이며 기존 경계로 충분하면 추가하지 않는다.

- [ ] 기존 workflow·commands 호출자와 관련 시험을 검색한다. 원본 key/digest/mission/attempt/principal 및 저장 상태의 대응을 고정하는 실패 시험을 먼저 추가한다.
- [ ] 어댑터에서 제출·원본 조회·취소 결과를 검증하고 앱 결과로 변환한다. 업무 진행부가 HTTP 경로나 wire dictionary를 해석하지 않도록 최소 경계를 만든다. 기존 public client·호환 경로를 보존한다.
- [ ] 승인 대기, 알려지지 않은 상태/schema, identity 불일치, 늦은 다른 attempt, 결과 불명·receipt 부재 시험을 통과시킨다. 원본 응답의 보존과 fail-closed 처리를 확인한다.
- [ ] `python -m unittest discover -s tests -v`와 설정된 Django 전체 시험을 실행한다. 실제 Fleet HTTP 검증은 고정 양 끝 버전·설정이 갖춰진 경우 별도로 실행하고 skip을 통과로 보고하지 않는다. 검토 후 커밋한다.

완료 조건: Harbor 업무 전이 시험이 ROSY 대역을 통해 정상/보류/불명을 검증하고, 기존 HTTP 계약 시험의 원본 검증이 유지된다.

## 작업2: 단일 writer의 내구 업무 실행기

대상: `harbor_web/commands.py`, `harbor_web/runtime.py`, `harbor_web/__main__.py`의 실제 lifecycle 진입점 확인 후 통합; 새 `harbor_web/execution_runner.py`, 필요한 모델과 versioned migration·시험. 기존 `command_models.py`·`runtime_models.py`의 제약을 먼저 조사한다.

- [ ] 확정 의도 저장 직후/전송 직전/전송 직후/결과 저장 전 crash, 중복 tick, 브라우저 미접속의 실패 시험을 작성한다.
- [ ] 사용자 확정의 내구 의도와 단계별 안정 key를 보존한다. 실행기 tick은 제한된 수의 업무만 처리하고 lock/version과 기존 WriterLease를 사용한다. 네트워크 호출을 DB transaction 밖에서 수행한다.
- [ ] 기존 서비스의 시작·종료와 runner를 결속한다. 자동 실행은 명시적인 환경 설정으로 구분하고 수용되지 않은 preview/live에서 켜지 않는다. 주기·backoff·복구 대조를 제한한다. 웹 GET의 조회 부작용을 추가하지 않는다.
- [ ] 복원 차단·재시작·응답 불명 후 동일 key 대조·오래된 worker 결과 저장 거절·정상 3단계 합성 진행 시험을 통과시킨다. 스키마 변경은 기존 DB migration·백업/복원 시험을 포함한다.
- [ ] 계정·역할·Command 회귀와 브라우저 GET 무쓰기 검사를 실행하고 검토 후 커밋한다.

완료 조건: UI가 닫혀도 수용된 합성 업무가 근거를 확인하며 후속 단계로 진행한다. crash 뒤 동일 업무·원래 실행으로 복구되고 제출이 중복되지 않는다.

## 작업3: 재계획과 사용자 화면의 연속성

대상: `harbor_web/autoplanning.py`, `harbor_web/command_models.py`의 Order당 단일 불변 Command 관계, `harbor_web/workspace.py`, `harbor_web/operations.py`, `harbor_web/pipeline.py`, 업무/관제 템플릿과 시험. 필요한 별도 실행 이력 모델은 migration 전에 설계 검토한다.

- [ ] 일부 단계 완료 뒤 목표 변경, 이미 제출된 단계, 취소 ACK만 도착, 같은 위치를 쓰는 두 업무의 실패 시험을 먼저 작성한다.
- [ ] 완료·제출 단계와 원래 실행은 보존한다. 안정 상태 대조 후 미제출 후속 단계만 새 계획에 연결한다. 확정 범위 밖의 목표 변경은 사용자 결정을 다시 받는다. 기존 Command의 binding·plan을 덮어쓰거나 Order당 제약을 단순 삭제하지 않는다.
- [ ] 관제/업무 화면에 남은 단계·대기 사유·근거 시각·재계획 연결을 같은 업무에서 표시한다. 실제/목표/가상 배치를 구별한다. 완료 근거가 없는 처리량은 미집계로 둔다.
- [ ] 실제 브라우저에서 확정→화면 종료→재접속, 승인 대기, 불명, 재계획·취소 상태를 검사한다. 합성 자동 진행과 실제 장치 실행을 구분한 검증 기록을 남긴다.
- [ ] 전체 회귀·계약 검사·별도 검토를 마친 뒤 PR/CI/병합한다. 배포는 수용된 범위만 활성화하고 기존 preview는 Fleet 미연결을 유지한다.

완료 조건: 사용자가 한 업무에서 원래 목표·실제 진행·변경된 남은 계획을 추적할 수 있다. Harbor 업무 규칙 변경 없이 ROSY 내부 구현을 바꿀 수 있는 공개 계약 경계가 유지된다.
