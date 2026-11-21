import os

from django.apps import AppConfig


class HarborWebConfig(AppConfig):
    name = 'harbor_web'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        from django.contrib.auth import get_user_model
        from harbor_web.auth import validate_no_bang_at_username

        field = get_user_model()._meta.get_field('username')
        if validate_no_bang_at_username not in field.validators:
            field.validators.append(validate_no_bang_at_username)

        # Explicit opt-in: the standing business executor (design 24/27 P1) runs only in
        # processes that ask for it, after deployment checks have configured execution.
        if os.environ.get('HARBOR_EXECUTOR_ENABLED') == '1':
            from harbor_web import executor
            executor.start()
