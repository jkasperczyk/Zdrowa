from __future__ import annotations
import json
import re
from dataclasses import dataclass
from typing import List, Optional, Dict, Set

ALERTS = {"migraine", "allergy", "heart"}
PHONE_RE = re.compile(r"(\+?\d{8,15})")
EMAIL_RE = re.compile(r"[^\s]+@[^\s]+\.[^\s]+")

@dataclass
class ParsedUser:
    first_name: str
    last_name: str
    email: str | None
    phone_e164: str
    enabled_alerts: List[str]

def _norm_phone(s: str) -> Optional[str]:
    s = (s or "").strip().replace(" ", "")
    m = PHONE_RE.fullmatch(s)
    if not m:
        return None
    if not s.startswith("+"):
        s = "+" + s
    return s

def parse_users_txt(content: str) -> List[ParsedUser]:
    out: List[ParsedUser] = []
    for raw in (content or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
                phone = _norm_phone(str(obj.get("phone") or obj.get("phone_e164") or ""))
                if not phone:
                    continue
                email = (obj.get("email") or None)
                name = str(obj.get("name") or "").strip()
                parts = name.split()
                first = parts[0] if parts else "User"
                last = " ".join(parts[1:]) if len(parts) > 1 else "Imported"
                alerts = obj.get("alerts") or obj.get("profiles") or []
                alerts = [a for a in alerts if a in ALERTS] or ["migraine"]
                out.append(ParsedUser(first, last, email, phone, alerts))
                continue
            except Exception:
                pass

        sep = ";" if ";" in line else ("," if "," in line else None)
        tokens = [t.strip() for t in line.split(sep)] if sep else line.split()

        email = None
        phone = None
        alerts: List[str] = []
        name_tokens: List[str] = []

        for t in tokens:
            if EMAIL_RE.fullmatch(t):
                email = t
                continue
            p = _norm_phone(t)
            if p:
                phone = p
                continue
            lo = t.lower()
            if lo in ALERTS:
                alerts.append(lo)
                continue
            name_tokens.append(t)

        if not phone:
            m = PHONE_RE.search(line.replace(" ", ""))
            if m:
                phone = _norm_phone(m.group(1))
        if not phone:
            continue

        if not alerts:
            alerts = ["migraine"]

        first = name_tokens[0] if name_tokens else "User"
        last = " ".join(name_tokens[1:]) if len(name_tokens) > 1 else "Imported"
        out.append(ParsedUser(first, last, email, phone, alerts))

    return out

def dedupe_by_phone(users: List[ParsedUser]) -> List[ParsedUser]:
    merged: Dict[str, ParsedUser] = {}
    alerts_map: Dict[str, Set[str]] = {}

    for u in users:
        ph = u.phone_e164
        if ph not in merged:
            merged[ph] = ParsedUser(u.first_name, u.last_name, u.email, ph, list(u.enabled_alerts))
            alerts_map[ph] = set(a for a in u.enabled_alerts if a in ALERTS)
            continue

        alerts_map[ph].update(a for a in u.enabled_alerts if a in ALERTS)
        cur = merged[ph]
        if (not cur.email) and u.email:
            cur.email = u.email
        if (cur.first_name in {"User"} or cur.last_name in {"Imported"}) and (u.first_name not in {"User"}):
            cur.first_name = u.first_name
            cur.last_name = u.last_name
        merged[ph] = cur

    out: List[ParsedUser] = []
    for ph, cur in merged.items():
        al = sorted(alerts_map.get(ph) or set())
        cur.enabled_alerts = al or ["migraine"]
        out.append(cur)
    out.sort(key=lambda x: x.phone_e164)
    return out
