import os
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Set

def _connect_feedback(db_path: str) -> sqlite3.Connection:
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    return c

def _table_exists(c: sqlite3.Connection, name: str) -> bool:
    row = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return bool(row)

def _cols(c: sqlite3.Connection, table: str) -> List[str]:
    try:
        rows = c.execute(f"PRAGMA table_info({table})").fetchall()
        return [r[1] for r in rows]
    except Exception:
        return []

def _utc_dt(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def readings_last_days(db_path: str, phone: str, profile: str, days: int = 7, limit: int = 2000) -> List[Dict[str, Any]]:
    if not os.path.exists(db_path):
        return []
    since = int((datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp())
    c = _connect_feedback(db_path)
    try:
        if not _table_exists(c, "readings"):
            return []
        cols = set(_cols(c, "readings"))
        base_cols = ["ts", "score"]
        opt = [k for k in ["value", "risk", "threshold", "details", "meta"] if k in cols]
        sel = base_cols + opt
        q = f"SELECT {', '.join(sel)} FROM readings WHERE phone=? AND profile=? AND ts>=? ORDER BY ts DESC LIMIT ?"
        rows = c.execute(q, (phone, profile, since, limit)).fetchall()
        out = []
        for r in rows:
            d = {k: r[k] for k in sel if k in r.keys()}
            d["dt"] = _utc_dt(d.get("ts") or 0)
            out.append(d)
        return out
    except Exception:
        return []
    finally:
        c.close()

def alerts_last_days(db_path: str, phone: str, profile: Optional[str] = None, days: int = 7, limit: int = 1000) -> List[Dict[str, Any]]:
    if not os.path.exists(db_path):
        return []
    since = int((datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp())
    c = _connect_feedback(db_path)
    try:
        if not _table_exists(c, "alerts"):
            return []
        cols = set(_cols(c, "alerts"))
        base_cols = ["ts", "score"]
        if "profile" in cols:
            base_cols.insert(1, "profile")
        opt = [k for k in ["value", "risk", "threshold", "details", "message", "meta"] if k in cols]
        sel = base_cols + opt
        if profile and "profile" in cols:
            q = f"SELECT {', '.join(sel)} FROM alerts WHERE phone=? AND profile=? AND ts>=? ORDER BY ts DESC LIMIT ?"
            rows = c.execute(q, (phone, profile, since, limit)).fetchall()
        else:
            q = f"SELECT {', '.join(sel)} FROM alerts WHERE phone=? AND ts>=? ORDER BY ts DESC LIMIT ?"
            rows = c.execute(q, (phone, since, limit)).fetchall()
        out = []
        for r in rows:
            d = {k: r[k] for k in sel if k in r.keys()}
            d["dt"] = _utc_dt(d.get("ts") or 0)
            out.append(d)
        return out
    except Exception:
        return []
    finally:
        c.close()

def available_profiles(db_path: str, phone: str, days: int = 30) -> List[str]:
    if not os.path.exists(db_path):
        return []
    since = int((datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp())
    c = _connect_feedback(db_path)
    profs: Set[str] = set()
    try:
        if _table_exists(c, "readings"):
            try:
                rows = c.execute("SELECT DISTINCT profile FROM readings WHERE phone=? AND ts>=? LIMIT 50", (phone, since)).fetchall()
                for r in rows:
                    if r[0]:
                        profs.add(str(r[0]))
            except Exception:
                pass
        if _table_exists(c, "alerts"):
            try:
                rows = c.execute("SELECT DISTINCT profile FROM alerts WHERE phone=? AND ts>=? LIMIT 50", (phone, since)).fetchall()
                for r in rows:
                    if r[0]:
                        profs.add(str(r[0]))
            except Exception:
                pass
        return sorted(profs)
    except Exception:
        return []
    finally:
        c.close()

def sms_subscription_status(db_path: str, phone: str) -> Optional[bool]:
    try:
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        try:
            row = c.execute("SELECT subscribed FROM sms_users WHERE phone=?", (phone,)).fetchone()
        finally:
            c.close()
        if row is None:
            return None
        return bool(row["subscribed"])
    except Exception:
        return None


def set_sms_subscription(db_path: str, phone: str, subscribed: bool) -> bool:
    try:
        now = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        c = sqlite3.connect(db_path)
        try:
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute(
                """
                INSERT INTO sms_users(phone, subscribed, factors_json, created_at, updated_at, last_interaction_at)
                VALUES (?, ?, '{}', ?, ?, ?)
                ON CONFLICT(phone) DO UPDATE SET
                    subscribed          = excluded.subscribed,
                    updated_at          = excluded.updated_at,
                    last_interaction_at = excluded.last_interaction_at
                """,
                (phone, 1 if subscribed else 0, now, now, now),
            )
            c.commit()
        finally:
            c.close()
        return True
    except Exception:
        return False

def list_trend_files(trends_dir: str, phone: str, limit: int = 20) -> List[str]:
    import glob
    import os as _os
    phone_digits = phone.replace("+", "").replace(" ", "")
    patt = _os.path.join(trends_dir, f"*{phone_digits}*.png")
    files = glob.glob(patt)
    files.sort(key=lambda p: _os.path.getmtime(p), reverse=True)
    return files[:limit]


def write_wg_user(
    db_path: str,
    phone: str,
    profiles: list,
    location: str,
    threshold=None,
    quiet_hours=None,
    enabled: bool = True,
) -> bool:
    """Upsert a user's WeatherGuard runner config into the wg_users table in feedback.db."""
    try:
        import json as _json
        now = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        c = sqlite3.connect(db_path)
        try:
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute(
                """
                INSERT INTO wg_users(phone, profiles_json, location, threshold, quiet_hours, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(phone) DO UPDATE SET
                    profiles_json = excluded.profiles_json,
                    location      = excluded.location,
                    threshold     = excluded.threshold,
                    quiet_hours   = excluded.quiet_hours,
                    enabled       = excluded.enabled,
                    updated_at    = excluded.updated_at
                """,
                (
                    phone,
                    _json.dumps(profiles or ["migraine"], ensure_ascii=False),
                    location or "",
                    threshold if threshold is not None else None,
                    quiet_hours or None,
                    1 if enabled else 0,
                    now,
                ),
            )
            c.commit()
        finally:
            c.close()
        return True
    except Exception:
        return False


def write_wellbeing(
    db_path: str,
    phone: str,
    day: str,
    stress_1_10: Optional[int] = None,
    exercise_1_10: Optional[int] = None,
) -> bool:
    """Upsert a user's daily wellbeing into the wellbeing table in feedback.db."""
    try:
        now = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        c = sqlite3.connect(db_path)
        try:
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute(
                """
                INSERT INTO wellbeing(phone, day, stress_1_10, exercise_1_10, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(phone, day) DO UPDATE SET
                    stress_1_10   = excluded.stress_1_10,
                    exercise_1_10 = excluded.exercise_1_10,
                    updated_at    = excluded.updated_at
                """,
                (phone, day, stress_1_10, exercise_1_10, now),
            )
            c.commit()
        finally:
            c.close()
        return True
    except Exception:
        return False


def last_readings(db_path: str, phone: str, profile: str, limit: int = 80):
    """Backward-compatible helper for older portal/views.py.
    Returns list of dicts with keys: ts, dt, score.
    """
    try:
        rows = readings_last_days(db_path, phone, profile, days=3650, limit=limit)
        out = []
        for r in rows[:limit]:
            out.append({
                "ts": int(r.get("ts") or 0),
                "dt": r.get("dt"),
                "score": r.get("score"),
            })
        return out
    except Exception:
        return []
