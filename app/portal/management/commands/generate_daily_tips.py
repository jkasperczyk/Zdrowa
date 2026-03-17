"""
Management command: generate_daily_tips

Usage:
  python manage.py generate_daily_tips                      # all active users
  python manage.py generate_daily_tips --phone +48505019600 # single user

Designed to run once daily at 06:00 via cron.
Stores 3-5 AI-generated tips per user in daily_tips table in feedback.db.
Falls back to rule-based tips if AI call fails.
"""
import json
import os
import sqlite3
import random
from datetime import datetime, timezone

from django.conf import settings
from django.core.management.base import BaseCommand

from portal.wg_sources import dashboard_summary, _connect_feedback, _table_exists


def _ensure_daily_tips_table(c: sqlite3.Connection) -> None:
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_tips (
            phone        TEXT NOT NULL,
            day          TEXT NOT NULL,
            tips_json    TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            PRIMARY KEY (phone, day)
        )
    """)
    c.commit()


def _get_active_users(db_path: str):
    """Return list of (phone, profiles_json) for enabled wg_users."""
    try:
        c = _connect_feedback(db_path)
        try:
            if not _table_exists(c, "wg_users"):
                return []
            rows = c.execute(
                "SELECT phone, profiles_json FROM wg_users WHERE enabled=1"
            ).fetchall()
            result = []
            for phone, profiles_json in rows:
                try:
                    profiles = json.loads(profiles_json or '["migraine"]')
                except Exception:
                    profiles = ["migraine"]
                result.append((phone, profiles))
            return result
        finally:
            c.close()
    except Exception:
        return []


def _generate_rule_based_tips(env_data: dict, profiles: list, scores: dict) -> list:
    """Generate 3-5 rule-based tips based on environmental data."""
    tips = []
    has_allergy = "allergy" in profiles
    has_migraine = "migraine" in profiles
    has_heart = "heart" in profiles

    pollen = env_data.get("pollen_max_6h") or env_data.get("google_pollen_max") or 0
    try:
        pollen = float(pollen)
    except Exception:
        pollen = 0

    aqi = env_data.get("aqi_us_max_6h") or 0
    try:
        aqi = float(aqi)
    except Exception:
        aqi = 0

    pressure_delta = env_data.get("pressure_delta_6h")
    try:
        pressure_delta = float(pressure_delta) if pressure_delta is not None else None
    except Exception:
        pressure_delta = None

    humidity = env_data.get("humidity_now") or 0
    try:
        humidity = float(humidity)
    except Exception:
        humidity = 0

    kp = env_data.get("kp_index") or 0
    try:
        kp = float(kp)
    except Exception:
        kp = 0

    gp_type = env_data.get("google_pollen_type") or "pyłków"

    # Pollen tip
    if has_allergy and pollen >= 40:
        tips.append(f"Wysokie stężenie {gp_type.lower()} — rozważ lek antyhistaminowy przed wyjściem.")
    elif has_allergy and pollen >= 20:
        tips.append(f"Umiarkowane stężenie {gp_type.lower()} — po powrocie do domu zmień odzież i umyj twarz.")

    # Pressure tip
    if has_migraine and pressure_delta is not None and pressure_delta <= -4:
        tips.append(f"Ciśnienie spada ({pressure_delta:+.1f} hPa/6h) — unikaj alkoholu i zadbaj o nawodnienie.")
    elif has_migraine and pressure_delta is not None and pressure_delta >= 4:
        tips.append(f"Ciśnienie rośnie ({pressure_delta:+.1f} hPa/6h) — możliwy wzrost bólu głowy, odpocznij.")

    # Air quality tip
    if aqi >= 101:
        if has_heart:
            tips.append(f"Słaba jakość powietrza (AQI {aqi:.0f}) — ogranicz wysiłek fizyczny na zewnątrz.")
        else:
            tips.append(f"Słaba jakość powietrza (AQI {aqi:.0f}) — zalecane ograniczenie aktywności na zewnątrz.")
    elif aqi >= 51:
        tips.append("Umiarkowana jakość powietrza — osoby wrażliwe powinny ograniczyć długi pobyt na zewnątrz.")

    # Humidity tip
    if humidity >= 80:
        tips.append("Wysoka wilgotność powietrza — nawadniaj się regularnie i unikaj intensywnego wysiłku.")

    # Geomagnetic tip
    if has_heart and kp >= 5:
        tips.append(f"Podwyższona aktywność geomagnetyczna (Kp={kp:.0f}) — stosuj się do zaleceń lekarza kardiologicznego.")

    # Generic positive tips (used when conditions are favorable)
    generic_tips = [
        "Zadbaj o regularną dawkę ruchu na świeżym powietrzu — co najmniej 30 minut dziennie.",
        "Pamiętaj o odpowiednim nawodnieniu — minimum 1,5-2 litry wody dziennie.",
        "Regularny sen jest kluczowy dla zdrowia — staraj się spać 7-8 godzin.",
        "Warunki sprzyjające — dobry dzień na aktywność na świeżym powietrzu!",
        "Krótki spacer po posiłku poprawia trawienie i obniża poziom cukru we krwi.",
    ]

    # Make sure we have 3-5 tips
    if len(tips) < 3:
        random.shuffle(generic_tips)
        for t in generic_tips:
            if t not in tips:
                tips.append(t)
            if len(tips) >= 3:
                break

    return tips[:5]


def _generate_ai_tips(env_data: dict, profiles: list, scores: dict) -> list:
    """Call Claude Haiku to generate 3-5 contextual tips. Returns list or raises."""
    from anthropic import Anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("No ANTHROPIC_API_KEY")

    model = os.getenv("CLAUDE_MODEL_FAST", "claude-haiku-4-5-20251001")
    pressure_delta = env_data.get("pressure_delta_6h")
    aqi = env_data.get("aqi_us_max_6h") or 0
    pollen = env_data.get("google_pollen_max") or env_data.get("pollen_max_6h") or 0
    gp_type = env_data.get("google_pollen_type") or "pyłki"
    humidity = env_data.get("humidity_now") or 0
    kp = env_data.get("kp_index") or 0
    gios = env_data.get("gios_index_name") or ""
    max_score = max(
        (scores.get(p, {}).get("score", 0) for p in profiles if scores.get(p)),
        default=0,
    )

    prompt = (
        f"Wygeneruj 4 krótkie, konkretne, praktyczne porady zdrowotne po polsku "
        f"dla użytkownika monitorującego profile zdrowotne: {', '.join(profiles)}. "
        f"Aktualne dane środowiskowe: zmiana ciśnienia={pressure_delta} hPa/6h, "
        f"AQI={aqi}, pyłki={pollen} ({gp_type}), wilgotność={humidity}%, "
        f"indeks Kp={kp}, GIOŚ={gios}, najwyższy wynik ryzyka={max_score}/100. "
        f"Format odpowiedzi: JSON array z 4 stringami po polsku, każdy to osobna porada (1-2 zdania). "
        f"Przykład: [\"Porada pierwsza.\", \"Porada druga.\", \"Porada trzecia.\", \"Porada czwarta.\"]. "
        f"Odpowiedź TYLKO JSON array, bez żadnego dodatkowego tekstu."
    )

    client = Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = (msg.content[0].text or "").strip()

    # Try to parse JSON array
    tips = json.loads(raw)
    if not isinstance(tips, list) or not tips:
        raise ValueError(f"Unexpected response format: {raw[:100]}")
    return [str(t).strip() for t in tips if str(t).strip()][:5]


def generate_tips_for_user(db_path: str, phone: str, profiles: list, today: str, stdout=None) -> bool:
    """Generate and store tips for one user. Returns True on success."""
    def log(msg):
        if stdout:
            stdout.write(msg)

    # Get latest env/score data from readings
    summary = dashboard_summary(db_path, phone, profiles)
    env_data = summary.get("env", {})
    scores = summary.get("scores", {})

    # Try AI first, fall back to rule-based
    tips = None
    source = "ai"
    try:
        tips = _generate_ai_tips(env_data, profiles, scores)
        if not tips:
            raise ValueError("Empty tips list from AI")
    except Exception as e:
        log(f"    AI failed ({e}), using rule-based fallback")
        source = "rules"
        tips = _generate_rule_based_tips(env_data, profiles, scores)

    if not tips:
        log(f"    No tips generated for {phone}")
        return False

    # Ensure at least 3 tips
    if len(tips) < 3:
        extra = _generate_rule_based_tips(env_data, profiles, scores)
        for t in extra:
            if t not in tips:
                tips.append(t)
            if len(tips) >= 3:
                break

    generated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    try:
        c = _connect_feedback(db_path)
        try:
            _ensure_daily_tips_table(c)
            c.execute(
                "INSERT OR REPLACE INTO daily_tips (phone, day, tips_json, generated_at) VALUES (?,?,?,?)",
                (phone, today, json.dumps(tips, ensure_ascii=False), generated_at),
            )
            c.commit()
        finally:
            c.close()
    except Exception as e:
        log(f"    DB write failed: {e}")
        return False

    log(f"    OK [{source}] {len(tips)} tips stored")
    return True


class Command(BaseCommand):
    help = "Pre-generate daily AI tips for all active users (run at 06:00 via cron)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--phone",
            default=None,
            help="Generate only for this phone number (E.164). Omit to process all active users.",
        )

    def handle(self, *args, **options):
        db_path = settings.WEATHERGUARD_DB
        phone_filter = options.get("phone")
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

        if phone_filter:
            # Single user: look up their profiles from wg_users
            users = []
            try:
                c = _connect_feedback(db_path)
                try:
                    row = c.execute(
                        "SELECT profiles_json FROM wg_users WHERE phone=?",
                        (phone_filter,),
                    ).fetchone()
                    if row:
                        profiles = json.loads(row[0] or '["migraine"]')
                    else:
                        profiles = ["migraine", "allergy", "heart"]
                    users = [(phone_filter, profiles)]
                finally:
                    c.close()
            except Exception:
                users = [(phone_filter, ["migraine", "allergy", "heart"])]
        else:
            users = _get_active_users(db_path)

        self.stdout.write(f"Generating daily tips for {len(users)} user(s) [{today}]...")

        ok = 0
        fail = 0
        for phone, profiles in users:
            self.stdout.write(f"  {phone} {profiles}")
            try:
                success = generate_tips_for_user(
                    db_path, phone, profiles, today, stdout=self.stdout
                )
                if success:
                    ok += 1
                else:
                    fail += 1
            except Exception as e:
                fail += 1
                self.stdout.write(self.style.ERROR(f"    ERR: {e}"))

        self.stdout.write(f"\nDone: {ok} OK, {fail} failed.")
