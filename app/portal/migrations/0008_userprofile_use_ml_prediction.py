from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0007_userprofile_security_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="use_ml_prediction",
            field=models.BooleanField(
                default=False,
                help_text="Użyj modelu ML do personalizacji predykcji ryzyka",
            ),
        ),
    ]
