import os
import json
import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional

def _connect_feedback(db_path: str) -> sqlite3.Connection:
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    return c

def last_readings(db_path: str, phone: str, profile: str, limit: int = 50) -> List[Dict[str, Any]]:
    if not os.path.exists(db_path):
        return []
    c = _connect_feedback(db_path)
    try:
        # readings
        try:
            rows = c.execute(
                "SELECT ts, score FROM readings WHERE phone=? AND profile=? ORDER BY ts DESC LIMIT ?",
                (phone, profile, limit),
            ).fetchall()
            if rows:
                return [{
                    "ts": int(r["ts"]),
                    "dt": datetime.utcfromtimestamp(int(r["ts"])).strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "score": int(r["score"]) if r["score"] is not None else None,
                } for r in rows]
        except sqlite3.Error:
            pass

        # alerts fallback
        try:
            rows = c.execute(
                "SELECT ts, score FROM alerts WHERE phone=? AND profile=? ORDER BY ts DESC LIMIT ?",
                (phone, profile, limit),
            ).fetchall()
            return [{
                "ts": int(r["ts"]),
                "dt": datetime.utcfromtimestamp(int(r["ts"])).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "score": int(r["score"]) if r["score"] is not None else None,
            } for r in rows]
        except sqlite3.Error:
            return []
    finally:
        c.close()

def sms_subscription_status(sms_users_path: str, phone: str) -> Optional[bool]:
    try:
        with open(sms_users_path, "r", encoding="utf-8") as f:
            db = json.load(f)
        entry = db.get(phone)
        if entry is None:
            return None
        return bool(entry.get("subscribed", True))
    except Exception:
        return None

def set_sms_subscription(sms_users_path: str, phone: str, subscribed: bool) -> bool:
    try:
        os.makedirs(os.path.dirname(sms_users_path) or ".", exist_ok=True)
        db = {}
        if os.path.exists(sms_users_path):
            with open(sms_users_path, "r", encoding="utf-8") as f:
                db = json.load(f) or {}
        entry = db.get(phone) or {}
        entry["subscribed"] = bool(subscribed)
        db[phone] = entry
        tmp = sms_users_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
        os.replace(tmp, sms_users_path)
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
