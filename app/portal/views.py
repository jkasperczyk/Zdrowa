from __future__ import annotations

import os
from pathlib import Path
from typing import List, Dict, Any
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, FileResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404

from .models import UserProfile
from .wg_sources import last_readings, sms_subscription_status, set_sms_subscription, list_trend_files
from .forms import AdminCreateUserForm, AdminEditUserForm, ImportUsersForm, DeleteUserForm, gen_password
from .users_import import parse_users_txt, dedupe_by_phone

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
def password_change_view(request: HttpRequest) -> HttpResponse:
    prof = _get_profile(request.user)
    if request.method == "POST":
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            # clear flag
            if not request.user.is_staff:
                prof.must_change_password = False
                prof.save(update_fields=["must_change_password", "updated_at"])
            messages.success(request, "Hasło zostało zmienione.")
            return redirect("dashboard")
    else:
        form = PasswordChangeForm(user=request.user)
    return render(request, "portal/password_change.html", {"form": form, "must": bool(getattr(prof, "must_change_password", False))})

@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    prof = _get_profile(request.user)
    return render(request, "portal/dashboard.html", {"prof": prof})

@login_required
def alerts(request: HttpRequest) -> HttpResponse:
    prof = _get_profile(request.user)
    status = None
    if prof.phone_e164:
        status = sms_subscription_status(settings.WEATHERGUARD_DB, prof.phone_e164)

    if request.method == "POST" and prof.phone_e164:
        action = request.POST.get("action")
        if action == "stop":
            ok = set_sms_subscription(settings.WEATHERGUARD_DB, prof.phone_e164, False)
            if ok:
                prof.sms_enabled = False
                prof.save(update_fields=["sms_enabled", "updated_at"])
            messages.success(request, "Alerty SMS wyłączone." if ok else "Nie udało się wyłączyć alertów.")
        elif action == "start":
            ok = set_sms_subscription(settings.WEATHERGUARD_DB, prof.phone_e164, True)
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

def _email_enabled() -> bool:
    backend = getattr(settings, "EMAIL_BACKEND", "") or ""
    return "smtp" in backend.lower()

@user_passes_test(_is_staff)
def admin_tools(request: HttpRequest) -> HttpResponse:
    create_form = AdminCreateUserForm()
    import_form = ImportUsersForm()
    delete_form = DeleteUserForm()

    q = (request.GET.get("q") or "").strip()
    qs = User.objects.filter(is_staff=False).select_related("profile").order_by("username")
    if q:
        qs = qs.filter(
            Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
            | Q(profile__phone_e164__icontains=q)
        )

    users_rows = []
    for u in qs:
        prof = getattr(u, "profile", None)
        users_rows.append({
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "name": (u.first_name + " " + u.last_name).strip(),
            "active": u.is_active,
            "phone": getattr(prof, "phone_e164", ""),
            "alerts": getattr(prof, "enabled_alerts", []) if prof else [],
            "sms_enabled": bool(getattr(prof, "sms_enabled", True)) if prof else True,
        })

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "reset_password":
            uid = int(request.POST.get("user_id") or "0")
            target = get_object_or_404(User, id=uid)
            prof = _get_profile(target)
            tmp_pwd = gen_password(16)
            target.set_password(tmp_pwd)
            target.save()
            prof.must_change_password = True
            prof.save(update_fields=["must_change_password", "updated_at"])
            # optional email
            sent = False
            if _email_enabled() and target.email:
                try:
                    send_mail(
                        subject="Zdrowa.Pracunia – reset hasła",
                        message=f"Ustawiono hasło tymczasowe: {tmp_pwd}\nPo zalogowaniu system poprosi o zmianę hasła.",
                        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None) or "no-reply@zdrowa.pracunia.pl",
                        recipient_list=[target.email],
                        fail_silently=True,
                    )
                    sent = True
                except Exception:
                    sent = False
            messages.success(request, f"RESET hasła OK dla {target.username}. Hasło tymczasowe: {tmp_pwd}" + (" (wysłano email)" if sent else ""))
            return redirect("admin_tools")

        if action == "toggle_active":
            uid = int(request.POST.get("user_id") or "0")
            target = get_object_or_404(User, id=uid)
            target.is_active = not target.is_active
            target.save(update_fields=["is_active"])
            messages.success(request, f"{'Odblokowano' if target.is_active else 'Zablokowano'}: {target.username}")
            return redirect("admin_tools")

        if action == "delete":
            uid = int(request.POST.get("user_id") or "0")
            target = get_object_or_404(User, id=uid)
            df = DeleteUserForm(request.POST)
            if df.is_valid():
                uname = target.username
                try:
                    prof = getattr(target, "profile", None)
                    if prof and prof.phone_e164:
                        set_sms_subscription(settings.WEATHERGUARD_DB, prof.phone_e164, False)
                except Exception:
                    pass
                target.delete()
                messages.success(request, f"Usunięto: {uname}")
                return redirect("admin_tools")
            messages.error(request, "Potwierdź usunięcie checkboxem.")
            return redirect("admin_tools")

        if action == "create":
            create_form = AdminCreateUserForm(request.POST)
            if create_form.is_valid():
                cd = create_form.cleaned_data
                email = cd["email"].lower().strip()
                username = email
                if User.objects.filter(username=username).exists():
                    messages.error(request, f"Użytkownik {username} już istnieje.")
                    return redirect("admin_tools")

                pwd = gen_password(16)
                u = User.objects.create_user(
                    username=username,
                    email=email,
                    password=pwd,
                    first_name=cd["first_name"].strip(),
                    last_name=cd["last_name"].strip(),
                    is_active=True,
                )
                prof = _get_profile(u)
                prof.phone_e164 = cd["phone_e164"]
                prof.enabled_alerts = cd.get("enabled_alerts") or []
                prof.sms_enabled = bool(cd.get("sms_enabled"))
                prof.default_profile = (prof.enabled_alerts[0] if prof.enabled_alerts else "migraine")
                prof.gender = cd.get("gender") or "unspecified"
                prof.cycle_length_days = cd.get("cycle_length_days")
                prof.cycle_start_date = cd.get("cycle_start_date")
                prof.must_change_password = True
                prof.save()

                set_sms_subscription(settings.WEATHERGUARD_DB, prof.phone_e164, prof.sms_enabled)
                messages.success(request, f"Utworzono użytkownika: {username}. Hasło tymczasowe: {pwd} (wymagana zmiana przy logowaniu)")
                return redirect("admin_tools")

        if action == "import":
            import_form = ImportUsersForm(request.POST)
            if import_form.is_valid():
                src = "/opt/weatherguard/config/users.txt"
                try:
                    with open(src, "r", encoding="utf-8") as f:
                        parsed = dedupe_by_phone(parse_users_txt(f.read()))
                except Exception as e:
                    messages.error(request, f"Nie mogę czytać {src}: {e}")
                    return redirect("admin_tools")

                created_cnt = 0
                updated_cnt = 0
                for pu in parsed:
                    email = (pu.email or "").strip().lower() or None
                    username = email or pu.phone_e164.replace("+", "")
                    if User.objects.filter(username=username).exists():
                        u = User.objects.get(username=username)
                        updated_cnt += 1
                    else:
                        tmp_pwd = gen_password(16)
                        u = User.objects.create_user(
                            username=username,
                            email=email or "",
                            password=tmp_pwd,
                            first_name=pu.first_name,
                            last_name=pu.last_name,
                            is_active=True,
                        )
                        prof = _get_profile(u)
                        prof.must_change_password = True
                        prof.save(update_fields=["must_change_password", "updated_at"])
                        created_cnt += 1

                    prof = _get_profile(u)
                    prof.phone_e164 = pu.phone_e164
                    current = set(prof.enabled_alerts or [])
                    merged = sorted(current.union(set(pu.enabled_alerts or [])))
                    prof.enabled_alerts = merged
                    prof.default_profile = (merged[0] if merged else "migraine")
                    prof.save()

                messages.success(request, f"Import OK: utworzone={created_cnt}, zaktualizowane={updated_cnt} (dedupe po telefonie).")
                return redirect("admin_tools")

        return redirect("admin_tools")

    return render(request, "portal/admin_tools.html", {
        "create_form": create_form,
        "import_form": import_form,
        "delete_form": delete_form,
        "users_rows": users_rows,
        "q": q,
    })

@user_passes_test(_is_staff)
def admin_user_edit(request: HttpRequest, user_id: int) -> HttpResponse:
    u = get_object_or_404(User, id=user_id)
    prof = _get_profile(u)

    if request.method == "POST":
        form = AdminEditUserForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            u.first_name = cd["first_name"].strip()
            u.last_name = cd["last_name"].strip()
            u.email = (cd.get("email") or "").strip()
            u.is_active = bool(cd.get("is_active"))
            u.save()

            prof.phone_e164 = cd["phone_e164"]
            prof.enabled_alerts = cd.get("enabled_alerts") or []
            prof.sms_enabled = bool(cd.get("sms_enabled"))
            prof.default_profile = (prof.enabled_alerts[0] if prof.enabled_alerts else "migraine")
            prof.gender = cd.get("gender") or "unspecified"
            prof.cycle_length_days = cd.get("cycle_length_days")
            prof.cycle_start_date = cd.get("cycle_start_date")
            prof.save()

            try:
                set_sms_subscription(settings.WEATHERGUARD_DB, prof.phone_e164, prof.sms_enabled)
            except Exception:
                pass

            messages.success(request, f"Zapisano: {u.username}")
            return redirect("admin_tools")
    else:
        form = AdminEditUserForm(initial={
            "first_name": u.first_name,
            "last_name": u.last_name,
            "email": u.email,
            "phone_e164": prof.phone_e164,
            "is_active": u.is_active,
            "gender": prof.gender,
            "enabled_alerts": prof.enabled_alerts or [],
            "sms_enabled": prof.sms_enabled,
            "cycle_length_days": prof.cycle_length_days,
            "cycle_start_date": prof.cycle_start_date,
        })

    return render(request, "portal/admin_user_edit.html", {"target": u, "form": form})
