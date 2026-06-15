from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("chat", "0013_encryptedfile_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="encryptedfilekey",
            name="sender",
            field=models.ForeignKey(
                blank=True,
                help_text="User whose identity key wrapped this file key. Null falls back to file owner for legacy rows.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="wrapped_file_keys",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
