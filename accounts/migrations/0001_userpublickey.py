from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UserPublicKey',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('identity_public_key', models.TextField()),
                ('key_fingerprint', models.CharField(db_index=True, max_length=64)),
                ('algorithm', models.CharField(default='ECDH-P256', max_length=50)),
                ('key_version', models.PositiveIntegerField()),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='public_keys', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-key_version'],
                'indexes': [models.Index(fields=['user', 'is_active'], name='accounts_us_user_id_011054_idx')],
                'constraints': [models.UniqueConstraint(fields=('user', 'key_version'), name='unique_user_public_key_version')],
            },
        ),
    ]
