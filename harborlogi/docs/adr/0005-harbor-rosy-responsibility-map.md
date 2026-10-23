# 0005 — Harbor와 ROSY의 책임 지도: 팔 계획 · 도달 검증 · 관측 근거 · Step별 역할

Status: Accepted, 2026-10-24. 사용자 결정. 결정만 기록하며 코드 변경은 후속이다. 실물 팔 경로·관측 producer·현장 수용은 아니다.

## 결정

1. 협응 수준. 1차는 플랫폼 D-778의 L1 독립 협응이다. 두 팔은 서로 다른 구역에서, 혼자 안정한 자리에만 화물을 둔다. 방해 화물은 임시 칸에 둔다. 공중 일시 보유·인계·받치기(단단한 결합)는 D-778이 L1에서 열지 않으므로 Harbor도 계획하지 않는다(확장 후보). 같은 구역의 동시 사용은 Fleet의 zone claim(D-779 §5)이 막는다.

2. 과제 가설. Harbor는 "한 팔로 닿지 않는 배치에서 추가 운반(Pinky 왕복) 없이 회수"를 계측한다. 주 지표는 추가 운반 횟수와 사람 개입 횟수, 부차 지표는 처리 시간이다. 이점이 없으면 채택하지 않는 기각 조건을 유지한다.

3. 팔 선택. Harbor 공정 변환이 Step마다 주 팔과 대체 팔(alternates)을 정한다(D-856 §1, D-779 §4의 모양). Fleet은 arm·zone claim과 주 팔이 HOLD/OFFLINE일 때의 대체 팔 전환을 맡는다. Pinky는 기존대로 Harbor가 후보를 내고 Fleet이 확정한다(ADR 0001).

4. 도달 정보. Harbor는 작업대 기하를 복제하지 않는다. 플랫폼 compile을 사전 검증으로 써서 (칸, 팔, 동작) 조합의 가능 여부를 얻고, 플랫폼이 돌려주는 기하·보정 revision과 함께 캐시한다. revision이 바뀌면 캐시를 버린다. 이 조회 계약의 정확한 path·schema는 D-856 P1에서 정한다. 아직 없으므로 Harbor 코드는 있는 API처럼 쓰지 않는다.

5. Step별 역할. 고정 S·A·B 역할을 두지 않는다. 공급 하역·적층·회수 같은 역할은 Step마다 정한다(D-778 §6). 1차 팔은 2대(한 현장 PC 최대 2대, D-773)이고 셋째 팔은 예비·티칭용이다. 따라서 `harbor/site.py`의 S·A·B 3팔 전제는 바꿔야 한다.

6. 관측 근거. 팔 퇴피는 owner의 측정 자세(관절값, 관측 시각·revision)로, 화물 위치·ID·하역은 top 카메라 관측(D-797 §5 `camera_item_pose`, 미착지)으로 판단한다. 둘 다 동작 결과(`ACTION_SUCCEEDED` 등)와 별도 근거로 받는다. 측정값은 동작 성공 주장이 아니다. 인증된 producer가 없으면 HOLD한다(ADR 0001 유지). 실물 팔 경로는 D-858 S1~S5 진행 중이며 아직 열리지 않았다.

7. 바꾸지 않는 것. ADR 0001의 업무 완료 3요소, 자동 승인 없음, Fleet HTTP만 사용.

## 영향 (후속 변경)

2026-10-24 main 기준 grep으로 확인한 위치다.

- `harbor/site.py`: `set(self.arms) != {"S", "A", "B"}` 검증과 오류 문구 "Expected supply S and yard A/B roles". 고정 역할 키 대신 팔 목록(1차 2대)과 예비 팔을 받도록 바꾼다.
- `config/site.example.json`: `"arms": {"S": ..., "A": ..., "B": ...}`.
- `harbor/retrieval.py`: `CargoMove`에 팔 배정이 없다. Step별 주 팔·대체 팔을 붙이고, compile 사전 검증 결과(revision 포함)로 도달 불가 조합을 HOLD한다. `required_completion`의 `target_unloaded_by_supply`는 공급 역할 고정을 전제한 이름이다.
- `harbor/workflow.py`: `MoveEvidence`가 한 출처의 `arms_clear`·`unloaded` 불리언으로 퇴피와 하역을 함께 받는다(`_validate`). 팔 측정 자세와 카메라 화물 관측을 별도 근거로 대조하도록 나눈다.
- 시험 `tests/test_policy.py`: `test_example_and_identity_change_keep_roles_and_pool`이 `{"S", "A", "B"}`를 단언하고, 역할 중복 거절 사례 `{"S": "a", "A": "a", "B": "b"}`를 쓴다.
- 시험 `tests/test_execution.py`, `tests/test_http_execution.py`, `tests/integration/test_real_fleet_http.py`: `MoveEvidence(..., arms_clear, unloaded=True)` 형태의 단일 근거를 쓴다. 근거 분리 시 함께 바뀐다.
