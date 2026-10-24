# Harbor 브랜드 웹·ROSY 조회 연동 검증

상세설계 기준 `ddd8eaa`, 구현 브랜치 `feat/harbor-web-integration`. 검증한 Harbor 소스 commit은 `a36038310f7edd68bb3d8df9537a34264637ad0b`다. 이후 문서 갱신은 이 소스와 구분한다.

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

기존 `tests/integration/arm_http.py`에 웹 projection 검사를 추가하고 **팀 시험 PC에서 명시 실행했다**. 실제 Fleet 소스는 `bb38fdcdd0140873e4e9d0133662982c1d6a30e0`, Harbor는 위 소스 commit이며 두 checkout 모두 깨끗한 상태로 고정했다. 시험 PC의 기존 캐시에 있는 commit으로 별도 detached checkout을 준비했고 실제 서비스나 장치를 사용하지 않았다.

```text
python -m tools.integration.real_fleet_http --platform-root <고정-checkout> --platform-sha <본문의-플랫폼-commit> --arm-only
Ran 1 test in 1.222s
OK
exit 0 / skip 0
```

실제 Fleet HTTP 서버와 독립 관측자·synthetic UDS owner를 사용했다. 웹 서버의 `GET /api/work?key=neutral-arm-http`가 실제 RosyClient를 통해 같은 job·receipt를 읽었다. 화면 projection은 `mode=live`, Fleet `GOAL_CONFIRMED`, receipt `VERIFIED`, 같은 attempt를 반환했지만 업무 장부는 `WAITING`이며 조회 전후 모든 row 값이 같았다. Harbor child에는 승인자·관측자 자격이나 플랫폼 import가 없다.

하네스 결과: UDS SubmitAction1회, GetAction1회, PRE_DISPATCH/POST_ACTION proof 각각1개. 이전 ARM 결과를 재사용한 것이 아니라 새 Harbor 소스로 실행했다. raw log는 저장소 밖 작업 기록 `Rosy/docs/workspace-records/2026-10-25/harbor-web-http-evidence/arm-web-http.txt`에 보존한다. 첫 탐색에서는 기존 고정 checkout이 없었고, 이후 캐시에서 별도 소스를 준비한 재실행이 위 성공 근거다.

이 검증은 실제 Fleet 코드의 HTTP/UDS 계약과 웹 projection 연동이다. 물리 시뮬레이터·실측 센서·실물 actuator·현장 안전·브라우저에서의 실제 운영 승인 수용은 아니다. 브라우저 화면 시험은 위 합성 데모와 별도의 근거다.

## 남은 수용

로컬 개발 조회 앱이며 세션/RBAC·scope·TLS·감사·LAN 태블릿 공개는 미구현이다. 업무 제안/현장 확인/TRIP 쓰기, Fleet ARM 승인 UI 지원, 인증된 관측 어댑터, 다단계·실물 수용은 남아 있다. 물리 receipt를 업무 완료나 현재 센서 최신성으로 표시하지 않는다.

CE: 해당 없음 — 화면/패키징 수정의 이유와 검증은 최종 코드·회귀 검사·이 기록으로 복원할 수 있다. 운영 장애나 새 장치 성질을 해결한 작업이 아니다.
