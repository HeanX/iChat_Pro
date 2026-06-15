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
    # T27: Auto-delete messages — global default for the conversation (seconds)
    auto_delete_seconds = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Auto-delete messages after N seconds. NULL means disabled.",
    )
    # T37: Group mute
    muted_until = models.DateTimeField(null=True, blank=True)
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
    # T19 per-user conversation state
    is_pinned = models.BooleanField(default=False)
    muted_until = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    hidden_at = models.DateTimeField(null=True, blank=True)
    cleared_at = models.DateTimeField(null=True, blank=True)
    # T27: Per-conversation auto-delete override (seconds, null = inherit global)
    auto_delete_seconds = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Per-conversation auto-delete override in seconds.",
    )

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
            models.Index(fields=["user", "hidden_at", "is_pinned"]),
            models.Index(fields=["user", "archived_at"]),
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
        RECALLED = "recalled", "Recalled"

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
    reply_to_message_id = models.IntegerField(null=True, blank=True, help_text="ID of the message being replied to.")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SENT
    )
    recalled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    file_id = models.ForeignKey(
        "EncryptedFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="private_messages",
    )
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
        RECALLED = "recalled", "Recalled"

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
    reply_to_message_id = models.IntegerField(null=True, blank=True, help_text="ID of the message being replied to.")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )
    recalled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    file_id = models.ForeignKey(
        "EncryptedFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="group_messages",
    )

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
        FAILED = "failed", "Failed"
        RECALLED = "recalled", "Recalled"

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


# ── T21: Per-user message deletion tracking ────────────────────────


class UserMessageDeletion(models.Model):
    """Tracks messages a user has hidden from their own view (T20/T21).

    Messages are never hard-deleted; this record filters them from the
    requesting user's message history queries.
    """

    class MessageType(models.TextChoices):
        PRIVATE = "private", "Private"
        GROUP = "group", "Group"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="message_deletions",
    )
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="user_deletions",
    )
    message_type = models.CharField(
        max_length=10, choices=MessageType.choices,
    )
    message_id = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "message_type", "message_id"],
                name="unique_user_message_deletion",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "message_type", "message_id"]),
            models.Index(fields=["conversation", "user"]),
        ]

    def __str__(self):
        return f"User #{self.user_id} deleted {self.message_type} msg #{self.message_id}"


# ── T22: User presence / online status ──────────────────────────────


class ChatReport(models.Model):
    """A user-submitted report for a conversation."""

    class Reason(models.TextChoices):
        SPAM = "spam", "Spam"
        HARASSMENT = "harassment", "Harassment"
        ATTACK = "attack", "Personal Attack"
        ILLEGAL = "illegal", "Illegal Content"
        OTHER = "other", "Other"

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_reports",
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="reports",
    )
    reason = models.CharField(
        max_length=32,
        choices=Reason.choices,
        default=Reason.OTHER,
    )
    details = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
            models.Index(fields=["reporter", "created_at"]),
        ]

    def __str__(self):
        return f"Report #{self.id} by #{self.reporter_id} on conversation #{self.conversation_id}"


class UserPresence(models.Model):
    """Tracks user online status and last-seen timestamp (T22)."""

    class Status(models.TextChoices):
        ONLINE = "online", "Online"
        AWAY = "away", "Away"
        BUSY = "busy", "Busy"
        OFFLINE = "offline", "Offline"

    class Visibility(models.TextChoices):
        EVERYONE = "everyone", "Everyone"
        CONTACTS = "contacts", "Contacts Only"
        NOBODY = "nobody", "Nobody"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="presence",
    )
    is_online = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ONLINE,
    )
    presence_visibility = models.CharField(
        max_length=20, choices=Visibility.choices, default=Visibility.EVERYONE,
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} is {self.get_status_display()}"


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


# ── T37: Group announcement ─────────────────────────────────────────


class GroupAnnouncement(models.Model):
    """Pinned group announcement. Only one active per group at a time."""

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="announcements",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="group_announcements",
    )
    content = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["conversation", "is_active"]),
        ]

    def __str__(self):
        return f"Announcement #{self.id} in Conversation #{self.conversation_id}"


# ── Encrypted File Transfer ───────────────────────────────────────


class EncryptedFile(models.Model):
    """Encrypted file uploaded by a user. Server never sees plaintext."""

    class MessageKind(models.TextChoices):
        IMAGE = "image", "Image"
        FILE = "file", "File"
        STICKER = "sticker", "Sticker"

    class Status(models.TextChoices):
        UPLOADING = "uploading", "Uploading"
        AVAILABLE = "available", "Available"
        FAILED = "failed", "Failed"
        DELETED = "deleted", "Deleted"

    class DerivativeRole(models.TextChoices):
        ORIGINAL = "original", "Original"
        THUMBNAIL = "thumbnail", "Thumbnail"
        PREVIEW = "preview", "Preview"

    upload_id = models.CharField(
        max_length=36, unique=True,
        help_text="UUID for the upload session, used in API URLs.",
    )
    client_file_id = models.CharField(
        max_length=64,
        help_text="Client-generated UUID, idempotency key.",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="uploaded_files",
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="files",
    )
    message_kind = models.CharField(
        max_length=20, choices=MessageKind.choices,
    )
    parent_file_id = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="derivatives",
    )
    derivative_role = models.CharField(
        max_length=20, null=True, blank=True, choices=DerivativeRole.choices,
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.UPLOADING,
    )
    storage_path = models.CharField(
        max_length=512, blank=True,
        help_text="Relative path to merged ciphertext file under MEDIA_ROOT.",
    )
    total_size_bytes = models.PositiveBigIntegerField()
    chunk_size_bytes = models.PositiveIntegerField(default=1048576)
    chunk_count = models.PositiveIntegerField()
    ciphertext_sha256 = models.CharField(
        max_length=64, blank=True,
        help_text="SHA-256 hex digest of the complete merged ciphertext.",
    )
    encrypted_metadata = models.TextField(blank=True)
    metadata_nonce = models.CharField(max_length=64, blank=True)
    metadata_auth_tag = models.CharField(max_length=64, blank=True)
    algorithm = models.CharField(max_length=50, default="AES-256-GCM")
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "client_file_id"],
                name="unique_client_file_per_owner",
            ),
        ]
        indexes = [
            models.Index(fields=["conversation", "status"]),
            models.Index(fields=["owner", "-created_at"]),
            models.Index(fields=["upload_id"]),
        ]

    def __str__(self):
        return f"EncryptedFile #{self.id} ({self.message_kind}) by #{self.owner_id}"


class EncryptedFileChunk(models.Model):
    """Metadata for one chunk of an encrypted file upload."""

    file = models.ForeignKey(
        EncryptedFile, on_delete=models.CASCADE, related_name="chunks",
    )
    chunk_index = models.PositiveIntegerField()
    size_bytes = models.PositiveIntegerField()
    offset_bytes = models.PositiveBigIntegerField()
    nonce = models.CharField(max_length=64)
    auth_tag = models.CharField(max_length=64)
    ciphertext_sha256 = models.CharField(max_length=64, blank=True)
    storage_path = models.CharField(
        max_length=512, null=True, blank=True,
        help_text="Temp chunk file path; null after merge into complete file.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["file", "chunk_index"],
                name="unique_file_chunk_index",
            ),
        ]

    def __str__(self):
        return f"Chunk #{self.chunk_index} of EncryptedFile #{self.file_id}"


class EncryptedFileKey(models.Model):
    """Per-recipient wrapped file key. Each holder can decrypt the file."""

    file = models.ForeignKey(
        EncryptedFile, on_delete=models.CASCADE, related_name="keys",
    )
    holder = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="file_keys",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wrapped_file_keys",
        help_text="User whose identity key wrapped this file key. Null falls back to file owner for legacy rows.",
    )
    encrypted_file_key = models.TextField()
    nonce = models.CharField(max_length=64)
    auth_tag = models.CharField(max_length=64)
    algorithm = models.CharField(max_length=50, default="AES-256-GCM")
    sender_key_version = models.PositiveIntegerField(null=True, blank=True)
    receiver_key_version = models.PositiveIntegerField(null=True, blank=True)
    membership_version = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["file", "holder"],
                name="unique_file_key_per_holder",
            ),
        ]

    def __str__(self):
        return f"FileKey for EncryptedFile #{self.file_id} holder #{self.holder_id}"


# ── Soft-delete policy (documented — T31) ─────────────────────────
#
# - Conversation:   status=DELETED  (soft delete; data preserved)
# - GroupMessage:   status=DELETED  (soft delete; data preserved)
# - EncryptedMessage: deleted_at    (soft delete; ciphertext preserved)
#
# Hard-deletion is never performed so that audit trails and
# historical ciphertexts remain recoverable by administrators.
