from django.conf import settings
from django.db import models


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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.nickname or self.user.get_short_name() or self.user.username


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


class Group(models.Model):
    """A group chat."""

    name = models.CharField(max_length=100)
    description = models.TextField(max_length=500, blank=True)
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_groups',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    @property
    def member_count(self):
        return self.members.count()


class GroupMember(models.Model):
    """Membership of a user in a group."""

    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        MEMBER = 'member', 'Member'

    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name='members',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='group_memberships',
    )
    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.MEMBER,
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['joined_at']
        constraints = [
            models.UniqueConstraint(
                fields=['group', 'user'],
                name='unique_group_member',
            ),
        ]

    def __str__(self):
        return f'{self.user.username} in {self.group.name} ({self.role})'