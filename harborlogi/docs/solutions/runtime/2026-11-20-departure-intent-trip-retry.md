---
title: 출발 허락 소비 뒤 이송 준비 오류로 intent가 사라지는 문제
date: 2026-11-20
category: runtime
tags: [departure, transaction, savepoint, retry]
agent: codex
---

## Symptom
ROSY 허락을 소비한 뒤 prepare_trip이 실패하면 웹 intent가 롤백되지만 허락은 소비된 채 남는다.
다음 tick은 intent 없는 회차에서 같은 허락을 다시 얻을 수 없다.

## Root cause
허락 SQLite와 웹 DB는 별도 트랜잭션인데 후속 이송 준비까지 웹 atomic 안에서 예외를 전파했다.
기존 tick은 이송 기록을 읽기만 해서 intent만 남은 경우도 재시도하지 못했다.

## Fix
field_episodes.py에서 허락 소비와 intent·감사를 먼저 저장하고, 이송 준비를 별도 savepoint와 try/except로 격리한다.
intake_loop.py는 저장된 intent로 멱등 이송 준비를 재시도하며 회차별 오류를 기록하고 다음 차량을 처리한다.

## Prevention
DB 오류 주입 시험에서 intent·허락 소비 유지와 다음 tick의 이송 준비 성공을 확인한다.
허락 소비 직후 프로세스 중단이나 intent 저장 자체의 실패는 별도 DB 원자성을 보장하지 못하므로 사람 CLEAR 복구가 필요하다.
