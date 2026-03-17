from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy
from . import views
from . import views_wg

urlpatterns = [
    path("manifest.json", views.pwa_manifest, name="pwa_manifest"),
    path("sw.js", views.pwa_sw, name="pwa_sw"),
    path("offline/", views.pwa_offline, name="pwa_offline"),
    path("", views.landing_view, name="landing"),
    path("dashboard/", views.dashboard, name="dashboard"),
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

    # Password reset (Django built-in views, custom templates)
    path("password/reset/", auth_views.PasswordResetView.as_view(
        template_name="portal/password_reset.html",
        email_template_name="portal/email/password_reset.txt",
        html_email_template_name="portal/email/password_reset.html",
        subject_template_name="portal/email/password_reset_subject.txt",
        success_url=reverse_lazy("password_reset_done"),
    ), name="password_reset"),
    path("password/reset/done/", auth_views.PasswordResetDoneView.as_view(
        template_name="portal/password_reset_done.html",
    ), name="password_reset_done"),
    path("password/reset/confirm/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="portal/password_reset_confirm.html",
        success_url=reverse_lazy("password_reset_complete"),
    ), name="password_reset_confirm"),
    path("password/reset/complete/", auth_views.PasswordResetCompleteView.as_view(
        template_name="portal/password_reset_complete.html",
    ), name="password_reset_complete"),

    path("admin-tools/", views.admin_tools, name="admin_tools"),
    path("admin-tools/user/<int:user_id>/", views.admin_user_edit, name="admin_user_edit"),
    path("admin-tools/system/", views.admin_system, name="admin_system"),

    path("account/export/", views.account_export_view, name="account_export"),
    path("account/delete/", views.account_delete_view, name="account_delete"),
    path("account/deleted/", views.account_deleted_view, name="account_deleted"),

    path("register/", views.register_view, name="register"),
    path("verify-email/<uidb64>/<token>/", views.verify_email_view, name="verify_email"),
    path("verify-email/resend/", views.resend_verification_view, name="resend_verification"),

    # JSON API
    path("api/alerts-preview/", views.api_alerts_preview, name="api_alerts_preview"),
    path("api/trend-scores/", views_wg.api_trend_scores, name="api_trend_scores"),
    path("api/trend-factors/", views_wg.api_trend_factors, name="api_trend_factors"),
    path("api/trend-wellbeing/", views_wg.api_trend_wellbeing, name="api_trend_wellbeing"),
    path("api/trend-symptoms/", views_wg.api_trend_symptoms, name="api_trend_symptoms"),
]
