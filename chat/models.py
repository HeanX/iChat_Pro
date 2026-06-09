from django.conf import settings
from django.db import models


class Conversation(models.Model):
    """Unified conversation table for both private and group chats."""

    class Type(models.TextChoices):
        SINGLE = "single", "Private Chat"
        GROUP = "group", "Group Chat"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"
        DELETED = "deleted", "Deleted"

    type = models.CharField(max_length=20, choices=Type.choices)
    name = models.CharField(max_length=100, blank=True, help_text="Group chat name; empty for private chats.")
    avatar = models.CharField(max_length=255, blank=True, help_text="Group avatar URL.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_conversations",
    )
    last_message_at = models.DateTimeField(null=True, blank=True)
    last_message_id = models.IntegerField(null=True, blank=True)
    membership_version = models.IntegerField(default=1)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["type"]),
            models.Index(fields=["last_message_at"]),
        ]

    def __str__(self):
        return f"Conversation #{self.id} ({self.get_type_display()})"


class ConversationMember(models.Model):
    """Membership relationship between conversations and users."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        LEFT = "left", "Left"
        REMOVED = "removed", "Removed"
        MUTED = "muted", "Muted"

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="members"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversation_memberships",
    )
    role = models.CharField(
        max_length=20, choices=Role.choices, default=Role.MEMBER
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )
    unread_count = models.IntegerField(default=0)
    last_read_message_id = models.IntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "user"],
                name="unique_conversation_member",
            )
        ]
        indexes = [
            models.Index(fields=["conversation_id"]),
            models.Index(fields=["user_id"]),
            models.Index(fields=["conversation_id", "status"]),
        ]

    def __str__(self):
        return f"Member #{self.user_id} in Conversation #{self.conversation_id}"


class EncryptedMessage(models.Model):
    """Private chat encrypted message. NO plaintext fields allowed."""

    class MessageType(models.TextChoices):
        TEXT = "text", "Text"
        IMAGE = "image", "Image"
        FILE = "file", "File"
        STICKER = "sticker", "Sticker"
        SYSTEM = "system", "System"

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        READ = "read", "Read"
        DELETED = "deleted", "Deleted"
        FAILED = "failed", "Failed"

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_messages",
    )
    receiver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="received_messages",
    )
    message_type = models.CharField(
        max_length=20, choices=MessageType.choices, default=MessageType.TEXT
    )
    ciphertext = models.TextField(null=True, blank=True)
    nonce = models.CharField(max_length=64, null=True, blank=True)
    auth_tag = models.CharField(max_length=64, null=True, blank=True)
    algorithm = models.CharField(max_length=50)
    sender_key_version = models.IntegerField(null=True, blank=True)
    receiver_key_version = models.IntegerField(null=True, blank=True)
    client_message_id = models.CharField(max_length=64, null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SENT
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["conversation_id", "created_at"]),
            models.Index(fields=["sender_id"]),
            models.Index(fields=["receiver_id"]),
            models.Index(fields=["status"]),
            models.Index(fields=["conversation_id", "id"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["sender", "client_message_id"],
                name="unique_client_private_message",
                condition=models.Q(client_message_id__isnull=False),
            ),
        ]

    def __str__(self):
        return f"Message #{self.id} from #{self.sender_id} to #{self.receiver_id}"


class GroupMessage(models.Model):
    """Logical group message record. Ciphertext is stored per-recipient."""

    class MessageType(models.TextChoices):
        TEXT = "text", "Text"
        IMAGE = "image", "Image"
        FILE = "file", "File"
        STICKER = "sticker", "Sticker"
        SYSTEM = "system", "System"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DELETED = "deleted", "Deleted"

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="group_messages"
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_group_messages",
    )
    message_type = models.CharField(
        max_length=20, choices=MessageType.choices, default=MessageType.TEXT
    )
    client_message_id = models.CharField(max_length=64, null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["conversation_id", "created_at"]),
            models.Index(fields=["sender_id"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["sender", "client_message_id"],
                name="unique_client_group_message",
                condition=models.Q(client_message_id__isnull=False),
            ),
        ]

    def __str__(self):
        return f"GroupMessage #{self.id} in Conversation #{self.conversation_id}"


class GroupMessageRecipient(models.Model):
    """Per-recipient encrypted copy of a group message."""

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        READ = "read", "Read"

    group_message = models.ForeignKey(
        GroupMessage, on_delete=models.CASCADE, related_name="recipients"
    )
    receiver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="group_message_copies",
    )
    ciphertext = models.TextField(null=True, blank=True)
    nonce = models.CharField(max_length=64, null=True, blank=True)
    auth_tag = models.CharField(max_length=64, null=True, blank=True)
    algorithm = models.CharField(max_length=50)
    sender_key_version = models.IntegerField(null=True, blank=True)
    receiver_key_version = models.IntegerField(null=True, blank=True)
    membership_version = models.IntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SENT
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["group_message", "receiver"],
                name="unique_group_message_recipient",
            )
        ]
        indexes = [
            models.Index(fields=["receiver_id", "created_at"]),
            models.Index(fields=["group_message_id"]),
        ]

    def __str__(self):
        return f"Recipient #{self.receiver_id} for GroupMessage #{self.group_message_id}"


# ── Admin audit log (T31) ─────────────────────────────────────────


class AdminOperationLog(models.Model):
    """Timestamped record of sensitive admin actions for audit."""

    class Action(models.TextChoices):
        ACTIVATE_USER = "activate_user", "Activate User"
        DEACTIVATE_USER = "deactivate_user", "Deactivate User"
        DELETE_GROUP = "delete_group", "Delete Group"
        DELETE_MESSAGE = "delete_message", "Delete Message"

    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="admin_actions",
    )
    action = models.CharField(max_length=30, choices=Action.choices)
    target_type = models.CharField(max_length=50)
    target_id = models.IntegerField()
    details = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["admin_id", "-created_at"]),
            models.Index(fields=["action"]),
        ]

    def __str__(self):
        return f"AdminOp #{self.id}: {self.get_action_display()} by {self.admin}"


# ── Soft-delete policy (documented — T31) ─────────────────────────
#
# - Conversation:   status=DELETED  (soft delete; data preserved)
# - GroupMessage:   status=DELETED  (soft delete; data preserved)
# - EncryptedMessage: deleted_at    (soft delete; ciphertext preserved)
#
# Hard-deletion is never performed so that audit trails and
# historical ciphertexts remain recoverable by administrators.
