from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0019_add_sender_copy_and_ephemeral_key_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="userllmconfig",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="llm_configs",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="userllmconfig",
            name="assistant_id",
            field=models.CharField(default="ai-assistant", max_length=80),
        ),
        migrations.AddConstraint(
            model_name="userllmconfig",
            constraint=models.UniqueConstraint(
                fields=("user", "assistant_id"),
                name="unique_llm_config_per_assistant",
            ),
        ),
    ]
