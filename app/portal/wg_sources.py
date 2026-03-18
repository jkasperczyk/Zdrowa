import os
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Set


# ── AI cache helpers ──────────────────────────────────────────────────────────

def _ensure_ai_cache_table(c: sqlite3.Connection) -> None:
    c.execute("""
        CREATE TABLE IF NOT EXISTS ai_cache (
            cache_key TEXT PRIMARY KEY,
            response  TEXT,
            model     TEXT,
            created_at TEXT,
            expires_at TEXT
        )
    """)
    c.commit()


def _get_ai_cache(db_path: str, cache_key: str) -> Optional[str]:
    if not db_path or not os.path.exists(db_path):
        return None
    try:
        c = sqlite3.connect(db_path)
        try:
            row = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ai_cache'").fetchone()
            if not row:
                return None
            now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            row = c.execute(
                "SELECT response FROM ai_cache WHERE cache_key=? AND expires_at > ?",
                (cache_key, now),
            ).fetchone()
            return row[0] if row else None
        finally:
            c.close()
    except Exception:
        return None


def _set_ai_cache(db_path: str, cache_key: str, response: str, model: str, ttl_seconds: int) -> None:
    if not db_path:
        return
    try:
        c = sqlite3.connect(db_path)
        try:
            _ensure_ai_cache_table(c)
            now = datetime.now(tz=timezone.utc)
            created_at = now.strftime("%Y-%m-%dT%H:%M:%S")
            expires_at = (
                "2099-12-31T00:00:00"
                if ttl_seconds <= 0
                else (now + timedelta(seconds=ttl_seconds)).strftime("%Y-%m-%dT%H:%M:%S")
            )
            c.execute(
                "INSERT OR REPLACE INTO ai_cache (cache_key, response, model, created_at, expires_at) VALUES (?,?,?,?,?)",
                (cache_key, response, model, created_at, expires_at),
            )
            c.commit()
        finally:
            c.close()
    except Exception:
        pass


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
    use_ml: bool = False,
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
                    updated_at    TEXT NOT NULL DEFAULT '',
                    use_ml        INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            # Additive migration: add use_ml column if not present
            existing_cols = {r[1] for r in c.execute("PRAGMA table_info(wg_users)").fetchall()}
            if "use_ml" not in existing_cols:
                c.execute("ALTER TABLE wg_users ADD COLUMN use_ml INTEGER NOT NULL DEFAULT 0")
            c.execute(
                """
                INSERT INTO wg_users(phone, profiles_json, location, threshold, quiet_hours, enabled, updated_at, use_ml)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(phone) DO UPDATE SET
                    profiles_json = excluded.profiles_json,
                    location      = excluded.location,
                    threshold     = excluded.threshold,
                    quiet_hours   = excluded.quiet_hours,
                    enabled       = excluded.enabled,
                    updated_at    = excluded.updated_at,
                    use_ml        = excluded.use_ml
                """,
                (
                    phone,
                    _json.dumps(profiles or ["migraine"], ensure_ascii=False),
                    location or "",
                    threshold if threshold is not None else None,
                    quiet_hours or None,
                    1 if enabled else 0,
                    now,
                    1 if use_ml else 0,
                ),
            )
            c.commit()
        finally:
            c.close()
        return True
    except Exception:
        return False


def get_ml_status(db_path: str, phone: str, profiles: list) -> Dict[str, Any]:
    """Return ML model status for a user: sample count and model info per profile."""
    result: Dict[str, Any] = {
        "sample_counts": {},
        "models": {},
        "min_samples": 30,
    }
    if not db_path or not os.path.exists(db_path):
        return result
    try:
        c = sqlite3.connect(db_path)
        try:
            # Count positive samples (symptom_log) per profile
            if _table_exists(c, "symptom_log"):
                for profile in profiles:
                    row = c.execute(
                        "SELECT COUNT(*) FROM symptom_log WHERE phone=? AND profile=? AND feats_json IS NOT NULL",
                        (phone, profile),
                    ).fetchone()
                    result["sample_counts"][profile] = row[0] if row else 0
            # Get model info from ml_models
            if _table_exists(c, "ml_models"):
                for profile in profiles:
                    row = c.execute(
                        "SELECT accuracy, f1, feature_importances_json, trained_at, sample_count FROM ml_models WHERE phone=? AND profile=?",
                        (phone, profile),
                    ).fetchone()
                    if row:
                        fi = {}
                        try:
                            fi = json.loads(row[2]) if row[2] else {}
                        except Exception:
                            pass
                        result["models"][profile] = {
                            "accuracy": row[0],
                            "f1": row[1],
                            "feature_importances": fi,
                            "trained_at": row[3],
                            "sample_count": row[4],
                        }
        finally:
            c.close()
    except Exception:
        pass
    return result


def write_wellbeing(
    db_path: str,
    phone: str,
    day: str,
    stress_1_10: Optional[int] = None,
    exercise_1_10: Optional[int] = None,
    sleep_quality_1_10: Optional[int] = None,
    hydration_1_10: Optional[int] = None,
    headache_1_10: Optional[int] = None,
    notes: Optional[str] = None,
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
                    phone              TEXT NOT NULL,
                    day                TEXT NOT NULL,
                    stress_1_10        INTEGER,
                    exercise_1_10      INTEGER,
                    sleep_quality_1_10 INTEGER,
                    hydration_1_10     INTEGER,
                    headache_1_10      INTEGER,
                    updated_at         TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (phone, day)
                )
                """
            )
            # Additive migrations for older tables
            existing_cols = {r[1] for r in c.execute("PRAGMA table_info(wellbeing)").fetchall()}
            for col in ("sleep_quality_1_10", "hydration_1_10", "headache_1_10"):
                if col not in existing_cols:
                    c.execute(f"ALTER TABLE wellbeing ADD COLUMN {col} INTEGER;")
            if "notes" not in existing_cols:
                c.execute("ALTER TABLE wellbeing ADD COLUMN notes TEXT;")

            c.execute(
                """
                INSERT INTO wellbeing(phone, day, stress_1_10, exercise_1_10, sleep_quality_1_10, hydration_1_10, headache_1_10, notes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(phone, day) DO UPDATE SET
                    stress_1_10        = excluded.stress_1_10,
                    exercise_1_10      = excluded.exercise_1_10,
                    sleep_quality_1_10 = excluded.sleep_quality_1_10,
                    hydration_1_10     = excluded.hydration_1_10,
                    headache_1_10      = excluded.headache_1_10,
                    notes              = excluded.notes,
                    updated_at         = excluded.updated_at
                """,
                (phone, day, stress_1_10, exercise_1_10, sleep_quality_1_10, hydration_1_10, headache_1_10, notes, now),
            )
            c.commit()
        finally:
            c.close()
        return True
    except Exception:
        return False


def dashboard_summary(db_path: str, phone: str, profiles: List[str]) -> Dict[str, Any]:
    """Returns live data for the dashboard: latest score per profile + env factors + trend.

    Returns:
        scores: dict of profile -> {score, base_score, threshold, label, dt, ts, reasons, tier,
                                     trend (up/down/stable), modifier_pct}
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
        has_base_score = "base_score" in cols
        has_ml_score = "ml_score" in cols

        scores: Dict[str, Any] = {}
        newest_ts = 0
        newest_feats: Dict[str, Any] = {}
        since_24h = int((datetime.now(tz=timezone.utc) - timedelta(hours=24)).timestamp())

        for profile in profiles:
            sel = ["ts", "score", "threshold"]
            if has_base_score:
                sel.append("base_score")
            if has_ml_score:
                sel.append("ml_score")
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
            base_score_val = d.get("base_score") if has_base_score else None
            if base_score_val is None:
                base_score_val = score  # fallback for old rows without base_score
            base_score_val = int(base_score_val)

            th = d.get("threshold") or 60
            if score >= th:
                tier = "red"
            elif score >= int(th * 0.6):
                tier = "orange"
            else:
                tier = "green"

            # Modifier percentage display and string
            modifier_pct = 0
            modifier_float = 1.0
            if base_score_val > 0:
                modifier_float = score / base_score_val
                if score != base_score_val:
                    modifier_pct = round((modifier_float - 1) * 100)
            modifier_str = f"×{modifier_float:.2f}"

            # Detect if personal (wellbeing) data was present in the last reading
            _wb_keys = ("stress_1_10", "exercise_1_10", "sleep_quality_1_10", "hydration_1_10", "headache_1_10")
            has_personal_data = any(feats.get(k) is not None for k in _wb_keys)

            # Trend: compare current base_score to 24h average base_score
            trend = "stable"
            try:
                base_col = "base_score" if has_base_score else "score"
                avg_row = c.execute(
                    f"SELECT AVG(COALESCE({base_col}, score)) FROM readings "
                    "WHERE phone=? AND profile=? AND ts>=?",
                    (phone, profile, since_24h)
                ).fetchone()
                if avg_row and avg_row[0] is not None and avg_row[0] > 0:
                    avg_24h = float(avg_row[0])
                    ratio = base_score_val / avg_24h
                    if ratio > 1.10:
                        trend = "up"
                    elif ratio < 0.90:
                        trend = "down"
            except Exception:
                trend = "stable"

            ml_score = d.get("ml_score") if has_ml_score else None

            scores[profile] = {
                "score": score,
                "base_score": base_score_val,
                "ml_score": int(ml_score) if ml_score is not None else None,
                "modifier_pct": modifier_pct,
                "modifier_str": modifier_str,
                "has_personal_data": has_personal_data,
                "threshold": th,
                "label": d.get("label") or "",
                "dt": _utc_dt(ts) if ts else None,
                "ts": ts,
                "reasons": reasons[:5],
                "tier": tier,
                "trend": trend,
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


def db_stats(db_path: str) -> Dict[str, Any]:
    """Aggregate stats from feedback.db for admin panel."""
    out: Dict[str, Any] = {
        "total_alerts": 0, "total_readings": 0,
        "total_wg_users": 0, "total_sms_users": 0,
        "alerts_24h": 0, "readings_24h": 0,
        "last_alert_ts": None, "last_reading_ts": None,
        "db_size_mb": 0.0, "db_exists": False,
    }
    if not os.path.exists(db_path):
        return out
    out["db_exists"] = True
    try:
        out["db_size_mb"] = round(os.path.getsize(db_path) / 1024 / 1024, 2)
    except Exception:
        pass
    since_24h = int((datetime.now(tz=timezone.utc) - timedelta(hours=24)).timestamp())
    c = _connect_feedback(db_path)
    try:
        if _table_exists(c, "alerts"):
            out["total_alerts"] = c.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
            out["alerts_24h"] = c.execute("SELECT COUNT(*) FROM alerts WHERE ts>=?", (since_24h,)).fetchone()[0]
            row = c.execute("SELECT MAX(ts) FROM alerts").fetchone()
            if row and row[0]:
                out["last_alert_ts"] = _utc_dt(row[0])
        if _table_exists(c, "readings"):
            out["total_readings"] = c.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
            out["readings_24h"] = c.execute("SELECT COUNT(*) FROM readings WHERE ts>=?", (since_24h,)).fetchone()[0]
            row = c.execute("SELECT MAX(ts) FROM readings").fetchone()
            if row and row[0]:
                out["last_reading_ts"] = _utc_dt(row[0])
        if _table_exists(c, "wg_users"):
            out["total_wg_users"] = c.execute("SELECT COUNT(*) FROM wg_users").fetchone()[0]
        if _table_exists(c, "sms_users"):
            out["total_sms_users"] = c.execute("SELECT COUNT(*) FROM sms_users").fetchone()[0]
    except Exception:
        pass
    finally:
        c.close()
    return out


def all_users_latest_scores(db_path: str) -> Dict[str, List[Dict[str, Any]]]:
    """Most recent reading per (phone, profile). Returns {phone: [{profile, score, label, dt, tier}]}"""
    if not os.path.exists(db_path):
        return {}
    c = _connect_feedback(db_path)
    try:
        if not _table_exists(c, "readings"):
            return {}
        cols = set(_cols(c, "readings"))
        has_label = "label" in cols
        sel = ["phone", "profile", "ts", "score", "threshold"] + (["label"] if has_label else [])
        q = f"""
            SELECT {', '.join(sel)} FROM readings r
            WHERE ts = (SELECT MAX(ts) FROM readings WHERE phone=r.phone AND profile=r.profile)
            ORDER BY phone, profile
        """
        rows = c.execute(q).fetchall()
        result: Dict[str, List] = {}
        for row in rows:
            d = {k: row[k] for k in sel if k in row.keys()}
            score = d.get("score") or 0
            th = d.get("threshold") or 60
            tier = "red" if score >= th else ("amber" if score >= int(th * 0.6) else "green")
            phone = d["phone"]
            if phone not in result:
                result[phone] = []
            result[phone].append({
                "profile": d.get("profile", ""),
                "score": score,
                "label": d.get("label", ""),
                "ts": d.get("ts", 0),
                "dt": _utc_dt(d["ts"]) if d.get("ts") else None,
                "tier": tier,
            })
        return result
    except Exception:
        return {}
    finally:
        c.close()


def users_last_scores(db_path: str, phones: List[str]) -> Dict[str, Dict[str, Any]]:
    """Most recent reading per phone (any profile). Returns {phone: {score, profile, dt, tier}}"""
    if not os.path.exists(db_path) or not phones:
        return {}
    c = _connect_feedback(db_path)
    try:
        if not _table_exists(c, "readings"):
            return {}
        cols = set(_cols(c, "readings"))
        has_profile = "profile" in cols
        result: Dict[str, Dict] = {}
        for phone in phones:
            if has_profile:
                row = c.execute(
                    "SELECT score, profile, ts, threshold FROM readings WHERE phone=? ORDER BY ts DESC LIMIT 1",
                    (phone,)
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT score, ts, threshold FROM readings WHERE phone=? ORDER BY ts DESC LIMIT 1",
                    (phone,)
                ).fetchone()
            if row:
                score = row["score"] or 0
                th = row["threshold"] or 60
                tier = "red" if score >= th else ("amber" if score >= int(th * 0.6) else "green")
                result[phone] = {
                    "score": score,
                    "profile": row["profile"] if has_profile else "",
                    "dt": _utc_dt(row["ts"]) if row["ts"] else None,
                    "tier": tier,
                }
        return result
    except Exception:
        return {}
    finally:
        c.close()


def recent_alerts_all(db_path: str, hours: int = 24, limit: int = 200) -> List[Dict[str, Any]]:
    """Recent alerts across ALL users for admin overview."""
    if not os.path.exists(db_path):
        return []
    since = int((datetime.now(tz=timezone.utc) - timedelta(hours=hours)).timestamp())
    c = _connect_feedback(db_path)
    try:
        if not _table_exists(c, "alerts"):
            return []
        cols = set(_cols(c, "alerts"))
        sel = ["ts", "phone", "score"]
        for k in ["profile", "label", "threshold"]:
            if k in cols:
                sel.append(k)
        q = f"SELECT {', '.join(sel)} FROM alerts WHERE ts>=? ORDER BY ts DESC LIMIT ?"
        rows = c.execute(q, (since, limit)).fetchall()
        out = []
        for r in rows:
            d = {k: r[k] for k in sel if k in r.keys()}
            d["dt"] = _utc_dt(d["ts"]) if d.get("ts") else ""
            out.append(d)
        return out
    except Exception:
        return []
    finally:
        c.close()


def batch_recent_alerts(db_path: str, phones: List[str], days: int = 7, limit_per_user: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch recent alerts for multiple phones in a single query.
    Returns {phone: [alert_dict, ...]} sorted newest-first, capped at limit_per_user."""
    if not os.path.exists(db_path) or not phones:
        return {}
    since = int((datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp())
    placeholders = ",".join("?" * len(phones))
    c = _connect_feedback(db_path)
    try:
        if not _table_exists(c, "alerts"):
            return {}
        cols = set(_cols(c, "alerts"))
        sel = ["ts", "phone", "score"]
        for k in ["profile", "label", "threshold"]:
            if k in cols:
                sel.append(k)
        q = (
            f"SELECT {', '.join(sel)} FROM alerts "
            f"WHERE phone IN ({placeholders}) AND ts>=? "
            "ORDER BY phone, ts DESC"
        )
        rows = c.execute(q, phones + [since]).fetchall()
        result: Dict[str, List] = {p: [] for p in phones}
        for r in rows:
            d = {k: r[k] for k in sel if k in r.keys()}
            d["dt"] = _utc_dt(d["ts"]) if d.get("ts") else ""
            phone = d["phone"]
            if phone in result and len(result[phone]) < limit_per_user:
                result[phone].append(d)
        return result
    except Exception:
        return {}
    finally:
        c.close()


def wellbeing_history(db_path: str, phone: str, days: int = 30) -> List[Dict[str, Any]]:
    """Return wellbeing entries for a phone, newest first, within last N days."""
    if not os.path.exists(db_path):
        return []
    since_day = (datetime.now(tz=timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    c = _connect_feedback(db_path)
    try:
        if not _table_exists(c, "wellbeing"):
            return []
        cols = set(_cols(c, "wellbeing"))
        sel = ["day", "stress_1_10", "exercise_1_10"]
        for col in ("sleep_quality_1_10", "hydration_1_10", "headache_1_10"):
            if col in cols:
                sel.append(col)
        q = f"SELECT {', '.join(sel)} FROM wellbeing WHERE phone=? AND day>=? ORDER BY day DESC"
        rows = c.execute(q, (phone, since_day)).fetchall()
        return [{k: r[k] for k in sel if k in r.keys()} for r in rows]
    except Exception:
        return []
    finally:
        c.close()


def export_user_data(
    db_path: str,
    phone: str,
    profile: str,
    days: int = 90,
) -> List[Dict[str, Any]]:
    """Export unified data for CSV: readings + wellbeing + alerts, date-keyed, newest first."""
    if not os.path.exists(db_path):
        return []
    since = int((datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp())
    since_day = (datetime.now(tz=timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    c = _connect_feedback(db_path)
    try:
        # readings
        readings_map: Dict[str, Dict] = {}
        if _table_exists(c, "readings"):
            cols_r = set(_cols(c, "readings"))
            has_base = "base_score" in cols_r
            base_col = "base_score, " if has_base else ""
            rows = c.execute(
                f"SELECT ts, score, {base_col}threshold, label FROM readings "
                "WHERE phone=? AND profile=? AND ts>=? ORDER BY ts DESC",
                (phone, profile, since)
            ).fetchall()
            for r in rows:
                day = datetime.fromtimestamp(int(r["ts"]), tz=timezone.utc).strftime("%Y-%m-%d")
                if day not in readings_map:
                    readings_map[day] = {
                        "date": day,
                        "score": r["score"],
                        "base_score": r["base_score"] if has_base else r["score"],
                        "threshold": r["threshold"],
                        "label": r["label"] or "",
                        "alert_sent": False,
                    }

        # alerts
        if _table_exists(c, "alerts"):
            rows = c.execute(
                "SELECT ts FROM alerts WHERE phone=? AND profile=? AND ts>=?",
                (phone, profile, since)
            ).fetchall()
            for r in rows:
                day = datetime.fromtimestamp(int(r["ts"]), tz=timezone.utc).strftime("%Y-%m-%d")
                if day in readings_map:
                    readings_map[day]["alert_sent"] = True

        # wellbeing
        wb_map: Dict[str, Dict] = {}
        if _table_exists(c, "wellbeing"):
            cols_wb = set(_cols(c, "wellbeing"))
            sel = ["day", "stress_1_10", "exercise_1_10"]
            for col in ("sleep_quality_1_10", "hydration_1_10", "headache_1_10"):
                if col in cols_wb:
                    sel.append(col)
            rows = c.execute(
                f"SELECT {', '.join(sel)} FROM wellbeing WHERE phone=? AND day>=? ORDER BY day DESC",
                (phone, since_day)
            ).fetchall()
            for r in rows:
                wb_map[r["day"]] = {k: r[k] for k in sel}

        # Merge into unified rows (all dates from readings + wellbeing)
        all_days = sorted(set(list(readings_map.keys()) + list(wb_map.keys())), reverse=True)
        result = []
        for day in all_days:
            row: Dict[str, Any] = {"date": day}
            row.update(readings_map.get(day, {}))
            wb = wb_map.get(day, {})
            for f in ("stress_1_10", "exercise_1_10", "sleep_quality_1_10", "hydration_1_10", "headache_1_10"):
                row[f] = wb.get(f)
            result.append(row)
        return result
    except Exception:
        return []
    finally:
        c.close()


def write_symptom_log(
    db_path: str,
    phone: str,
    profile: str,
    severity_1_10: int,
    notes: Optional[str] = None,
    feats: Optional[Dict[str, Any]] = None,
) -> bool:
    """Record a user-reported symptom entry in feedback.db."""
    try:
        now = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        c = sqlite3.connect(db_path)
        try:
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS symptom_log (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone         TEXT NOT NULL,
                    timestamp     TEXT NOT NULL,
                    profile       TEXT NOT NULL,
                    severity_1_10 INTEGER NOT NULL,
                    notes         TEXT,
                    feats_json    TEXT
                )
                """
            )
            feats_json = json.dumps(feats, ensure_ascii=False) if feats else None
            c.execute(
                "INSERT INTO symptom_log(phone, timestamp, profile, severity_1_10, notes, feats_json) VALUES(?,?,?,?,?,?)",
                (phone, now, profile, severity_1_10, notes or None, feats_json),
            )
            c.commit()
        finally:
            c.close()
        return True
    except Exception:
        return False


# ── Push subscription management ──────────────────────────────────────────────

def save_push_subscription(db_path: str, phone: str, endpoint: str, p256dh: str, auth: str) -> bool:
    try:
        now = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        c = sqlite3.connect(db_path)
        try:
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute("""
                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone TEXT NOT NULL, endpoint TEXT NOT NULL,
                    keys_p256dh TEXT NOT NULL, keys_auth TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(phone, endpoint)
                )""")
            c.execute("""
                INSERT INTO push_subscriptions(phone, endpoint, keys_p256dh, keys_auth, created_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(phone, endpoint) DO UPDATE SET
                    keys_p256dh=excluded.keys_p256dh, keys_auth=excluded.keys_auth""",
                (phone, endpoint, p256dh, auth, now))
            c.commit()
        finally:
            c.close()
        return True
    except Exception:
        return False


def delete_push_subscription(db_path: str, phone: str, endpoint: str) -> bool:
    try:
        c = sqlite3.connect(db_path)
        try:
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute("DELETE FROM push_subscriptions WHERE phone=? AND endpoint=?", (phone, endpoint))
            c.commit()
        finally:
            c.close()
        return True
    except Exception:
        return False


def get_push_subscriptions(db_path: str, phone: str) -> List[Dict[str, Any]]:
    if not os.path.exists(db_path):
        return []
    c = _connect_feedback(db_path)
    try:
        if not _table_exists(c, "push_subscriptions"):
            return []
        rows = c.execute(
            "SELECT endpoint, keys_p256dh, keys_auth FROM push_subscriptions WHERE phone=?", (phone,)
        ).fetchall()
        return [{"endpoint": r[0], "keys": {"p256dh": r[1], "auth": r[2]}} for r in rows]
    except Exception:
        return []
    finally:
        c.close()


def process_alerts_queue(
    db_path: str,
    vapid_private_key: str,
    vapid_public_key: str,
    vapid_subject: str,
) -> int:
    """Read unsent alerts from alerts_queue, send Web Push to subscribers, mark as sent.
    Returns count of successfully sent notifications."""
    if not os.path.exists(db_path) or not vapid_private_key:
        return 0
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return 0

    c = sqlite3.connect(db_path)
    sent = 0
    try:
        c.execute("PRAGMA journal_mode=WAL;")
        if not _table_exists_raw(c, "alerts_queue"):
            return 0
        rows = c.execute(
            "SELECT id, phone, profile, score, message FROM alerts_queue WHERE sent_at IS NULL ORDER BY created_at ASC LIMIT 100"
        ).fetchall()
        now = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        for alert_id, phone, profile, score, message in rows:
            subs = get_push_subscriptions(db_path, phone)
            if not subs:
                # No subscriptions — mark as sent anyway (no-op)
                c.execute("UPDATE alerts_queue SET sent_at=? WHERE id=?", (now, alert_id))
                continue
            profile_label = {"migraine": "migreny", "allergy": "alergii", "heart": "sercowe"}.get(profile, profile)
            push_data = json.dumps({
                "title": f"Alert {profile_label}: {score}/100",
                "body": (message or f"Ryzyko {profile_label} osiągnęło {score}/100")[:200],
                "url": "/",
            }, ensure_ascii=False)
            failed_endpoints = []
            ok = False
            for sub in subs:
                try:
                    webpush(
                        subscription_info=sub,
                        data=push_data,
                        vapid_private_key=vapid_private_key,
                        vapid_claims={"sub": vapid_subject},
                        ttl=3600,
                        content_encoding="aes128gcm",
                    )
                    ok = True
                    sent += 1
                except Exception as e:
                    err_str = str(e)
                    if "410" in err_str or "404" in err_str:
                        failed_endpoints.append(sub["endpoint"])
            # Clean up expired subscriptions
            for ep in failed_endpoints:
                try:
                    c.execute("DELETE FROM push_subscriptions WHERE phone=? AND endpoint=?", (phone, ep))
                except Exception:
                    pass
            if ok or not subs:
                c.execute("UPDATE alerts_queue SET sent_at=? WHERE id=?", (now, alert_id))
        c.commit()
    except Exception:
        pass
    finally:
        c.close()
    return sent


def _table_exists_raw(c: sqlite3.Connection, name: str) -> bool:
    row = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return bool(row)


def get_unread_alerts_count(db_path: str, phone: str, hours: int = 48) -> int:
    """Count unsent alerts in the queue for this phone (proxy for 'unread')."""
    if not os.path.exists(db_path):
        return 0
    since = (datetime.now(tz=timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    c = _connect_feedback(db_path)
    try:
        if not _table_exists(c, "alerts_queue"):
            return 0
        row = c.execute(
            "SELECT COUNT(*) FROM alerts_queue WHERE phone=? AND created_at>=?", (phone, since)
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0
    finally:
        c.close()


# ── Forecast alerts ────────────────────────────────────────────────────────────

def forecast_alerts_for_user(db_path: str, phone: str, hours: int = 12) -> List[Dict[str, Any]]:
    """Return forecast alerts for the user from the last `hours` hours."""
    if not os.path.exists(db_path):
        return []
    since = (datetime.now(tz=timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    c = _connect_feedback(db_path)
    try:
        if not _table_exists(c, "forecast_alerts"):
            return []
        rows = c.execute(
            "SELECT profile, hour_offset, forecast_score, current_score, threshold, message, created_at"
            " FROM forecast_alerts WHERE phone=? AND created_at>=? ORDER BY created_at DESC LIMIT 50",
            (phone, since)
        ).fetchall()
        return [
            {"profile": r[0], "hour_offset": r[1], "forecast_score": r[2],
             "current_score": r[3], "threshold": r[4], "message": r[5], "created_at": r[6]}
            for r in rows
        ]
    except Exception:
        return []
    finally:
        c.close()


# ── Weekly reports ──────────────────────────────────────────────────────────────

def get_weekly_reports(db_path: str, phone: str, limit: int = 10) -> List[Dict[str, Any]]:
    if not os.path.exists(db_path):
        return []
    c = _connect_feedback(db_path)
    try:
        if not _table_exists(c, "weekly_reports"):
            return []
        rows = c.execute(
            "SELECT id, week_start, week_end, report_html, created_at"
            " FROM weekly_reports WHERE phone=? ORDER BY week_start DESC LIMIT ?",
            (phone, limit)
        ).fetchall()
        return [{"id": r[0], "week_start": r[1], "week_end": r[2],
                 "report_html": r[3], "created_at": r[4]} for r in rows]
    except Exception:
        return []
    finally:
        c.close()


def save_weekly_report(db_path: str, phone: str, week_start: str, week_end: str, report_html: str) -> bool:
    try:
        now = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        c = sqlite3.connect(db_path)
        try:
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute("""
                CREATE TABLE IF NOT EXISTS weekly_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone TEXT NOT NULL, week_start TEXT NOT NULL, week_end TEXT NOT NULL,
                    report_html TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(phone, week_start)
                )""")
            c.execute("""
                INSERT INTO weekly_reports(phone, week_start, week_end, report_html, created_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(phone, week_start) DO UPDATE SET
                    report_html=excluded.report_html, created_at=excluded.created_at""",
                (phone, week_start, week_end, report_html, now))
            c.commit()
        finally:
            c.close()
        return True
    except Exception:
        return False


# ── Contextual daily tips ──────────────────────────────────────────────────────

def _rule_based_tip(scores: Dict[str, Any], env_data: Dict[str, Any], profiles: List[str]) -> Optional[str]:
    """Return a single rule-based tip (instant, no network calls)."""
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

    tips = []
    if has_allergy and pollen >= 40:
        gp_type = env_data.get("google_pollen_type") or "pyłków"
        tips.append(f"Wysokie stężenie {gp_type.lower()} — rozważ lek antyhistaminowy przed wyjściem.")

    if has_migraine and pressure_delta is not None and pressure_delta <= -4:
        tips.append(f"Ciśnienie spada ({pressure_delta:+.1f} hPa/6h) — unikaj alkoholu i zadbaj o nawodnienie.")

    if has_heart and aqi >= 101:
        tips.append(f"Słaba jakość powietrza (AQI {aqi:.0f}) — ogranicz wysiłek na zewnątrz.")

    if not tips:
        all_scores = [scores.get(p, {}).get("score", 0) for p in profiles if scores.get(p)]
        if all_scores and max(all_scores) < 30:
            tips.append("Warunki sprzyjające — dobry dzień na aktywność na świeżym powietrzu!")

    return tips[0] if tips else None


def generate_daily_tip(
    scores: Dict[str, Any],
    env_data: Dict[str, Any],
    profiles: List[str],
    db_path: str = "",
    phone: str = "",
) -> Optional[str]:
    """Return a pre-generated tip from daily_tips table (instant SQLite read + random choice).

    Tips are pre-generated daily at 06:00 by the generate_daily_tips management command.
    If no pre-generated tips exist for today, falls back to rule-based tips immediately.
    No AI API calls are made here.
    """
    import random as _random

    if db_path and phone:
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        try:
            c = sqlite3.connect(db_path)
            try:
                row = c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='daily_tips'"
                ).fetchone()
                if row:
                    row = c.execute(
                        "SELECT tips_json FROM daily_tips WHERE phone=? AND day=?",
                        (phone, today),
                    ).fetchone()
                    if row:
                        tips = json.loads(row[0] or "[]")
                        if tips:
                            return _random.choice(tips)
            finally:
                c.close()
        except Exception:
            pass

    # No pre-generated tips — instant rule-based fallback, zero network calls
    return _rule_based_tip(scores, env_data, profiles)


def get_ai_risk_summary(
    db_path: str,
    profile: str,
    score: int,
    reasons: List[str],
    ts: int = 0,
    cache_only: bool = False,
) -> Optional[str]:
    """One-sentence AI summary for a risk reading, cached permanently per reading timestamp.

    When cache_only=True (used on dashboard page load), returns None on cache miss
    instead of calling the AI API — ensures zero API calls during page rendering.
    Pre-generation is handled by the precompute_dashboard_cache management command.
    """
    if not db_path or score == 0:
        return None

    cache_key = f"risk_summary:{profile}:{ts}" if ts else ""
    if cache_key:
        cached = _get_ai_cache(db_path, cache_key)
        if cached is not None:
            return cached

    # Cache miss: if cache_only mode, return None immediately (no API call on page load)
    if cache_only:
        return None

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    try:
        from anthropic import Anthropic
        model = os.getenv("CLAUDE_MODEL_FAST", "claude-haiku-4-5-20251001")
        reasons_str = "; ".join(reasons[:4]) if reasons else "brak danych"
        prompt = (
            f"Napisz jedno zdanie po polsku podsumowujące ryzyko zdrowotne: "
            f"profil={profile}, wynik={score}/100, główne czynniki: {reasons_str}. "
            f"Tylko jedno zdanie, bez nagłówka."
        )
        client = Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model,
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}],
        )
        summary = (msg.content[0].text or "").strip()
        if summary and cache_key:
            _set_ai_cache(db_path, cache_key, summary, model, 0)  # permanent
        return summary or None
    except Exception:
        return None


def generate_symptom_feedback(
    db_path: str,
    phone: str,
    profile: str,
    severity: int,
    notes: str,
    env_data: Dict[str, Any],
) -> Optional[str]:
    """AI-generated acknowledgment after a symptom report. Never cached (always fresh)."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    try:
        from anthropic import Anthropic
        model = os.getenv("CLAUDE_MODEL_FAST", "claude-haiku-4-5-20251001")
        pressure_delta = env_data.get("pressure_delta_6h")
        aqi = env_data.get("aqi_us_max_6h") or 0
        pollen = env_data.get("google_pollen_max") or env_data.get("pollen_max_6h") or 0
        note_part = f", notatka: '{notes}'" if notes else ""
        prompt = (
            f"Użytkownik z profilem '{profile}' zgłosił dolegliwość: nasilenie {severity}/10{note_part}. "
            f"Warunki środowiskowe: zmiana ciśnienia={pressure_delta} hPa/6h, AQI={aqi}, pyłki={pollen}. "
            f"Napisz 1-2 zdania po polsku z empatycznym komentarzem dlaczego obecne warunki mogły "
            f"przyczynić się do dolegliwości i jedną praktyczną wskazówką. "
            f"Bez nagłówka. Nie stawiaj diagnozy."
        )
        client = Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        return (msg.content[0].text or "").strip() or None
    except Exception:
        return None


# ── Correlation data (for correlation dashboard) ───────────────────────────────

def correlation_data(db_path: str, phone: str, profile: str, days: int = 30) -> Dict[str, Any]:
    """Compute correlation between daily risk scores and symptom reports.

    Returns:
        dates: list of date strings
        scores: list of max daily scores
        symptoms: list of symptom severity (0 if no report that day)
        correlations: dict of {env_key: pearson_r}  (pressure_delta_6h, aqi, pollen)
        symptom_dates: set of dates with reported symptoms
    """
    empty: Dict[str, Any] = {"dates": [], "scores": [], "symptoms": [], "correlations": {}, "symptom_dates": [], "symptom_scores": []}
    if not os.path.exists(db_path):
        return empty

    since = int((datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp())
    since_day = (datetime.now(tz=timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    c = _connect_feedback(db_path)
    try:
        # Daily max scores
        score_map: Dict[str, float] = {}
        env_map: Dict[str, Dict[str, float]] = {}  # date -> feats
        if _table_exists(c, "readings"):
            cols = set(_cols(c, "readings"))
            has_feats = "feats_json" in cols
            sel = "ts, score" + (", feats_json" if has_feats else "")
            rows = c.execute(
                f"SELECT {sel} FROM readings WHERE phone=? AND profile=? AND ts>=? ORDER BY ts ASC",
                (phone, profile, since)
            ).fetchall()
            for r in rows:
                day = datetime.fromtimestamp(int(r[0]), tz=timezone.utc).strftime("%Y-%m-%d")
                score = float(r[1] or 0)
                if day not in score_map or score > score_map[day]:
                    score_map[day] = score
                if has_feats and r[2] and day not in env_map:
                    try:
                        env_map[day] = json.loads(r[2]) or {}
                    except Exception:
                        env_map[day] = {}

        # Symptom reports
        symptom_map: Dict[str, float] = {}
        if _table_exists(c, "symptom_log"):
            rows = c.execute(
                "SELECT timestamp, severity_1_10 FROM symptom_log WHERE phone=? AND profile=? AND timestamp>=?"
                " ORDER BY timestamp ASC",
                (phone, profile, (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat())
            ).fetchall()
            for r in rows:
                try:
                    day = r[0][:10]
                    sev = float(r[1] or 0)
                    if day not in symptom_map or sev > symptom_map[day]:
                        symptom_map[day] = sev
                except Exception:
                    pass

        if not score_map:
            return empty

        all_days = sorted(set(list(score_map.keys()) + list(symptom_map.keys())))
        dates = all_days
        scores = [score_map.get(d, 0.0) for d in all_days]
        symptoms = [symptom_map.get(d, 0.0) for d in all_days]

        # Pearson correlation helper
        def pearson(x_list, y_list):
            n = len(x_list)
            if n < 3:
                return None
            mean_x = sum(x_list) / n
            mean_y = sum(y_list) / n
            num = sum((x - mean_x) * (y - mean_y) for x, y in zip(x_list, y_list))
            den_x = sum((x - mean_x) ** 2 for x in x_list) ** 0.5
            den_y = sum((y - mean_y) ** 2 for y in y_list) ** 0.5
            if den_x == 0 or den_y == 0:
                return None
            return round(num / (den_x * den_y), 3)

        correlations: Dict[str, Any] = {}
        for feat_key in ["pressure_delta_6h", "aqi_us_max_6h", "pollen_max_6h", "humidity_now", "temp_delta_6h"]:
            feat_vals = [env_map.get(d, {}).get(feat_key) for d in all_days]
            pairs = [(f, s) for f, s in zip(feat_vals, symptoms) if f is not None]
            if len(pairs) >= 3:
                fv, sv = zip(*pairs)
                r_val = pearson(list(fv), list(sv))
                if r_val is not None:
                    label = "silna" if abs(r_val) >= 0.6 else ("umiarkowana" if abs(r_val) >= 0.3 else "słaba")
                    correlations[feat_key] = {"r": r_val, "label": label}

        sorted_symp_dates = sorted(symptom_map.keys())
        return {
            "dates": dates,
            "scores": scores,
            "symptoms": symptoms,
            "correlations": correlations,
            "symptom_dates": sorted_symp_dates,
            "symptom_scores": [symptom_map[d] for d in sorted_symp_dates],
        }
    except Exception:
        return empty
    finally:
        c.close()


def delete_all_user_data(db_path: str, phone: str) -> None:
    """Delete ALL data for a phone number from feedback.db. Called on account self-deletion."""
    if not phone or not os.path.exists(db_path):
        return
    import sqlite3 as _sq3
    c = _sq3.connect(db_path)
    c.row_factory = _sq3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
        # Create audit log
        c.execute("""
            CREATE TABLE IF NOT EXISTS deletions_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                deleted_at TEXT NOT NULL,
                note TEXT
            )
        """)
        now = datetime.now(tz=timezone.utc).isoformat()
        c.execute("INSERT INTO deletions_log (phone, deleted_at, note) VALUES (?,?,?)",
                  (phone, now, "self-delete via web UI"))
        # Purge tables keyed by phone
        for tbl, col in [
            ("readings", "phone"), ("alerts", "phone"), ("wellbeing", "phone"),
            ("symptom_log", "phone"), ("forecast_alerts", "phone"),
            ("alerts_queue", "phone"), ("push_subscriptions", "phone"),
            ("weekly_reports", "phone"), ("wg_users", "phone"),
        ]:
            try:
                rows = c.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)).fetchone()
                if rows:
                    c.execute(f"DELETE FROM {tbl} WHERE {col}=?", (phone,))
            except Exception:
                pass
        # sms_users uses phone_e164
        try:
            rows = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sms_users'").fetchone()
            if rows:
                c.execute("DELETE FROM sms_users WHERE phone_e164=?", (phone,))
        except Exception:
            pass
        c.commit()
    except Exception:
        pass
    finally:
        c.close()


def symptom_log_history(db_path: str, phone: str, days: int = 30) -> List[Dict[str, Any]]:
    """Return symptom log entries for a phone, newest first."""
    if not os.path.exists(db_path):
        return []
    since = (datetime.now(tz=timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    c = _connect_feedback(db_path)
    try:
        if not _table_exists(c, "symptom_log"):
            return []
        rows = c.execute(
            "SELECT id, timestamp, profile, severity_1_10, notes FROM symptom_log "
            "WHERE phone=? AND timestamp>=? ORDER BY timestamp DESC LIMIT 100",
            (phone, since)
        ).fetchall()
        return [{"id": r["id"], "timestamp": r["timestamp"], "profile": r["profile"],
                 "severity": r["severity_1_10"], "notes": r["notes"] or ""} for r in rows]
    except Exception:
        return []
    finally:
        c.close()


def get_recent_alerts_for_user(db_path: str, phone: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Return recent alerts from alerts_queue for a user, newest first."""
    if not os.path.exists(db_path):
        return []
    c = _connect_feedback(db_path)
    try:
        if not _table_exists(c, "alerts_queue"):
            return []
        cols = set(_cols(c, "alerts_queue"))
        sel = ["id", "profile", "score", "message", "created_at"]
        sel = [col for col in sel if col in cols]
        if not sel:
            return []
        q = f"SELECT {', '.join(sel)} FROM alerts_queue WHERE phone=? ORDER BY created_at DESC LIMIT ?"
        rows = c.execute(q, (phone, limit)).fetchall()
        out = []
        for r in rows:
            d = {k: r[k] for k in sel if k in r.keys()}
            out.append({
                "id": d.get("id"),
                "profile": d.get("profile") or "",
                "score": d.get("score") or 0,
                "message": d.get("message") or "",
                "created_at": d.get("created_at") or "",
            })
        return out
    except Exception:
        return []
    finally:
        c.close()


def mark_alerts_read(db_path: str, phone: str) -> None:
    """Mark all unread alerts_queue entries for a phone as read by user."""
    if not os.path.exists(db_path):
        return
    c = sqlite3.connect(db_path)
    try:
        c.execute("PRAGMA journal_mode=WAL;")
        if not _table_exists_raw(c, "alerts_queue"):
            return
        # Add user_read_at column if it doesn't exist yet
        existing_cols = {r[1] for r in c.execute("PRAGMA table_info(alerts_queue)").fetchall()}
        if "user_read_at" not in existing_cols:
            c.execute("ALTER TABLE alerts_queue ADD COLUMN user_read_at TEXT;")
        now = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        c.execute(
            "UPDATE alerts_queue SET user_read_at=? WHERE phone=? AND user_read_at IS NULL",
            (now, phone)
        )
        c.commit()
    except Exception:
        pass
    finally:
        c.close()


# ── Badges ────────────────────────────────────────────────────────────────────

def _ensure_badges_table(c: sqlite3.Connection) -> None:
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_badges (
            phone TEXT NOT NULL,
            badge_id TEXT NOT NULL,
            earned_at TEXT NOT NULL,
            PRIMARY KEY (phone, badge_id)
        )
    """)
    c.commit()


def award_badge(db_path: str, phone: str, badge_id: str) -> bool:
    """Award a badge to a user if not already earned. Returns True if newly awarded."""
    if not db_path or not os.path.exists(db_path):
        return False
    try:
        now = datetime.now(tz=timezone.utc).isoformat()
        c = sqlite3.connect(db_path)
        try:
            c.execute("PRAGMA journal_mode=WAL;")
            _ensure_badges_table(c)
            existing = c.execute(
                "SELECT 1 FROM user_badges WHERE phone=? AND badge_id=?", (phone, badge_id)
            ).fetchone()
            if existing:
                return False
            c.execute(
                "INSERT INTO user_badges (phone, badge_id, earned_at) VALUES (?,?,?)",
                (phone, badge_id, now),
            )
            c.commit()
            return True
        finally:
            c.close()
    except Exception:
        return False


def get_user_badges(db_path: str, phone: str) -> List[Dict[str, Any]]:
    """Return list of earned badges for a user."""
    if not db_path or not os.path.exists(db_path):
        return []
    try:
        c = _connect_feedback(db_path)
        try:
            if not _table_exists(c, "user_badges"):
                return []
            rows = c.execute(
                "SELECT badge_id, earned_at FROM user_badges WHERE phone=? ORDER BY earned_at ASC",
                (phone,),
            ).fetchall()
            return [{"badge_id": r[0], "earned_at": r[1]} for r in rows]
        finally:
            c.close()
    except Exception:
        return []


# ── Weekly stats ──────────────────────────────────────────────────────────────

def get_weekly_stats(db_path: str, phone: str, profiles: List[str]) -> Dict[str, Any]:
    """Compute compact weekly stats for dashboard summary card.
    Returns: avg_risk, worst_day_label, worst_score, logged_days, week_vs_prev_pct
    """
    empty: Dict[str, Any] = {"avg_risk": None, "worst_day": None, "worst_score": None, "logged_days": 0, "vs_prev_pct": None}
    if not db_path or not os.path.exists(db_path) or not profiles:
        return empty
    from datetime import timedelta as _td
    now = datetime.now(tz=timezone.utc)
    week_start = int((now - _td(days=7)).timestamp())
    prev_start = int((now - _td(days=14)).timestamp())

    c = _connect_feedback(db_path)
    try:
        if not _table_exists(c, "readings"):
            return empty
        # This week scores
        rows = c.execute(
            "SELECT ts, score FROM readings WHERE phone=? AND ts>=? ORDER BY ts ASC",
            (phone, week_start),
        ).fetchall()
        if not rows:
            return empty
        day_scores: Dict[str, float] = {}
        for r in rows:
            day = datetime.fromtimestamp(int(r[0]), tz=timezone.utc).strftime("%Y-%m-%d")
            day_scores[day] = max(day_scores.get(day, 0), float(r[1] or 0))
        if not day_scores:
            return empty
        avg_risk = int(round(sum(day_scores.values()) / len(day_scores)))
        worst_day, worst_score = max(day_scores.items(), key=lambda x: x[1])
        # Convert worst_day to Polish weekday
        from datetime import date as _date
        try:
            wd_date = _date.fromisoformat(worst_day)
            pl_days = ["Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek", "Sobota", "Niedziela"]
            worst_day_label = pl_days[wd_date.weekday()]
        except Exception:
            worst_day_label = worst_day

        # Count logged days (wellbeing entries this week)
        logged_days = 0
        if _table_exists(c, "wellbeing"):
            since_day = (now - _td(days=7)).strftime("%Y-%m-%d")
            row = c.execute(
                "SELECT COUNT(DISTINCT day) FROM wellbeing WHERE phone=? AND day>=?",
                (phone, since_day),
            ).fetchone()
            logged_days = int(row[0]) if row else 0

        # Previous week average for comparison
        vs_prev_pct = None
        prev_rows = c.execute(
            "SELECT ts, score FROM readings WHERE phone=? AND ts>=? AND ts<? ORDER BY ts ASC",
            (phone, prev_start, week_start),
        ).fetchall()
        if prev_rows:
            prev_day_scores: Dict[str, float] = {}
            for r in prev_rows:
                day = datetime.fromtimestamp(int(r[0]), tz=timezone.utc).strftime("%Y-%m-%d")
                prev_day_scores[day] = max(prev_day_scores.get(day, 0), float(r[1] or 0))
            if prev_day_scores:
                prev_avg = sum(prev_day_scores.values()) / len(prev_day_scores)
                if prev_avg > 0:
                    vs_prev_pct = int(round((avg_risk - prev_avg) / prev_avg * 100))

        return {
            "avg_risk": avg_risk,
            "worst_day": worst_day_label,
            "worst_score": int(worst_score),
            "logged_days": logged_days,
            "vs_prev_pct": vs_prev_pct,
        }
    except Exception:
        return empty
    finally:
        c.close()


# ── Evening push reminders ─────────────────────────────────────────────────────

def check_and_queue_evening_reminders(
    db_path: str,
    vapid_private_key: str,
    vapid_public_key: str,
    vapid_subject: str,
) -> int:
    """Check if users should receive evening wellbeing reminders. Returns count sent."""
    if not db_path or not os.path.exists(db_path) or not vapid_private_key:
        return 0
    try:
        from pywebpush import webpush, WebPushException  # type: ignore
    except ImportError:
        return 0
    import json as _json

    now = datetime.now(tz=timezone.utc)
    hour = now.hour
    # Only run between 19:00 and 21:00 UTC (approximate local evening)
    if not (19 <= hour <= 21):
        return 0

    today = now.strftime("%Y-%m-%d")
    sent = 0

    c = sqlite3.connect(db_path)
    try:
        c.execute("PRAGMA journal_mode=WAL;")
        # Ensure push_reminders table
        c.execute("""
            CREATE TABLE IF NOT EXISTS push_reminders (
                phone TEXT NOT NULL,
                day TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (phone, day)
            )
        """)
        c.commit()

        if not _table_exists_raw(c, "push_subscriptions"):
            return 0

        subs_rows = c.execute(
            "SELECT DISTINCT phone FROM push_subscriptions"
        ).fetchall()

        for (phone,) in subs_rows:
            try:
                # Check if already sent today
                already = c.execute(
                    "SELECT 1 FROM push_reminders WHERE phone=? AND day=?", (phone, today)
                ).fetchone()
                if already:
                    continue

                # Check if wellbeing logged today
                has_wb = False
                if _table_exists_raw(c, "wellbeing"):
                    wb = c.execute(
                        "SELECT 1 FROM wellbeing WHERE phone=? AND day=?", (phone, today)
                    ).fetchone()
                    has_wb = bool(wb)

                if has_wb:
                    continue  # Already logged, no reminder needed

                # Get subscriptions for this user
                subs = c.execute(
                    "SELECT endpoint, keys_p256dh, keys_auth FROM push_subscriptions WHERE phone=?",
                    (phone,)
                ).fetchall()

                push_data = _json.dumps({
                    "title": "Jak minął Twój dzień?",
                    "body": "Zaloguj samopoczucie — to zajmie 10 sekund",
                    "url": "/wellbeing/",
                }, ensure_ascii=False)

                ok = False
                for endpoint, p256dh, auth in subs:
                    try:
                        webpush(
                            subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
                            data=push_data,
                            vapid_private_key=vapid_private_key,
                            vapid_claims={"sub": vapid_subject},
                            ttl=7200,
                            content_encoding="aes128gcm",
                        )
                        ok = True
                        sent += 1
                    except Exception:
                        pass

                if ok:
                    c.execute(
                        "INSERT OR REPLACE INTO push_reminders (phone, day, sent_at) VALUES (?,?,?)",
                        (phone, today, now.isoformat()),
                    )
                    c.commit()
            except Exception:
                pass
    except Exception:
        pass
    finally:
        c.close()
    return sent
