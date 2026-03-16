from django.contrib import admin
from .models import UserProfile, DailyWellbeing

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone_e164", "gender", "sms_enabled", "default_profile", "updated_at")
    search_fields = ("user__username", "user__email", "user__first_name", "user__last_name", "phone_e164")
    list_filter = ("gender", "sms_enabled", "default_profile")

@admin.register(DailyWellbeing)
class DailyWellbeingAdmin(admin.ModelAdmin):
    list_display = ("user", "day", "stress_1_10", "exercise_1_10", "updated_at")
    search_fields = ("user__username", "user__email")
    list_filter = ("day",)
