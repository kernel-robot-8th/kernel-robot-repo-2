import os

from django.apps import AppConfig


class HarborWebConfig(AppConfig):
    name = 'harbor_web'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        # Explicit opt-in: the standing business executor (design 24/27 P1) runs only in
        # processes that ask for it, after deployment checks have configured execution.
        if os.environ.get('HARBOR_EXECUTOR_ENABLED') == '1':
            from harbor_web import executor
            executor.start()
