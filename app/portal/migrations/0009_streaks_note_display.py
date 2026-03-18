from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('portal', '0008_userprofile_use_ml_prediction'),
    ]
    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='current_streak',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='longest_streak',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='last_log_date',
            field=models.DateField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='font_size_preference',
            field=models.CharField(
                choices=[('small', 'Mały (14px)'), ('normal', 'Normalny (16px)'), ('large', 'Duży (18px)')],
                default='normal',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='high_contrast',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='evening_reminder',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='dailywellbeing',
            name='daily_note',
            field=models.TextField(blank=True, default=''),
        ),
    ]
