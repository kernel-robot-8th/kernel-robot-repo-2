# 독립 인증·협력 경계 구현 계획

**Goal:** 기존 공개 API로 협력하면서 Harbor 사람/기계·Fleet 승인·독립 관측·장치 권한을 분리하고 실제 호출을 검증된 서비스 신원에 결속한다.
**Spec:** ADR0015·설계27. Django와 기존 ROSY HTTP adapter·Registry·장부를 유지한다.

- [x] 실제 양쪽 route/role/creator/observer 계약과 최신 게시 상태를 읽기 전용 대조한다. 플랫폼 reviewer와 일반 Harbor reviewer의 범위를 구분한다.
- [x] 잘못된 자격이 POST 뒤에만 발견되는 경로를 RED로 재현하고 호출 전 신원 검사로 고친다. Registry service 역할과 설정 검사를 일치시킨다.
- [x] 사람 계정과 서비스 신뢰·공개 계약·설치/자격 회전·책임 표를 ADR·설계·설정 UI에 기록하고 독립 검토한다.
- [x] Django/도메인·실제 브라우저·고정 양 끝 Fleet HTTP를 검증한다. 실제 Fleet은 승인된 비현장 원격 시험 PC에서만 실행한다.
- [ ] CI·병합·로컬8060 반영·Confluence를 갱신한다. 운영 배포·DNS 관리자 작업·자격 발급·실물 수용은 최신 증거 없이 완료 처리하지 않는다.
