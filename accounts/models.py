from django.conf import settings
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


class UserProfile(models.Model):
    """
    Extended profile for Django's built-in User model.
    Supports nickname, avatar, and bio.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
    )
    nickname = models.CharField(max_length=100, blank=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    bio = models.TextField(max_length=500, blank=True)

    # P2 T29: extended profile fields
    phone_number = models.CharField(max_length=20, blank=True)
    location = models.CharField(max_length=100, blank=True)
    birthday = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.nickname or self.user.get_short_name() or self.user.username


class UserPrivacySettings(models.Model):
    """
    Per-user privacy and security preferences (P2 T06).
    One-to-one with User; auto-created via post_save signal.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='privacy_settings',
    )

    # ── Visibility: 'everyone' | 'contacts' | 'nobody' ──
    last_seen_visibility = models.CharField(max_length=20, default='everyone')
    profile_photo_visibility = models.CharField(max_length=20, default='everyone')
    phone_number_visibility = models.CharField(max_length=20, default='contacts')
    bio_visibility = models.CharField(max_length=20, default='everyone')
    forward_link_visibility = models.CharField(max_length=20, default='everyone')

    # ── Permissions: 'everyone' | 'contacts' ──
    who_can_send_messages = models.CharField(max_length=20, default='contacts')
    who_can_voice_video_call = models.CharField(max_length=20, default='contacts')

    # ── Auto-delete messages: 0=off, 1, 7, 30 days ──
    auto_delete_messages_days = models.PositiveSmallIntegerField(default=0)

    # ── Toggles ──
    sensitive_content_filter = models.BooleanField(default=False)
    passcode_lock_enabled = models.BooleanField(default=False)
    two_step_verification_enabled = models.BooleanField(default=False)
    passkey_enabled = models.BooleanField(default=False)  # P2 T28 placeholder

    # ── Login email for two-step verification ──
    login_email = models.EmailField(blank=True, default='')

    # ── Passcode placeholder (P2 T28) ──
    passcode = models.CharField(max_length=128, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'PrivacySettings for {self.user.username}'


class BlockedUser(models.Model):
    """Tracks which users a user has blocked (P2 T06)."""
    blocker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='blocked_users',
    )
    blocked = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='blocked_by',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['blocker', 'blocked'],
                name='unique_block',
            ),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.blocker.username} blocked {self.blocked.username}'


class UserStorageSettings(models.Model):
    """Per-user storage & auto-download preferences (P2 T05).

    Persisted in the database so settings survive session expiry and sync
    across the user's login sessions.  The `settings_json` field holds the
    full auto-download, file-size-limit, cache-retention and cache-max-size
    blob that the frontend reads and saves.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='storage_settings',
    )
    settings_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'StorageSettings for {self.user.username}'


class UserNotificationSettings(models.Model):
    """Per-user notification preferences (P2 T23)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notification_settings',
    )
    offline_notifications = models.BooleanField(default=True)
    all_accounts_notifications = models.BooleanField(default=True)
    notification_sound = models.CharField(max_length=100, default='default')
    volume = models.PositiveSmallIntegerField(default=80)
    message_sent_sound = models.CharField(max_length=100, default='default')
    private_chat_notifications = models.BooleanField(default=True)
    group_chat_notifications = models.BooleanField(default=True)
    channel_notifications = models.BooleanField(default=False)
    message_preview_private = models.BooleanField(default=True)
    message_preview_group = models.BooleanField(default=True)
    message_preview_channel = models.BooleanField(default=True)
    contact_join_notifications = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'NotificationSettings for {self.user.username}'


class MultiAccountContext(models.Model):
    """Per-user multi-account context storage (P2 T35).

    Stores the user's local account-switching state so the frontend
    can remember which accounts the user has added on this device.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='multi_account_context',
    )
    context_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'MultiAccountContext for {self.user.username}'


class FriendRequest(models.Model):
    """A friend request from one user to another."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        ACCEPTED = 'accepted', 'Accepted'
        REJECTED = 'rejected', 'Rejected'

    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sent_requests',
    )
    receiver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='received_requests',
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['sender', 'receiver'],
                condition=models.Q(status='pending'),
                name='unique_pending_request',
            ),
        ]

    def __str__(self):
        return f'{self.sender} → {self.receiver} ({self.status})'


class Contact(models.Model):
    """
    Represents an established friendship between two users.
    The user who first created the contact is stored as `user`;
    the other party is `contact`.  Query both columns to find
    all contacts for a given user.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='contacts_initiated',
    )
    contact = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='contacts_received',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'contact'],
                name='unique_contact_pair',
            ),
        ]

    def __str__(self):
        return f'{self.user} ↔ {self.contact}'


class UserPublicKey(models.Model):
    """
    Stores a user's ECDH P-256 identity public key with multi-version
    history. Older versions stay in the table (`is_active=False`) so that
    historical ciphertexts remain decryptable after a key rotation.
    Private keys NEVER leave the client.
    """

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


# ── P2 T38: Key trust verification ─────────────────────────────────


class KeyTrust(models.Model):
    """Tracks whether a user has verified a contact's public key (T38).

    Trust is per-user, per-contact, per-key-fingerprint — when a contact
    rotates their key, the old trust record is preserved for audit and the
    new key starts as untrusted until verified again.
    """

    class TrustStatus(models.TextChoices):
        UNTRUSTED = "untrusted", "Untrusted"
        TRUSTED = "trusted", "Trusted"
        VERIFIED = "verified", "Verified"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="key_trusts",
    )
    contact = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="key_trusted_by",
    )
    key_fingerprint = models.CharField(max_length=64)
    key_version = models.PositiveIntegerField()
    trust_status = models.CharField(
        max_length=20,
        choices=TrustStatus.choices,
        default=TrustStatus.UNTRUSTED,
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "contact", "key_fingerprint"],
                name="unique_key_trust",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "contact"]),
            models.Index(fields=["contact", "key_fingerprint"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"User #{self.user_id} trusts {self.contact.username} key {self.key_fingerprint[:12]}"


# ── Signal: auto-create UserProfile on user creation (T27) ─────────


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    """Ensure every new user gets a UserProfile, UserPrivacySettings and UserStorageSettings immediately."""
    if created:
        UserProfile.objects.get_or_create(user=instance)
        UserPrivacySettings.objects.get_or_create(user=instance)
        UserStorageSettings.objects.get_or_create(user=instance)


# Group and GroupMember have been consolidated into chat.Conversation
# and chat.ConversationMember.  See T22 for rationale.
