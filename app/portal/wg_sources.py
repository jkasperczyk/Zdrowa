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
    """Creates the sms_users table if it doesn't exist yet (Health_Guard may not have run yet)."""
    try:
        now = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        c = sqlite3.connect(db_path)
        try:
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS sms_users (
                    phone               TEXT PRIMARY KEY,
                    subscribed          INTEGER NOT NULL DEFAULT 1,
                    factors_json        TEXT,
                    created_at          TEXT,
                    updated_at          TEXT,
                    last_interaction_at TEXT
                )
                """
            )
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
    """Upsert a user's WeatherGuard runner config into the wg_users table in feedback.db.
    Creates the table if it doesn't exist yet (Health_Guard may not have run yet)."""
    try:
        import json as _json
        now = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        c = sqlite3.connect(db_path)
        try:
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS wg_users (
                    phone         TEXT PRIMARY KEY,
                    profiles_json TEXT NOT NULL DEFAULT '["migraine"]',
                    location      TEXT NOT NULL DEFAULT '',
                    threshold     INTEGER,
                    quiet_hours   TEXT,
                    enabled       INTEGER NOT NULL DEFAULT 1,
                    updated_at    TEXT NOT NULL DEFAULT ''
                )
                """
            )
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
    """Upsert a user's daily wellbeing into the wellbeing table in feedback.db.
    Creates the table if it doesn't exist yet (Health_Guard may not have run yet)."""
    try:
        now = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        c = sqlite3.connect(db_path)
        try:
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS wellbeing (
                    phone         TEXT NOT NULL,
                    day           TEXT NOT NULL,
                    stress_1_10   INTEGER,
                    exercise_1_10 INTEGER,
                    updated_at    TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (phone, day)
                )
                """
            )
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


def dashboard_summary(db_path: str, phone: str, profiles: List[str]) -> Dict[str, Any]:
    """Returns live data for the dashboard: latest score per profile + env factors.

    Returns:
        scores: dict of profile -> {score, threshold, label, dt, ts, reasons, tier}
        env:    dict of selected env keys from feats_json of the most recent reading
        last_dt: UTC datetime string of the most recent reading, or None
    """
    empty: Dict[str, Any] = {"scores": {}, "env": {}, "last_dt": None}
    if not os.path.exists(db_path) or not profiles:
        return empty
    c = _connect_feedback(db_path)
    try:
        if not _table_exists(c, "readings"):
            return empty
        cols = set(_cols(c, "readings"))
        has_feats = "feats_json" in cols
        has_label = "label" in cols

        scores: Dict[str, Any] = {}
        newest_ts = 0
        newest_feats: Dict[str, Any] = {}

        for profile in profiles:
            sel = ["ts", "score", "threshold"]
            if has_label:
                sel.append("label")
            sel.append("reasons_json")
            if has_feats:
                sel.append("feats_json")
            q = (
                f"SELECT {', '.join(sel)} FROM readings "
                "WHERE phone=? AND profile=? ORDER BY ts DESC LIMIT 1"
            )
            row = c.execute(q, (phone, profile)).fetchone()
            if not row:
                continue
            d = {k: row[k] for k in sel if k in row.keys()}

            try:
                reasons = json.loads(d.get("reasons_json") or "[]") or []
            except Exception:
                reasons = []

            feats: Dict[str, Any] = {}
            if has_feats and d.get("feats_json"):
                try:
                    feats = json.loads(d["feats_json"]) or {}
                except Exception:
                    feats = {}

            ts = int(d.get("ts") or 0)
            if ts > newest_ts:
                newest_ts = ts
                newest_feats = feats

            score = d.get("score") or 0
            th = d.get("threshold") or 60
            if score >= th:
                tier = "red"
            elif score >= int(th * 0.6):
                tier = "orange"
            else:
                tier = "green"

            scores[profile] = {
                "score": score,
                "threshold": th,
                "label": d.get("label") or "",
                "dt": _utc_dt(ts) if ts else None,
                "ts": ts,
                "reasons": reasons[:5],
                "tier": tier,
            }

        env: Dict[str, Any] = {}
        if newest_feats:
            for key in [
                "pressure_delta_3h", "pressure_delta_6h",
                "temp_delta_6h", "humidity_now", "gust_max_6h",
                "aqi_us_max_6h", "pm2_5_max_6h", "pollen_max_6h",
                "google_pollen_max", "google_pollen_type", "google_pollen_category",
                "kp_index", "gios_index_name", "imgw_warning_level",
            ]:
                v = newest_feats.get(key)
                if v is not None:
                    env[key] = v

        return {
            "scores": scores,
            "env": env,
            "last_dt": _utc_dt(newest_ts) if newest_ts else None,
        }
    except Exception:
        return empty
    finally:
        c.close()


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
