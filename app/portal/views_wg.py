from __future__ import annotations

import csv
import io
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
    write_wg_user,
    wellbeing_history,
    export_user_data,
    write_symptom_log,
    symptom_log_history,
)

ALERT_PROFILES = ["migraine", "allergy", "heart"]


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
        return render(request, "portal/data.html", {
            "prof": prof, "rows": [], "profiles": [], "selected_profile": "",
            "wb_rows": [],
        })

    profiles = available_profiles(settings.WEATHERGUARD_DB, prof.phone_e164, days=30) or ["migraine", "allergy", "heart"]
    selected_profile = (request.GET.get("profile") or "").strip() or prof.default_profile or (profiles[0] if profiles else "migraine")
    if selected_profile not in profiles:
        profiles = [selected_profile] + profiles

    rows = readings_last_days(settings.WEATHERGUARD_DB, prof.phone_e164, selected_profile, days=7, limit=1500)

    kset = set()
    for r in rows:
        kset.update(r.keys())

    # Build wellbeing-by-date lookup for inline join
    wb_list = wellbeing_history(settings.WEATHERGUARD_DB, prof.phone_e164, days=7)
    wb_by_date: Dict[str, Dict] = {w["day"]: w for w in wb_list}

    # Enrich each reading row with wellbeing data for that day + modifier value
    from datetime import timezone as _tz
    from datetime import datetime as _dt
    for r in rows:
        ts = r.get("ts")
        day = _dt.fromtimestamp(int(ts), tz=_tz.utc).strftime("%Y-%m-%d") if ts else None
        wb = wb_by_date.get(day, {}) if day else {}
        r["wb_stress"]    = wb.get("stress_1_10")
        r["wb_exercise"]  = wb.get("exercise_1_10")
        r["wb_sleep"]     = wb.get("sleep_quality_1_10")
        r["wb_hydration"] = wb.get("hydration_1_10")
        r["wb_headache"]  = wb.get("headache_1_10")
        # Compute modifier string
        score = r.get("score")
        base = r.get("base_score")
        if base is not None and score is not None and base > 0:
            r["modifier_str"] = f"×{score/base:.2f}"
        else:
            r["modifier_str"] = None

    return render(request, "portal/data.html", {
        "prof": prof,
        "profiles": profiles,
        "selected_profile": selected_profile,
        "rows": rows,
        "has_value": "value" in kset,
        "has_risk": "risk" in kset,
        "has_threshold": "threshold" in kset,
        "has_details": "details" in kset,
        "has_base_score": "base_score" in kset,
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
        # ── Auth user fields ──────────────────────────────────────────
        user = request.user
        user.first_name = (request.POST.get("first_name") or "").strip()
        user.last_name  = (request.POST.get("last_name")  or "").strip()
        user.save(update_fields=["first_name", "last_name"])

        # ── Profile fields ────────────────────────────────────────────
        prof.phone_e164 = (request.POST.get("phone_e164") or "").strip()
        prof.gender     = (request.POST.get("gender")     or "unspecified").strip()

        enabled = request.POST.getlist("enabled_alerts") or []
        prof.enabled_alerts = [a for a in enabled if a in alert_types]
        prof.default_profile = (prof.enabled_alerts[0] if prof.enabled_alerts else (prof.default_profile or "migraine"))

        prof.sms_enabled = request.POST.get("sms_enabled") == "on"
        prof.location    = (request.POST.get("location") or "").strip()
        raw_thr = (request.POST.get("alert_threshold") or "").strip()
        prof.alert_threshold = int(raw_thr) if raw_thr.isdigit() else None
        prof.quiet_hours = (request.POST.get("quiet_hours") or "").strip()

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

        if prof.phone_e164:
            try:
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
            except Exception:
                pass

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
        def _int_or_none(key: str, min_val: int = 1, max_val: int = 10) -> Optional[int]:
            v = (request.POST.get(key) or "").strip()
            try:
                n = int(v)
                return n if min_val <= n <= max_val else None
            except ValueError:
                return None

        stress = _int_or_none("stress_1_10")
        exercise = _int_or_none("exercise_1_10")
        sleep_quality = _int_or_none("sleep_quality_1_10")
        hydration = _int_or_none("hydration_1_10")
        headache = _int_or_none("headache_1_10", min_val=0)  # headache starts at 0

        entry.stress_1_10 = stress
        entry.exercise_1_10 = exercise
        entry.sleep_quality_1_10 = sleep_quality
        entry.hydration_1_10 = hydration
        entry.headache_1_10 = headache
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
                sleep_quality_1_10=sleep_quality,
                hydration_1_10=hydration,
                headache_1_10=headache,
            )
            if ok:
                messages.success(request, "Zapisano samopoczucie na dziś.")
            else:
                messages.warning(request, "Zapisano lokalnie, ale synchronizacja z feedback.db nie powiodła się.")
        return redirect("wellbeing")

    return render(request, "portal/wellbeing.html", {"prof": prof, "entry": entry, "today": today})


@login_required
def csv_export_view(request: HttpRequest) -> HttpResponse:
    prof = _get_profile(request.user)
    if not prof.phone_e164:
        messages.error(request, "Brak numeru telefonu w profilu.")
        return redirect("data")

    profile = (request.GET.get("profile") or prof.default_profile or "migraine").strip()
    rows = export_user_data(settings.WEATHERGUARD_DB, prof.phone_e164, profile, days=90)

    today_str = date.today().strftime("%Y%m%d")
    phone_clean = prof.phone_e164.replace("+", "").replace(" ", "")
    filename = f"zdrowa_export_{phone_clean}_{today_str}.csv"

    output = io.StringIO()
    fieldnames = ["date", "score", "base_score", "threshold", "label", "alert_sent",
                  "stress_1_10", "exercise_1_10", "sleep_quality_1_10", "hydration_1_10", "headache_1_10"]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in fieldnames})

    response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def symptom_log_view(request: HttpRequest) -> HttpResponse:
    prof = _get_profile(request.user)

    if request.method == "POST":
        profile = (request.POST.get("profile") or "migraine").strip()
        if profile not in ALERT_PROFILES:
            profile = "migraine"
        sev_raw = (request.POST.get("severity_1_10") or "").strip()
        try:
            severity = int(sev_raw)
            if not (1 <= severity <= 10):
                severity = 5
        except ValueError:
            severity = 5
        notes = (request.POST.get("notes") or "").strip()[:500]

        if not prof.phone_e164:
            messages.warning(request, "Brak numeru telefonu — nie można zsynchronizować z systemem alertów.")
        else:
            ok = write_symptom_log(
                settings.WEATHERGUARD_DB,
                phone=prof.phone_e164,
                profile=profile,
                severity_1_10=severity,
                notes=notes or None,
            )
            if ok:
                messages.success(request, "Dolegliwość zapisana.")
            else:
                messages.warning(request, "Nie udało się zapisać dolegliwości.")
        return redirect("symptom_log")

    history = []
    if prof.phone_e164:
        history = symptom_log_history(settings.WEATHERGUARD_DB, prof.phone_e164, days=30)

    return render(request, "portal/symptom_log.html", {
        "prof": prof,
        "history": history,
        "profiles": ALERT_PROFILES,
    })
