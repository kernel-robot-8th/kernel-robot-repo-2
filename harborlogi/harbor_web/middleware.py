from ipaddress import ip_address
from django.conf import settings
from django.http import HttpResponseForbidden
from django.utils.cache import add_never_cache_headers

class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.MODE == 'development':
            try:
                loopback = ip_address(request.META.get('REMOTE_ADDR', '')).is_loopback
            except ValueError:
                loopback = False
            if not loopback:
                return HttpResponseForbidden('Development mode requires loopback access.')
        response = self.get_response(request)
        if request.user.is_authenticated:
            add_never_cache_headers(response)
        response['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        response['Referrer-Policy'] = 'same-origin'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
