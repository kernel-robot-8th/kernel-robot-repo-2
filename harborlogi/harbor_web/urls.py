from django.contrib import admin
from django.contrib.auth import views
from django.urls import include, path
from harbor_web.auth import has_role

# Django account management still uses its native model permissions.
admin.site.has_permission = lambda request: request.user.is_active and request.user.is_staff and has_role(request.user, 'admin')
urlpatterns = [
    path('accounts/login/', views.LoginView.as_view(), name='login'),
    path('accounts/logout/', views.LogoutView.as_view(), name='logout'),
    path('accounts/password_change/', views.PasswordChangeView.as_view(), name='password_change'),
    path('accounts/password_change/done/', views.PasswordChangeDoneView.as_view(), name='password_change_done'),
    path('admin/', admin.site.urls),
    path('', include('harbor_web.urls_product')),
]
