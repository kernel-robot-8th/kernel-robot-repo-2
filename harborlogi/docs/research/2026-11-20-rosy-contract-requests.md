# ROSY 플랫폼 연동 요청 목록 (초안, 2026-11-20)

> **초안** — rosy-platform 담당에게 낼 항목 목록입니다.

작성자: HarborLogi  
대상: rosy-platform 담당  
작성일: 2026-11-20

---

## 1. 자리별 인식 스냅샷 API

**무엇이 필요한가**

야드 자리(`slot_ref`: `<area>-r<n>-c<n>-l<n>`)별로 ROSY가 인식한 화물 정보를
Harbor가 주기적으로 조회할 수 있는 읽기 API.

제안 모델 (계약 없음 — 제안):
```
{
  "slot_ref":       "<area>-r<n>-c<n>-l<n>",   # 제안
  "object_ref_read": "<cargo-code 또는 null>",   # 제안
  "read_state":     "READ | UNREADABLE | EMPTY", # 제안
  "observed_at":    "<UTC ISO 8601>",             # 제안
  "source_revision": "<revision>",               # 제안
  "identity":       "observed | attested"        # 제안
}
```

**왜 필요한가 (Harbor 화면·기능)**

Harbor는 야드 보관 장부와 실제 인식 결과를 비교해 불일치를 사람 확인 보류(HOLD)로
전환해야 한다. 현재 계약이 없어 자동 감지가 불가능하다.

**지금 저장소 근거**

- `harbor/process.py:85-87` `anchor_address()` — 자리 주소 형식 `<area>-r<n>-c<n>-l<n>`
- `runs/topview-research.md` §(1) 컨테이너별 인식 — 공개 계약 없음 확인
- ROSY `schemas.py`에 박스·화물 인식 모델 없음 (`runs/topview-research.md`)
- `OverheadDetectionsPayload`는 천장 로봇 blob이고 개체 신원이 아님

**상태**: 새 계약 필요(ADR 대상) — `docs/adr/0032-per-slot-rosy-recognition-contract.md`

---

## 2. 차량별 도착 사건

**무엇이 필요한가**

이송 차량이 반입 지점에 도착한 시각을 Harbor가 알 수 있는 사건.  
`DepartureLedger.authorize(vehicle_id, since=...)` 의 `since` 기준으로 사용한다.

**왜 필요한가 (Harbor 화면·기능)**

현재 `vehicle.release_permitted` 사건(`harbor/departure.py`)은 출발 허락 시각만
담는다. 도착 시각 없이는 "이번 도착 이후에 받은 허락인가"를 판단할 기준이 없다.

**지금 저장소 근거**

- `harbor/departure.py:37-50` `DepartureLedger.authorize(since=...)` — `since` 매개변수가 도착 시각을 요구
- `harbor/departure.py` 모듈 설명과 `DepartureLedger.authorize`: 차량별 현재 도착 이후 허락을 한 번 소비
- ADR 0031 참조(다른 작업 소관)

**상태**: 새 계약 필요(ADR 대상) — ADR 0031(다른 작업 소관)

---

## 3. 장비 상태 요약 API

**무엇이 필요한가**

팔(arm)과 Pinky 상태 요약을 출처와 조회 시각과 함께 공개 API로 제공.  
ADR 0020:38의 재논의 조건을 그대로 인용:

> ROSY 통합관제 R이 팔·Pinky 상태 요약을 출처·조회 시각과 함께 공개 API로 내주고(푸시 또는 주기 조회), Harbor가 그 신선도를 보일 수 있을 때. 그 전에는 `CommandFact`의 작업 상태·조회 시각만 실행 결과로 보인다

**왜 필요한가 (Harbor 화면·기능)**

장비 상태판 표시. ADR 0020에서 거부됐으며 위 조건이 충족될 때까지 거부 상태 유지.

**지금 저장소 근거**

- `docs/adr/0020-port-control-patterns-as-planned-display.md:38` — 재논의 조건
- `harbor_web/readiness.py` — `arm`·`arm_clear`는 실행 준비 관문이며 장비 상태 요약 계약을 대신하지 않음

**상태**: 대기 — ADR 0020 재논의 조건 미충족. 그 전까지 장비 상태판 거부 유지.

---

## 4. 자리 이름 ↔ ROSY 자세(pose)/target_ref 이름 목록

**무엇이 필요한가**

Harbor 자리 주소(`<area>-r<n>-c<n>-l<n>`) ↔ ROSY 자세(pose)/`target_ref` 이름
대응 목록.

**왜 필요한가 (Harbor 화면·기능)**

Harbor 바인딩은 불투명한 `source_ref`·`target_ref`를 나르며 ROSY pose를 해석하지 않는다(`harbor/process.py`). `harbor/place_map.py`의
자리 이름 대응 검사기는 대응 파일을 외부에서 받아야 한다.  
이름을 지어내지 않는다는 규칙(`runs/COMMON.md`) 준수.

**지금 저장소 근거**

- `harbor_web/command_binding.py:25` `HARBOR_BINDINGS_PATH` — 기존 바인딩 파일 경로
- `harbor/process.py:85-87` `anchor_address()` — Harbor 쪽 자리 주소 형식
- `harbor/process.py` `load_bindings` — 기존 JSON 배열의 일반 이송·공간 배치 바인딩 검증
- `harbor/place_map.py` — 같은 파일에서 일반 이송의 `destination → target_ref`, 공간 배치의 `anchor() → target_ref`를 읽음. 공간 배치는 기존 계약상 두 이름이 같음.
- `check_slot(slot_ref, bindings_path)`는 대응된 `target_ref`를 반환하고, 누락·충돌·문법 오류는 거절함. 기본 표·새 환경 변수·시뮬레이션 템플릿의 이름 추정은 없음.

**상태**: ROSY 쪽 목록 미착지 — ROSY에서 `target_ref` 이름 목록 제공 필요.

---

## 5. 배포 확인 요청

**무엇이 필요한가**

다음 항목이 R(통합관제)에 배포됐는지 확인 요청:

1. `GET /api/fleet/port-events` 엔드포인트 (ADR 0029 조회 계약)
2. `vehicle.release_permitted` 사건 (ADR 0029 결정 2)
3. 탑뷰 CELL-A 카메라 노출 (vision sources에 포함 여부)
4. Harbor service principal이 `require_camera_viewer` 권한 통과 여부

**왜 필요한가 (Harbor 화면·기능)**

- `harbor/port_events.py`의 소비기는 엔드포인트가 없거나 닿지 않으면 진행을 기다린다. `IntakeLedger.poll`은 HTTP 오류를 호출부로 전달하며, 응답한 적 없는 연결에서는 시간 초과 LOST를 만들지 않는다.
- `harbor_web/readiness.py:39-40` `port_events`는 비관문, 표시 전용.
- 탑뷰 카메라: Harbor service principal 경로가 코드상 Bearer 일반 경로(`app.py:395`)이지만 실물 접근 가능한지 미확인.

**지금 저장소 근거**

- `harbor/port_events.py:20-30` — 사건 모델
- `harbor_web/readiness.py:39-40` — port_events readiness 비관문
- `runs/topview-research.md` §확인 못 함

**상태**: 미확인 — R에 배포 여부 확인 요청.
