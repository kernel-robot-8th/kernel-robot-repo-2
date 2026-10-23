# 용어 정리 · 근거 분리 뒤 실제 Fleet 계약 시험 (ADR 0005)

검증 소스: Harbor(하네스 포함) **`c107cecee612c7a2ebe7dfd885dd8f962281e5df`**, 플랫폼 **`584251615d5609a09ebcb52e18af0cbc0c9beaec`**(이전 기록과 같은 고정 커밋). 이 Harbor 커밋은 ADR 0005 용어로 식별자를 바꾸고, S를 외부 단계로 두고, `MoveEvidence`를 내려놓기(`PlacementEvidence`)와 퇴피(`RetreatEvidence`)로 나눈 상태다. Fleet에 보내는 HTTP 필드는 바꾸지 않았다.

실행 환경: Windows 11 개발 PC, Python 3.12. 플랫폼 시험 의존성이 깔린 기존 개발 환경을 썼다. 플랫폼은 깨끗한 임시 clone을 그 커밋으로 checkout해 썼다.

```text
python -m tools.integration.real_fleet_http --platform-root <임시 clone> --platform-sha 584251615d5609a09ebcb52e18af0cbc0c9beaec
```

| 시험 | 결과 |
|---|---|
| `test_installed_compile_proposal_and_job_are_consumed_over_http` | ok |
| `test_candidate_replay_conflict_busy_and_viewer_rejection` | ok |
| `test_business_completion_requires_reconciled_independent_observations` | ok (합성 내려놓기·퇴피 근거, 출처 종류 바꿔치기·claim 미해제·구역 안 자세 거절 포함) |
| 런처 exit code | 0 |
| 기본 suite `python -m unittest discover -s tests -q` | 62 tests OK, skip 1(통합 미설정) |

제한: simulation 프로필과 합성 근거 위에서만 확인했다. 퇴피 근거를 내줄 플랫폼 조회 계약(D-856 P1)과 카메라 관측 producer가 아직 없으므로, 실물 장치 · 인증된 관측 producer · 현장 수용이 아니다.
