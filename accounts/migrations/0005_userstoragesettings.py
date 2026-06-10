# Generated migration — P2 T05 UserStorageSettings persistence

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def create_default_storage_settings(apps, schema_editor):
    """Backfill UserStorageSettings for every existing user."""
    User = apps.get_model('auth', 'User')
    UserStorageSettings = apps.get_model('accounts', 'UserStorageSettings')
    existing = set(
        UserStorageSettings.objects.values_list('user_id', flat=True)
    )
    to_create = [
        UserStorageSettings(user=user)
        for user in User.objects.all()
        if user.pk not in existing
    ]
    if to_create:
        UserStorageSettings.objects.bulk_create(to_create)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_userprivacysettings_blockeduser'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UserStorageSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('settings_json', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='storage_settings', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.RunPython(create_default_storage_settings, migrations.RunPython.noop),
    ]
