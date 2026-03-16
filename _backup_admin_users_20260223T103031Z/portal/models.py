from django.conf import settings
from django.db import models

class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    display_name = models.CharField(max_length=120, blank=True, default="")
    phone_e164 = models.CharField(max_length=32, blank=True, default="", help_text="E.164, np. +4879...")
    default_profile = models.CharField(max_length=32, blank=True, default="migraine")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.user.username} ({self.phone_e164})"
