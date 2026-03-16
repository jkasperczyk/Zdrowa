from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("alerts/", views.alerts, name="alerts"),
    path("data/", views.data_view, name="data"),
    path("trends/", views.trends, name="trends"),
    path("trends/file/<str:fname>/", views.trend_file, name="trend_file"),
    path("settings/", views.settings_view, name="settings"),

    path("password/change/", views.password_change_view, name="password_change"),

    path("admin-tools/", views.admin_tools, name="admin_tools"),
    path("admin-tools/user/<int:user_id>/", views.admin_user_edit, name="admin_user_edit"),
]
