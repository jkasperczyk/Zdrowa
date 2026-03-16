from __future__ import annotations
from django.shortcuts import redirect
from django.urls import reverse

ALLOWED_PREFIXES = (
    "/logout/",
    "/password/change/",
    "/admin/",
    "/static/",
)

class ForcePasswordChangeMiddleware:
    """If user must change password, force redirect to /password/change/."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or "/"
        if request.user.is_authenticated and not request.user.is_staff:
            try:
                prof = getattr(request.user, "profile", None)
                if prof and getattr(prof, "must_change_password", False):
                    if not path.startswith(ALLOWED_PREFIXES):
                        return redirect(reverse("password_change"))
            except Exception:
                pass
        return self.get_response(request)
