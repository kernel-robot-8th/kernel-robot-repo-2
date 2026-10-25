# Fleet 연결 설정과 재생 시간 검증

2026-10-26. 책임 기준: ADR0013. 설정/검사 구현: 상세설계25. 실제 장치·운영 수용은 별도다.

## 재현과 수정

로컬8060의 반출 계획을 실제 Edge로 확인했다. 일반 동작 설정에서는 약2초 시점에 첫 화물의 이동33%·실제 mesh 중간 위치가 보였다. reduced-motion에서는 세 단계가 약0.23초에 최종 배치로 넘어갔다. 기존 코드는 이 환경에서 전환과 다음 단계 대기 시간을 모두0으로 처리했다.

이동 애니메이션 선택은 OS 설정을 기본으로 따르되 사용자가 직접 켤 수 있다. 애니메이션을 끄면 계획과 가상 편집 모두 기본4.5초 간격으로 배치를 보여준다. 출발 관측이 없는 새 입고 화물은 가상 이동 경로 없이 목적 배치 비교를4.5초 동안 보여준다. 가상 편집의 동기 재귀 진행도 공통 timer로 바꿔 즉시 종료를 막았다.

최종 브라우저 회귀에서 reduced 전체 계획 약14.14초, 두 번의 가상 변경 약9.64초, 입고 비교 약5.67초를 확인했다.0.5배 이동25% 시점의 실제 렌더 높이3.60은 원본 높이1.00보다 높았으며 약2.86초가 경과했다. 재생 후 반복·중지·원본 JSON·서버 불변도 확인했다.

## 설정과 연동

관리자 UI는 서버 allowlist 안의 Fleet origin·서비스 신원·ARM 콘솔 설치 선언을 version과 함께 저장한다. 기존 배포 origin이 있으면 우선하며 UI에서 덮어쓰지 않는다. 토큰·장부·binding 경로는 서버 환경에 남고 화면과 프로필 DB에 자격을 보관하지 않는다.

명시적인 연결 확인은 세션 신원과 새로운 요청 key의 ARM 조회만 GET으로 검사한다. 올바른 구조화된 NOT_FOUND와 일반404를 구별하며, 성공은 조회 API 지원을 뜻한다. 설정·토큰 변경 중의 결과나 과거 성공을 새 설정에 적용하지 않는다. 기존 Command가 있으면 origin/principal 변경을 거절한다. 같은 설정은 제출·대조·콘솔 링크와 백업 신원에 연결된다.

## 실행한 검사

- Django195개 통과, migration 생성물 불일치 없음.
- 도메인148개 수집,146개 통과·실제 통합 설정이 필요한2개 skip. Node 점유/회전/애니메이션 모델·토큰 생성물 대조 통과.
- 실제 Edge 재생/다칸 회귀18개 검사 통과, 브라우저 오류0, 네 화면 폭 확인. 근거: `runs/playback-reviewed-final/run-11c7ac3a7e4343569464a804ce224023/report.json`.
- 실제 Edge 연결 UI 검사4개 묶음 통과. 인증된 합성 HTTP double로 설정 저장·기존 config 소비·GET-only7회·generic404 거절·신원 불일치·역할·토큰 비노출·네 화면 폭을 확인했다. Command 생성0. 근거: `runs/fleet-bridge-browser/run-d8d1a403a0eb414fbc8ed4bc63b84f44/report.json`. 이것은 실제 Fleet producer 검증이 아니다.
- 별도 읽기 전용 검토에서 발견한 motionless 가상 편집 재생 누락을 수정하고 회귀로 검증했다. 나머지 high/medium 지적 없음.

## 실제 Fleet 검증과 운영 연결

고정 producer `bb38fdcdd0140873e4e9d0133662982c1d6a30e0`와 Harbor `e4bf6cd9de2d53d20060583b50ce2fde8103115d`의 실제 HTTP 통합을 적격 비현장 시험 PC에서 실행했다. 기존 checkout에는 정확한 pin이 없어 시험 캐시로부터 격리 clone을 준비했으며 기존 플랫폼 작업은 바꾸지 않았다. 별도 app venv는 플랫폼 모듈을 import하지 않고 공개 HTTP만 소비했다. pip check 통과, 실제 통합1개 실행·skip0·exit0, 수행 시간6.026초다.

실제 `product_web_http` 시험은 관리자 HTTP/CSRF 세션으로 연결 프로필을 저장하고 신원·구조화된 ARM 조회를 검증했다. 이어 계획 확정의 즉시 제출, 외부 승인 주체, 원래 key·본문·신원·receipt 결속, 재제출409, 물리 receipt 뒤에도 업무 완료 근거 없이는 WAITING 유지, 취소·원본 대조를 확인했다. raw 로그와 양 끝 SHA 기록은 비공개 작업 공간의 `Rosy/docs/workspace-records/2026-10-26/fleet-bridge-http-evidence/`에 보존한다. 실제 Fleet 코드에 합성 owner/observer를 연결한 시험이며 물리 장치·현장 수용이 아니다.

사용할 운영 Fleet origin·서비스 principal과 서버 자격은 실제 설치 담당자가 지정해야 한다. 합성 미리보기는 운영 설정을 상속하지 않는다. 다칸 binding·인증된 업무 근거·ARM 정책 admission·상시 업무 실행기는 이 연결 확인으로 활성화되지 않는다.

CE: 해당 없음. 실제 화면에서 놓친 preference/가상 편집 재생 경로를 새 회귀 시험에 포함했으며, 재발 방지 판단은 코드·시험·상세설계에서 확인할 수 있다.

## 후속 운영 서버 조사와 역할 증거

실제 운영 Fleet의 TLS 공개 schema와 설치 구성을 직접 조회했다. ARM transfer route와 Harbor 서비스 계정이 없는 상태였다. 현행 공식 사용자 CLI는 service row 검증은 지원하지만 service 계정 발급은 거절한다. Cell 연동도 이미 존재하는 service principal을 소비하므로 일반적인 발급 경로를 대신하지 않는다.

검증된 ARM producer 역시 설치를 제공하면 simulation profile을 요구한다. 설치가 없으면 공개 route를 등록할 수 있지만 조회는503 미준비로 응답하고 worker를 만들지 않는다. 따라서 API 코드를 배포하거나 합성 설치를 운영에 넣는 방식으로 실제 장치 연동을 완료했다고 표시할 수 없다. 정식 운영 설치 계약·서비스 발급/설치 도구·journal migration/복구 검증이 필요하다. 운영 배포 확대는 사용자 범위 확인 중이다.

실제 HTTP 하네스의 token 별칭 `operator`는 Fleet 역할이 `service`이고, 별도 `approver`가 사람 operator 역할이다. 혼동을 막기 위해 앱 소비 시험이 실제 session 응답의 역할을 명시적으로 검사하도록 보강했다. 이전 테스트의 별칭만으로 사람 자격을 앱에 재사용했다고 해석하지 않는다. 이 명시 검사를 포함한 Harbor `d506fa10e72949ebf6fe9a87cfd20a8e8ca2b352`와 동일 ROSY pin의 원격 실제 HTTP 시험도1개 실행·skip0·exit0,4.108초로 통과했다.

CE: 미해결 — 운영 API·설치 계약·정식 서비스 발급과 복구 경로는 선행 조건으로 남아 있다. 실제 서버 조사는 비공개 `fleet-live-discovery/`와 운영 연결 선행 기록에 보존한다.
