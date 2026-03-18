"""
Management command: precompute_dashboard_cache

Usage:
  python manage.py precompute_dashboard_cache                      # all active users
  python manage.py precompute_dashboard_cache --phone +48505019600 # single user

Designed to run hourly at :10 (after the Health_Guard runner at :05) via cron.
Pre-generates AI risk summaries for all active users so the dashboard page loads
instantly with zero AI API calls on page render.
"""
import json

from django.conf import settings
from django.core.management.base import BaseCommand

from portal.wg_sources import (
    dashboard_summary,
    get_ai_risk_summary,
    _connect_feedback,
    _table_exists,
)


def _get_active_users(db_path: str):
    """Return list of (phone, profiles) for enabled wg_users."""
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


class Command(BaseCommand):
    help = "Pre-generate AI risk summaries for all active users (run hourly at :10 via cron)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--phone",
            default=None,
            help="Process only this phone number (E.164). Omit to process all active users.",
        )

    def handle(self, *args, **options):
        db_path = settings.WEATHERGUARD_DB
        phone_filter = options.get("phone")

        if phone_filter:
            try:
                c = _connect_feedback(db_path)
                try:
                    row = c.execute(
                        "SELECT profiles_json FROM wg_users WHERE phone=?",
                        (phone_filter,),
                    ).fetchone()
                    profiles = json.loads(row[0] or '["migraine"]') if row else ["migraine", "allergy", "heart"]
                finally:
                    c.close()
            except Exception:
                profiles = ["migraine", "allergy", "heart"]
            users = [(phone_filter, profiles)]
        else:
            users = _get_active_users(db_path)

        self.stdout.write(f"Precomputing dashboard cache for {len(users)} user(s)...")

        ok = fail = skipped = 0
        for phone, profiles in users:
            self.stdout.write(f"  {phone} {profiles}")
            try:
                summary = dashboard_summary(db_path, phone, profiles)
                scores = summary.get("scores", {})
                if not scores:
                    self.stdout.write("    no readings yet, skipping")
                    skipped += 1
                    continue
                for profile_name, pdata in scores.items():
                    score = pdata.get("score", 0)
                    reasons = pdata.get("reasons", [])
                    ts = pdata.get("ts", 0)
                    # cache_only=False: allowed to call AI and populate cache
                    summary_text = get_ai_risk_summary(
                        db_path, profile_name, score, reasons, ts, cache_only=False
                    )
                    if summary_text:
                        self.stdout.write(f"    [{profile_name}] cached: {summary_text[:70]}")
                        ok += 1
                    else:
                        self.stdout.write(f"    [{profile_name}] skipped (score=0 or no API key)")
                        skipped += 1
            except Exception as e:
                fail += 1
                self.stdout.write(self.style.ERROR(f"    ERR: {e}"))

        self.stdout.write(f"\nDone: {ok} summaries cached, {skipped} skipped, {fail} failed.")
