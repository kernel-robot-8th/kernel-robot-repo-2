# 구역·칸 편집 검증

2026-10-25, Django 유지. [설계21](../design/21-versioned-map-editor.md), [ADR0009](../adr/0009-versioned-flexible-maps.md).

## 결과

- Django 155개 통과. 관리자/CSRF·DTO 오류·초안 fingerprint·새 기준 생성과 부모 감사 이력·원본 불변·미관측/과거 관측 선택을 검증했다.
- 도메인 unittest 136개 중134개 통과, 기존 통합 시험2개 건너뜀. 가변 크기·추가 보관 출발/복원·전체 지지·도달 ID 충돌·탐색/후보 작업량 상한·입력 목록 사전 제한을 확인했다.
- Node의 맵 편집 모델과 기존 화물 가상 편집 모델 시험 통과. 기존 토큰 생성물과 정본 일치, Django migration 변경 없음.
- 격리한 합성 DB와 실제 Edge에서 구역 추가/크기·역할·이름 변경, 적용 전 입력 차단, 화면 겹침 오류와 수정, 검증·등록·미관측 재고 이동, 원본 Condition/Observation 보존을 확인했다.1440/768/390에서 가로 overflow와 콘솔 오류 없음.
- 기존 다칸/3D 브라우저 회귀 통과: 실제 렌더 핸들 이동, 카메라·터치·전체화면·지원/겹침 거절, 관측 불변, context loss 대안, reduced motion/JS 없는 평면 보기.1440/1280/768/390에서 확인했다.
- 기존 전체 제품 브라우저 회귀 통과: 요청·현장 확인·계정·역할·로그아웃·키보드 메뉴·네 폭 화면.

로컬 증거(배포 패키지/원격 공개 대상 아님):

```
runs/versioned-map-django-final.log
runs/map-editor-browser/run-28b9937cf65141a3b1f2210b23b16e8c/report.json
runs/versioned-map-workbench/run-2c3a29c503c441afa661c407193406fb/report.json
runs/versioned-map-product/run-d4f5b49d0afe44fba9a5bfc5e342f446/report.json
```

## 검토와 한계

소유권을 분리한 검토에서 보조 구역 ID가 주 칸 ID를 재사용하여 기존 도달 권한을 오인할 수 있는 경계를 차단했다. 큰 cells 목록은 객체 생성 전에 거절하며 잘못된 UTF-16 메타데이터는 DB 쓰기 전에400으로 거절한다. 회귀 시험으로 고정했다.

설치 기준 등록은 실물 교정·도달·작업 경로·Fleet 독립 승인·전체 footprint 실행 binding을 검증하지 않는다. `grid-placement-v1` 실행 차단을 유지한다. 브라우저 자료는 합성이며 실제 현장 성능·실물/운영 수용은 수행하지 않았다. 원격 push/PR/배포는 하지 않았다.

CE: 해당 없음 — 판단 근거가 ADR·검증 규칙·회귀 시험에 남아 있어 별도 교훈 문서가 추가 정보를 제공하지 않는다.
