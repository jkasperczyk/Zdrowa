from django.urls import path
from . import views
from . import views_wg

urlpatterns = [
    path("manifest.json", views.pwa_manifest, name="pwa_manifest"),
    path("sw.js", views.pwa_sw, name="pwa_sw"),
    path("offline/", views.pwa_offline, name="pwa_offline"),
    path("", views.dashboard, name="dashboard"),
    path("alerts/", views_wg.alerts, name="alerts"),
    path("data/", views_wg.data_view, name="data"),
    path("data/export/", views_wg.csv_export_view, name="csv_export"),
    path("trends/", views.trends, name="trends"),
    path("trends/file/<str:fname>/", views.trend_file, name="trend_file"),
    path("settings/", views_wg.settings_view, name="settings"),
    path("wellbeing/", views_wg.wellbeing_view, name="wellbeing"),
    path("symptom/", views_wg.symptom_log_view, name="symptom_log"),
    path("raporty/", views_wg.raporty_view, name="raporty"),

    # Push notifications
    path("push/subscribe/", views.push_subscribe, name="push_subscribe"),
    path("admin-tools/push-queue/", views.process_push_queue, name="process_push_queue"),

    path("password/change/", views.password_change_view, name="password_change"),

    path("admin-tools/", views.admin_tools, name="admin_tools"),
    path("admin-tools/user/<int:user_id>/", views.admin_user_edit, name="admin_user_edit"),
    path("admin-tools/system/", views.admin_system, name="admin_system"),

    path("account/export/", views.account_export_view, name="account_export"),
    path("account/delete/", views.account_delete_view, name="account_delete"),
    path("account/deleted/", views.account_deleted_view, name="account_deleted"),
    path("register/", views.register_view, name="register"),
]
