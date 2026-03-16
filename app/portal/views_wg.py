from __future__ import annotations

from datetime import date
from typing import List, Dict, Any, Optional

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render, redirect

from .models import UserProfile, DailyWellbeing
from .wg_sources import (
    sms_subscription_status,
    set_sms_subscription,
    readings_last_days,
    alerts_last_days,
    available_profiles,
    write_wellbeing,
)

def _get_profile(user) -> UserProfile:
    prof, _ = UserProfile.objects.get_or_create(user=user)
    # harden against old rows / NULLs -> avoids template 500
    if getattr(prof, "enabled_alerts", None) is None:
        prof.enabled_alerts = []
        prof.save(update_fields=["enabled_alerts"])
    if not getattr(prof, "gender", None):
        prof.gender = "unspecified"
        prof.save(update_fields=["gender"])
    return prof

@login_required
def data_view(request: HttpRequest) -> HttpResponse:
    prof = _get_profile(request.user)
    if not prof.phone_e164:
        return render(request, "portal/data.html", {"prof": prof, "rows": [], "profiles": [], "selected_profile": ""})

    profiles = available_profiles(settings.WEATHERGUARD_DB, prof.phone_e164, days=30) or ["migraine", "allergy", "heart"]
    selected_profile = (request.GET.get("profile") or "").strip() or prof.default_profile or (profiles[0] if profiles else "migraine")
    if selected_profile not in profiles:
        profiles = [selected_profile] + profiles

    rows = readings_last_days(settings.WEATHERGUARD_DB, prof.phone_e164, selected_profile, days=7, limit=1500)

    kset = set()
    for r in rows:
        kset.update(r.keys())

    return render(request, "portal/data.html", {
        "prof": prof,
        "profiles": profiles,
        "selected_profile": selected_profile,
        "rows": rows,
        "has_value": "value" in kset,
        "has_risk": "risk" in kset,
        "has_threshold": "threshold" in kset,
        "has_details": "details" in kset,
    })

@login_required
def alerts(request: HttpRequest) -> HttpResponse:
    prof = _get_profile(request.user)
    status = None
    selected_profile = (request.GET.get("profile") or "").strip() or None

    profiles: List[str] = []
    events: List[Dict[str, Any]] = []
    has_profile = False
    has_threshold = False
    has_details = False

    if prof.phone_e164:
        status = sms_subscription_status(settings.WEATHERGUARD_DB, prof.phone_e164)
        profiles = available_profiles(settings.WEATHERGUARD_DB, prof.phone_e164, days=30) or ["migraine", "allergy", "heart"]
        events = alerts_last_days(settings.WEATHERGUARD_DB, prof.phone_e164, profile=selected_profile, days=7, limit=500)

        if events:
            kset = set()
            for e in events:
                kset.update(e.keys())
            has_profile = "profile" in kset
            has_threshold = "threshold" in kset
            has_details = ("details" in kset) or ("message" in kset)

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

    return render(request, "portal/alerts.html", {
        "prof": prof,
        "status": status,
        "profiles": profiles,
        "selected_profile": selected_profile,
        "events": events,
        "has_profile": has_profile,
        "has_threshold": has_threshold,
        "has_details": has_details,
    })

@login_required
def settings_view(request: HttpRequest) -> HttpResponse:
    prof = _get_profile(request.user)
    genders = [("unspecified","Nie podano"), ("female","Kobieta"), ("male","Mężczyzna"), ("other","Inne")]
    alert_types = ["migraine","allergy","heart"]

    if request.method == "POST":
        prof.display_name = (request.POST.get("display_name") or "").strip()
        prof.phone_e164 = (request.POST.get("phone_e164") or "").strip()
        prof.gender = (request.POST.get("gender") or "unspecified").strip()

        enabled = request.POST.getlist("enabled_alerts") or []
        prof.enabled_alerts = [a for a in enabled if a in alert_types]
        prof.default_profile = (prof.enabled_alerts[0] if prof.enabled_alerts else (prof.default_profile or "migraine"))

        if prof.gender == "female":
            cl = (request.POST.get("cycle_length_days") or "").strip()
            prof.cycle_length_days = int(cl) if cl.isdigit() else None
            cs = (request.POST.get("cycle_start_date") or "").strip()
            try:
                prof.cycle_start_date = date.fromisoformat(cs) if cs else None
            except Exception:
                prof.cycle_start_date = None
        else:
            prof.cycle_length_days = None
            prof.cycle_start_date = None

        prof.save()
        messages.success(request, "Zapisano ustawienia.")
        return redirect("settings")

    return render(request, "portal/settings.html", {"prof": prof, "genders": genders, "alert_types": alert_types})


@login_required
def wellbeing_view(request: HttpRequest) -> HttpResponse:
    prof = _get_profile(request.user)
    today = date.today()

    # Load existing entry for today if present
    entry, _ = DailyWellbeing.objects.get_or_create(user=request.user, day=today)

    if request.method == "POST":
        def _int_or_none(key: str) -> Optional[int]:
            v = (request.POST.get(key) or "").strip()
            try:
                n = int(v)
                return n if 1 <= n <= 10 else None
            except ValueError:
                return None

        stress = _int_or_none("stress_1_10")
        exercise = _int_or_none("exercise_1_10")

        entry.stress_1_10 = stress
        entry.exercise_1_10 = exercise
        entry.save()

        if not prof.phone_e164:
            messages.warning(request, "Zapisano lokalnie, ale brak numeru telefonu — dane nie zsynchronizowano z systemem alertów. Ustaw numer w Ustawieniach.")
        else:
            ok = write_wellbeing(
                settings.WEATHERGUARD_DB,
                phone=prof.phone_e164,
                day=today.isoformat(),
                stress_1_10=stress,
                exercise_1_10=exercise,
            )
            if ok:
                messages.success(request, "Zapisano samopoczucie na dziś.")
            else:
                messages.warning(request, "Zapisano lokalnie, ale synchronizacja z feedback.db nie powiodła się.")
        return redirect("wellbeing")

    return render(request, "portal/wellbeing.html", {"prof": prof, "entry": entry, "today": today})
