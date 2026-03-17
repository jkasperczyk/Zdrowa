import re
from django.core.exceptions import ValidationError

class HasNumberValidator:
    def validate(self, password, user=None):
        if not re.search(r'\d', password):
            raise ValidationError(
                "Hasło musi zawierać co najmniej jedną cyfrę.",
                code='password_no_number',
            )

    def get_help_text(self):
        return "Hasło musi zawierać co najmniej jedną cyfrę."
