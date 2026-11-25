# ROSY 자리별 인식 수신 계약

Rosy D-1075(ROSY #735 운영 배포)의 필드 이름과 확정 경로를 따르는 Harbor 수신부다. 정본 응답 예시는
[JSON 예시](rosy-cell-site-recognition.v1.example.json)이며, 37자리 중 일부만 담는다.
확정 API 경로는 `GET /api/fleet/cell-site-recognition`(viewer 권한)과 `POST /api/fleet/cell-site-recognition`(operator/service 권한)이며 Harbor는 읽기(`GET`)만 호출한다.

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
| `sites[].object_class` | 종류/색 문자열 또는 null(`object_class`는 `orange`/`pink`/`yellow`/`sky` 낱말을 기대, 다른 값도 허용하며 화물 신원으로 사용하지 않음) |
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
`object_class`는 `orange`/`pink`/`yellow`/`sky` 낱말을 기대한다(`harbor/cargo_colors.py`의 `CARGO_COLORS` 공유). 장부 화물 색을 알 때 OCCUPIED 자리의 `object_class`가 그 낱말 중 하나이고 다르면 기존 `result`는 그대로 두고 `color_mismatch: true`만 추가한다.
못 읽음은 사람 확인 대기이며, 응답에 없는 자리는 빈 자리로 추정하지 않는다.
이 결과는 ADR 0036의 표시·사람 확인 보류용이다. 사진 판정·장부 자동 수정은 하지 않는다.

## 연결 설정과 상태 문구

- 확정 경로는 `harbor/rosy_http.py`의 `SITE_RECOGNITION_PATH = '/api/fleet/cell-site-recognition'`이며,
  생성자 `site_recognition_path` 또는 환경 `HARBOR_ROSY_SITE_RECOGNITION_PATH`로 덮어쓸 수 있다
  (생성자 우선, 같은 origin의 절대 경로만 허용). 기존 Fleet 인증/TLS와 transport를 재사용한다.
- **기본 켜짐**이며 `HARBOR_ROSY_SITE_RECOGNITION=0`일 때만 조회를 끈다(「ROSY 인식 연동 전」).
- `load_site_recognition(client)`는 `(snapshot, '')` 또는 `(None, 상태 문구)`를 반환한다(HTTP 타임아웃 2초).
  client를 생략하면 `HARBOR_FLEET_ORIGIN`, `HARBOR_FLEET_TOKEN`, `HARBOR_FLEET_CA_FILE`을 쓴다.
- 404 응답은 본문을 최대 64KB까지 예외 없이 읽어 `detail.code`로 두 경우를 구분한다.
  - `detail.code == "CELL_SITE_RECOGNITION_NOT_YET_OBSERVED"` → 「ROSY 인식 스냅샷 수신 대기」
  - 그 밖의 404, JSON 아님, 본문 읽기 실패 → 「ROSY에 아직 인식 API 없음」
- 설정 미완료는 「ROSY 인식 연동 전」, 그 밖의 HTTP/연결 오류는 「ROSY 인식 연결 오류」,
  스키마 검증 실패는 「ROSY 인식 응답 형식 오류」다.
- `RosyClient.site_recognition()`는 직접 읽기 메서드이고, 기본 켜짐/`=0` 끔과 상태 문구 변환은 `load_site_recognition()`이 담당한다.

## UI 연결 범위

- 웹 연결 헬퍼 `harbor_web/site_recognition_view.py`는 `harbor_web/fleet_connection.py`의 `connection_settings()`(DB 저장 연결 또는 env)와
  `HARBOR_FLEET_TOKEN`을 사용하고, 결과를 `(origin, token digest, path)` 키로 프로세스 메모리에 15초 캐시한다(타임아웃 2초).
- 야드 화면(`/inventory/`, 부분 템플릿 `site_recognition_panel.html`)은 상태 한 줄
  (「ROSY 인식 · HH:MM · source_id · 37자리 중 n자리」 또는 상태 문구), 최신 `grid-placement-v1` 장부와의
  일치 개수, 불일치(`ROSY_EMPTY_HARBOR_OCCUPIED`, `ROSY_OCCUPIED_HARBOR_EMPTY`, `CLASS_MISMATCH`, `UNREADABLE`)
  목록과 「현장 확인」(`/field/`) 링크만 표시한다.
- 처리할 것(`/alerts/`, `harbor_web/alert_pages.py` `ATTENTION_KIND['인식 불일치']`)에 불일치 건을 종류 「인식 불일치」로 연결한다.
- 화면 GET은 Plan·Command·Observation을 만들지 않으며, 장부를 자동으로 고치지 않는다(ADR 0036).
