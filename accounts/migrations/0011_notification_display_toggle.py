from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_add_profile_update_log'),
    ]

    operations = [
        migrations.AddField(
            model_name='usernotificationsettings',
            name='display_notifications',
            field=models.BooleanField(default=True),
        ),
    ]
