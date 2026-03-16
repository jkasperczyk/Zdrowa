from django.conf import settings
from django.db import models

class UserProfile(models.Model):
    GENDER_CHOICES = [
        ("unspecified", "Nie podano"),
        ("female", "Kobieta"),
        ("male", "Mężczyzna"),
        ("other", "Inne"),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    display_name = models.CharField(max_length=120, blank=True, default="")
    phone_e164 = models.CharField(max_length=32, blank=True, default="", help_text="E.164, np. +4879...")

    default_profile = models.CharField(max_length=32, blank=True, default="migraine")
    enabled_alerts = models.JSONField(default=list, blank=True)
    sms_enabled = models.BooleanField(default=True)

    gender = models.CharField(max_length=16, choices=GENDER_CHOICES, default="unspecified")
    cycle_length_days = models.PositiveSmallIntegerField(null=True, blank=True, help_text="20-45 (tylko dla kobiet)")
    cycle_start_date = models.DateField(null=True, blank=True, help_text="Data początku ostatniego cyklu (YYYY-MM-DD)")

    # Password policy
    must_change_password = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.user.username} ({self.phone_e164})"


class DailyWellbeing(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="daily_wellbeing")
    day = models.DateField()
    stress_1_10 = models.PositiveSmallIntegerField(null=True, blank=True)
    exercise_1_10 = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "day")
        indexes = [models.Index(fields=["user", "day"])]

    def __str__(self) -> str:
        return f"{self.user.username} {self.day} S={self.stress_1_10} E={self.exercise_1_10}"
