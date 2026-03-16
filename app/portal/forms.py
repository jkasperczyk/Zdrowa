from __future__ import annotations
from django import forms
import re
import secrets
import string

ALERT_CHOICES = [
    ("migraine", "Migrena"),
    ("allergy", "Alergia"),
    ("heart", "Serce"),
]
GENDER_CHOICES = [
    ("unspecified", "Nie podano"),
    ("female", "Kobieta"),
    ("male", "Mężczyzna"),
    ("other", "Inne"),
]
_PHONE_RE = re.compile(r"^\+\d{8,15}$")

def gen_password(n: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))

class AdminCreateUserForm(forms.Form):
    first_name = forms.CharField(label="Imię", max_length=80, required=True)
    last_name = forms.CharField(label="Nazwisko", max_length=120, required=True)
    email = forms.EmailField(label="E-mail (login)", required=True)
    phone_e164 = forms.CharField(label="Telefon (E.164, np. +4879...)", max_length=32, required=True)

    gender = forms.ChoiceField(label="Płeć", choices=GENDER_CHOICES, required=False, initial="unspecified")
    enabled_alerts = forms.MultipleChoiceField(label="Typy alertów", choices=ALERT_CHOICES, required=False)
    sms_enabled = forms.BooleanField(label="SMS alerts włączone", required=False, initial=True)

    location = forms.CharField(label="Lokalizacja (np. Zawiercie,PL)", max_length=120, required=False)
    alert_threshold = forms.IntegerField(label="Próg alertu (1-100)", required=False, min_value=1, max_value=100)
    quiet_hours = forms.CharField(label="Cisza nocna (np. 22-7)", max_length=10, required=False)

    cycle_length_days = forms.IntegerField(label="Długość cyklu (dni)", required=False, min_value=20, max_value=45)
    cycle_start_date = forms.DateField(label="Start cyklu (YYYY-MM-DD)", required=False, input_formats=["%Y-%m-%d"])

    def clean_phone_e164(self):
        v = (self.cleaned_data.get("phone_e164") or "").strip().replace(" ", "")
        if v and not v.startswith("+") and v.isdigit():
            v = "+" + v
        if not _PHONE_RE.match(v):
            raise forms.ValidationError("Wymagany format E.164, np. +48791234567")
        return v

    def clean(self):
        data = super().clean()
        if (data.get("gender") or "unspecified") != "female":
            data["cycle_length_days"] = None
            data["cycle_start_date"] = None
        return data

class AdminEditUserForm(AdminCreateUserForm):
    email = forms.EmailField(label="E-mail", required=False)
    is_active = forms.BooleanField(label="Konto aktywne", required=False, initial=True)

class ImportUsersForm(forms.Form):
    confirm = forms.BooleanField(label="Tak, importuj z /opt/weatherguard/config/users.txt", required=True)

class DeleteUserForm(forms.Form):
    confirm_delete = forms.BooleanField(label="Potwierdzam usunięcie konta", required=True)

class PasswordChangeOnlyForm(forms.Form):
    # placeholder for template grouping if needed
    pass
