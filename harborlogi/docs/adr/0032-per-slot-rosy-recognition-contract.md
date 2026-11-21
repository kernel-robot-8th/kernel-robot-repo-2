# ADR 0032: 자리별 ROSY 인식 — 새 계약 필요(ADR 대상)

날짜: 2026-11-20

상태: 제안 — 새 계약 필요(ADR 대상)

## 배경

ADR 0029(`docs/adr/0029-port-events-and-intake-identity.md`)는 반입 사건에만 `object_ref`와
`identity: observed|attested`를 부여한다. 야드 자리별 인식 결과를 Harbor로 전달하는 계약은 없다.

조사 결과(`runs/topview-research.md`):
- ROSY `schemas.py`에 상자·화물 인식 모델이 없다.
- 비전 관련 외부 export는 `OverheadDetectionsPayload`가 유일하며, 이것은 천장 로봇의 blob(x, y,
  footprint_m, score, marker_id?)이고 개체 신원(어느 컨테이너인지)이 아니다.
- 셀 로컬 인식(`deploy/robot/omx/cell_objects.py`)은 종류·크기 판정만 하고 Fleet으로 보내는
  코드가 없다(cell_* 스크립트 안에서만 사용).
- `GET /api/fleet/port-events`에 자리별 인식 필드는 없다.

Harbor 자리 주소 형식은 `<area>-r<n>-c<n>-l<n>` (`harbor/process.py:85-87`,
`anchor_address` 함수)이다.

현재 Harbor는 야드 자리별 인식 결과를 받아 보관 장부와 비교할 계약이 없어, 불일치 감지와
사람 확인 보류를 자동으로 수행할 수 없다.

## 결정

1. **이 문제는 새 계약이 필요한 ADR 대상이다.** ROSY에 자리별 인식 스냅샷 읽기 API를
   요청한다. ROSY 저장소는 건드리지 않는다.

2. **ROSY에 요청할 API(전부 「제안」 표시 — 계약 없음).**

   응답 모델 `SlotRecognitionSnapshot` (제안):

   | 필드 | 설명 |
   |---|---|
   | `slot_ref` (제안) | Harbor 자리 주소 `<area>-r<n>-c<n>-l<n>` |
   | `object_ref_read` (제안) | ROSY가 판독한 화물 코드, 판독 불가이면 null |
   | `read_state` (제안) | `READ` \| `UNREADABLE` \| `EMPTY` |
   | `observed_at` (제안) | 관측 시각(UTC ISO 8601) |
   | `source_revision` (제안) | 인식 출처 revision |
   | `identity` (제안) | `observed` \| `attested` |

3. **Harbor 쓰는 규칙.**
   - 수신한 스냅샷은 화면 표시와 야드 보관 장부 비교에만 사용한다.
   - 불일치(`object_ref_read` ≠ 장부의 화물 코드)가 있으면 사람 확인 보류(HOLD)로
     전환하고, 장부를 혼자 바꾸지 않는다.
   - `read_state: EMPTY`인데 장부에 화물이 있으면 사람 확인 보류로 전환한다.
   - `read_state: UNREADABLE`이면 사람 확인 대기 상태(`UNREADABLE`)로 표시하고 장부를
     바꾸지 않는다.

4. **동작 권한은 바뀌지 않는다.** 인식 결과로 장치 동작을 직접 일으키지 않는다.

## 버린 대안

| 대안 | 판단 |
|---|---|
| 셀 스크립트 출력 파싱 | cell_objects.py 출력이 Fleet으로 가지 않으며, 개체 신원이 아닌 종류·크기만 판정한다. |
| 반입 사건 순서(FIFO)로 신원 추정 | ADR 0029 결정 5: 순서나 추정으로 완료까지 가지 않는다. |

## 열린 질문

- 마커 id ↔ `object_ref` 대응: ROSY marker-profile 소관(D-954). Harbor는 `marker_id`를 저장하지 않는다(ADR 0029 결정 5).
- 인식 그림(image)의 출처 및 접근 경로.
- Harbor service principal이 `require_camera_viewer`를 통과하는지 여부.
- 자리 이름(`slot_ref`) ↔ ROSY 자세(pose)/`target_ref` 이름 대응 목록(Harbor에 pose 어휘 없음).

## 짝 결정

- ROSY 요청 문서: `docs/research/2026-11-20-rosy-contract-requests.md` (항목 1)
- ADR 0029: 반입 사건 계약(`docs/adr/0029-port-events-and-intake-identity.md`)
- ADR 0033: 차량별 도착 사건(다른 작업 소관)
