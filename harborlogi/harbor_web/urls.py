from django.contrib import admin
from django.contrib.auth import views
from django.urls import include, path
from django.views.generic import RedirectView
from django.templatetags.static import static
from harbor_web.auth import has_role

# Django account management still uses its native model permissions.
admin.site.has_permission = lambda request: request.user.is_active and request.user.is_staff and has_role(request.user, 'admin')
admin.site.site_header = 'Harbor 계정 관리'
admin.site.site_title = 'Harbor 관리'
admin.site.index_title = '팀 계정과 역할'
urlpatterns = [
    path('favicon.ico', RedirectView.as_view(url=static('harbor-app-icon-20261023.png'), permanent=True)),
    path('accounts/login/', views.LoginView.as_view(), name='login'),
    path('accounts/logout/', views.LogoutView.as_view(), name='logout'),
    path('accounts/password_change/', views.PasswordChangeView.as_view(), name='password_change'),
    path('accounts/password_change/done/', views.PasswordChangeDoneView.as_view(), name='password_change_done'),
    path('admin/', admin.site.urls),
    path('', include('harbor_web.urls_product')),
]
