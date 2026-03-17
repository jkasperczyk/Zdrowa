from django.core.management.base import BaseCommand
from django.conf import settings

class Command(BaseCommand):
    help = "Process unsent push notification queue and send Web Push alerts"

    def handle(self, *args, **options):
        from portal.wg_sources import process_alerts_queue
        private_key = getattr(settings, 'VAPID_PRIVATE_KEY', '')
        public_key = getattr(settings, 'VAPID_PUBLIC_KEY', '')
        subject = getattr(settings, 'VAPID_SUBJECT', 'mailto:admin@zdrowa.pracunia.pl')
        if not private_key or not public_key:
            self.stderr.write(self.style.ERROR("VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY not set in settings."))
            return
        try:
            sent = process_alerts_queue(settings.WEATHERGUARD_DB, private_key, public_key, subject)
            self.stdout.write(self.style.SUCCESS(f"Sent {sent} push notification(s)."))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error: {e}"))
