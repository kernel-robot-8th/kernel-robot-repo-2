# Harbor 운영 설치 (Docker Compose)

Harbor는 통합관제 R과 같은 운영 PC에 둔다. 지금은 OMX 관제 PC에 R과 함께 두고, R이 전용 PC로 옮기면 Harbor도 함께 옮긴다. 결정과 아직 수용되지 않은 조건은 [ADR 0021](../../docs/adr/0021-harbor-runs-beside-integrated-control-r.md)에 있다.

이 폴더의 구성은 R·CELL-A와 섞이지 않는다. Compose project 이름(`harbor`), 공개 포트, 데이터 volume(`harbor_harbor-data`), TLS 인증서, R이 발급한 서비스 principal을 각자 따로 둔다.

| 서비스 | 하는 일 |
|---|---|
| `harbor` | Waitress 한 프로세스. 웹과 상시 실행기가 함께 돌고, 컨테이너 안 `127.0.0.1:8060`에서만 듣는다 |
| `proxy` | nginx. `harbor`의 네트워크를 함께 써서 TLS를 끝내고 loopback으로 넘긴다. 규칙은 [nginx 예시](../web/nginx.conf.example)와 같다 |
| `manage` | 일회성 관리 실행(migrate·계정·백업·복원). 실행기를 켜지 않는다 |

## 처음 설치

R 호스트에서 이 저장소의 고정 커밋을 체크아웃한 뒤 실행한다. 비밀 값은 저장소 밖에 둔다.

1. `harbor.env.example`을 저장소 밖(예: `~/.config/harbor/harbor.env`, 권한 600)에 복사하고 채운다. R origin·principal·token은 R 설치 담당자에게 받는다.
2. 저장소 밖 폴더 두 개를 준비한다.
   - 설정 폴더: 설치 binding `bindings.json`을 넣는다. R 인증서가 사설 CA이면 `r-ca.pem`도 넣는다.
   - TLS 폴더: Harbor 자기 인증서 `fullchain.pem`과 `private-key.pem`을 넣는다. R 인증서를 빌려 쓰지 않는다.
3. 환경 변수를 지정한다: `HARBOR_ENV_FILE`, `HARBOR_CONFIG_DIR`, `HARBOR_TLS_DIR`, `HARBOR_HOSTNAME`, `HARBOR_BACKUP_DIR`, 그리고 R·CELL-A와 겹치지 않는 `HARBOR_HTTPS_PORT`(기본 8443).
4. 다음 순서로 실행한다.

```bash
cd harborlogi/deploy/compose
docker compose build
docker compose --profile setup run --rm manage                      # migrate
docker compose --profile setup run --rm manage python -m harbor_web.manage bootstrap_roles
docker compose --profile setup run --rm manage python -m harbor_web.manage createsuperuser
docker compose up -d harbor proxy
```

migrate를 하지 않으면 `harbor`가 시작을 거절한다. R principal·token·binding 중 하나라도 비어 있으면 Fleet 실행 연결 없이 화면만 뜬다.

## 갱신

팀 `main`에 병합된 커밋만 올린다. 그 커밋을 체크아웃하고 `HARBOR_IMAGE=harborlogi:<커밋 앞 12자리>`로 빌드·기동한다. 아래 갱신 순서를 따른다.

1. `docker compose stop harbor`로 실행기를 멈춘다.
2. 백업한다.
3. `manage`로 migrate한다.
4. `up -d`로 다시 띄운다.

되돌릴 때는 이전 이미지 태그로 같은 순서를 밟고, 백업에서 복원한다.

## 백업

writer가 멈춘 상태에서 짝 백업한다. 복원 규칙은 [제품 웹 안내](../../docs/product-web.md#배포백업검증)를 따른다.

```bash
docker compose stop harbor
docker compose --profile setup run --rm manage python -m harbor_web.manage backup_product --output /backup/$(date +%Y%m%d-%H%M)
docker compose up -d harbor proxy
```

## R과 함께 옮기기

R을 옮길 때(D-893 §6) Harbor도 같은 창에서 옮긴다.

1. 진행 중이거나 결과 불명인 인계를 원래 작업 화면에서 대조한다.
2. `harbor`를 멈추고 백업한다.
3. 새 호스트에 같은 커밋을 설치한다.
4. `restore_product`로 새 파일에 복원한다.
5. env의 R origin을 새 주소로 바꾼다.

새 호스트의 volume은 비어 있으므로 compose가 쓰는 경로로 바로 복원한다.

```bash
docker compose --profile setup run --rm manage python -m harbor_web.manage restore_product \
  --backup /backup/<백업 폴더> --web-database /data/web.sqlite3 --ledger-database /data/ledger.sqlite3
```

복원된 DB는 새 제출을 막는다. 원래 실행 key 대조를 마치기 전에는 장부나 상태를 지워 새 요청으로 재시도하지 않는다. 이전 호스트의 `harbor`는 지운 것을 확인한 뒤에야 새 호스트를 연다.
