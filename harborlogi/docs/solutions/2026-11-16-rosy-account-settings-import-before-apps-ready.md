# settings.py가 부르는 모듈에 Django 모델·뷰 최상위 import가 있으면 HARBOR_ROSY_ACCOUNT_FILE 활성화 때 기동이 죽는다

2026-11-16. `rosy-fleet-r`에서 Harbor의 Rosy Account 로그인(`HARBOR_ROSY_ACCOUNT_FILE=/config/rosy-account.json`)을 실제로 켜다 찾았다. 단위 시험(`test_rosy_account.py`)은 모두 통과했지만 컨테이너 기동은 `AppRegistryNotReady`로 즉시 실패했다.

## 증상

- `/etc/harbor/harbor.env`에 `HARBOR_ROSY_ACCOUNT_FILE=/config/rosy-account.json`을 넣고 `harbor-harbor-1` 컨테이너를 재기동하자 `harbor_web/__main__.py`의 `django.setup()` 단계에서 `django.core.exceptions.AppRegistryNotReady: Apps aren't loaded yet.`가 발생해 컨테이너가 `unhealthy`로 멈췄다.
- 기존 `test_rosy_account.py` 9개 시험은 `@override_settings(ROSY_ACCOUNT=CONFIG)`와 `rosy_account.load_config(...)`를 모두 통과하고 있었다.

## 원인

1. `harbor_web/settings.py:114-116`은 환경 변수 `HARBOR_ROSY_ACCOUNT_FILE`이 설정되어 있으면 `from harbor_web.rosy_account import load_config`를 실행한다. 이 시점은 `django.setup()`이 설정 모듈을 읽는 중이라 Django 앱 레지스트리가 아직 준비되지 않은 상태다.
2. `harbor_web/rosy_account.py` 최상위에 `from django.contrib.auth import views as auth_views`와 `from django.contrib.auth.models import Group, User`가 있어, 모듈이 import되자마자 `User` 모델 메타클래스가 `apps.check_apps_ready()`를 호출하며 예외를 던졌다.
3. 단위 시험에서는 시험 실행 시점에 `HARBOR_ROSY_ACCOUNT_FILE`이 비어 있어 `django.setup()`이 끝난 뒤에야 `test_rosy_account.py`가 `rosy_account`를 import했기 때문에 이 경로를 밟지 않았다.

## 해결

- `harbor_web/rosy_account.py` 최상위에서 `django.contrib.auth.views`와 `Group, User` import를 제거하고, `Group`·`User`는 `_sync()`·`callback()` 함수 내부에서, `LoginView`는 모듈 `__getattr__('LoginView')`(`urls.py` 로드 시점에는 앱 레지스트리 준비 완료)에서 지연 생성하도록 고쳤다.
- `harbor_web/tests/test_rosy_account.py`에 `apps.check_apps_ready`가 `AppRegistryNotReady`를 던지는 상태에서 `importlib.reload(rosy_account)`와 `load_config()`가 동작하는지 검증하는 회귀 시험(`test_load_config_imports_before_django_apps_are_ready`)을 추가했다.

## 검증

- 로컬 단위 시험: `python -m django test harbor_web.tests.test_rosy_account --settings=harbor_web.settings` 10개 시험 통과.
- 운영 서버(`rosy-fleet-r`) 검증: `harbor-harbor-1` 기동 후 `healthy` 전환 확인, `rosy-site-account-login-check.service` 실행 시 `PASS harbor (session 200)`을 포함한 전체 10개 웹 앱 E2E 로그인 통과 확인.

## 다음에 적용할 조건

- `settings.py`에서 조건부로 import하는 모듈은 표준 라이브러리와 `django.conf`·`django.core.exceptions`만 최상위에서 import하고, `django.contrib.auth.models`나 `views`처럼 앱 레지스트리에 의존하는 객체는 요청·URL 해석 시점으로 지연시킨다.
- 환경 변수로 켜지는 설정 분기(`if os.environ.get(...):`)를 시험할 때는 `@override_settings`만 쓰지 말고 `apps.check_apps_ready` 미준비 상태에서의 모듈 import 회귀 시험을 함께 둔다.
