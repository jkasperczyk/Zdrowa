import os
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

class Command(BaseCommand):
    help = "Create (or update) admin user from env vars."

    def handle(self, *args, **kwargs):
        username = os.environ.get("ZDROWA_ADMIN_USER", "admin")
        email = os.environ.get("ZDROWA_ADMIN_EMAIL", "admin@pracunia.pl")
        password = os.environ.get("ZDROWA_ADMIN_PASSWORD")
        if not password:
            self.stdout.write(self.style.ERROR("Missing ZDROWA_ADMIN_PASSWORD"))
            return

        u, created = User.objects.get_or_create(
            username=username,
            defaults={"email": email, "is_staff": True, "is_superuser": True},
        )
        u.email = email
        u.is_staff = True
        u.is_superuser = True
        u.set_password(password)
        u.save()
        self.stdout.write(self.style.SUCCESS(f"Admin ready: {username} ({'created' if created else 'updated'})"))
