from django.contrib import admin
from django.urls import path, include
from portal import views as portal_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", portal_views.login_view, name="login"),
    path("logout/", portal_views.logout_view, name="logout"),
    path("", include("portal.urls")),
]
