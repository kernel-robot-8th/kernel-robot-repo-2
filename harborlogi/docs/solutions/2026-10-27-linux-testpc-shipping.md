# Windows checkout을 Linux 시험 PC로 옮겨 하네스 재검증하기

2026-10-27. Harbor 통합 하네스(`tools.integration.real_fleet_http`)를 Windows 개발 노트북에서 Linux 시험 PC로 옮겨 실행할 때 다섯 번 연속 실패한 뒤 확정한 절차다. 증상이 서로 다른 오류로 나타나 원인을 하나씩 오해하기 쉬워 기록한다.

## 증상

한 번의 실행 시도에서 다음 오류가 차례로 나타났다.

1. `The virtual environment was not created successfully because ensurepip is not available`
2. `.../webvenv/bin/python3: No module named pip`
3. `ERROR: file:///.../harbor-stack does not appear to be a Python project: neither 'setup.py' nor 'pyproject.toml' found`
4. `ValueError: Integration source checkout must be clean` — 옮긴 checkout은 `git clone` 직후인데도 status가 깨끗하지 않음
5. `error: unable to normalize alternate object path: /home/.../.git/objects/D:\Dev\...` → `fatal: bad object HEAD`

추가로 PowerShell here-string을 `ssh bash -s`로 넘기면 각 줄 끝에 `\r`이 남아 `tail: cannot open 'webarm.txt\r'`처럼 원격 파일 이름이 깨진다.

## 원인

1. 시험 PC에 `python3-venv` 패키지가 없다(`dpkg -l` 결과 `un`). `python3 -m venv`는 `ensurepip`을 못 찾아 중단한다. 다만 시스템 `pip`(24.0)는 있으므로 venv 자체는 만들 수 있다.
2. venv를 `PATH` 앞에 두면 venv의 `python3`가 먼저 잡히는데 그 venv에는 pip가 없다. pip 설치 단계가 자기 자신을 부른다.
3. tar는 저장소 루트를 담았고 실제 Python 프로젝트는 그 아래 `harborlogi/`에 있다. `-e .`를 루트에서 부르면 실패한다.
4. Windows Git은 기본 `core.autocrlf=true`다. Windows에서 만든 checkout을 tar로 옮기면 Linux Git이 모든 텍스트 파일을 수정으로 본다. 하네스의 `verify_checkout`은 깨끗한 checkout을 요구하므로 여기서 멈춘다.
5. `git clone --shared`는 `objects/info/alternates`에 원본 경로를 적는다. Windows 경로(`D:\...`)가 그대로 적혀 Linux에서 객체를 찾지 못한다.
6. PowerShell here-string은 CRLF로 조립되고 `ssh`는 그 바이트를 그대로 넘긴다. bash는 `\r`을 인자 일부로 읽는다.

## 해결

```bash
# 1–2. pip 없는 venv + 시스템 pip로 대상 지정 설치
python3 -m venv --without-pip "$RUN/venv"
/usr/bin/python3 -m pip --python "$RUN/venv/bin/python" install httpx uvicorn pyyaml pydantic fastapi cryptography websockets

# 3. 프로젝트 루트에서 설치
(cd "$RUN/harbor-stack/harborlogi" && /usr/bin/python3 -m pip --python "$RUN/webvenv/bin/python" install -e '.[web]')

# 4. 줄바꿈을 고정해 복제·checkout
git -c core.autocrlf=false clone --quiet --no-local --no-checkout "file:///D:/.../rosy-platform" "$T/platform-pin"
git -C "$T/platform-pin" fetch --quiet --depth 1 origin <sha>
git -c core.autocrlf=false -C "$T/platform-pin" checkout --quiet FETCH_HEAD

# 5. 자기완결 복제(--shared 금지), 검사: .git/objects/info/alternates 부재
# 6. 원격 스크립트에서 CR 제거
$script = ($lines -join "`n") -replace "`r", ""
```

핀 commit이 `origin/main`의 조상이 아니면 증분 bundle이 "empty bundle"으로 거부된다(실제로 2206커밋 떨어진 계열이었다). 이때는 새 clone에 `fetch --depth 1 origin <sha>`로 받아 checkout하고 tar로 운반한다.

## 검증 근거

- 수정 절차로 만든 두 checkout이 실행 전 `git status --porcelain` 0줄, `rev-parse HEAD`가 핀 SHA와 일치.
- `--arm-only` 1.171초·`--web-arm-only` 4.170초 각 1실행 통과, 실패0·skip0.
- 실행 후 원격에서 실행 디렉터리·tar·예약 잠금을 제거하고 `/home` 여유가 복구됨을 확인.

결과의 해석 범위는 [재검증 기록](../validation/2026-10-27-neutral-arm-modelpc-reverify.md)에 적었다.

## 다음에 적용할 조건

- Windows checkout을 Linux 시험 PC로 옮겨 `verify_checkout`을 통과시켜야 할 때.
- 공유 시험 호스트에 `python3-venv`가 없고 시스템 `pip`만 있을 때.
- 통합 하네스를 원격에서 돌리려는데 checkout이 깨끗하지 않다고 거절될 때 — 먼저 `autocrlf`를 의심한다.
- 원격 bash 스크립트 인자가 `\r`로 깨질 때.

재발 방지: 이 절차를 코드로 굳히지 않았다. 하네스의 `verify_checkout`이 계속 CRLF 오염을 잡아 주므로, 옮기는 쪽이 `autocrlf=false`를 지키는 것이 현재의 방어선이다.
