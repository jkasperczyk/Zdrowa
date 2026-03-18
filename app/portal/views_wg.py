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
    get_weekly_reports,
    save_weekly_report,
    correlation_data,
    generate_symptom_feedback,
    get_ml_status,
    award_badge,
    get_user_badges,
    get_weekly_stats,
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

    if request.method == "POST":
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
    font_sizes = [("small","Mały (14px)"), ("normal","Normalny (16px)"), ("large","Duży (18px)")]

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

        prof.use_ml_prediction = request.POST.get("use_ml_prediction") == "1"

        # ── Display preferences ───────────────────────────────────────
        fs = (request.POST.get("font_size_preference") or "normal").strip()
        if fs in ("small", "normal", "large"):
            prof.font_size_preference = fs
        prof.high_contrast = request.POST.get("high_contrast") == "1"
        prof.evening_reminder = request.POST.get("evening_reminder") == "1"

        prof.save()

        if prof.phone_e164:
            try:
                write_wg_user(
                    settings.WEATHERGUARD_DB,
                    phone=prof.phone_e164,
                    profiles=prof.enabled_alerts or ["migraine"],
                    location=prof.location,
                    threshold=prof.alert_threshold,
                    quiet_hours=prof.quiet_hours or None,
                    enabled=request.user.is_active,
                    use_ml=prof.use_ml_prediction,
                )
            except Exception:
                pass

        messages.success(request, "Zapisano ustawienia.")
        return redirect("settings")

    ml_status = {}
    if prof.phone_e164 and prof.enabled_alerts:
        try:
            ml_status = get_ml_status(settings.WEATHERGUARD_DB, prof.phone_e164, prof.enabled_alerts)
        except Exception:
            pass

    # ── Badges context ────────────────────────────────────────────────
    badges_list = []
    if prof.phone_e164:
        try:
            badges_list = get_user_badges(settings.WEATHERGUARD_DB, prof.phone_e164)
        except Exception:
            pass
    badges_dict = {b["badge_id"]: b["earned_at"] for b in badges_list}

    all_badges = [
        {"id": "streak_3",  "emoji": "🔥", "name": "3 dni z rzędu",   "desc": "Zaloguj samopoczucie 3 dni z rzędu"},
        {"id": "streak_7",  "emoji": "⚡", "name": "Tydzień",          "desc": "7 dni z rzędu"},
        {"id": "streak_14", "emoji": "💎", "name": "2 tygodnie",       "desc": "14 dni z rzędu"},
        {"id": "streak_30", "emoji": "👑", "name": "Mistrz miesiąca",  "desc": "30 dni z rzędu"},
    ]

    return render(request, "portal/settings.html", {
        "prof": prof,
        "genders": genders,
        "alert_types": alert_types,
        "ml_status": ml_status,
        "font_sizes": font_sizes,
        "badges": badges_dict,
        "all_badges": all_badges,
    })


@login_required
def wellbeing_view(request: HttpRequest) -> HttpResponse:
    prof = _get_profile(request.user)
    today = date.today()

    # Load existing entry for today if present
    entry, _ = DailyWellbeing.objects.get_or_create(user=request.user, day=today)

    milestone_msg = None

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
        daily_note = (request.POST.get("daily_note") or "").strip()[:500]

        entry.stress_1_10 = stress
        entry.exercise_1_10 = exercise
        entry.sleep_quality_1_10 = sleep_quality
        entry.hydration_1_10 = hydration
        entry.headache_1_10 = headache
        entry.daily_note = daily_note
        entry.save()

        # ── Streak logic ──────────────────────────────────────────────
        from datetime import timedelta
        last = prof.last_log_date
        if last is None:
            # First ever log
            prof.current_streak = 1
        elif last == today:
            # Already logged today — don't change streak
            pass
        elif last == today - timedelta(days=1):
            # Consecutive day
            prof.current_streak += 1
        else:
            # Gap > 1 day — reset
            prof.current_streak = 1
        prof.longest_streak = max(prof.longest_streak, prof.current_streak)
        prof.last_log_date = today

        # ── Badge awards ──────────────────────────────────────────────
        streak = prof.current_streak
        badge_milestones = {3: "streak_3", 7: "streak_7", 14: "streak_14", 30: "streak_30"}
        if streak in badge_milestones and prof.phone_e164:
            badge_id = badge_milestones[streak]
            newly_awarded = award_badge(settings.WEATHERGUARD_DB, prof.phone_e164, badge_id)
            if newly_awarded:
                names = {3: "3 dni z rzędu!", 7: "Tydzień konsekwencji!", 14: "2 tygodnie!", 30: "Mistrz miesiąca!"}
                milestone_msg = f"Zdobyto odznakę: {names.get(streak, str(streak) + ' dni')}"

        prof.save(update_fields=["current_streak", "longest_streak", "last_log_date", "updated_at"])

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
                notes=daily_note or None,
            )
            if ok:
                messages.success(request, "Zapisano samopoczucie na dziś.")
            else:
                messages.warning(request, "Zapisano lokalnie, ale synchronizacja z feedback.db nie powiodła się.")

        if milestone_msg:
            messages.success(request, milestone_msg)

        return redirect("wellbeing")

    return render(request, "portal/wellbeing.html", {
        "prof": prof,
        "entry": entry,
        "today": today,
        "milestone_msg": milestone_msg,
        "streak": prof.current_streak,
    })


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

    # Enrich export rows with daily_note from Django DB
    from datetime import datetime as _dt, timezone as _tz
    wb_notes: Dict[str, str] = {}
    try:
        wb_qs = DailyWellbeing.objects.filter(user=request.user).values("day", "daily_note")
        for wb in wb_qs:
            wb_notes[str(wb["day"])] = wb["daily_note"] or ""
    except Exception:
        pass

    output = io.StringIO()
    fieldnames = ["date", "score", "base_score", "threshold", "label", "alert_sent",
                  "stress_1_10", "exercise_1_10", "sleep_quality_1_10", "hydration_1_10", "headache_1_10",
                  "daily_note"]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        day_key = (row.get("date") or row.get("dt") or "")[:10]
        row_out = {k: ("" if row.get(k) is None else row.get(k)) for k in fieldnames}
        row_out["daily_note"] = wb_notes.get(day_key, "")
        writer.writerow(row_out)

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
            # Snapshot current environmental features for ML training data
            current_feats: Optional[Dict] = None
            try:
                import sqlite3 as _sq3, json as _json
                _c = _sq3.connect(settings.WEATHERGUARD_DB)
                try:
                    row = _c.execute(
                        "SELECT feats_json FROM readings WHERE phone=? AND profile=? ORDER BY ts DESC LIMIT 1",
                        (prof.phone_e164, profile)
                    ).fetchone()
                    if row and row[0]:
                        current_feats = _json.loads(row[0])
                finally:
                    _c.close()
            except Exception:
                pass

            ok = write_symptom_log(
                settings.WEATHERGUARD_DB,
                phone=prof.phone_e164,
                profile=profile,
                severity_1_10=severity,
                notes=notes or None,
                feats=current_feats,
            )
            if ok:
                messages.success(request, "Dolegliwość zapisana.")
                try:
                    _env = {k: current_feats.get(k) for k in [
                        "pressure_delta_6h", "aqi_us_max_6h", "google_pollen_max", "pollen_max_6h",
                    ] if current_feats and current_feats.get(k) is not None}
                    _fb = generate_symptom_feedback(
                        settings.WEATHERGUARD_DB, prof.phone_e164, profile, severity, notes or "", _env
                    )
                    if _fb:
                        messages.info(request, _fb)
                except Exception:
                    pass
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


@login_required
def raporty_view(request: HttpRequest) -> HttpResponse:
    prof = _get_profile(request.user)
    reports = []
    correlation = {}
    corr_profile = (request.GET.get("profile") or "").strip() or prof.default_profile or "migraine"

    if prof.phone_e164:
        reports = get_weekly_reports(settings.WEATHERGUARD_DB, prof.phone_e164)
        correlation = correlation_data(settings.WEATHERGUARD_DB, prof.phone_e164, corr_profile, days=30)

    # Staff can trigger generation
    if request.method == "POST" and request.user.is_staff:
        action = request.POST.get("action")
        if action == "generate" and prof.phone_e164:
            _generate_report_for_user(prof.phone_e164)
            messages.success(request, "Raport wygenerowany.")
            return redirect("raporty")

    profiles = available_profiles(settings.WEATHERGUARD_DB, prof.phone_e164, days=30) if prof.phone_e164 else []
    if not profiles:
        profiles = ALERT_PROFILES

    return render(request, "portal/raporty.html", {
        "prof": prof,
        "reports": reports,
        "correlation": correlation,
        "corr_profile": corr_profile,
        "profiles": profiles,
    })


def _generate_report_for_user(phone: str) -> bool:
    """Generate a weekly AI report for a user. Returns True on success."""
    from datetime import date, timedelta
    from django.conf import settings as _settings
    import sqlite3 as _sq3, json as _json

    db = _settings.WEATHERGUARD_DB
    week_end = date.today()
    week_start = week_end - timedelta(days=7)

    # Collect data
    readings = readings_last_days(db, phone, "migraine", days=7, limit=500)
    wb = wellbeing_history(db, phone, days=7)
    from .wg_sources import symptom_log_history as _slh
    symptoms = _slh(db, phone, days=7)

    # Build prompt data
    avg_score = round(sum(r.get("score", 0) for r in readings) / len(readings)) if readings else 0
    peak = max((r.get("score", 0) for r in readings), default=0)

    anthropic_key = getattr(_settings, "ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        report_html = f"""
<h3>Podsumowanie tygodnia {week_start} — {week_end}</h3>
<p>Średnie ryzyko: <b>{avg_score}/100</b>, szczyt: <b>{peak}/100</b></p>
<p>Odczyty: {len(readings)}, wpisy samopoczucia: {len(wb)}, dolegliwości: {len(symptoms)}</p>
<p class="muted small">(Klucz Anthropic nie skonfigurowany — raport podstawowy.)</p>
"""
    else:
        try:
            from anthropic import Anthropic as _ANT
            client = _ANT(api_key=anthropic_key)
            model = getattr(_settings, "CLAUDE_MODEL_SMART", "claude-sonnet-4-6")
            data_summary = (
                f"Odczyty ryzyka (7 dni): {len(readings)} rekordów. "
                f"Średnie ryzyko: {avg_score}/100. Szczyt: {peak}/100. "
                f"Wpisy samopoczucia: {len(wb)}. Zgłoszone dolegliwości: {len(symptoms)}. "
                f"Najgorszy dzień: {max((r.get('dt','?') for r in readings if r.get('score', 0)==peak), default='brak')}."
            )
            msg = client.messages.create(
                model=model,
                max_tokens=2000,
                system="Jesteś asystentem zdrowotnym. Twórz zwięzłe raporty po polsku w formacie HTML (używaj h3, p, ul/li). Maksymalnie 400 słów.",
                messages=[{"role": "user", "content": (
                    f"Wygeneruj tygodniowy raport zdrowotny na podstawie danych: {data_summary}\n\n"
                    "Uwzględnij sekcje: podsumowanie tygodnia, analiza ryzyka, korelacje, rekomendacje."
                )}],
            )
            report_html = msg.content[0].text.strip()
        except Exception as e:
            report_html = f"<p>Błąd generowania raportu: {e}</p>"

    return save_weekly_report(db, phone, week_start.isoformat(), week_end.isoformat(), report_html)


@login_required
def pdf_export_view(request: HttpRequest) -> HttpResponse:
    """Generate a PDF health report for the last 30 days using reportlab."""
    prof = _get_profile(request.user)
    try:
        from reportlab.lib.pagesizes import A4  # type: ignore
        from reportlab.lib import colors  # type: ignore
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer  # type: ignore
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle  # type: ignore
        from reportlab.lib.units import cm  # type: ignore
    except ImportError:
        messages.error(request, "Moduł reportlab nie jest zainstalowany. Skontaktuj się z administratorem.")
        return redirect("data")

    import io as _io
    today_str = date.today().strftime("%Y%m%d")
    phone_clean = prof.phone_e164.replace("+", "").replace(" ", "") if prof.phone_e164 else "noname"
    filename = f"zdrowa_raport_{phone_clean}_{today_str}.pdf"

    buf = _io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Heading1"], fontSize=18, spaceAfter=6)
    sub_style = ParagraphStyle("sub", parent=styles["Normal"], fontSize=10, textColor=colors.HexColor("#64748b"), spaceAfter=12)
    section_style = ParagraphStyle("section", parent=styles["Heading2"], fontSize=13, spaceBefore=16, spaceAfter=6)
    normal_style = styles["Normal"]

    story = []
    story.append(Paragraph("Raport zdrowotny — Zdrowa", title_style))
    story.append(Paragraph(
        f"Użytkownik: {request.user.get_full_name() or request.user.username} &nbsp;|&nbsp; "
        f"Wygenerowano: {date.today().strftime('%d.%m.%Y')} &nbsp;|&nbsp; Ostatnie 30 dni",
        sub_style,
    ))
    story.append(Spacer(1, 0.3 * cm))

    # ── Wellbeing section ─────────────────────────────────────────────
    story.append(Paragraph("Samopoczucie (ostatnie 30 dni)", section_style))
    wb_rows = DailyWellbeing.objects.filter(
        user=request.user
    ).order_by("-day")[:30]

    if wb_rows:
        table_data = [["Data", "Stres", "Aktywność", "Sen", "Nawodnienie", "Ból głowy", "Notatka"]]
        for wb in wb_rows:
            table_data.append([
                str(wb.day),
                str(wb.stress_1_10) if wb.stress_1_10 is not None else "—",
                str(wb.exercise_1_10) if wb.exercise_1_10 is not None else "—",
                str(wb.sleep_quality_1_10) if wb.sleep_quality_1_10 is not None else "—",
                str(wb.hydration_1_10) if wb.hydration_1_10 is not None else "—",
                str(wb.headache_1_10) if wb.headache_1_10 is not None else "—",
                (wb.daily_note or "")[:60],
            ])
        col_widths = [2.2 * cm, 1.4 * cm, 1.9 * cm, 1.3 * cm, 2.2 * cm, 2.0 * cm, None]
        t = Table(table_data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",   (0, 0), (-1, -1), 8),
            ("ALIGN",      (1, 0), (-2, -1), "CENTER"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f4f8")]),
            ("GRID",       (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("TOPPADDING",  (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("Brak wpisów samopoczucia.", normal_style))

    story.append(Spacer(1, 0.5 * cm))

    # ── Risk scores section ───────────────────────────────────────────
    if prof.phone_e164:
        story.append(Paragraph("Odczyty ryzyka (ostatnie 7 dni)", section_style))
        profiles = list(prof.enabled_alerts) if prof.enabled_alerts else ["migraine"]
        for profile_name in profiles:
            rows_risk = readings_last_days(settings.WEATHERGUARD_DB, prof.phone_e164, profile_name, days=7, limit=50)
            if rows_risk:
                story.append(Paragraph(f"Profil: {profile_name}", ParagraphStyle("ph", parent=normal_style, fontSize=10, fontName="Helvetica-Bold", spaceBefore=8)))
                risk_data = [["Data/czas (UTC)", "Wynik", "Baza", "Próg"]]
                for r in rows_risk[:20]:
                    risk_data.append([
                        str(r.get("dt", ""))[:16],
                        str(r.get("score", "—")),
                        str(r.get("base_score", "—")),
                        str(r.get("threshold", "—")),
                    ])
                rt = Table(risk_data, colWidths=[4.5 * cm, 2 * cm, 2 * cm, 2 * cm], repeatRows=1)
                rt.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
                    ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
                    ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE",   (0, 0), (-1, -1), 8),
                    ("ALIGN",      (1, 0), (-1, -1), "CENTER"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f4f8")]),
                    ("GRID",       (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                    ("TOPPADDING",  (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]))
                story.append(rt)

    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(
        f"Raport wygenerowany automatycznie przez system Zdrowa. Data: {date.today().strftime('%d.%m.%Y')}.",
        ParagraphStyle("footer", parent=normal_style, fontSize=8, textColor=colors.HexColor("#94a3b8")),
    ))

    doc.build(story)
    buf.seek(0)

    response = HttpResponse(buf.read(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ── JSON API — trend charts ────────────────────────────────────────────────────

@login_required
def api_trend_scores(request):
    """GET /api/trend-scores/?days=30&profile=migraine
    Returns: {"labels": ["2026-03-01", ...], "base_scores": [...], "final_scores": [...]}
    """
    from django.http import JsonResponse
    import sqlite3 as _sq3, json as _json
    from datetime import datetime, timedelta, timezone

    prof = _get_profile(request.user)
    if not prof.phone_e164:
        return JsonResponse({"labels": [], "base_scores": [], "final_scores": []})

    try:
        days = max(1, min(365, int(request.GET.get("days", 30))))
    except (ValueError, TypeError):
        days = 30

    profile = (request.GET.get("profile") or "").strip() or prof.default_profile or "migraine"

    db = settings.WEATHERGUARD_DB
    import os
    if not os.path.exists(db):
        return JsonResponse({"labels": [], "base_scores": [], "final_scores": []})

    since = int((datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp())
    try:
        c = _sq3.connect(db)
        c.row_factory = _sq3.Row
        try:
            cols_info = {r[1] for r in c.execute("PRAGMA table_info(readings)").fetchall()}
            has_base = "base_score" in cols_info
            if has_base:
                rows = c.execute(
                    "SELECT ts, score, base_score FROM readings WHERE phone=? AND profile=? AND ts>=? ORDER BY ts ASC",
                    (prof.phone_e164, profile, since)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT ts, score FROM readings WHERE phone=? AND profile=? AND ts>=? ORDER BY ts ASC",
                    (prof.phone_e164, profile, since)
                ).fetchall()
        finally:
            c.close()
    except Exception:
        return JsonResponse({"labels": [], "base_scores": [], "final_scores": []})

    # Aggregate by day (max score per day)
    day_base: Dict[str, float] = {}
    day_final: Dict[str, float] = {}
    for row in rows:
        ts = row["ts"] or 0
        score = float(row["score"] or 0)
        base = float(row["base_score"] if has_base and row["base_score"] is not None else score)
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        if day not in day_final or score > day_final[day]:
            day_final[day] = score
        if day not in day_base or base > day_base[day]:
            day_base[day] = base

    labels = sorted(day_final.keys())
    return JsonResponse({
        "labels": labels,
        "base_scores": [day_base.get(d, 0) for d in labels],
        "final_scores": [day_final.get(d, 0) for d in labels],
    })


@login_required
def api_trend_factors(request):
    """GET /api/trend-factors/?days=30&factor=pressure_delta
    Returns: {"labels": [...], "scores": [...], "factor_values": [...], "factor_label": "...", "r": 0.45}
    """
    from django.http import JsonResponse
    import sqlite3 as _sq3, json as _json
    from datetime import datetime, timedelta, timezone
    import os

    FACTOR_KEYS = {
        "pressure_delta": "pressure_delta_6h",
        "aqi":            "aqi_us_max_6h",
        "pollen_total":   "pollen_max_6h",
        "humidity":       "humidity_now",
        "temperature":    "temp_delta_6h",
        "dew_point":      "dew_point_now",
    }
    FACTOR_LABELS = {
        "pressure_delta": "Zmiana ciśnienia (hPa/6h)",
        "aqi":            "Jakość powietrza (AQI)",
        "pollen_total":   "Pyłki",
        "humidity":       "Wilgotność (%)",
        "temperature":    "Zmiana temp. (°C/6h)",
        "dew_point":      "Punkt rosy (°C)",
    }

    prof = _get_profile(request.user)
    if not prof.phone_e164:
        return JsonResponse({"labels": [], "scores": [], "factor_values": [], "factor_label": "", "r": None})

    try:
        days = max(1, min(365, int(request.GET.get("days", 30))))
    except (ValueError, TypeError):
        days = 30

    factor_key_user = (request.GET.get("factor") or "pressure_delta").strip()
    factor_key = FACTOR_KEYS.get(factor_key_user, "pressure_delta_6h")
    factor_label = FACTOR_LABELS.get(factor_key_user, factor_key_user)

    profile = (request.GET.get("profile") or "").strip() or prof.default_profile or "migraine"

    db = settings.WEATHERGUARD_DB
    if not os.path.exists(db):
        return JsonResponse({"labels": [], "scores": [], "factor_values": [], "factor_label": factor_label, "r": None})

    since = int((datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp())
    try:
        c = _sq3.connect(db)
        c.row_factory = _sq3.Row
        try:
            cols_info = {r[1] for r in c.execute("PRAGMA table_info(readings)").fetchall()}
            has_feats = "feats_json" in cols_info
            if has_feats:
                rows = c.execute(
                    "SELECT ts, score, feats_json FROM readings WHERE phone=? AND profile=? AND ts>=? ORDER BY ts ASC",
                    (prof.phone_e164, profile, since)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT ts, score FROM readings WHERE phone=? AND profile=? AND ts>=? ORDER BY ts ASC",
                    (prof.phone_e164, profile, since)
                ).fetchall()
        finally:
            c.close()
    except Exception:
        return JsonResponse({"labels": [], "scores": [], "factor_values": [], "factor_label": factor_label, "r": None})

    # Aggregate by day
    day_score: Dict[str, float] = {}
    day_factor: Dict[str, Optional[float]] = {}
    for row in rows:
        ts = row["ts"] or 0
        score = float(row["score"] or 0)
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        if day not in day_score or score > day_score[day]:
            day_score[day] = score
        if has_feats and row["feats_json"] and day not in day_factor:
            try:
                feats = _json.loads(row["feats_json"]) or {}
                val = feats.get(factor_key)
                if val is not None:
                    day_factor[day] = float(val)
            except Exception:
                pass

    labels = sorted(day_score.keys())
    scores = [day_score.get(d, 0) for d in labels]
    factor_values = [day_factor.get(d) for d in labels]

    # Pearson r between scores and factor_values (skip None)
    r_val = None
    pairs = [(s, f) for s, f in zip(scores, factor_values) if f is not None]
    if len(pairs) >= 3:
        xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
        n = len(xs)
        mx, my = sum(xs)/n, sum(ys)/n
        num = sum((x-mx)*(y-my) for x,y in zip(xs,ys))
        dx = sum((x-mx)**2 for x in xs)**0.5
        dy = sum((y-my)**2 for y in ys)**0.5
        if dx > 0 and dy > 0:
            r_val = round(num / (dx * dy), 3)

    return JsonResponse({
        "labels": labels,
        "scores": scores,
        "factor_values": factor_values,
        "factor_label": factor_label,
        "r": r_val,
    })


@login_required
def api_trend_wellbeing(request):
    """GET /api/trend-wellbeing/?days=30
    Returns: {"labels": [...], "stress": [...], "exercise": [...], "sleep": [...], "hydration": [...], "headache": [...]}
    null for days with no data.
    """
    from django.http import JsonResponse
    from datetime import datetime, timedelta, timezone

    prof = _get_profile(request.user)
    if not prof.phone_e164:
        return JsonResponse({"labels": [], "stress": [], "exercise": [], "sleep": [], "hydration": [], "headache": []})

    try:
        days = max(1, min(365, int(request.GET.get("days", 30))))
    except (ValueError, TypeError):
        days = 30

    wb_rows = wellbeing_history(settings.WEATHERGUARD_DB, prof.phone_e164, days=days)

    # Build day-keyed lookup
    wb_map: Dict[str, Dict] = {}
    for r in wb_rows:
        wb_map[r["day"]] = r

    # Generate all days in range
    now = datetime.now(tz=timezone.utc).date()
    start = now - timedelta(days=days - 1)
    labels = []
    d = start
    while d <= now:
        labels.append(d.isoformat())
        d = d + timedelta(days=1)

    def _get(day, key):
        return wb_map[day].get(key) if day in wb_map else None

    return JsonResponse({
        "labels": labels,
        "stress":    [_get(d, "stress_1_10") for d in labels],
        "exercise":  [_get(d, "exercise_1_10") for d in labels],
        "sleep":     [_get(d, "sleep_quality_1_10") for d in labels],
        "hydration": [_get(d, "hydration_1_10") for d in labels],
        "headache":  [_get(d, "headache_1_10") for d in labels],
    })


@login_required
def api_trend_symptoms(request):
    """GET /api/trend-symptoms/?days=30
    Returns: {"dates": ["2026-03-05", ...], "severities": [7, ...]}
    """
    from django.http import JsonResponse

    prof = _get_profile(request.user)
    if not prof.phone_e164:
        return JsonResponse({"dates": [], "severities": []})

    try:
        days = max(1, min(365, int(request.GET.get("days", 30))))
    except (ValueError, TypeError):
        days = 30

    history = symptom_log_history(settings.WEATHERGUARD_DB, prof.phone_e164, days=days)

    dates = []
    severities = []
    for entry in reversed(history):  # oldest first
        ts = entry.get("timestamp", "")
        day = ts[:10] if ts else ""
        if day:
            dates.append(day)
            severities.append(entry.get("severity"))

    return JsonResponse({"dates": dates, "severities": severities})
