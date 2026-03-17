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


@register.filter
def get_item(dictionary, key):
    """Dict lookup by variable key: {{ mydict|get_item:key }}"""
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None
