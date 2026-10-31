from ..settings import *

# Isolate authentication tests from product templates under concurrent construction.
TEMPLATES = [dict(TEMPLATES[0], APP_DIRS=False, OPTIONS={**TEMPLATES[0]['OPTIONS'], 'loaders': [('django.template.loaders.locmem.Loader', {'registration/login.html': '<form method="post">{% csrf_token %}{{ form.as_p }}<button>Login</button></form>', 'registration/password_change_form.html': '<form method="post">{% csrf_token %}{{ form.as_p }}</form>'}), 'django.template.loaders.app_directories.Loader']})]

TEMPLATES[0]['OPTIONS']['context_processors'] = [p for p in TEMPLATES[0]['OPTIONS']['context_processors'] if p != 'harbor_web.presentation.context']
