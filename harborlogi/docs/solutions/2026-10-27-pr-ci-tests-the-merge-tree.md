# PR CI는 head를 단독으로 시험하지 않는다 — merge tree를 먼저 의심하기

2026-10-27. `feat/spatial-placement-binding`(#43)과 `feat/per-arm-bindings`(#44)의
`product-web` 잡만 7~8개 실패했는데, 같은 SHA를 Windows 로컬과 Linux 모델 시험 PC에서
전체 suite로 돌리면 모두 통과했다. 원인을 찾는 데 붙인 시간의 대부분은 원인 자체보다
"어디를 봐야 하는지"를 잘못 잡은 데서 나왔다.

## 증상

| 관측 | 결과 |
|---|---|
| Windows 깨끗한 clone + 새 venv, `python -m django test harbor_web.tests` | 통과 |
| Linux 모델 시험 PC, 같은 트리 전체 suite(임시 래퍼로 `_save` 추적) | 228 통과 |
| GitHub `product-web` (3.10/3.12) | `test_executor`·`test_evidence`·`test_spatial_binding` 7개 실패 |
| GitHub `unittest` | 통과 |

실패 문장은 계약 위반처럼 보였다: `'UNKNOWN' != 'COMPLETED'`, `'HOLD' != 'WAITING'`,
`Lists differ: [] != ['harbor:<uuid>:0']`. 즉 실행기가 제안을 보내지 못하고 되돌아간
것처럼 보였다. 모델 PC에서 붙인 래퍼가 잡은 유일한 `UNKNOWN`은 시험이 일부러 만든
`TimeoutError`(영수증 엔드포인트 단절)라서, 플랫폼 차이·시험 순서 누수·파일 잠금을
의심하며 시간을 더 썼다.

## 원인

GitHub Actions의 `pull_request` 실행은 head SHA를 그대로 쓰지 않고 **head와 base를 합친
merge tree**(`refs/pull/<N>/merge`)를 checkout한다. 그 사이 `main`에 ADR0015 변경이
들어와 `RosyClient`가 모든 공개 호출 전에 `GET /api/fleet/session`으로 설정된 신원을
확인하게 되었다.

테스트의 인메모리 Fleet은 그 경로를 처리하지 않았다. `__call__`이 ARM job 조회 분기로
흘러 빈 job 목록에서 `StopIteration`을 냈고, 실행기는 그 예외를 정상 동작대로
`HOLD`/`UNKNOWN`으로 바꿨다. 브랜치 코드는 정상이었고 **시험 대상 tree가 달랐다**.

## 해결

로컬에서 CI와 같은 tree를 만든다.

```bash
git switch feat/spatial-placement-binding
git merge origin/main --no-edit
python -m django test harbor_web.tests --settings=harbor_web.settings
```

같은 7개가 그대로 재현됐다. 그 다음 fake가 실제 Fleet처럼 신원 probe에 응답하게 했다.

- `test_executor.Fleet`이 `GET /api/fleet/session`에 `{'principal_id': ..., 'role': 'service'}`를 돌려준다.
- 이 probe는 `calls`(ARM 작업 로그)에 넣지 않는다. `calls[0]`이 첫 제안이라고 가정하는 기존 단언을 그대로 살리기 위해서다.
- 결과: 병합 tree에서 Django 230·root 168 통과(모델 시험 PC 아님, 로컬), `makemigrations --check` clean, #43·#44 `product-web`·`unittest` 전부 통과.

## 다음에 적용할 조건

- PR CI만 실패하고 브랜치 로컬·시험 PC 전체가 통과하면, 플랫폼 차이보다 **merge tree**를 먼저 본다.
- base가 공유 계약을 바꿨는지 확인한다: `git diff $(git merge-base HEAD origin/main) origin/main -- <제품 경로>`.
- 원격 시험 PC에 디버그 래퍼를 옮기기 전에 merge 후 재실행을 먼저 한다. 비용 차이가 크다.
- 재발 방지: 코드로 굳히지 않았다. 대신 게시 전에 PR 브랜치를 최신 `main`으로 합쳐 그 tree에서 한 번 더 시험하는 순서를 지킨다.

## 검증 근거

- 병합 전: 로컬 228/230 통과, CI 7~8 실패.
- 병합 직후 로컬: 같은 7실패 재현(수정 전).
- 수정 후: #43 `product-web` 3.10·3.12 pass, `unittest` 3.10·3.12 pass(run 40377210445), #44 동일(run 40377341695).
