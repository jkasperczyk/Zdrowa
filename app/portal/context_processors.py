from django.conf import settings as django_settings


def push_context(request):
    """Inject VAPID public key and unread alert count into every template."""
    if not request.user.is_authenticated:
        return {}

    vapid_public_key = getattr(django_settings, "VAPID_PUBLIC_KEY", "")

    unread_count = 0
    try:
        from .models import UserProfile
        prof = UserProfile.objects.get(user=request.user)
        if prof.phone_e164:
            from .wg_sources import get_unread_alerts_count
            unread_count = get_unread_alerts_count(django_settings.WEATHERGUARD_DB, prof.phone_e164)
    except Exception:
        pass

    return {
        "vapid_public_key": vapid_public_key,
        "unread_count": unread_count,
    }
