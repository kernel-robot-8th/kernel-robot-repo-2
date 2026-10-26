# 설계 30 — ARM preflight 소비를 위한 설치 catalog digest

2026-10-27. [설계 29](29-spatial-binding-and-arm-assignment.md)가 열린 질문 3으로 남긴 "preflight `catalog_digest`의 발급·갱신 절차"를 다룬다. 이 문서는 구현이 아니라 **선택지와 결정 요청**이며, 결정 없이 코드를 열지 않는다.

## 문제

설계 29는 v1.299 preflight를 운영 도달 판정이 아니라 설치 대응 검증 보조로 한정했다. preflight를 실제로 호출하려면 요청마다 설치 catalog digest가 필요하다.

| 사실 | 근거 |
|---|---|
| preflight 요청 필드는 `workcell_id`, `instance_id`, `catalog_digest`, `source`, `target`, `geometry`이고 `catalog_digest`는 소문자 SHA-256이다 | API reference v1.299 절 |
| 비시뮬레이션 배포는 503 `ARM_PREFLIGHT_CLOSED`, 미설정·불일치 대상은 404 `ARM_PREFLIGHT_TARGET_UNAVAILABLE` | 같은 절 |
| ARM 작업 제안(v1.303 job)은 `workcell_id`·`instance_id`·`object_ref`·`source`·`target`만 보내고 세 digest는 설치 측이 해석한다 | v1.303 절, `ArmTransferBinding.proposal_request` |
| 공개 계약에는 설치 catalog digest를 **발급하거나 조회하는 경로**가 없다 | 위 두 절 |

즉 작업 제안 경로는 digest를 Harbor에 요구하지 않지만 preflight만 호출자에게 digest를 요구한다. 오늘 상태에서 Harbor가 preflight를 부르려면 값을 추측하거나 하드코딩해야 하고, 틀린 값은 404로만 돌아와 "설치가 다르다"는 사실만 알려 준다. 이 구조로 preflight를 켜면 fail-open이 아니라 **fail-silent**가 된다(검증했다고 착각할 수 있다).

## 선택지

| | 방법 | 장점 | 비용·위험 |
|---|---|---|---|
| A | 설치 산출물에 digest를 기록한다. 설치 통합 담당이 설치 시 `workcell_id`·`instance_id`별 `catalog_digest`를 발급해 설치 manifest/binding 파일에 넣고, Harbor는 그 파일에서 읽는다 | 공개 계약 변경 없음. 설치와 같은 출처 | 발급 도구·서명·갱신 주체가 설치 쪽 일. 파일 형식 확장 필요 |
| B | Fleet이 설치 catalog digest를 조회하는 공개 경로를 추가한다 | 단일 출처, 자동 최신 | API 계약 변경(새 버전), 플랫폼 작업, 새 노출면·권한 |
| C | preflight를 소비하지 않는다. 운영 도달은 설계 29의 설치 대응으로만 정하고 preflight는 설치자 자체 점검·시뮬레이션에만 쓴다 | 오늘 구현 가능, 계약 변경 없음 | 설치 오류를 실행 전에 못 잡는다. 첫 작업 실패로 드러난다 |

## 잠정 권고

**C를 유지하고, 설치 산출물 표준으로 A를 채택한다.** 결정은 설치 통합 담당과 사용자가 한다.

- preflight가 시뮬레이션 전용인 동안 운영 판정의 원본이 될 수 없다(설계 31 P4 기록). C는 그 사실을 코드에 그대로 반영한 상태다.
- B는 계약 확장 비용이 가장 크고, 지금 필요한 것은 "설치가 무엇을 깔았는지"를 아는 일이지 Fleet의 새 조회면이 아니다.
- A는 이미 설치 binding 파일로 물리 참조를 받는 구조에 필드 하나를 더하는 일이라 Harbor 변경이 작다.

## A를 택할 때의 계약(제안)

- **필드:** binding 항목에 `catalog_digest`(소문자 64자리)와 설치가 정하는 `catalog_revision`을 둔다. 선택 필드이며, 없으면 preflight를 호출하지 않는다.
- **충돌:** 같은 (`workcell_id`, `instance_id`)에 서로 다른 digest를 주장하는 binding이 있으면 설치 오류로 거절한다. 조용히 하나를 고르지 않는다.
- **갱신 트리거:** 재설치, 캘리브레이션·기하 변경, catalog 개체 추가·삭제, workcell 교체. 갱신 전 digest로 받은 preflight 결과는 무효다.
- **소비 규칙:** digest가 없으면 호출 0회(보류가 아니라 미사용). 404·503·불일치는 도달 판정을 바꾸지 않고 설치 검증 실패로만 기록한다.
- **시험:** 같은 digest로 1회 통과, 낡은 digest로 404, 비시뮬레이션 배포에서 503, digest 누락 시 preflight 호출 0회. 마지막 두 개는 negative 시험이며 통과로 세지 않는다.

## 하지 않는 것

- Harbor가 digest를 만들어 내지 않는다. 추측·하드코딩·기본값으로 preflight를 부르지 않는다.
- preflight를 운영 도달 원본으로 승격하지 않는다(설계 29 수용 시험의 금지 항목).
- 실제 설치 digest의 최초 발급, 발급 도구의 서명 방식, 보관 위치는 설치 통합 담당 소유다.
- B를 택하면 API 버전·권한·감사 규칙을 담은 별도 플랫폼 설계가 먼저다.

## 결정이 필요한 것

1. A·B·C 가운데 무엇을 택하는가(권고: C 유지 + A 표준 채택).
2. A라면 설치 manifest에 digest를 쓰는 주체와 갱신 확인 방법.
3. B라면 플랫폼에서 새 조회 경로를 열 버전과 권한.

결정이 오면 그 선택에 맞춰 구현 PR을 연다. 결정 전에는 preflight 소비 코드를 추가하지 않는다.
