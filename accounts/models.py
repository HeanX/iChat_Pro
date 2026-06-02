from django.db import models
from django.conf import settings


class UserPublicKey(models.Model):
    ALGORITHM_ECDH_P256 = 'ECDH-P256'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='public_keys',
    )
    identity_public_key = models.TextField()
    key_fingerprint = models.CharField(max_length=64, db_index=True)
    algorithm = models.CharField(max_length=50, default=ALGORITHM_ECDH_P256)
    key_version = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'key_version'],
                name='unique_user_public_key_version',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'is_active']),
        ]
        ordering = ['-key_version']

    def __str__(self):
        return f'{self.user.username} key v{self.key_version}'
