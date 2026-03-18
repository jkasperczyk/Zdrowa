"""
Management command: watchdog

Usage:
  python manage.py watchdog                  # run all checks, email on issues
  python manage.py watchdog --daily-summary  # run all checks, always send email
  python manage.py watchdog --verbose        # show detailed output for every check

Designed to run every 15 minutes via cron (as root).
Checks data freshness, service health, API keys, log errors, disk, and data integrity.
Sends HTML email alerts to ADMIN_NOTIFY_EMAIL with cooldown-based deduplication.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand

from portal.wg_sources import _connect_feedback, _table_exists

# ── Constants ─────────────────────────────────────────────────────────────────

OK       = "OK"
WARNING  = "WARNING"
CRITICAL = "CRITICAL"

LEVEL_ORDER = {OK: 0, WARNING: 1, CRITICAL: 2}

# Cooldown: don't re-email the same alert within this many seconds
COOLDOWN = {CRITICAL: 3600, WARNING: 14400}   # 1h / 4h

LOG_DIR   = "/opt/weatherguard/logs"
LOGS = {
    "cron":    os.path.join(LOG_DIR, "cron.log"),
    "push":    os.path.join(LOG_DIR, "push_cron.log"),
    "ml":      os.path.join(LOG_DIR, "ml_retrain.log"),
    "tips":    os.path.join(LOG_DIR, "tips_cron.log"),
    "cache":   os.path.join(LOG_DIR, "cache_cron.log"),
    "reports": os.path.join(LOG_DIR, "reports_cron.log"),
}

# ANSI colours for terminal output
C_RED    = "\033[91m"
C_YELLOW = "\033[93m"
C_GREEN  = "\033[92m"
C_RESET  = "\033[0m"
C_BOLD   = "\033[1m"


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    name:    str
    level:   str          # OK / WARNING / CRITICAL
    message: str
    details: str = ""

    @property
    def icon(self) -> str:
        return {"OK": "✅", "WARNING": "🟡", "CRITICAL": "🔴"}.get(self.level, "❓")

    @property
    def color(self) -> str:
        return {OK: C_GREEN, WARNING: C_YELLOW, CRITICAL: C_RED}.get(self.level, "")


# ── State table helpers ────────────────────────────────────────────────────────

def _ensure_watchdog_state(c: sqlite3.Connection) -> None:
    c.execute("""
        CREATE TABLE IF NOT EXISTS watchdog_state (
            check_name   TEXT PRIMARY KEY,
            status       TEXT,
            last_alert_at TEXT,
            message      TEXT
        )
    """)
    c.commit()


def _get_state(db_path: str, check_name: str) -> Optional[dict]:
    if not db_path or not os.path.exists(db_path):
        return None
    try:
        c = _connect_feedback(db_path)
        try:
            _ensure_watchdog_state(c)
            row = c.execute(
                "SELECT status, last_alert_at, message FROM watchdog_state WHERE check_name=?",
                (check_name,),
            ).fetchone()
            if row:
                return {"status": row[0], "last_alert_at": row[1], "message": row[2]}
            return None
        finally:
            c.close()
    except Exception:
        return None


def _set_state(db_path: str, check_name: str, status: str, message: str) -> None:
    if not db_path:
        return
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        c = _connect_feedback(db_path)
        try:
            _ensure_watchdog_state(c)
            c.execute(
                "INSERT OR REPLACE INTO watchdog_state (check_name, status, last_alert_at, message) VALUES (?,?,?,?)",
                (check_name, status, now, message),
            )
            c.commit()
        finally:
            c.close()
    except Exception:
        pass


def _should_send_alert(db_path: str, check_name: str, level: str) -> bool:
    """Return True if we should send an alert (cooldown not active)."""
    if level == OK:
        return False
    state = _get_state(db_path, check_name)
    if not state or not state.get("last_alert_at"):
        return True
    try:
        last = datetime.fromisoformat(state["last_alert_at"]).replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(tz=timezone.utc) - last).total_seconds()
        return elapsed >= COOLDOWN.get(level, 3600)
    except Exception:
        return True


# ── A) Data freshness ─────────────────────────────────────────────────────────

def check_data_freshness(db_path: str, verbose: bool) -> List[CheckResult]:
    results = []
    now_ts = int(time.time())

    if not db_path or not os.path.exists(db_path):
        results.append(CheckResult(
            "db_exists", CRITICAL,
            f"feedback.db nie istnieje: {db_path}",
            "Sprawdź zmienną WG_FEEDBACK_DB i czy runner jest uruchomiony.",
        ))
        return results

    try:
        c = _connect_feedback(db_path)
        try:
            if not _table_exists(c, "readings"):
                results.append(CheckResult("readings_table", CRITICAL,
                    "Tabela readings nie istnieje w feedback.db",
                    "Runner może nie działać lub baza danych jest uszkodzona."))
                return results

            # Global last reading
            row = c.execute("SELECT MAX(ts) FROM readings").fetchone()
            last_ts = row[0] if row and row[0] else None
            if last_ts is None:
                results.append(CheckResult("readings_global", WARNING,
                    "Brak odczytów w tabeli readings", ""))
            else:
                age_h = (now_ts - int(last_ts)) / 3600
                if age_h > 4:
                    results.append(CheckResult("readings_global", CRITICAL,
                        f"Runner nie generuje odczytów od {age_h:.1f} godzin",
                        f"Ostatni odczyt: {datetime.fromtimestamp(int(last_ts), tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"))
                elif age_h > 2:
                    results.append(CheckResult("readings_global", WARNING,
                        f"Odczyty nie były generowane od {age_h:.1f} godzin",
                        f"Ostatni odczyt: {datetime.fromtimestamp(int(last_ts), tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"))
                else:
                    results.append(CheckResult("readings_global", OK,
                        f"Odczyty: świeże (ostatni {age_h*60:.0f} min temu)", ""))

            # Per-user freshness
            if _table_exists(c, "wg_users"):
                users = c.execute("SELECT phone FROM wg_users WHERE enabled=1").fetchall()
                stale_users = []
                for (phone,) in users:
                    urow = c.execute(
                        "SELECT MAX(ts) FROM readings WHERE phone=?", (phone,)
                    ).fetchone()
                    u_last = urow[0] if urow and urow[0] else None
                    if u_last is None:
                        stale_users.append((phone, None))
                    else:
                        age_h = (now_ts - int(u_last)) / 3600
                        if age_h > 3:
                            stale_users.append((phone, age_h))
                if stale_users:
                    msgs = []
                    for phone, age in stale_users:
                        if age is None:
                            msgs.append(f"{phone}: brak odczytów")
                        else:
                            msgs.append(f"{phone}: brak od {age:.1f}h")
                    results.append(CheckResult("readings_per_user", WARNING,
                        f"Brak odczytów dla {len(stale_users)} użytkownika(-ów) >3h",
                        "\n".join(msgs)))
                elif verbose:
                    results.append(CheckResult("readings_per_user", OK,
                        f"Odczyty per użytkownik: wszystkie świeże ({len(users)} aktywnych)", ""))

        finally:
            c.close()
    except Exception as e:
        results.append(CheckResult("readings_global", CRITICAL,
            f"Błąd odczytu tabeli readings: {e}", ""))

    return results


# ── B) Service health ─────────────────────────────────────────────────────────

def check_service_health(db_path: str, verbose: bool) -> List[CheckResult]:
    results = []

    # Zdrowa gunicorn: try HTTP GET to localhost:5070
    gunicorn_ok = False
    gunicorn_detail = ""
    try:
        req = urllib.request.Request("http://127.0.0.1:5070/", method="GET")
        req.add_header("Host", "localhost")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status in (200, 301, 302, 403):
                gunicorn_ok = True
                gunicorn_detail = f"HTTP {resp.status}"
    except Exception as e:
        gunicorn_detail = str(e)

    # Fallback: check for gunicorn/python process in /proc
    if not gunicorn_ok:
        try:
            out = subprocess.run(
                ["pgrep", "-af", "gunicorn.*zdrowa"],
                capture_output=True, text=True, timeout=5
            ).stdout.strip()
            if out:
                gunicorn_ok = True
                gunicorn_detail = f"process found: {out.splitlines()[0][:80]}"
        except Exception:
            pass

    if gunicorn_ok:
        results.append(CheckResult("zdrowa_service", OK,
            f"Zdrowa: działa ({gunicorn_detail})", ""))
    else:
        results.append(CheckResult("zdrowa_service", CRITICAL,
            "Zdrowa (gunicorn) nie odpowiada",
            f"HTTP na localhost:5070 nie powiódł się: {gunicorn_detail}"))

    # Cron service
    try:
        ret = subprocess.run(
            ["systemctl", "is-active", "cron"],
            capture_output=True, text=True, timeout=5
        )
        cron_active = ret.stdout.strip() == "active"
        if not cron_active:
            # try crond
            ret2 = subprocess.run(
                ["systemctl", "is-active", "crond"],
                capture_output=True, text=True, timeout=5
            )
            cron_active = ret2.stdout.strip() == "active"
        if cron_active:
            results.append(CheckResult("cron_service", OK, "Cron: aktywny", ""))
        else:
            results.append(CheckResult("cron_service", CRITICAL,
                "Cron service nie jest aktywny",
                "Uruchom: systemctl start cron"))
    except FileNotFoundError:
        # systemctl not available (dev environment)
        results.append(CheckResult("cron_service", OK,
            "Cron: systemctl niedostępny (środowisko dev)", ""))
    except Exception as e:
        results.append(CheckResult("cron_service", WARNING,
            f"Nie można sprawdzić statusu crona: {e}", ""))

    # feedback.db accessibility and writability
    if db_path and os.path.exists(db_path):
        try:
            c = _connect_feedback(db_path)
            try:
                _ensure_watchdog_state(c)
                c.execute(
                    "INSERT OR REPLACE INTO watchdog_state (check_name, status, last_alert_at, message) "
                    "VALUES ('heartbeat', 'OK', ?, 'watchdog alive')",
                    (datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),)
                )
                c.commit()
                results.append(CheckResult("db_writable", OK,
                    "feedback.db: dostępna i zapisywalna", ""))
            finally:
                c.close()
        except Exception as e:
            results.append(CheckResult("db_writable", CRITICAL,
                f"feedback.db niedostępna lub tylko do odczytu: {e}", ""))
    elif db_path and not os.path.exists(db_path):
        results.append(CheckResult("db_writable", CRITICAL,
            f"feedback.db nie istnieje: {db_path}", ""))
    else:
        results.append(CheckResult("db_writable", WARNING,
            "WEATHERGUARD_DB nie skonfigurowane", ""))

    # feedback.db file size
    if db_path and os.path.exists(db_path):
        size_mb = os.path.getsize(db_path) / (1024 * 1024)
        if size_mb > 500:
            results.append(CheckResult("db_size", WARNING,
                f"feedback.db rozmiar: {size_mb:.0f} MB (>500 MB)",
                "Rozważ archiwizację lub czyszczenie starych odczytów."))
        elif verbose:
            results.append(CheckResult("db_size", OK,
                f"feedback.db rozmiar: {size_mb:.1f} MB", ""))

    return results


# ── C) API health ─────────────────────────────────────────────────────────────

def check_api_health(db_path: str, verbose: bool) -> List[CheckResult]:
    results = []

    # Anthropic API key
    api_key = getattr(settings, "ANTHROPIC_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        results.append(CheckResult("anthropic_key", WARNING,
            "Klucz ANTHROPIC_API_KEY nie jest skonfigurowany",
            "Funkcje AI (porady, podsumowania ryzyka) są wyłączone."))
    else:
        # Check if any ai_cache entry was written recently (last 3 hours)
        ai_fresh = False
        ai_detail = "brak wpisu w ai_cache w ciągu ostatnich 3h"
        if db_path and os.path.exists(db_path):
            try:
                c = _connect_feedback(db_path)
                try:
                    if _table_exists(c, "ai_cache"):
                        cutoff = (datetime.now(tz=timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S")
                        row = c.execute(
                            "SELECT cache_key, created_at FROM ai_cache WHERE created_at >= ? LIMIT 1",
                            (cutoff,)
                        ).fetchone()
                        if row:
                            ai_fresh = True
                            ai_detail = f"ostatni wpis: {row[1]}"
                finally:
                    c.close()
            except Exception:
                pass

        if ai_fresh or verbose:
            results.append(CheckResult("anthropic_api", OK,
                f"Anthropic API: skonfigurowany ({ai_detail})", ""))
        elif not ai_fresh:
            # Key is set but no recent cache — try a minimal API call
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=api_key)
                msg = client.messages.create(
                    model=getattr(settings, "CLAUDE_MODEL_FAST", "claude-haiku-4-5-20251001"),
                    max_tokens=1,
                    messages=[{"role": "user", "content": "1"}],
                )
                results.append(CheckResult("anthropic_api", OK,
                    "Anthropic API: klucz działa (test OK)", ""))
            except Exception as e:
                err = str(e)
                level = CRITICAL if "authentication" in err.lower() or "invalid" in err.lower() else WARNING
                results.append(CheckResult("anthropic_api", level,
                    f"Anthropic API: błąd testu ({err[:100]})", ""))

    # VAPID keys
    vapid_priv = getattr(settings, "VAPID_PRIVATE_KEY", "")
    vapid_pub  = getattr(settings, "VAPID_PUBLIC_KEY", "")
    if vapid_priv and vapid_pub:
        results.append(CheckResult("vapid_keys", OK,
            "VAPID keys: skonfigurowane (push notifications aktywne)", ""))
    else:
        missing = []
        if not vapid_priv: missing.append("VAPID_PRIVATE_KEY")
        if not vapid_pub:  missing.append("VAPID_PUBLIC_KEY")
        results.append(CheckResult("vapid_keys", WARNING,
            f"Push notifications wyłączone — brak: {', '.join(missing)}", ""))

    # SMTP connection test (EHLO only, no auth, no email sent)
    smtp_host = getattr(settings, "EMAIL_HOST", "")
    smtp_port = getattr(settings, "EMAIL_PORT", 587)
    if smtp_host:
        try:
            sock = socket.create_connection((smtp_host, smtp_port), timeout=8)
            banner = sock.recv(256).decode("ascii", errors="ignore").strip()
            sock.sendall(b"EHLO watchdog.health.guard\r\n")
            time.sleep(0.3)
            reply = sock.recv(512).decode("ascii", errors="ignore").strip()
            sock.sendall(b"QUIT\r\n")
            sock.close()
            results.append(CheckResult("smtp", OK,
                f"SMTP: połączono z {smtp_host}:{smtp_port}", ""))
        except Exception as e:
            results.append(CheckResult("smtp", WARNING,
                f"SMTP: nie można połączyć z {smtp_host}:{smtp_port} — {e}",
                "Weryfikacja e-mail i alerty mogą nie działać."))
    else:
        results.append(CheckResult("smtp", WARNING,
            "EMAIL_HOST nie skonfigurowany", ""))

    return results


# ── D) Error detection ────────────────────────────────────────────────────────

_ERROR_PATTERNS = re.compile(
    r"(traceback|error:|exception:|permission denied|errno|critical|failed|no such file)",
    re.IGNORECASE,
)


def _parse_log_tail(path: str, lines: int = 100) -> Tuple[int, List[str]]:
    """Return (error_count, list_of_error_lines) from the last N lines."""
    if not os.path.exists(path):
        return 0, []
    try:
        size = os.path.getsize(path)
        chunk = min(size, lines * 200)
        with open(path, "rb") as f:
            f.seek(max(0, size - chunk))
            raw = f.read().decode("utf-8", errors="replace")
        tail = raw.splitlines()[-lines:]
        errors = [l for l in tail if _ERROR_PATTERNS.search(l)]
        return len(errors), errors[:10]
    except Exception:
        return 0, []


def check_error_detection(verbose: bool) -> List[CheckResult]:
    results = []

    for name, path in LOGS.items():
        err_count, err_lines = _parse_log_tail(path, 100)
        if not os.path.exists(path):
            if verbose:
                results.append(CheckResult(f"log_{name}", OK,
                    f"Log {name}: plik nie istnieje jeszcze", ""))
            continue

        age_h = (time.time() - os.path.getmtime(path)) / 3600
        size_mb = os.path.getsize(path) / (1024 * 1024)

        if err_count > 10:
            results.append(CheckResult(f"log_{name}", WARNING,
                f"Log {name}: {err_count} błędów w ostatnich 100 liniach",
                "\n".join(err_lines[:5])))
        elif err_count > 0 and verbose:
            results.append(CheckResult(f"log_{name}", OK,
                f"Log {name}: {err_count} drobnych błędów, {size_mb:.1f} MB",
                "\n".join(err_lines[:3])))
        elif verbose:
            results.append(CheckResult(f"log_{name}", OK,
                f"Log {name}: bez błędów ({size_mb:.1f} MB)", ""))

        # Oversized log
        if size_mb > 100:
            results.append(CheckResult(f"log_{name}_size", WARNING,
                f"Log {name}: {size_mb:.0f} MB (>100 MB) — rozważ rotację", ""))

    # ML retrain specific check: look for recent errors
    ml_path = LOGS["ml"]
    if os.path.exists(ml_path):
        _, ml_errors = _parse_log_tail(ml_path, 50)
        if ml_errors:
            results.append(CheckResult("ml_retrain_errors", WARNING,
                f"Błędy w logu ML retrain ({len(ml_errors)} linii)",
                "\n".join(ml_errors[:5])))

    return results


# ── E) Disk and system ────────────────────────────────────────────────────────

def check_disk_and_system(db_path: str, verbose: bool) -> List[CheckResult]:
    results = []

    # Disk usage of /opt
    try:
        usage = shutil.disk_usage("/opt")
        pct = usage.used / usage.total * 100
        free_gb = usage.free / (1024 ** 3)
        if pct > 90:
            results.append(CheckResult("disk_opt", CRITICAL,
                f"Dysk /opt: {pct:.0f}% zajęty (wolne: {free_gb:.1f} GB)",
                "Natychmiast zwolnij miejsce — grozi zatrzymaniem usług."))
        elif pct > 85:
            results.append(CheckResult("disk_opt", WARNING,
                f"Dysk /opt: {pct:.0f}% zajęty (wolne: {free_gb:.1f} GB)",
                "Rozważ wyczyszczenie logów lub starych danych."))
        elif verbose:
            results.append(CheckResult("disk_opt", OK,
                f"Dysk /opt: {pct:.0f}% zajęty (wolne: {free_gb:.1f} GB)", ""))
    except Exception as e:
        results.append(CheckResult("disk_opt", WARNING,
            f"Nie można sprawdzić dysku /opt: {e}", ""))

    # RAM: read /proc/meminfo
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mem[parts[0].rstrip(":")] = int(parts[1])
        avail_mb = mem.get("MemAvailable", mem.get("MemFree", 0)) // 1024
        total_mb = mem.get("MemTotal", 0) // 1024
        if avail_mb < 50:
            results.append(CheckResult("ram", CRITICAL,
                f"RAM: tylko {avail_mb} MB dostępne z {total_mb} MB",
                "Krytycznie mało pamięci — serwer może nie działać stabilnie."))
        elif avail_mb < 100:
            results.append(CheckResult("ram", WARNING,
                f"RAM: {avail_mb} MB dostępne z {total_mb} MB",
                "Mało pamięci — rozważ restart usług lub upgrade RAM."))
        elif verbose:
            results.append(CheckResult("ram", OK,
                f"RAM: {avail_mb} MB dostępne z {total_mb} MB", ""))
    except Exception as e:
        if verbose:
            results.append(CheckResult("ram", OK,
                f"RAM: nie można odczytać /proc/meminfo ({e})", ""))

    # feedback.db WAL file size
    if db_path:
        wal_path = db_path + "-wal"
        if os.path.exists(wal_path):
            wal_mb = os.path.getsize(wal_path) / (1024 * 1024)
            if wal_mb > 50:
                results.append(CheckResult("db_wal", WARNING,
                    f"feedback.db WAL: {wal_mb:.0f} MB (>50 MB) — checkpoint nie działa",
                    "Uruchomiono PRAGMA wal_checkpoint(TRUNCATE)."))
            elif verbose:
                results.append(CheckResult("db_wal", OK,
                    f"feedback.db WAL: {wal_mb:.1f} MB", ""))
        elif verbose:
            results.append(CheckResult("db_wal", OK,
                "feedback.db WAL: brak pliku (baza normalnie zamknięta)", ""))

    return results


# ── F) Data integrity ─────────────────────────────────────────────────────────

def check_data_integrity(db_path: str, verbose: bool) -> List[CheckResult]:
    results = []
    if not db_path or not os.path.exists(db_path):
        return results

    try:
        from django.contrib.auth.models import User as DjangoUser
        django_users = DjangoUser.objects.count()
    except Exception:
        django_users = None

    try:
        c = _connect_feedback(db_path)
        try:
            # wg_users vs Django users sync
            if _table_exists(c, "wg_users") and django_users is not None:
                wg_count = c.execute("SELECT COUNT(*) FROM wg_users WHERE enabled=1").fetchone()[0]
                if abs(wg_count - django_users) > 2:
                    results.append(CheckResult("user_sync", WARNING,
                        f"Rozbieżność użytkowników: wg_users={wg_count}, Django auth_user={django_users}",
                        "Sprawdź czy migracje zostały zastosowane i czy import użytkowników przebiegł poprawnie."))
                elif verbose:
                    results.append(CheckResult("user_sync", OK,
                        f"Użytkownicy: wg_users={wg_count}, Django={django_users} (OK)", ""))

            # Push subscriptions
            if _table_exists(c, "push_subscriptions") and _table_exists(c, "wg_users"):
                sub_count = c.execute("SELECT COUNT(DISTINCT phone) FROM push_subscriptions").fetchone()[0]
                user_count = c.execute("SELECT COUNT(*) FROM wg_users WHERE enabled=1").fetchone()[0]
                if user_count > 0 and sub_count == 0:
                    results.append(CheckResult("push_subs", WARNING,
                        f"Brak subskrypcji push dla żadnego z {user_count} aktywnych użytkowników",
                        "Użytkownicy mogą nie otrzymywać powiadomień."))
                elif verbose:
                    results.append(CheckResult("push_subs", OK,
                        f"Subskrypcje push: {sub_count}/{user_count} użytkowników", ""))

            # Daily tips generated today
            today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
            if _table_exists(c, "daily_tips"):
                tips_today = c.execute(
                    "SELECT COUNT(*) FROM daily_tips WHERE day=?", (today,)
                ).fetchone()[0]
                if tips_today == 0:
                    results.append(CheckResult("daily_tips", WARNING,
                        f"Porady dzienne nie zostały wygenerowane na dziś ({today})",
                        "Sprawdź cron job generate_daily_tips (06:00)."))
                elif verbose:
                    results.append(CheckResult("daily_tips", OK,
                        f"Porady dzienne: {tips_today} wpisów na {today}", ""))
            else:
                results.append(CheckResult("daily_tips", WARNING,
                    "Tabela daily_tips nie istnieje",
                    "Uruchom: python manage.py generate_daily_tips"))

            # Readings today count
            today_start = int(datetime.now(tz=timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).timestamp())
            if _table_exists(c, "readings"):
                readings_today = c.execute(
                    "SELECT COUNT(*) FROM readings WHERE ts >= ?", (today_start,)
                ).fetchone()[0]
                if verbose:
                    results.append(CheckResult("readings_today", OK,
                        f"Odczyty dzisiaj: {readings_today}", ""))

        finally:
            c.close()
    except Exception as e:
        results.append(CheckResult("data_integrity", WARNING,
            f"Błąd sprawdzania integralności danych: {e}", ""))

    return results


# ── Quick stats for email body ────────────────────────────────────────────────

def _gather_quick_stats(db_path: str) -> dict:
    stats = {
        "active_users": "?",
        "readings_today": "?",
        "last_runner": "?",
        "disk_pct": "?",
    }
    try:
        usage = shutil.disk_usage("/opt")
        stats["disk_pct"] = f"{usage.used / usage.total * 100:.0f}%"
    except Exception:
        pass

    if not db_path or not os.path.exists(db_path):
        return stats

    try:
        c = _connect_feedback(db_path)
        try:
            if _table_exists(c, "wg_users"):
                stats["active_users"] = c.execute(
                    "SELECT COUNT(*) FROM wg_users WHERE enabled=1"
                ).fetchone()[0]

            if _table_exists(c, "readings"):
                today_start = int(datetime.now(tz=timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ).timestamp())
                stats["readings_today"] = c.execute(
                    "SELECT COUNT(*) FROM readings WHERE ts >= ?", (today_start,)
                ).fetchone()[0]

                row = c.execute("SELECT MAX(ts) FROM readings").fetchone()
                if row and row[0]:
                    dt = datetime.fromtimestamp(int(row[0]), tz=timezone.utc)
                    stats["last_runner"] = dt.strftime("%Y-%m-%d %H:%M UTC")
        finally:
            c.close()
    except Exception:
        pass

    return stats


# ── Self-healing ──────────────────────────────────────────────────────────────

def run_self_healing(db_path: str, results: List[CheckResult], log) -> None:
    """Apply safe automatic fixes."""

    # WAL checkpoint if >50 MB
    if db_path and os.path.exists(db_path + "-wal"):
        wal_mb = os.path.getsize(db_path + "-wal") / (1024 * 1024)
        if wal_mb > 50:
            try:
                c = sqlite3.connect(db_path)
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                c.close()
                log(f"  [self-heal] WAL checkpoint(TRUNCATE) wykonany ({wal_mb:.0f} MB)")
            except Exception as e:
                log(f"  [self-heal] WAL checkpoint nie powiódł się: {e}")

    # Truncate oversized logs (>100 MB → keep last 10 000 lines)
    for name, path in LOGS.items():
        if os.path.exists(path):
            size_mb = os.path.getsize(path) / (1024 * 1024)
            if size_mb > 100:
                try:
                    with open(path, "rb") as f:
                        f.seek(max(0, os.path.getsize(path) - 2_000_000))
                        tail = f.read().decode("utf-8", errors="replace")
                    lines = tail.splitlines()[-10_000:]
                    with open(path, "w") as f:
                        f.write("\n".join(lines) + "\n")
                    log(f"  [self-heal] Log {name} skrócony ({size_mb:.0f} MB → ~{len(lines)} linii)")
                except Exception as e:
                    log(f"  [self-heal] Skracanie logu {name} nie powiodło się: {e}")

    # Fix permissions on feedback.db if unreadable
    if db_path and os.path.exists(db_path) and not os.access(db_path, os.W_OK):
        try:
            os.chmod(db_path, 0o664)
            log(f"  [self-heal] Uprawnienia feedback.db naprawione (chmod 664)")
        except Exception as e:
            log(f"  [self-heal] Nie można naprawić uprawnień feedback.db: {e}")


# ── Email builder ─────────────────────────────────────────────────────────────

def _build_email_html(
    overall_level: str,
    results: List[CheckResult],
    stats: dict,
    run_ts: str,
    is_daily: bool = False,
) -> Tuple[str, str]:
    """Return (subject, html_body)."""

    non_ok = [r for r in results if r.level != OK]
    criticals = [r for r in non_ok if r.level == CRITICAL]
    warnings  = [r for r in non_ok if r.level == WARNING]

    if is_daily:
        subject = f"✅ Health Guard: Raport dzienny — {run_ts[:10]}"
        icon = "✅"
    elif overall_level == CRITICAL:
        first = criticals[0].message[:60] if criticals else "problem krytyczny"
        subject = f"🔴 Health Guard: CRITICAL — {first}"
        icon = "🔴"
    else:
        subject = f"🟡 Health Guard: WARNING — {len(warnings)} ostrzeżeń"
        icon = "🟡"

    level_badge = {
        OK:       '<span style="color:#16a34a;font-weight:bold">✅ OK</span>',
        WARNING:  '<span style="color:#d97706;font-weight:bold">🟡 WARNING</span>',
        CRITICAL: '<span style="color:#dc2626;font-weight:bold">🔴 CRITICAL</span>',
    }

    rows = ""
    for r in results:
        detail_html = f'<br><small style="color:#555">{r.details.replace(chr(10),"<br>")}</small>' if r.details else ""
        rows += f"""
        <tr>
          <td style="padding:6px 10px;border-bottom:1px solid #eee">{r.icon} <b>{r.name}</b></td>
          <td style="padding:6px 10px;border-bottom:1px solid #eee">{level_badge[r.level]}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #eee">{r.message}{detail_html}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;font-size:14px;color:#222;max-width:800px;margin:auto">
<h2 style="background:#1e293b;color:#fff;padding:16px 20px;border-radius:6px 6px 0 0;margin:0">
  {icon} Health Guard Watchdog — {'Raport dzienny' if is_daily else overall_level}
</h2>
<div style="background:#f8fafc;padding:12px 20px;border:1px solid #e2e8f0;font-size:13px">
  <b>Czas:</b> {run_ts} &nbsp;|&nbsp;
  <b>Aktywni użytkownicy:</b> {stats['active_users']} &nbsp;|&nbsp;
  <b>Odczyty dziś:</b> {stats['readings_today']} &nbsp;|&nbsp;
  <b>Ostatni runner:</b> {stats['last_runner']} &nbsp;|&nbsp;
  <b>Dysk:</b> {stats['disk_pct']}
</div>
{"" if not criticals else f'<div style="background:#fef2f2;border-left:4px solid #dc2626;padding:10px 16px;margin:12px 0"><b>🔴 CRITICAL ({len(criticals)}):</b> ' + "; ".join(r.message[:80] for r in criticals) + "</div>"}
{"" if not warnings else f'<div style="background:#fffbeb;border-left:4px solid #d97706;padding:10px 16px;margin:12px 0"><b>🟡 WARNING ({len(warnings)}):</b> ' + "; ".join(r.message[:80] for r in warnings) + "</div>"}
<table width="100%" cellspacing="0" style="border-collapse:collapse;border:1px solid #e2e8f0;margin-top:12px">
  <thead>
    <tr style="background:#1e293b;color:#fff">
      <th style="padding:8px 10px;text-align:left;width:25%">Sprawdzenie</th>
      <th style="padding:8px 10px;text-align:left;width:15%">Status</th>
      <th style="padding:8px 10px;text-align:left">Szczegóły</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
<p style="color:#888;font-size:12px;margin-top:16px">
  Ten e-mail został wygenerowany automatycznie przez Health Guard Watchdog.<br>
  Aby wyłączyć powiadomienia, skonfiguruj ADMIN_NOTIFY_EMAIL w .env.
</p>
</body></html>"""

    return subject, html


# ── Email dispatch ────────────────────────────────────────────────────────────

def send_watchdog_email(subject: str, html: str, log) -> bool:
    try:
        from django.core.mail import EmailMessage
        admin_email = getattr(settings, "ADMIN_NOTIFY_EMAIL", "")
        if not admin_email:
            log("  [email] ADMIN_NOTIFY_EMAIL nie skonfigurowany — pomijam")
            return False
        msg = EmailMessage(
            subject=subject,
            body=html,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "Health Guard <no-reply@pracunia.pl>"),
            to=[admin_email],
        )
        msg.content_subtype = "html"
        msg.send()
        log(f"  [email] Wysłano: {subject}")
        return True
    except Exception as e:
        log(f"  [email] Błąd wysyłania: {e}")
        return False


# ── Main command ──────────────────────────────────────────────────────────────

class Command(BaseCommand):
    help = "Health Guard system watchdog — sprawdza usługi, dane, API i wysyła alerty e-mail."

    def add_arguments(self, parser):
        parser.add_argument(
            "--daily-summary",
            action="store_true",
            default=False,
            help="Wyślij dzienny raport e-mail niezależnie od statusu.",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            default=False,
            help="Pokaż szczegółowe wyniki każdego sprawdzenia.",
        )

    def handle(self, *args, **options):
        db_path = getattr(settings, "WEATHERGUARD_DB", "")
        is_daily = options["daily_summary"]
        verbose  = options["verbose"]
        run_ts   = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        def log(msg):
            self.stdout.write(msg)

        log(f"\n{C_BOLD}Health Guard Watchdog — {run_ts}{C_RESET}\n")

        # ── Run all checks ─────────────────────────────────────────────────
        all_results: List[CheckResult] = []

        log("A) Świeżość danych...")
        all_results += check_data_freshness(db_path, verbose)

        log("B) Status usług...")
        all_results += check_service_health(db_path, verbose)

        log("C) Stan API...")
        all_results += check_api_health(db_path, verbose)

        log("D) Analiza logów...")
        all_results += check_error_detection(verbose)

        log("E) Dysk i system...")
        all_results += check_disk_and_system(db_path, verbose)

        log("F) Integralność danych...")
        all_results += check_data_integrity(db_path, verbose)

        # ── Self-healing ───────────────────────────────────────────────────
        run_self_healing(db_path, all_results, log)

        # ── CLI output ─────────────────────────────────────────────────────
        log("")
        for r in all_results:
            color = r.color
            line = f"  {r.icon} {r.name}: {r.message}"
            if r.details and verbose:
                line += f"\n      {r.details[:200]}"
            self.stdout.write(f"{color}{line}{C_RESET}")

        criticals = [r for r in all_results if r.level == CRITICAL]
        warnings  = [r for r in all_results if r.level == WARNING]
        overall   = CRITICAL if criticals else (WARNING if warnings else OK)

        log(f"\n{C_BOLD}----{C_RESET}")
        status_color = {OK: C_GREEN, WARNING: C_YELLOW, CRITICAL: C_RED}[overall]
        log(f"{status_color}{C_BOLD}Status: {len(warnings)} WARNING, {len(criticals)} CRITICAL{C_RESET}\n")

        # ── Email decision ─────────────────────────────────────────────────
        stats = _gather_quick_stats(db_path)

        if is_daily:
            # Always send daily summary
            subject, html = _build_email_html(overall, all_results, stats, run_ts, is_daily=True)
            send_watchdog_email(subject, html, log)
            _set_state(db_path, "daily_summary", overall, f"Raport dzienny wysłany {run_ts}")

        elif overall == CRITICAL:
            # Collect CRITICAL alerts that need sending (cooldown-aware)
            to_alert = [r for r in criticals if _should_send_alert(db_path, r.name, CRITICAL)]
            if to_alert:
                subject, html = _build_email_html(CRITICAL, all_results, stats, run_ts)
                if send_watchdog_email(subject, html, log):
                    for r in to_alert:
                        _set_state(db_path, r.name, CRITICAL, r.message)
            else:
                log("  [email] CRITICAL cooldown aktywny — pomijam e-mail")

        elif overall == WARNING:
            # Collect WARNING alerts that need sending (cooldown-aware)
            to_alert = [r for r in warnings if _should_send_alert(db_path, r.name, WARNING)]
            if to_alert:
                subject, html = _build_email_html(WARNING, all_results, stats, run_ts)
                if send_watchdog_email(subject, html, log):
                    for r in to_alert:
                        _set_state(db_path, r.name, WARNING, r.message)
            else:
                log("  [email] WARNING cooldown aktywny — pomijam e-mail")

        else:
            # All OK — update heartbeat state
            log("  Wszystko OK — e-mail nie jest wysyłany")
            # Clear old CRITICAL/WARNING states so they re-alert after recovery
            for r in all_results:
                prev = _get_state(db_path, r.name)
                if prev and prev["status"] in (CRITICAL, WARNING):
                    _set_state(db_path, r.name, OK, "resolved")
