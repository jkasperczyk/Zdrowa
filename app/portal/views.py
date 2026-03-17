from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import List, Dict, Any
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, FileResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode

from datetime import date

from .models import UserProfile, DailyWellbeing
from .utils import greeting as make_greeting
from .wg_sources import (
    last_readings, sms_subscription_status, set_sms_subscription,
    list_trend_files, write_wg_user, dashboard_summary, db_stats,
    all_users_latest_scores, users_last_scores, recent_alerts_all, batch_recent_alerts,
    save_push_subscription, delete_push_subscription, process_alerts_queue,
    forecast_alerts_for_user, generate_daily_tip,
)
from .forms import AdminCreateUserForm, AdminEditUserForm, ImportUsersForm, DeleteUserForm, gen_password
from .users_import import parse_users_txt, dedupe_by_phone

def login_view(request: HttpRequest) -> HttpResponse:
    from django.utils import timezone as _tz
    from datetime import timedelta
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        # Check if account is locked
        try:
            target_user = User.objects.get(username__iexact=username)
            target_prof = UserProfile.objects.get(user=target_user)
            if target_prof.locked_until and target_prof.locked_until > _tz.now():
                remaining = max(1, int((target_prof.locked_until - _tz.now()).total_seconds() / 60) + 1)
                messages.error(request, f"Konto zablokowane na {remaining} min. z powodu zbyt wielu nieudanych prób.")
                return render(request, "portal/login.html", {})
        except (User.DoesNotExist, UserProfile.DoesNotExist):
            pass
        user = authenticate(request, username=username, password=password)
        if user is not None:
            # Reset lockout on successful login
            try:
                p = UserProfile.objects.get(user=user)
                if p.failed_login_count or p.locked_until:
                    p.failed_login_count = 0
                    p.locked_until = None
                    p.save(update_fields=["failed_login_count", "locked_until"])
            except UserProfile.DoesNotExist:
                pass
            login(request, user)
            return redirect("dashboard")
        # Failed login
        try:
            target_user = User.objects.get(username__iexact=username)
            target_prof, _ = UserProfile.objects.get_or_create(user=target_user)
            target_prof.failed_login_count = (target_prof.failed_login_count or 0) + 1
            if target_prof.failed_login_count >= 5:
                target_prof.locked_until = _tz.now() + timedelta(minutes=15)
                target_prof.save(update_fields=["failed_login_count", "locked_until"])
                messages.error(request, "Zbyt wiele nieudanych prób. Konto zablokowane na 15 minut.")
            else:
                remaining_tries = max(0, 5 - target_prof.failed_login_count)
                target_prof.save(update_fields=["failed_login_count"])
                messages.error(request, f"Nieprawidłowy login lub hasło. Pozostało prób: {remaining_tries}.")
        except (User.DoesNotExist, Exception):
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
            # clear flag; trigger first-login onboarding popup
            if not request.user.is_staff:
                was_forced = bool(prof.must_change_password)
                prof.must_change_password = False
                prof.save(update_fields=["must_change_password", "updated_at"])
                # Show onboarding popup once if this was a forced password change
                if was_forced and not prof.has_seen_onboarding:
                    request.session["show_onboarding"] = True
            messages.success(request, "Hasło zostało zmienione.")
            return redirect("dashboard")
    else:
        form = PasswordChangeForm(user=request.user)
    return render(request, "portal/password_change.html", {"form": form, "must": bool(getattr(prof, "must_change_password", False))})

@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    prof = _get_profile(request.user)

    summary: dict = {"scores": {}, "env": {}, "last_dt": None}
    today_wb = None
    forecast_data = []
    tip = None

    if prof.phone_e164:
        profiles = list(prof.enabled_alerts) if prof.enabled_alerts else ["migraine", "allergy", "heart"]
        summary = dashboard_summary(settings.WEATHERGUARD_DB, prof.phone_e164, profiles)
        forecast_data = forecast_alerts_for_user(settings.WEATHERGUARD_DB, prof.phone_e164, hours=12)
        tip = generate_daily_tip(summary.get("scores", {}), summary.get("env", {}), profiles)

    try:
        today_wb = DailyWellbeing.objects.get(user=request.user, day=date.today())
    except Exception:
        today_wb = None

    # First-login onboarding popup
    show_onboarding = False
    if request.session.pop("show_onboarding", False) and not prof.has_seen_onboarding:
        show_onboarding = True
        prof.has_seen_onboarding = True
        prof.save(update_fields=["has_seen_onboarding", "updated_at"])

    return render(request, "portal/dashboard.html", {
        "prof": prof,
        "summary": summary,
        "today_wb": today_wb,
        "greeting": make_greeting(request.user.first_name, getattr(prof, "gender", "")),
        "show_onboarding": show_onboarding,
        "forecast_data": forecast_data,
        "tip": tip,
    })

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
    from portal.models import UserProfile
    prof = _get_profile(request.user)
    genders = UserProfile.GENDER_CHOICES
    alert_types = ["migraine", "allergy", "heart"]

    if request.method == "POST":
        request.user.first_name = request.POST.get("first_name", "").strip()
        request.user.last_name  = request.POST.get("last_name",  "").strip()
        request.user.save(update_fields=["first_name", "last_name"])
        prof.phone_e164 = request.POST.get("phone_e164", "").strip()
        prof.gender = request.POST.get("gender", "unspecified").strip()
        prof.enabled_alerts = request.POST.getlist("enabled_alerts")
        prof.sms_enabled = request.POST.get("sms_enabled") == "on"
        prof.default_profile = prof.enabled_alerts[0] if prof.enabled_alerts else "migraine"

        # WeatherGuard runner config
        prof.location = request.POST.get("location", "").strip()
        raw_threshold = request.POST.get("alert_threshold", "").strip()
        prof.alert_threshold = int(raw_threshold) if raw_threshold.isdigit() else None
        prof.quiet_hours = request.POST.get("quiet_hours", "").strip()

        # Menstrual cycle (optional)
        raw_cycle = request.POST.get("cycle_length_days", "").strip()
        prof.cycle_length_days = int(raw_cycle) if raw_cycle.isdigit() else None
        raw_csd = request.POST.get("cycle_start_date", "").strip()
        from datetime import date as _date
        try:
            prof.cycle_start_date = _date.fromisoformat(raw_csd) if raw_csd else None
        except ValueError:
            prof.cycle_start_date = None

        prof.save()

        try:
            set_sms_subscription(settings.WEATHERGUARD_DB, prof.phone_e164, prof.sms_enabled)
        except Exception:
            pass
        if prof.phone_e164:
            try:
                write_wg_user(
                    settings.WEATHERGUARD_DB,
                    phone=prof.phone_e164,
                    profiles=prof.enabled_alerts or ["migraine"],
                    location=prof.location,
                    threshold=prof.alert_threshold,
                    quiet_hours=prof.quiet_hours or None,
                    enabled=prof.sms_enabled and request.user.is_active,
                )
            except Exception:
                pass

        messages.success(request, "Zapisano ustawienia.")
        return redirect("settings")

    return render(request, "portal/settings.html", {
        "prof": prof,
        "genders": genders,
        "alert_types": alert_types,
    })

def _is_staff(u) -> bool:
    return bool(u and u.is_authenticated and u.is_staff)

def _email_enabled() -> bool:
    backend = getattr(settings, "EMAIL_BACKEND", "") or ""
    return "smtp" in backend.lower()


def _send_verification_email(request: HttpRequest, user) -> None:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    verify_url = request.build_absolute_uri(
        reverse("verify_email", kwargs={"uidb64": uid, "token": token})
    )
    ctx = {"first_name": user.first_name or user.email, "verify_url": verify_url}
    subject = "Potwierdź swoje konto w Health Guard"
    text_body = render_to_string("portal/email/verification.txt", ctx)
    html_body = render_to_string("portal/email/verification.html", ctx)
    try:
        send_mail(
            subject=subject,
            message=text_body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "Health Guard <no-reply@pracunia.pl>"),
            recipient_list=[user.email],
            html_message=html_body,
            fail_silently=True,
        )
    except Exception:
        pass


def _notify_admin_user_verified(user, prof) -> None:
    admin_email = getattr(settings, "ADMIN_NOTIFY_EMAIL", "")
    if not admin_email or not _email_enabled():
        return
    from datetime import datetime as _dt
    body = (
        f"Nowy użytkownik aktywował konto w Health Guard.\n\n"
        f"Imię i nazwisko: {user.first_name} {user.last_name}\n"
        f"E-mail: {user.email}\n"
        f"Telefon: {prof.phone_e164}\n"
        f"Lokalizacja: {prof.location}\n"
        f"Data rejestracji: {_dt.now().strftime('%Y-%m-%d %H:%M')}\n"
    )
    try:
        send_mail(
            subject=f"Nowy użytkownik Health Guard: {user.first_name} {user.last_name}",
            message=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "Health Guard <no-reply@pracunia.pl>"),
            recipient_list=[admin_email],
            fail_silently=True,
        )
    except Exception:
        pass


def _notify_admin_deletion(phone: str, user_email: str = "") -> None:
    admin_email = getattr(settings, "ADMIN_NOTIFY_EMAIL", "")
    if not admin_email or not _email_enabled():
        return
    from datetime import datetime as _dt
    body = (
        f"Użytkownik usunął swoje konto z Health Guard.\n\n"
        f"Telefon: {phone}\n"
        f"E-mail: {user_email}\n"
        f"Data usunięcia: {_dt.now().strftime('%Y-%m-%d %H:%M')}\n"
    )
    try:
        send_mail(
            subject=f"Użytkownik usunął konto: {phone}",
            message=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "Health Guard <no-reply@pracunia.pl>"),
            recipient_list=[admin_email],
            fail_silently=True,
        )
    except Exception:
        pass


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
            "location": getattr(prof, "location", "") if prof else "",
            "threshold": getattr(prof, "alert_threshold", None) if prof else None,
            "last_login": u.last_login,
            "recent_alerts": [],
        })

    # Fetch last scores + recent alert history from feedback.db (batch queries)
    phones = [r["phone"] for r in users_rows if r["phone"]]
    last_scores = users_last_scores(settings.WEATHERGUARD_DB, phones) if phones else {}
    recent_hist = batch_recent_alerts(settings.WEATHERGUARD_DB, phones, days=7, limit_per_user=5) if phones else {}
    for r in users_rows:
        ls = last_scores.get(r["phone"], {})
        r["last_score"] = ls.get("score")
        r["last_score_dt"] = ls.get("dt")
        r["last_score_tier"] = ls.get("tier", "")
        r["last_score_profile"] = ls.get("profile", "")
        r["recent_alerts"] = recent_hist.get(r["phone"], [])

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "bulk_enable" or action == "bulk_disable":
            ids = [int(x) for x in request.POST.getlist("selected_users") if x.isdigit()]
            enable = (action == "bulk_enable")
            cnt = 0
            for uid in ids:
                try:
                    target = User.objects.get(id=uid, is_staff=False)
                    target.is_active = enable
                    target.save(update_fields=["is_active"])
                    cnt += 1
                    # sync to wg_users
                    tprof = getattr(target, "profile", None)
                    if tprof and tprof.phone_e164:
                        write_wg_user(
                            settings.WEATHERGUARD_DB,
                            phone=tprof.phone_e164,
                            profiles=getattr(tprof, "enabled_alerts", None) or ["migraine"],
                            location=getattr(tprof, "location", ""),
                            threshold=getattr(tprof, "alert_threshold", None),
                            quiet_hours=getattr(tprof, "quiet_hours", None) or None,
                            enabled=enable,
                        )
                except Exception:
                    pass
            messages.success(request, f"{'Odblokowano' if enable else 'Zablokowano'}: {cnt} użytkowników.")
            return redirect("admin_tools")

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
                        write_wg_user(
                            settings.WEATHERGUARD_DB,
                            phone=prof.phone_e164,
                            profiles=getattr(prof, "enabled_alerts", None) or ["migraine"],
                            location=getattr(prof, "location", ""),
                            threshold=getattr(prof, "alert_threshold", None),
                            quiet_hours=getattr(prof, "quiet_hours", None) or None,
                            enabled=False,
                        )
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
                prof.location = cd.get("location") or ""
                prof.alert_threshold = cd.get("alert_threshold")
                prof.quiet_hours = cd.get("quiet_hours") or ""
                prof.must_change_password = True
                prof.save()

                set_sms_subscription(settings.WEATHERGUARD_DB, prof.phone_e164, prof.sms_enabled)
                write_wg_user(
                    settings.WEATHERGUARD_DB,
                    phone=prof.phone_e164,
                    profiles=prof.enabled_alerts or ["migraine"],
                    location=prof.location,
                    threshold=prof.alert_threshold,
                    quiet_hours=prof.quiet_hours or None,
                    enabled=prof.sms_enabled,
                )
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

                    write_wg_user(
                        settings.WEATHERGUARD_DB,
                        phone=prof.phone_e164,
                        profiles=prof.enabled_alerts or ["migraine"],
                        location=getattr(prof, "location", ""),
                        threshold=getattr(prof, "alert_threshold", None),
                        quiet_hours=getattr(prof, "quiet_hours", None) or None,
                        enabled=bool(getattr(prof, "sms_enabled", True)),
                    )

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
            prof.location = cd.get("location") or ""
            prof.alert_threshold = cd.get("alert_threshold")
            prof.quiet_hours = cd.get("quiet_hours") or ""
            prof.save()

            try:
                set_sms_subscription(settings.WEATHERGUARD_DB, prof.phone_e164, prof.sms_enabled)
            except Exception:
                pass
            write_wg_user(
                settings.WEATHERGUARD_DB,
                phone=prof.phone_e164,
                profiles=prof.enabled_alerts or ["migraine"],
                location=prof.location,
                threshold=prof.alert_threshold,
                quiet_hours=prof.quiet_hours or None,
                enabled=prof.sms_enabled and u.is_active,
            )

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
            "location": prof.location,
            "alert_threshold": prof.alert_threshold,
            "quiet_hours": prof.quiet_hours,
        })

    return render(request, "portal/admin_user_edit.html", {"target": u, "form": form})


def _read_log_tail(log_path: str, lines: int = 80) -> str:
    """Read last N lines of the weatherguard log file."""
    try:
        if not os.path.exists(log_path):
            return f"(brak pliku logu: {log_path})"
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(size, 32768)
            f.seek(max(0, size - chunk))
            data = f.read().decode("utf-8", errors="replace")
        return "\n".join(data.splitlines()[-lines:])
    except Exception as e:
        return f"(błąd odczytu logu: {e})"


def _systemd_status() -> Dict[str, Any]:
    """Read systemd timer and service status."""
    result: Dict[str, Any] = {"available": False, "timer": {}, "service": {}, "error": ""}
    props_wanted = (
        "ActiveState,SubState,ActiveEnterTimestamp,InactiveEnterTimestamp,"
        "NextElapseUSecRealtime,LastTriggerUSec,ExecMainStatus,MainPID"
    )
    try:
        for key, unit in [("timer", "weatherguard.timer"), ("service", "weatherguard.service")]:
            r = subprocess.run(
                ["systemctl", "show", unit, "--no-pager", f"--property={props_wanted}"],
                capture_output=True, text=True, timeout=5
            )
            props: Dict[str, str] = {}
            for line in r.stdout.splitlines():
                if "=" in line:
                    k, _, v = line.partition("=")
                    props[k.strip()] = v.strip()
            result[key] = props
        result["available"] = True
    except FileNotFoundError:
        result["error"] = "systemctl nie jest dostępny w tym środowisku."
    except Exception as e:
        result["error"] = str(e)
    return result


@user_passes_test(_is_staff)
def admin_system(request: HttpRequest) -> HttpResponse:
    run_output = None
    run_error = None

    if request.method == "POST":
        action = request.POST.get("action")
        wg_base = getattr(settings, "WEATHERGUARD_BASE_DIR", "/opt/weatherguard")
        wg_python = f"{wg_base}/venv/bin/python"
        wg_env = f"{wg_base}/config/.env"

        if action == "run_dry":
            try:
                r = subprocess.run(
                    [wg_python, "-m", "app.runner", "--dry-run", "--env", wg_env],
                    capture_output=True, text=True, timeout=90, cwd=wg_base,
                )
                run_output = (r.stdout + "\n" + r.stderr).strip()
                if r.returncode != 0:
                    run_error = f"Exit code: {r.returncode}"
            except subprocess.TimeoutExpired:
                run_error = "Timeout (90s) — runner działa zbyt długo."
            except Exception as e:
                run_error = str(e)

        elif action == "run_real":
            try:
                r = subprocess.run(
                    ["sudo", "-n", "systemctl", "start", "weatherguard.service"],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0:
                    run_output = "✓ Zlecono: systemctl start weatherguard.service"
                else:
                    run_error = (r.stderr or r.stdout or f"Exit code: {r.returncode}").strip()
            except Exception as e:
                run_error = str(e)

        messages.success(request, "Dry-run zakończony.") if (run_output and not run_error) else None
        if run_error:
            messages.error(request, f"Błąd: {run_error}")

    db_path = settings.WEATHERGUARD_DB
    log_path = getattr(settings, "WEATHERGUARD_LOG", "/opt/weatherguard/logs/weatherguard.log")

    return render(request, "portal/admin_system.html", {
        "systemd": _systemd_status(),
        "stats": db_stats(db_path),
        "all_scores": all_users_latest_scores(db_path),
        "recent_alerts": recent_alerts_all(db_path, hours=24, limit=100),
        "log_tail": _read_log_tail(log_path, lines=80),
        "run_output": run_output,
        "run_error": run_error,
        "db_path": db_path,
        "log_path": log_path,
    })


# ── PWA views ────────────────────────────────────────────────────────────────

def pwa_manifest(request: HttpRequest) -> HttpResponse:
    base = request.build_absolute_uri('/').rstrip('/')
    icons_url = f"{base}/static/portal/icons"
    manifest = {
        "name": "Zdrowa",
        "short_name": "Zdrowa",
        "description": "Panel zdrowia – alerty pogodowe i samopoczucie",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#080f1d",
        "theme_color": "#080f1d",
        "orientation": "portrait-primary",
        "icons": [
            {"src": f"{icons_url}/icon-192.png?v=4", "sizes": "192x192", "type": "image/png"},
            {"src": f"{icons_url}/icon-512.png?v=4", "sizes": "512x512", "type": "image/png"},
            {"src": f"{icons_url}/icon-512.png?v=4", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    return HttpResponse(
        json.dumps(manifest, ensure_ascii=False),
        content_type="application/manifest+json",
    )


def pwa_sw(request: HttpRequest) -> HttpResponse:
    sw_js = r"""
const CACHE = 'zdrowa-v3';
const SHELL = [
  '/',
  '/static/portal/icons/icon-192.png',
  '/offline/',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request).then(resp => {
        const clone = resp.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return resp;
      }))
    );
    return;
  }
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request).catch(() =>
        caches.match('/offline/').then(r => r || new Response('Offline', {status: 503}))
      )
    );
    return;
  }
});

/* ── Push notification handler ── */
self.addEventListener('push', e => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch(err) {}
  const title = data.title || 'Zdrowa — Alert zdrowotny';
  const options = {
    body: data.body || 'Sprawdź panel zdrowia.',
    icon: '/static/portal/icons/icon-192.png',
    badge: '/static/portal/icons/icon-192.png',
    data: { url: data.url || '/' },
    vibrate: [200, 100, 200],
    requireInteraction: true,
    tag: 'zdrowa-alert',
    renotify: true,
  };
  e.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data || {}).url || '/';
  e.waitUntil(
    clients.matchAll({type: 'window', includeUncontrolled: true}).then(cls => {
      for (const c of cls) {
        if (c.url.includes(location.origin) && 'focus' in c) {
          c.navigate(url);
          return c.focus();
        }
      }
      return clients.openWindow(url);
    })
  );
});
""".strip()
    resp = HttpResponse(sw_js, content_type="application/javascript; charset=utf-8")
    resp['Service-Worker-Allowed'] = '/'
    resp['Cache-Control'] = 'no-cache'
    return resp


@login_required
def pwa_offline(request: HttpRequest) -> HttpResponse:
    return render(request, "portal/offline.html", {})


@login_required
def push_subscribe(request: HttpRequest) -> HttpResponse:
    """Store or remove a Web Push subscription for the logged-in user."""
    import json as _json
    from django.http import JsonResponse
    prof = _get_profile(request.user)
    if not prof.phone_e164:
        return JsonResponse({"ok": False, "error": "no_phone"}, status=400)
    if request.method == "POST":
        try:
            body = _json.loads(request.body)
        except Exception:
            return JsonResponse({"ok": False, "error": "bad_json"}, status=400)
        action = body.get("action", "subscribe")
        endpoint = body.get("endpoint", "")
        if not endpoint:
            return JsonResponse({"ok": False, "error": "no_endpoint"}, status=400)
        if action == "unsubscribe":
            delete_push_subscription(settings.WEATHERGUARD_DB, prof.phone_e164, endpoint)
        else:
            keys = body.get("keys") or {}
            p256dh = keys.get("p256dh", "")
            auth = keys.get("auth", "")
            if not p256dh or not auth:
                return JsonResponse({"ok": False, "error": "missing_keys"}, status=400)
            save_push_subscription(settings.WEATHERGUARD_DB, prof.phone_e164, endpoint, p256dh, auth)
        return JsonResponse({"ok": True})
    return JsonResponse({"ok": False, "error": "method"}, status=405)


@user_passes_test(_is_staff)
def process_push_queue(request: HttpRequest) -> HttpResponse:
    """Staff-only: process the alerts_queue and send Web Push notifications."""
    sent = 0
    error = None
    if request.method == "POST":
        try:
            sent = process_alerts_queue(
                settings.WEATHERGUARD_DB,
                getattr(settings, "VAPID_PRIVATE_KEY", ""),
                getattr(settings, "VAPID_PUBLIC_KEY", ""),
                getattr(settings, "VAPID_SUBJECT", "mailto:admin@zdrowa.pracunia.pl"),
            )
            messages.success(request, f"Wysłano {sent} powiadomień push.")
        except Exception as e:
            error = str(e)
            messages.error(request, f"Błąd: {error}")
        return redirect("admin_system")
    return HttpResponse(f"Wyślij POST aby przetworzyć kolejkę. Przykład: curl -X POST {request.build_absolute_uri()}", content_type="text/plain")


@login_required
def account_export_view(request: HttpRequest) -> HttpResponse:
    """Generate and serve a ZIP archive of all user data."""
    import zipfile, io, csv as _csv, json as _json, sqlite3 as _sq3
    prof = _get_profile(request.user)
    phone = prof.phone_e164

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        # profile.json
        profile_data = {
            "username": request.user.username,
            "email": request.user.email,
            "first_name": request.user.first_name,
            "last_name": request.user.last_name,
            "phone_e164": phone,
            "gender": prof.gender,
            "location": prof.location,
            "alert_threshold": prof.alert_threshold,
            "quiet_hours": prof.quiet_hours,
            "enabled_alerts": prof.enabled_alerts,
            "created_at": prof.created_at.isoformat() if prof.created_at else "",
        }
        zf.writestr("profile.json", _json.dumps(profile_data, ensure_ascii=False, indent=2))

        # wellbeing.csv from Django DB
        wb_buf = io.StringIO()
        wb_w = _csv.writer(wb_buf)
        wb_w.writerow(["day","stress_1_10","exercise_1_10","sleep_quality_1_10","hydration_1_10","headache_1_10"])
        for row in DailyWellbeing.objects.filter(user=request.user).order_by("day"):
            wb_w.writerow([row.day, row.stress_1_10, row.exercise_1_10,
                           row.sleep_quality_1_10, row.hydration_1_10, row.headache_1_10])
        zf.writestr("wellbeing.csv", wb_buf.getvalue())

        # Data from feedback.db
        if phone and os.path.exists(settings.WEATHERGUARD_DB):
            try:
                conn = _sq3.connect(settings.WEATHERGUARD_DB)
                conn.row_factory = _sq3.Row
                for tbl, fname_csv in [
                    ("readings", "readings.csv"), ("alerts", "alerts.csv"),
                    ("symptom_log", "symptoms.csv"), ("weekly_reports", "reports.csv"),
                ]:
                    try:
                        rows = conn.execute(f"SELECT * FROM {tbl} WHERE phone=? ORDER BY rowid", (phone,)).fetchall()
                        if rows:
                            f_buf = io.StringIO()
                            w = _csv.writer(f_buf)
                            w.writerow(list(rows[0].keys()))
                            for r in rows:
                                w.writerow(list(r))
                            zf.writestr(fname_csv, f_buf.getvalue())
                    except Exception:
                        pass
                conn.close()
            except Exception:
                pass

    buf.seek(0)
    phone_clean = (phone or "user").replace("+", "").replace(" ", "")
    from datetime import date as _d
    out_fname = f"zdrowa_export_{phone_clean}_{_d.today()}.zip"
    resp = HttpResponse(buf.read(), content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="{out_fname}"'
    return resp


@login_required
def account_delete_view(request: HttpRequest) -> HttpResponse:
    """Self-service account deletion with password confirmation."""
    if request.user.is_staff or request.user.is_superuser:
        messages.error(request, "Konta administracyjne nie mogą być usunięte z tego poziomu.")
        return redirect("settings")
    if request.method != "POST":
        return redirect("settings")
    password = request.POST.get("delete_password", "")
    if not request.user.check_password(password):
        messages.error(request, "Nieprawidłowe hasło — konto nie zostało usunięte.")
        return redirect("settings")
    prof = _get_profile(request.user)
    phone = prof.phone_e164
    user_email = request.user.email
    from .wg_sources import delete_all_user_data
    try:
        delete_all_user_data(settings.WEATHERGUARD_DB, phone)
    except Exception:
        pass
    _notify_admin_deletion(phone, user_email)
    request.user.delete()
    logout(request)
    return redirect("account_deleted")


def account_deleted_view(request: HttpRequest) -> HttpResponse:
    return render(request, "portal/account_deleted.html", {})


def register_view(request: HttpRequest) -> HttpResponse:
    registration_open = getattr(settings, "REGISTRATION_OPEN", False)
    if not registration_open:
        return render(request, "portal/register.html", {"registration_open": False})
    if request.user.is_authenticated:
        return redirect("dashboard")

    errors: list = []
    form_data: dict = {}

    if request.method == "POST":
        form_data = request.POST
        first_name = request.POST.get("first_name", "").strip()
        last_name  = request.POST.get("last_name",  "").strip()
        email      = request.POST.get("email",      "").strip().lower()
        phone      = request.POST.get("phone_e164", "").strip()
        gender     = request.POST.get("gender",     "unspecified").strip()
        password   = request.POST.get("password",   "")
        password2  = request.POST.get("password2",  "")
        location   = request.POST.get("location",   "").strip()

        if not all([first_name, last_name, email, phone, password, location]):
            errors.append("Wypełnij wszystkie wymagane pola.")
        if password and password2 and password != password2:
            errors.append("Hasła nie są identyczne.")
        if password and len(password) < 8:
            errors.append("Hasło musi mieć co najmniej 8 znaków.")
        if password and not any(c.isdigit() for c in password):
            errors.append("Hasło musi zawierać co najmniej jedną cyfrę.")
        if email and User.objects.filter(username=email).exists():
            errors.append("Konto z tym adresem e-mail już istnieje.")
        if phone and UserProfile.objects.filter(phone_e164=phone).exists():
            errors.append("Konto z tym numerem telefonu już istnieje.")

        if not errors:
            user = User.objects.create_user(
                username=email,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                is_active=False,
            )
            prof = UserProfile(user=user)
            prof.phone_e164 = phone
            prof.gender = gender
            prof.location = location
            prof.enabled_alerts = ["migraine", "allergy", "heart"]
            prof.default_profile = "migraine"
            prof.save()
            _send_verification_email(request, user)
            return render(request, "portal/register_pending.html", {"email": email})

    return render(request, "portal/register.html", {
        "registration_open": True,
        "errors": errors,
        "form_data": form_data,
    })


def verify_email_view(request: HttpRequest, uidb64: str, token: str) -> HttpResponse:
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        if not user.is_active:
            user.is_active = True
            user.save(update_fields=["is_active"])
            prof = _get_profile(user)
            if prof.phone_e164:
                try:
                    write_wg_user(
                        settings.WEATHERGUARD_DB,
                        phone=prof.phone_e164,
                        profiles=prof.enabled_alerts or ["migraine"],
                        location=prof.location,
                        threshold=prof.alert_threshold,
                        quiet_hours=prof.quiet_hours or None,
                        enabled=True,
                    )
                except Exception:
                    pass
            _notify_admin_user_verified(user, prof)
        messages.success(request, "Konto aktywowane! Możesz się zalogować.")
        return redirect("login")
    return render(request, "portal/verify_email_invalid.html", {"uidb64": uidb64})


def resend_verification_view(request: HttpRequest) -> HttpResponse:
    sent = False
    error = None
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        try:
            user = User.objects.get(username=email, is_active=False)
            _send_verification_email(request, user)
            sent = True
        except User.DoesNotExist:
            error = "Nie znaleziono konta oczekującego na weryfikację dla tego adresu e-mail."
    return render(request, "portal/resend_verification.html", {"sent": sent, "error": error})


def landing_view(request: HttpRequest) -> HttpResponse:
    """Public landing page — redirects authenticated users to dashboard."""
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "portal/landing.html", {})


@login_required
def api_alerts_preview(request: HttpRequest) -> HttpResponse:
    from django.http import JsonResponse
    from .wg_sources import get_recent_alerts_for_user, mark_alerts_read
    prof = _get_profile(request.user)
    if not prof.phone_e164:
        return JsonResponse({"alerts": [], "count": 0})
    # Mark as read
    try:
        mark_alerts_read(settings.WEATHERGUARD_DB, prof.phone_e164)
    except Exception:
        pass
    alerts = get_recent_alerts_for_user(settings.WEATHERGUARD_DB, prof.phone_e164, limit=5)
    return JsonResponse({"alerts": alerts, "count": len(alerts)})
