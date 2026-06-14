from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0011_notification_display_toggle'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprivacysettings',
            name='birthday_visibility',
            field=models.CharField(default='contacts', max_length=20),
        ),
        migrations.AddField(
            model_name='userprivacysettings',
            name='gifts_visibility',
            field=models.CharField(default='everyone', max_length=20),
        ),
        migrations.AddField(
            model_name='userprivacysettings',
            name='saved_music_visibility',
            field=models.CharField(default='everyone', max_length=20),
        ),
        migrations.AddField(
            model_name='userprivacysettings',
            name='who_can_add_me_to_groups',
            field=models.CharField(default='everyone', max_length=20),
        ),
    ]
