# Harbor 브랜드 웹·ROSY 조회 연동 검증

상세설계 기준 `ddd8eaa`, 구현 브랜치 `feat/harbor-web-integration`. 구현 소스 commit과 실제 Fleet 실행 결과는 아래 후속 증거에서 구분한다.

## 로컬 검증

- `python -m unittest discover -s tests -v`: 101 tests, `OK (skipped=1)`. 실제 Fleet 선택 suite는 환경 미설정으로 skip이며 통과로 계산하지 않는다.
- 신규 웹 시험 10개: 읽기 전용 파일 불변성, SUBMITTING 복구 미실행, mission/key/binding 및 receipt fence 불일치, 비밀 필드/예외 비노출, 동시 변경, 로컬 Host/원격/쓰기 차단, 없는 장부 미생성, 완료 후 현재 job 없음, 콘솔 URL 검증.
- 그중 합성 서버 시험은 `Harbor HTTP → RosyClient → 합성 Fleet HTTP`의 실제 TCP를 사용한다. GET job와 receipt만 발생했고 업무 장부는 바뀌지 않았다. 실제 ROSY 구현이나 장치 시험과 구분한다.
- Edge headless + Playwright: 1440×1000, 768×1024, 390×844에서 검색·필터·상세·재조회·포커스·실패 처리 통과. page error0, request failure0, 가로 넘침0. 합성 시안 캡처는 Git 제외 `harborlogi/runs/web-review/`.
- 실제 화면 검토에서 조회 실패를 빈 장부로 표시하는 문제를 발견했다. `listAvailable`로 구분하고 실패→검색→필터→재시도 회귀 검사를 추가했다. 독립 시각 검토에서 해당 finding 해소와 로컬 조회 범위 `ship`을 확인했다.
- 색상 대조 계산: muted/paper5.28, white/teal6.37, teal/soft-teal5.40, warning/soft-orange7.86. 헤더 포커스는 아이보리/navy12.63으로 보정했다. 전체 WCAG 준수나 스크린리더 수용은 주장하지 않는다.
- 디자인 detector는 served `/assets/app.css`를 파일 경로로 해석하지 못해 계층 경고를 냈다. 실제 CSS와 렌더의 body15px/section18px/task30px를 확인했으며 detector 전체 통과로 기록하지 않는다.
- wheel 빌드·별도 target 설치 후 소스 트리 밖에서 HTML/CSS/JS/아이콘/배너 모두 HTTP200 projection 확인. 초기 target 설치의 데이터 파일 탐색 실패를 수정하고 재검증했다. 런타임 외부 의존성은 없다.

## 실제 Fleet 명시 시험

기존 `tests/integration/arm_http.py`에 웹 projection 검사를 추가했다. 고정 소스의 팀 시험 PC 실행은 아직 이 기록에서 완료로 주장하지 않는다. 이전 2026-10-24의 ARM 통과 결과를 새 웹 구현의 근거로 대신하지 않는다.

## 남은 수용

로컬 개발 조회 앱이며 세션/RBAC·scope·TLS·감사·LAN 태블릿 공개는 미구현이다. 업무 제안/현장 확인/TRIP 쓰기, Fleet ARM 승인 UI 지원, 인증된 관측 어댑터, 다단계·실물 수용은 남아 있다. 물리 receipt를 업무 완료나 현재 센서 최신성으로 표시하지 않는다.

CE: 해당 없음 — 화면/패키징 수정의 이유와 검증은 최종 코드·회귀 검사·이 기록으로 복원할 수 있다. 운영 장애나 새 장치 성질을 해결한 작업이 아니다.
