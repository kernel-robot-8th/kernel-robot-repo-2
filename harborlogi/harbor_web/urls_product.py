"""Harbor product routes, separate from account and Fleet routes."""
from django.urls import path
from harbor_web import views, planning_pages, field_pages, record_pages

urlpatterns = [
    path('', views.overview, name='overview'),
    path('cargo/', views.cargo_list, name='cargo'),
    path('cargo/new/', views.cargo_new, name='cargo_new'),
    path('requests/', views.order_list, name='requests'),
    path('requests/new/', views.order_new, name='request_new'),
    path('requests/<int:pk>/', views.order_detail, name='request_detail'),
    path('inventory/', views.inventory, name='inventory'),
    path('inventory/import/', views.observation_new, name='observation_new'),
    path('settings/', views.settings_page, name='settings'),
    path('settings/conditions/new/', views.condition_new, name='condition_new'),
    path('plans/<int:pk>/', planning_pages.plan_detail, name='plan_detail'),
    path('work/', planning_pages.work_list, name='work'),
    path('work/<int:pk>/', planning_pages.work_detail, name='work_detail'),
    path('field/', field_pages.field_list, name='field'),
    path('field/new/', field_pages.field_new, name='field_new'),
    path('field/<int:pk>/', field_pages.field_detail, name='field_detail'),
    path('records/', record_pages.records, name='records'),
    path('records/compare/', record_pages.comparison, name='comparison'),
    path('records/measurements/new/', record_pages.measurement_new, name='measurement_new'),
]
