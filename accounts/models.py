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
    Stores a userʼs ECDH P-256 public key (SPKI format) and its
    SHA-256 fingerprint.  The private key NEVER leaves the client.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='public_key',
    )
    public_key = models.TextField(
        help_text='Base64-encoded SPKI public key',
    )
    fingerprint = models.CharField(
        max_length=64,
        unique=True,
        help_text='SHA-256 hex digest of the SPKI public key',
    )
    algorithm = models.CharField(
        max_length=32,
        default='ECDH-P256',
        help_text='Key algorithm identifier',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'PublicKey({self.user.username})  {self.fingerprint[:16]}…'
