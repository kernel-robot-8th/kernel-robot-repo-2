# ROSY 자리별 인식 수신 계약 (제안)

Rosy D-1075(Proposed)의 필드 이름을 따르는 Harbor 수신부다. 정본 응답 예시는
[JSON 예시](rosy-cell-site-recognition.v1.example.json)이며, 37자리 중 일부만 담는다.
API 경로·보관 개수·유효 시간은 아직 Rosy 미정이다.

| 필드 | 형식과 의미 |
|---|---|
| `schema` | `rosy.cell-site-recognition.v1` 고정 |
| `snapshot_id` | 스냅샷 식별 문자열 |
| `observed_at` | UTC ISO 시각, `Z` 또는 `+00:00`, 소수 초는 최대 6자리 |
| `source_id` | 관측 카메라 출처 식별 문자열(예: `omx_overhead`) |
| `calibration_revision` | 카메라 보정 revision 문자열 |
| `site_map_revision` | 양의 정수인 셀 자리 맵 revision |
| `sites` | 최대 512개 자리, 빈 목록·부분 스냅샷 허용 |
| `sites[].site_ref` | `<area>-r<row>-c<슬롯 첫 열>-l<tier>`, 최대 96자 |
| `sites[].state` | `EMPTY`, `OCCUPIED`, `UNREADABLE` |
| `sites[].object_class` | 종류/색 문자열 또는 null, 화물 신원으로 사용하지 않음 |
| `sites[].object_ref_read` | 실제 판독한 화물 코드 문자열 또는 null |
| `sites[].confidence` | 0~1 유한수, bool 불가 |

모든 필드는 필수이며 모르는 필드·스키마·상태, 잘못되거나 중복된 자리 주소,
UTC가 아닌/잘못된 시각, 잘못된 타입·범위는 `ValueError(코드: 메시지)`로 거절한다.
일반 문자열은 비어 있지 않은 최대 192자이며 앞뒤 공백·제어문자·잘못된 Unicode를 거절한다.
`known_site_refs`에 설치된 셀 맵의 wire ref 집합을 주면 그 밖의 주소도 거절한다
(1×3 컨테이너의 가운데 열 c2/c3/c5/c6 포함). 집합이 없으면 주소 문법만 검사한다.

`harbor/site_recognition.py`의 `placement_to_site_ref(area, row, column, level)`은
열 1~3을 c1, 4~6을 c4로 묶고, `site_ref_to_placement`는 첫 열의 좌표를 반환한다.
`harbor_cell_to_site_ref('Y12', level=2)`는 `yard-r1-c1-l2`,
`site_ref_to_harbor_cells('yard-r1-c4-l1')`는 `('Y14', 'Y15', 'Y16')`이다.
장부 placements는 `area,row,column,level,cargo_id` 딕셔너리 목록이며 column은 1 또는 4다.

`compare_with_yard(snapshot, placements)`는 인식된 자리별로 원래 자리 필드와
`harbor_cargo_id`, `result`를 반환한다. 결과는 `MATCH`, `ROSY_EMPTY_HARBOR_OCCUPIED`,
`ROSY_OCCUPIED_HARBOR_EMPTY`, `UNREADABLE`, `CLASS_MISMATCH`다.
`CLASS_MISMATCH`는 OCCUPIED 자리의 실제 `object_ref_read`와 장부 화물 코드가 다를 때만이다.
못 읽음은 사람 확인 대기이며, 응답에 없는 자리는 빈 자리로 추정하지 않는다.
이 결과는 ADR 0036의 표시·사람 확인 보류용이다. 사진 판정·장부 자동 수정은 하지 않는다.

Rosy가 붙일 때: 서비스 주체 `harbor`에 Fleet 읽기 권한을 주고 실제 경로를 알려 준다.
`harbor/rosy_http.py`의 `SITE_RECOGNITION_PATH_PROPOSED`만 교체하거나 생성자의
`site_recognition_path` / 환경 `HARBOR_ROSY_SITE_RECOGNITION_PATH`로 덮어쓴다
(생성자 우선, 같은 origin의 절대 경로만 허용). 기존 Fleet 인증/TLS와 transport를 재사용한다.
`HARBOR_ROSY_SITE_RECOGNITION=1`일 때만 `load_site_recognition(client)`가 조회한다.
client를 생략하면 기존 `HARBOR_FLEET_ORIGIN`, `HARBOR_FLEET_TOKEN`, `HARBOR_FLEET_CA_FILE`을 쓴다.
반환은 `(snapshot, '')` 또는 `(None, 상태 문구)`이며 기본은 「ROSY 인식 연동 전」,
404는 「ROSY에 아직 인식 API 없음」, 검증 실패는 「ROSY 인식 응답 형식 오류」다.
`RosyClient.site_recognition()`는 직접 읽기 메서드이고, opt-in은 위 헬퍼가 담당한다.
UI 연결·보관/만료 정책·실제 API와의 접속 검증은 이번 수신부 범위 밖이다.
