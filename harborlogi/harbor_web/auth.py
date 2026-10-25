from functools import wraps
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse

def roles(user):
    if not user.is_authenticated or not user.is_active:
        return set()
    return set(user.groups.filter(name__in=['viewer', 'operator', 'field', 'admin']).values_list('name', flat=True))

def has_role(user, *names):
    return bool(roles(user).intersection(names))

def require_roles(*names):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated or not request.user.is_active:
                return redirect_to_login(request.get_full_path())
            if not has_role(request.user, *names):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorate

def client_ip(request):
    return request.META.get('REMOTE_ADDR')

def lockout_response(request, credentials=None, *args, **kwargs):
    return HttpResponse('Too many login attempts. Try again later.', status=429, content_type='text/plain')
