# 다칸 점유 화물의 관측·계획·완료

2026-10-25 사용자 요구: 맵은3행×3열×3층이며 화물 하나가1×1·2×1·3×1의 바닥 면적을 점유한다. Django는 유지한다. FastAPI 전환은 이번 범위에서 제외한다. 기존 UR/SR와 팀 상세 설계의 정본은 비공개 Confluence이며 이 문서는 공개 앱의 구현 계약만 기록한다.

## 업무 의미

화물 ID 하나를 한 번 저장하고 기준 위치·층·방향·불변 점유 크기 revision으로 점유 영역을 계산한다. 여러 칸을 점유해도 화물/이동/재취급 건수는 하나다.27개 위치가27개 화물을 뜻하지 않는다. 같은 층의 겹침, 맵 밖 점유, 상단 화물 제거 전 이동, 아래 전체 지지가 없는 배치를 거절한다. 현재 구현은 화물 높이1격자, 고정 방향 이동이며90도 방향은 원본에 명시할 수 있으나 planner가 임의 회전하지 않는다. 물리 치수·하중·안정성·보정·팔 도달은 별도 설치 수용이다.

임시 위치와 반출대도 명시적인 행·열·층 크기가 있어야 한다. 컨베이어/반출대는 재취급 임시 위치가 아니다. 재취급 화물을 유효한 보관 위치에 복귀시킬 수 없으면 부분 실행 계획을 내지 않고 전체 HOLD한다. 원래 위치가 대상 반출 후 지지를 잃었다면 원래 칸에 강제로 복귀시키지 않는다. 탐색 한도 도달은 공간 없음과 구별한다.

## 도메인 인터페이스 (stdlib)

새 `harbor/spatial.py`는 기존1×1 legacy 모델을 바꾸지 않는 공간 모델이다.

- `CargoShape(cargo_id: str, length_cells: int, footprint_revision: int)`: 허용 길이1/2/3, 짧은 변1. revision은 양수. 원본의 크기 revision을 고정한다.
- `Placement(cargo_id: str, area_id: str, row: int, column: int, level: int, orientation: int = 0)`:1기반 주소. orientation은0/90, 모든 이동에서 보존한다.
- `Area(area_id: str, rows: int, columns: int, levels: int)`: 보관 구역 yard는3×3×3, 별도 임시/반출 구역은 명시한 크기. 각 축1..3.
- `SpatialSnapshot(revision, observed_at, layout: YardLayout, areas: tuple[Area,...], shapes: tuple[CargoShape,...], placements: tuple[Placement,...], holdings: tuple[CargoHolding,...] = ())`: 동일 화물/shape 중복, 알려지지 않은 구역, 겹침·범위·지지 오류를 검증한다. `occupied()`는 `(area_id,row,column,level) -> cargo_id`, `cells(placement)`는 그 점유 좌표 집합, `shape(cargo_id)`, `placement(cargo_id)`, `with_placements(tuple)`는 불변 대조/투영을 제공한다. `position_id(placement)`는 yard의 Y11-L1, 보조 위치의 temp-R1C1-L1 형태로 반환한다.
- `SpatialPolicy(outbound_area, temporary_areas: tuple[str,...], allowed_moves: frozenset[tuple[str,str]], arm_reach: frozenset[tuple[str,str]], max_observation_age: timedelta)`: 허용 이동은 area쌍. 도달 참조는 yard의 점유한 모든 칸 ID와 보조 area ID다. 한 팔이 출발/도착 전체를 담당할 수 있어야 한다.
- `SpatialMove(cargo_id, source: str, destination: str, primary_arm, alternate_arms, source_placement: Placement, destination_placement: Placement, phase_label: str, footprint_revision: int)`: 화물 하나의 원자적 이동. phase_label은 재취급/반출/복귀다.
- `plan_spatial(snapshot, request: OutboundRequest, policy, *, now)`: legacy와 같은 status/reason/source_revision/moves/required_completion 필드를 가진 불변 제안을 반환한다. 상단 의존 화물을 중복 없이 옮기고, 유효한 반출/복귀를 함께 찾는다. HOLD에는 moves가 없다. bounded search가 끝나지 않으면 `SPATIAL_SEARCH_LIMIT_REACHED`로 구별한다.

## 저장과 입력

`CargoFootprint`는 기존 Cargo와 분리한 불변 크기 revision이다: FK cargo, length_cells(1/2/3), created_by/created_at. 기존 화물/관측/계획을 수정하거나 과거 크기를 추측해 backfill하지 않는다. 새 화물 등록에는 크기 선택을 넣고 기존 화물에는 크기 등록/변경을 제공한다. 기존 service 호출은 length_cells 기본1로 호환하되 legacy 기록을 다칸 관측으로 승격하지 않는다.

새 Condition 선택 키는 `spatial_areas`(yard를 제외한 Area dict 배열), `spatial_moves`(area쌍 배열)다. yard는 `yard_layout`에서 구성한다. 기존 outbound_stand/temporary_bays/arm_reach/max_observation_age_seconds를 사용한다. legacy 조건은 그대로 읽는다.

새 Observation payload는 정확히 다음 필드를 가진다.

```json
{"schema":"grid-placement-v1","observed_areas":["yard","temp","stand"],"placements":[{"cargo_id":"C1","area_id":"yard","row":1,"column":1,"level":1,"orientation":0,"footprint_revision":1}],"holdings":[]}
```

모든 구성 area의 전체 관측 범위가 명시돼야 한다. placement별 footprint_revision은 해당 Cargo의 불변 크기 원본을 가리켜야 한다. 없는 원본/다른 화물의 revision/중복 개체/부분 범위는 거절한다. 관측 부재를 빈 위치로 추측하지 않는다. 오래된 bays/holdings 원본은 legacy1×1 표현으로 보존하며 다칸 크기가 등록된 화물의 새 계획에는 공간 관측을 요구한다.

Plan.result는 새 모델에 `model: grid-placement-v1`, `source_shapes`(CargoShape dict 배열), `projected_placements`(Placement dict 배열 또는 HOLD이면 null)를 기록하고 moves는 SpatialMove의 dataclass JSON이다. projected_bays는 null이다. request_snapshot/queue_snapshot/queue_digest와 원본 Observation/Condition 결속은 유지한다. 기존 실행을 새 계획으로 우회하지 않는다.

현재 설치 binding/업무 근거 계약은1×1 legacy 인계만 검증됐다. 새 공간 제안의 실행 gate는 `SPATIAL_EXECUTION_BINDINGS_REQUIRED`로 차단한다. 미검증된 크기/방향/전체 위치 대응을 기존 ARM binding에 억지로 넣지 않으며 ROSY API를 바꾸거나 직접 장치를 제어하지 않는다. 업무 완료3조건은 유지한다.

## 화면과 검증

화물 크기/원본 revision을 등록·조회하고, 층별 지도에서 한 화물은 colspan/rowspan으로 점유 면적을 이어 표시한다. 다른 칸에 같은 cargo를 독립 개체처럼 반복하지 않는다. 단계 선택은 전체 점유 영역을 한 번에 이동시키며 실제 관측은 유지한다. 보조 구역과 지지/영역/중복/회전·신선도 HOLD 이유를 표시한다. 새 지도와 legacy 기록을 같은 상태로 섞지 않는다.

검증은1×1/2×1/3×1·90도 원본, 경계/겹침/지지/중복/크기 revision/불완전 원본, 양쪽 위의 방해 화물·큰 방해 화물의 중복 제거, 맞는 임시 면적과 유효 복귀/공간 부족·탐색 한도, 화물별 이동 수, 입력/권한/불변 이력·GET 무변경, 기존 Django/도메인 전체 회귀와 네 폭의 실제 브라우저를 포함한다. UR/SR 정본 반영과 공개 앱 설계/코드 검증은 구분해 기록한다.
