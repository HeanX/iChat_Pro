from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("chat", "0011_backfill_group_recipient_metadata"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChatReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reason", models.CharField(choices=[("spam", "Spam"), ("harassment", "Harassment"), ("attack", "Personal Attack"), ("illegal", "Illegal Content"), ("other", "Other")], default="other", max_length=32)),
                ("details", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("conversation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reports", to="chat.conversation")),
                ("reporter", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="chat_reports", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name="chatreport",
            index=models.Index(fields=["conversation", "created_at"], name="chat_chatre_convers_da2801_idx"),
        ),
        migrations.AddIndex(
            model_name="chatreport",
            index=models.Index(fields=["reporter", "created_at"], name="chat_chatre_reporte_cb2e6a_idx"),
        ),
    ]
