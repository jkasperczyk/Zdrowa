from __future__ import annotations

import os
from pathlib import Path
from typing import List, Dict, Any
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse, FileResponse, Http404
from django.shortcuts import render, redirect
from django.utils import timezone

from .models import UserProfile
from .wg_sources import last_readings, sms_subscription_status, set_sms_subscription, list_trend_files
from .forms import AdminCreateUserForm, ImportUsersForm, gen_password
from .users_import import parse_users_txt

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
            if ok:
                prof.sms_enabled = False
                prof.save(update_fields=["sms_enabled", "updated_at"])
            messages.success(request, "Alerty SMS wyłączone." if ok else "Nie udało się wyłączyć alertów.")
        elif action == "start":
            ok = set_sms_subscription(settings.WEATHERGUARD_SMS_USERS, prof.phone_e164, True)
            if ok:
                prof.sms_enabled = True
                prof.save(update_fields=["sms_enabled", "updated_at"])
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

def _is_staff(u) -> bool:
    return bool(u and u.is_authenticated and u.is_staff)

@user_passes_test(_is_staff)
def admin_tools(request: HttpRequest) -> HttpResponse:
    create_form = AdminCreateUserForm()
    import_form = ImportUsersForm()

    created: List[Dict[str, Any]] = []
    imported: List[Dict[str, Any]] = []

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            create_form = AdminCreateUserForm(request.POST)
            if create_form.is_valid():
                cd = create_form.cleaned_data
                email = cd["email"].lower().strip()
                username = email  # login = email
                phone = cd["phone_e164"]

                if User.objects.filter(username=username).exists():
                    messages.error(request, f"Użytkownik {username} już istnieje.")
                else:
                    pwd = gen_password()
                    u = User.objects.create_user(
                        username=username,
                        email=email,
                        password=pwd,
                        first_name=cd["first_name"].strip(),
                        last_name=cd["last_name"].strip(),
                        is_active=True,
                    )
                    prof = _get_profile(u)
                    prof.phone_e164 = phone
                    prof.gender = cd.get("gender") or "unspecified"
                    prof.enabled_alerts = cd.get("enabled_alerts") or []
                    prof.sms_enabled = bool(cd.get("sms_enabled"))
                    # default_profile = first enabled alert or migraine
                    prof.default_profile = (prof.enabled_alerts[0] if prof.enabled_alerts else "migraine")
                    prof.cycle_length_days = cd.get("cycle_length_days")
                    prof.cycle_start_date = cd.get("cycle_start_date")
                    prof.save()

                    # sync subscription to WeatherGuard (best-effort)
                    set_sms_subscription(settings.WEATHERGUARD_SMS_USERS, phone, prof.sms_enabled)

                    created.append({"username": username, "password": pwd, "phone": phone})
                    messages.success(request, f"Utworzono użytkownika: {username}")

        elif action == "import":
            import_form = ImportUsersForm(request.POST)
            if import_form.is_valid():
                src = "/opt/weatherguard/config/users.txt"
                try:
                    with open(src, "r", encoding="utf-8") as f:
                        parsed = parse_users_txt(f.read())
                except Exception as e:
                    messages.error(request, f"Nie mogę czytać {src}: {e}")
                    parsed = []

                for pu in parsed:
                    email = (pu.email or "").strip().lower() or None
                    username = email or pu.phone_e164.replace("+", "")
                    if User.objects.filter(username=username).exists():
                        u = User.objects.get(username=username)
                        was_new = False
                        pwd = None
                    else:
                        pwd = gen_password()
                        u = User.objects.create_user(
                            username=username,
                            email=email or "",
                            password=pwd,
                            first_name=pu.first_name,
                            last_name=pu.last_name,
                            is_active=True,
                        )
                        was_new = True

                    prof = _get_profile(u)
                    prof.phone_e164 = pu.phone_e164
                    prof.enabled_alerts = pu.enabled_alerts
                    prof.default_profile = (pu.enabled_alerts[0] if pu.enabled_alerts else "migraine")
                    # keep existing gender/cycle if already set
                    prof.save()

                    # if profile has sms_enabled set, sync; otherwise don't change global file
                    try:
                        set_sms_subscription(settings.WEATHERGUARD_SMS_USERS, pu.phone_e164, bool(prof.sms_enabled))
                    except Exception:
                        pass

                    imported.append({"username": username, "phone": pu.phone_e164, "new": was_new, "password": pwd})
                messages.success(request, f"Import zakończony: {len(imported)} wpisów (z pliku users.txt).")

    return render(request, "portal/admin_tools.html", {
        "create_form": create_form,
        "import_form": import_form,
        "created": created,
        "imported": imported,
    })
