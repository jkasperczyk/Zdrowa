from __future__ import annotations

import os
from pathlib import Path
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, FileResponse, Http404
from django.shortcuts import render, redirect

from .models import UserProfile
from .wg_sources import last_readings, sms_subscription_status, set_sms_subscription, list_trend_files

def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect("dashboard")
        messages.error(request, "Nieprawidłowy login lub hasło.")
    return render(request, "portal/login.html", {})

def logout_view(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect("login")

def _get_profile(user) -> UserProfile:
    prof, _ = UserProfile.objects.get_or_create(user=user)
    return prof

@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    prof = _get_profile(request.user)
    return render(request, "portal/dashboard.html", {"prof": prof})

@login_required
def alerts(request: HttpRequest) -> HttpResponse:
    prof = _get_profile(request.user)
    status = None
    if prof.phone_e164:
        status = sms_subscription_status(settings.WEATHERGUARD_SMS_USERS, prof.phone_e164)

    if request.method == "POST" and prof.phone_e164:
        action = request.POST.get("action")
        if action == "stop":
            ok = set_sms_subscription(settings.WEATHERGUARD_SMS_USERS, prof.phone_e164, False)
            messages.success(request, "Alerty SMS wyłączone." if ok else "Nie udało się wyłączyć alertów.")
        elif action == "start":
            ok = set_sms_subscription(settings.WEATHERGUARD_SMS_USERS, prof.phone_e164, True)
            messages.success(request, "Alerty SMS włączone." if ok else "Nie udało się włączyć alertów.")
        return redirect("alerts")

    return render(request, "portal/alerts.html", {"prof": prof, "status": status})

@login_required
def data_view(request: HttpRequest) -> HttpResponse:
    prof = _get_profile(request.user)
    rows = []
    if prof.phone_e164 and prof.default_profile:
        rows = last_readings(settings.WEATHERGUARD_DB, prof.phone_e164, prof.default_profile, limit=80)
    return render(request, "portal/data.html", {"prof": prof, "rows": rows})

@login_required
def trends(request: HttpRequest) -> HttpResponse:
    prof = _get_profile(request.user)
    files = []
    if prof.phone_e164:
        files = list_trend_files(settings.WEATHERGUARD_TRENDS_DIR, prof.phone_e164, limit=30)
    items = [{"name": os.path.basename(p)} for p in files]
    return render(request, "portal/trends.html", {"prof": prof, "items": items})

@login_required
def trend_file(request: HttpRequest, fname: str) -> HttpResponse:
    prof = _get_profile(request.user)
    if not prof.phone_e164:
        raise Http404("No phone configured")
    phone_digits = prof.phone_e164.replace("+", "").replace(" ", "")
    if phone_digits not in fname:
        raise Http404("Not allowed")
    full = Path(settings.WEATHERGUARD_TRENDS_DIR) / fname
    if not full.exists():
        raise Http404("Not found")
    return FileResponse(open(full, "rb"), content_type="image/png")

@login_required
def settings_view(request: HttpRequest) -> HttpResponse:
    prof = _get_profile(request.user)
    if request.method == "POST":
        prof.display_name = request.POST.get("display_name", "").strip()
        prof.phone_e164 = request.POST.get("phone_e164", "").strip()
        prof.default_profile = request.POST.get("default_profile", "migraine").strip()
        prof.save()
        messages.success(request, "Zapisano ustawienia.")
        return redirect("settings")
    return render(request, "portal/settings.html", {"prof": prof})
