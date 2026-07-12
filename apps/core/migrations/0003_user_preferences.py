import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def copy_visibility_toggles(apps, schema_editor):
    User = apps.get_model('core', 'User')
    UserPreferences = apps.get_model('core', 'UserPreferences')
    for user in User.objects.only('id', 'leaderboard_visible', 'others_learning_visible'):
        UserPreferences.objects.update_or_create(
            user=user,
            defaults={
                'leaderboard_visible': user.leaderboard_visible,
                'others_learning_visible': user.others_learning_visible,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_password_reset_token'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserPreferences',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('leaderboard_visible', models.BooleanField(default=True)),
                ('others_learning_visible', models.BooleanField(default=True)),
                ('auto_select_subjects_enabled', models.BooleanField(default=False)),
                ('auto_select_subjects_consent_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='preferences', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.RunPython(copy_visibility_toggles, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='user',
            name='leaderboard_visible',
        ),
        migrations.RemoveField(
            model_name='user',
            name='others_learning_visible',
        ),
    ]
