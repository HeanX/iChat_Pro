from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_userprofile_user_type"),
    ]

    operations = [
        migrations.CreateModel(
            name="KeyVerificationRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("requester_key_fingerprint", models.CharField(max_length=64)),
                ("requester_key_version", models.PositiveIntegerField()),
                ("responder_key_fingerprint", models.CharField(max_length=64)),
                ("responder_key_version", models.PositiveIntegerField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("accepted", "Accepted"),
                            ("declined", "Declined"),
                            ("expired", "Expired"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("responded_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "requester",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="key_verification_requests_sent",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "responder",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="key_verification_requests_received",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["requester", "responder", "status"], name="accounts_ke_request_aec6be_idx"),
                    models.Index(fields=["responder", "status"], name="accounts_ke_respond_a4d8f7_idx"),
                ],
            },
        ),
    ]
