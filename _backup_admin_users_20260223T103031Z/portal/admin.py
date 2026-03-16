from django.contrib import admin
from .models import UserProfile

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "display_name", "phone_e164", "default_profile", "updated_at")
    search_fields = ("user__username", "display_name", "phone_e164")
