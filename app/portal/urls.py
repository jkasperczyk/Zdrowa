from django.urls import path
from . import views
from . import views_wg

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("alerts/", views_wg.alerts, name="alerts"),
    path("data/", views_wg.data_view, name="data"),
    path("trends/", views.trends, name="trends"),
    path("trends/file/<str:fname>/", views.trend_file, name="trend_file"),
    path("settings/", views_wg.settings_view, name="settings"),
    path("wellbeing/", views_wg.wellbeing_view, name="wellbeing"),

    path("password/change/", views.password_change_view, name="password_change"),

    path("admin-tools/", views.admin_tools, name="admin_tools"),
    path("admin-tools/user/<int:user_id>/", views.admin_user_edit, name="admin_user_edit"),
    path("admin-tools/system/", views.admin_system, name="admin_system"),
]
