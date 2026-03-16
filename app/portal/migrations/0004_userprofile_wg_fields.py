from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0003_userprofile_must_change_password'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='location',
            field=models.CharField(blank=True, default='', help_text='Np. Zawiercie,PL', max_length=120),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='alert_threshold',
            field=models.PositiveSmallIntegerField(blank=True, null=True, help_text='Próg alertu (1-100), puste = domyślny'),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='quiet_hours',
            field=models.CharField(blank=True, default='', help_text='Cisza nocna, np. 22-7', max_length=10),
        ),
    ]
