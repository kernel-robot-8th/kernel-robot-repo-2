---
title: 탑뷰 초기 중복 요청과 잘못된 카메라 항목의 장애 전파
date: 2026-11-21
category: runtime
tags: [topview, pageshow, polling, cameras]
agent: codex
---

## 증상

탑뷰를 마운트한 직후 일반 `pageshow`에서도 갱신하면 첫 요청을 취소하고
다시 요청한다. 서버에서는 이미 진행 중인 Fleet 조회·lease가 중복될 수 있다.
카메라 목록 전체에 source_id 유효성을 강제하면 출처 없는 다른 항목 하나가
정상 카메라의 기존 프레임 프록시까지 502로 바꾼다.

## 원인

최초 로드와 뒤로 가기 캐시 복원의 수명주기를 구분하지 않았다.
관측 응답의 구조 검증과 개별 카메라의 표시 가능성도 구분하지 않았다.

## 해결

`topview.mjs`는 `pageshow.persisted`일 때만 다시 시작한다.
숨김·전환 때 요청을 취소하고 이전 요청의 늦은 응답은 반영하지 않는다.
`topview_proxy.py`는 기존 응답 구조 검증을 유지하면서 사용 불가능한
source_id를 목록에서 제외한다. URL 정규화가 바꾸는 `.`·`..`도 제외한다.

## 예방

`tests/topview.test.mjs`에서 최초 pageshow의 단일 요청, 숨김 중 응답 무시,
재개·카메라 전환·blob 해제를 시험한다. Django 보기·프록시 시험에서는
잘못된 출처와 정상 출처가 섞여도 정상 카메라가 열리는지 확인한다.
