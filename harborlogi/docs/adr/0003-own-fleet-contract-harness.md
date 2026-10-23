# 0003 — Fleet 계약 시험 하네스를 Harbor가 가진다

Status: Accepted, 2026-10-24. 사용자 지시. [0002](0002-pinned-real-fleet-contract-tests.md)의 「고정 Vault 하네스를 로드한다」 부분을 대체한다. 나머지 0002 경계(고정 플랫폼 SHA, 별도 프로세스, 합성 자격, skip은 통과가 아님)는 그대로다.

0002는 서버 조립을 복사하지 않으려고 비공개 rosy-vault의 `tools/integration/real_fleet.py`를 고정 SHA로 불러 썼다. 그 뒤 보관고 도메인이 선정되지 않아 rosy-vault의 업무 앱은 개발을 멈췄다. 공개 팀 저장소의 시험이 비공개 저장소 체크아웃을 요구하면 팀원 대부분이 2단계 시험을 돌릴 수 없다.

그래서 하네스를 `tools/integration/fleet_harness.py`로 옮겨 Harbor 이름으로 맞췄다. 출처는 rosy-vault `9a744a3ed2e74e1775cf4622d15c138942336db8`이다. Fleet 코드는 고치지 않는다. 바꾼 것은 문서 id · 생산자 · 운영자 이름(`vault-*` → `harbor-*`)이고, Vault 배치 파일 전용 도우미는 뺐다. 런처는 `--platform-root`·`--platform-sha`만 받는다. 하네스가 Harbor 저장소 안에 있으므로 Harbor SHA가 하네스 SHA를 겸한다.

함께 고친 결함: Harbor가 팀 저장소의 `harborlogi/` 폴더로 옮겨진 뒤 런처가 `harborlogi/`를 Git 최상위로 확인해서 항상 실패했다. 이제 감싸는 체크아웃 전체가 깨끗한지 확인한다(`app_checkout`).

검증: `tests/test_integration_setup.py`가 하위 폴더 앱의 체크아웃 확인과, 하네스가 기본 시험에서 import되지 않는 것을 본다. 고정 플랫폼 `584251615d`로 2단계 시험을 다시 돌려 3개 모두 통과했다([기록](../validation/2026-10-24-own-harness.md)). Windows에서 임시 SQLite 정리가 실패해 exit 1이 되던 문제도 함께 고쳤다.

되돌릴 조건: 플랫폼이 같은 시뮬 조립을 공개 시험 도구로 제공하면 그것을 쓰고 이 하네스를 지운다.
