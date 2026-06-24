from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0017_group_recipient_ephemeral_key'),
    ]

    operations = [
        migrations.AddField(
            model_name='encryptedmessage',
            name='sender_ephemeral_public_key',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='encryptedmessage',
            name='sender_copy_ciphertext',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='encryptedmessage',
            name='sender_copy_nonce',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name='encryptedmessage',
            name='sender_copy_auth_tag',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name='encryptedmessage',
            name='sender_copy_ephemeral_public_key',
            field=models.TextField(blank=True, null=True),
        ),
    ]
