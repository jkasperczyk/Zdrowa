"""
Management command: generate_weekly_report

Usage:
  python manage.py generate_weekly_report            # all users with phone numbers
  python manage.py generate_weekly_report --phone +48123456789

Designed to be run weekly via cron or systemd timer.
"""
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

from portal.models import UserProfile
from portal.views_wg import _generate_report_for_user


class Command(BaseCommand):
    help = "Generate weekly AI health reports for users."

    def add_arguments(self, parser):
        parser.add_argument(
            "--phone",
            default=None,
            help="Generate only for this phone number (E.164). Omit to process all users.",
        )

    def handle(self, *args, **options):
        phone = options.get("phone")

        if phone:
            phones = [phone]
        else:
            phones = list(
                UserProfile.objects.filter(phone_e164__isnull=False)
                .exclude(phone_e164="")
                .values_list("phone_e164", flat=True)
            )

        self.stdout.write(f"Generating reports for {len(phones)} user(s)...")

        ok = 0
        fail = 0
        for p in phones:
            try:
                success = _generate_report_for_user(p)
                if success:
                    ok += 1
                    self.stdout.write(self.style.SUCCESS(f"  OK  {p}"))
                else:
                    fail += 1
                    self.stdout.write(self.style.WARNING(f"  SKIP {p} (save failed)"))
            except Exception as e:
                fail += 1
                self.stdout.write(self.style.ERROR(f"  ERR  {p}: {e}"))

        self.stdout.write(f"\nDone: {ok} generated, {fail} failed/skipped.")
