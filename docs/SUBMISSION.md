# ROSY 제출 기준

## 저장소와 기준 커밋

- 팀 원본: https://github.com/kernel-robot-8th/kernel-robot-repo-2
- 제출 포크: https://github.com/livsbittt/kernel-robot-repo-2
- 제출 브랜치: `feat/rosy-submission`
- ROSY 출처: https://github.com/robotics-team-1213/rosy-platform
- ROSY 기준: `38f0c452e410fca6c2c8fc72f45c3c250751d58f`
- 확인일: 2026-10-19

공개된 기준 커밋의 Git 트리를 `rosy/`에 복사합니다. Git 서브모듈 없이 소스와 실행 문서를 함께 받는 형태이며, ROSY의 기존 Git 이력은 제출 브랜치에 병합하지 않습니다. 원본 라이선스, 설정, 시험, 문서와 패키지 경로는 보존합니다.

개발 중인 ROSY 로컬 체크아웃의 미게시 커밋·미추적 파일·비공개 설정은 제출 대상에 포함하지 않습니다. 제출본에 기록된 성능과 시험 결과는 해당 원본 기록의 범위에서 해석합니다.

## 제출 흐름

`팀 원본 main → 개인 포크 feat/rosy-submission → 팀 원본 main 대상 PR` 순서로 진행합니다. 팀 원본 병합은 PR 검토 후 진행합니다.

루트 `README.md`는 팀 프로젝트의 입구이며 `docs/RUNNING.md`는 제출본의 실행 위치와 절차를 안내합니다. ROSY 내부 설명은 `rosy/`의 기존 문서를 참조합니다.

## 이후 갱신

ROSY의 변경을 반영할 때는 공개된 새 기준 커밋을 선정하고 `rosy/` 전체를 그 트리와 일치시킵니다. 이 문서와 루트 README의 기준 커밋을 함께 갱신하고, 삭제된 파일까지 반영됐는지 확인합니다. 라이선스 고지를 보존하고 제출본에서 필요한 빌드·시험 결과를 별도로 기록합니다.
