from django import template

register = template.Library()

PROFILE_LABELS = {
    "migraine": "Migrena",
    "allergy":  "Alergia",
    "heart":    "Serce",
}


@register.filter
def profile_label(value):
    """Translate internal profile key to Polish display name."""
    return PROFILE_LABELS.get(str(value), value)
