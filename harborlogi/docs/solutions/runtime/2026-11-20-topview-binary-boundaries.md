---
title: 탑뷰 바이너리 transport와 origin·응답 헤더 경계
date: 2026-11-20
category: runtime
tags: [rosy, vision, transport, origin, headers]
agent: codex
---

## 증상

이전 작업의 Vision 클라이언트는 주입 transport로 프레임만 읽을 수 있었고,
lease JSON 호출은 초기화되지 않은 `_token`을 참조했다. MIME 접미사를 허용했으며,
리다이렉트 시험은 실제 HTTP 없이 예외를 직접 발생시켜 거절 여부를 검증하지 않았다.
검토에서는 명시적 포트 `:0`을 기본 포트로 취급하는 문제와,
줄바꿈이 있는 프레임 헤더가 Django에서 500을 일으키는 문제도 발견했다.
추가 검토에서는 같은 origin의 다른 경로에도 lease 토큰을 보낼 수 있었다.

## 원인

JSON과 바이너리 전송 구현이 별도로 복제되어 transport 초기화가 달랐다.
`port or 기본값`은 0과 미지정을 구별하지 못하고, upstream 헤더도 신뢰 경계다.

## 해결

`harbor/rosy_vision.py`에서 RosyClient의 연결·TLS 설정을 재사용하고,
주입 transport는 JSON·바이너리 모두 같은 요청 서명을 사용한다.
프레임 origin을 비교하면서 포트 0을 거절하고 MIME을 정확히 비교한다.
`harbor_web/topview_proxy.py`는 응답 헤더 설정까지 오류 처리 범위에 넣는다.
lease 응답의 source_id를 요청과 대조하고, frame_path는 응답 source_id를
`quote(sid, safe='')`한 계약 경로와 정확히 일치하며 쿼리가 없어야 한다.

## 예방

`tests/test_rosy_vision.py`의 실제 가짜 HTTP 서버 시험과 transport 회귀 시험,
`harbor_web/tests/test_topview_proxy.py`의 잘못된 헤더 시험을 유지한다.
source_id 누락·불일치, 다른 카메라·계약 밖 경로·쿼리를 전송 전에 거절하는 시험을 둔다.
최종 대상 시험은 클라이언트 17개·Django 7개 통과했다.
