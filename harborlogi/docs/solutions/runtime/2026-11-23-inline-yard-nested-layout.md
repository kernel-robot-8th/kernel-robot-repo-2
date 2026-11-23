---
title: 야드 예정 3D가 좁아지고 평면 아래로 밀림
date: 2026-11-23
category: runtime
tags: [yard, css-grid, webgl, inline-3d]
agent: codex
---

## Symptom
야드의 남은 예정을 선택하면 3D 캔버스 폭이 수십 px로 줄고 평면 옆에 빈칸이 생겼다.
3D가 페이지 아래에 나타나 자동 재생을 확인하기 어려웠다.

## Root cause
`inventory.html`의 `yard-preview-col` 래퍼가 추가되면서 기존
`.yard-topview > .yard-linked` 세로 배치 규칙이 적용되지 않았다.
외부 야드/탑뷰 분할 안에서 `.yard-linked`의 3fr/최소 20rem 그리드가 다시 적용됐고,
평면의 `order:-1`이 3D보다 앞선 긴 그리드 행을 만들었다.

## Fix
`inventory.html`의 관측 미리보기에 `data-yard-upcoming-preview` 표식을 붙여
공유 `yard-inline-3d` 클래스를 쓰는 재배치/맵 정의까지 바뀌지 않게 한다.
`yard-page.css`에서 해당 야드만 한 열로 늘리고 3D를 미리보기 맨 위에 배치한다.
`yard-inline.mjs`의 `applyYardView`로 inline의 평면/3D를 상호 전환하며,
`yard-3d.js`는 예정 선택 시 재생하고 해제 시 기본 평면을 복원한다.
렌더러의 기존 `ResizeObserver`가 캔버스 크기와 카메라 비율을 갱신한다.

## Prevention
`tests/yard-inline.test.mjs`에서 inline/일반 화면의 숨김 상태·편집 클래스·DOM 위치 유지를 검사한다.
실제 Chrome에서 1280/1600/1920/1099/400px 크기, 재선택 닫기, 재생과 리사이즈를 확인한다.
좁은 화면에서는 목록까지 스크롤한 뒤 실제 클릭으로 재생 화면이 보이는지도 확인한다.
CSS `display`를 재정의할 때 `[hidden]`이 적용되는지도 함께 확인한다.

## 편집 모드 후속 확인
`flat-edit`는 캔버스 부모를 숨기므로 인라인 가상 편집에는 적용하지 않는다.
편집 도구·검사기·평면을 별도 스크롤 칸에 모으고, sticky 캔버스의 조상 `overflow:hidden`을 해제한다.
기존 평면의 `order:-1`이 새 flex 편집 칸에도 적용되므로 직접 자식의 `order`를 초기화한다.
좁은 화면의 카메라 도구는 캔버스 위에 겹쳐 실제 캔버스 높이 45vh를 확보한다.
Chrome에서 좌표 적용 직후와 편집 칸 스크롤 후 캔버스 경계·그리기 버퍼 크기를 확인한다.
Waitress의 Django 템플릿 캐시는 변경된 HTML을 자동 반영하지 않으므로 개발 미리보기를 재시작해 검증한다.
