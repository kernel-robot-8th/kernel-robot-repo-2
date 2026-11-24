---
title: 화면 개편 후 브라우저 검사에서 숨긴 링크와 모바일 넘침 실패
date: 2026-11-24
category: test
tags: [playwright, details, navigation, flex, overflow]
agent: codex
---

## Symptom
후보 2 링크 클릭이 not visible로 멈추고 메뉴 아이콘 개수 단정이 실패했다.
390px에서 planning-operator 계정의 문서 폭이 394px이었다.

## Root cause
5ae2d44에서 후보 링크가 중첩 details 안으로 이동했다.
fefad6f에서 반출 관제 메뉴가 작업으로 통합됐고,
bacc32a·0bbc1de에서 도움말이 역할 카드와 접힌 시나리오로 바뀌었다.
모바일 계정 flex 항목의 자동 최소 폭은 사용자명과 로그아웃 버튼을 합친 폭이었다.

## Fix
candidate_browser_checks.py에서 두 단계의 상세를 실제 클릭으로 펼친다.
navigation_browser_checks.py는 작업의 아이콘·툴팁·모바일 탐색을 검사한다.
multicell_browser_check.py는 역할 카드 4개·안내 12줄과 펼친 시나리오 11개·27단계를 검사한다.
product.css의 max-width:600px 안에서 .account에 min-width:0을 더해 기존 사용자명 말줄임을 적용한다.

## Prevention
선택자가 실패하면 현재 템플릿과 변경 커밋을 확인하고 실제 노출 흐름을 따른다.
단정을 삭제하거나 클릭을 강제하지 않는다.
긴 계정명으로 390px 문서 폭을 확인하고 768·1280·1440px 배치도 비교한다.
