from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0005_remove_display_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='has_seen_onboarding',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='dailywellbeing',
            name='sleep_quality_1_10',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='dailywellbeing',
            name='hydration_1_10',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='dailywellbeing',
            name='headache_1_10',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
    ]
