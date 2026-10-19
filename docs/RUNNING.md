# ROSY 설치·빌드·실행

이 제출본의 ROSY 작업 루트는 `rosy/`입니다. 아래 명령은 Ubuntu의 Bash에서 실행합니다. ROSY의 기준 환경은 Ubuntu 24.04와 ROS 2 Jazzy이며, 실제 Pinky Pro는 Raspberry Pi 5의 Ubuntu Server 24.04 arm64에서 실행합니다.

## 준비

```bash
git clone https://github.com/livsbittt/kernel-robot-repo-2.git --branch feat/rosy-submission
cd kernel-robot-repo-2/rosy
```

ROS 2, Gazebo와 Python 의존성, 호스트별 설정은 포함된 [개발 가이드](../rosy/docs/reference/developer-guide.md)와 [로봇 배포 문서](../rosy/docs/deployment/)를 따릅니다. 아래 명령은 이 준비가 완료된 환경을 전제로 합니다.

## 빌드

```bash
source env.sh
colcon --log-base log build --symlink-install --base-paths $(python3 tools/harness/colcon_roots.py) --build-base build --install-base install
source install/setup.bash
```

패키지 탐색 경로는 원본의 `colcon_roots.py`가 결정합니다. 제출 저장소의 최상위 디렉터리에서 빌드하지 않습니다.

## 시뮬레이션

빌드 후 `rosy/`에서 실행합니다.

```bash
source env.sh
source install/setup.bash
bash tools/run_fleet_sim.sh
```

개별 실행과 다로봇 시뮬레이션 옵션은 [개발 가이드의 빌드 및 시뮬레이션](../rosy/docs/reference/developer-guide.md#빌드-및-시뮬레이션)에 있습니다.

## 실제 로봇

실제 장치는 [로봇 실행 안내](../rosy/README.md#로봇-실행)와 [배포 문서](../rosy/docs/deployment/)에 따라 네이티브 런타임을 설치합니다. 시뮬레이션 명령을 실제 장치 배포 절차로 사용하지 않습니다. 실제 주소·계정·키·채워진 장치 설정은 공개 저장소에 넣지 않고 `rosy/private/`에서 관리합니다.

## 검증 범위

이번 제출 작업에서는 가져온 소스의 Git 트리 일치와 안내 문서의 로컬 링크를 확인합니다. ROS 2 빌드, 시뮬레이션, 실제 로봇 시험은 수행하지 않았습니다. 원본의 [상태 문서](../rosy/STATUS.md)와 기존 시험 기록은 원본 기준의 기록입니다. `rosy/.github/workflows/`에 보존된 워크플로는 이 제출 저장소의 루트 워크플로가 아니므로 GitHub Actions에서 자동 실행되지 않습니다.
