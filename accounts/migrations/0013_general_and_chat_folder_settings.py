from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_default_settings(apps, schema_editor):
    User = apps.get_model(settings.AUTH_USER_MODEL.split('.')[0], settings.AUTH_USER_MODEL.split('.')[1])
    UserGeneralSettings = apps.get_model('accounts', 'UserGeneralSettings')
    UserChatFolderSettings = apps.get_model('accounts', 'UserChatFolderSettings')

    general_existing = set(
        UserGeneralSettings.objects.values_list('user_id', flat=True)
    )
    folder_existing = set(
        UserChatFolderSettings.objects.values_list('user_id', flat=True)
    )

    users = User.objects.only('id')
    UserGeneralSettings.objects.bulk_create([
        UserGeneralSettings(user=user)
        for user in users
        if user.id not in general_existing
    ])
    UserChatFolderSettings.objects.bulk_create([
        UserChatFolderSettings(user=user)
        for user in users
        if user.id not in folder_existing
    ])


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_extend_privacy_settings'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserGeneralSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('settings_json', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='general_settings', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='UserChatFolderSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('settings_json', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='chat_folder_settings', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.RunPython(create_default_settings, migrations.RunPython.noop),
    ]
