from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0016_groupinvitation'),
    ]

    operations = [
        migrations.AddField(
            model_name='groupmessagerecipient',
            name='sender_ephemeral_public_key',
            field=models.TextField(blank=True, null=True),
        ),
    ]
