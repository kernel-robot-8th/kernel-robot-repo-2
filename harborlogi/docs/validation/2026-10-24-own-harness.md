# Harbor 하네스로 실제 Fleet 계약 시험 (ADR-0003)

검증 소스: Harbor(하네스 포함) **`2c719e8ede8a01d13abdb63b41135c1b9c21c65d`**, 플랫폼 **`584251615d5609a09ebcb52e18af0cbc0c9beaec`**(2026-10-22 업무 대조와 같은 고정 커밋). Vault 체크아웃은 쓰지 않았다.

실행 환경: Windows 11 개발 PC, Python 3.12. 플랫폼 시험 의존성이 이미 깔린 기존 개발 환경을 썼다. 플랫폼은 로컬 저장소에서 깨끗한 임시 clone을 만들어 그 커밋으로 checkout했다.

```text
python -m tools.integration.real_fleet_http --platform-root <임시 clone> --platform-sha 584251615d5609a09ebcb52e18af0cbc0c9beaec
```

| 시험 | 결과 |
|---|---|
| `test_installed_compile_proposal_and_job_are_consumed_over_http` | ok |
| `test_candidate_replay_conflict_busy_and_viewer_rejection` | ok |
| `test_business_completion_requires_reconciled_independent_observations` | ok |
| 런처 exit code | 0 |
| 기본 suite `python -m unittest discover -s tests -q` | 49 tests OK, skip 1(통합 미설정) |

첫 실행(`5cbb067`)은 시험 3개가 모두 통과했는데도 exit 1이었다. Windows에서는 같은 프로세스의 Fleet이 멈춘 뒤에도 임시 SQLite 파일을 잡고 있어서, 임시 폴더 정리가 실패했다. 2026-10-22 검증은 Linux 시험 PC라서 드러나지 않았다. `2c719e8`에서 임시 폴더 정리 실패를 무시하도록 고쳐 다시 실행했다. 시험 판정은 하위 unittest의 exit code 그대로다.

제한: simulation 프로필과 합성 관측 위에서만 확인했다. 실물 장치 · 인증된 관측 producer · 현장 수용이 아니다.
