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

## 2026-11-22 갱신: 새 프레임 수신 시 깜빡임 및 Epoch 시각 미표시

### 증상

- 정상 스트림에서 새 JPEG 프레임이 들어올 때마다 기존 이미지가 잠깐 사라졌다 다시 나타난다(높이 붕괴·깜빡임).
- 실제 ROSY Fleet의 `X-Frame-Captured-At`은 Unix epoch 초 문자열(`1795334643.410025`)로 오지만 화면의 찍은 시각이 빈칸으로 남는다.

### 원인

- `topview.mjs`의 `refresh()`가 새 blob을 표시하기 직전에 `clearImage()`를 호출해 `image.hidden = true`, `removeAttribute('src')`, 이전 blob 해제를 먼저 수행했다.
- `frameStatus()`가 문자열을 `new Date(capturedAt)`에만 넘겨 숫자 epoch 초를 파싱하지 못했다.
- `mountTopview()` 반환 cleanup이 `visibilitychange`만 해제하고 `change`/`pagehide`/`pageshow`를 남겼다.

### 해결

- `topview.mjs`에서 `new Image().decode()` 완료 후 새 URL을 `image.src`에 대입한 뒤에만 직전 `previousUrl`을 해제해 프레임 교체를 원자적으로 수행한다(`clearImage()`는 오류·디코드 실패·카메라 전환·숨김 종료 시에만 호출).
- `parseCapturedAt()`으로 Unix epoch 초(소수점 포함)·밀리초·ISO 문자열을 모두 파싱한다.
- `operations.css`의 `.topview-panel [role="status"]`에 `min-height:1.4em`을 두어 상태 문구 유무에 따른 레이아웃 밀림을 막고, cleanup이 등록된 4개 리스너를 모두 해제하도록 수정했다.
