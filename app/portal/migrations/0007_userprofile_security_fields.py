from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('portal', '0006_wellbeing_extra_fields_onboarding'),
    ]
    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='failed_login_count',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='locked_until',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
