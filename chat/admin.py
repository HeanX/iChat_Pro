from django.contrib import admin

from .models import (
    Conversation,
    ConversationMember,
    EncryptedMessage,
    GroupMessage,
    GroupMessageRecipient,
)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "name",
        "type",
        "created_by",
        "status",
        "last_message_at",
        "created_at",
    ]
    list_filter = ["type", "status"]
    search_fields = ["id", "name", "created_by__username"]
    readonly_fields = ["created_at", "updated_at"]
    ordering = ["-last_message_at", "-created_at"]


@admin.register(ConversationMember)
class ConversationMemberAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "conversation",
        "user",
        "role",
        "status",
        "unread_count",
        "joined_at",
    ]
    list_filter = ["role", "status"]
    search_fields = ["conversation__id", "user__username"]
    readonly_fields = ["joined_at"]


@admin.register(EncryptedMessage)
class EncryptedMessageAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "conversation",
        "sender",
        "receiver",
        "message_type",
        "algorithm",
        "status",
        "created_at",
    ]
    list_filter = ["message_type", "status", "algorithm"]
    search_fields = ["conversation__id", "sender__username", "receiver__username"]
    readonly_fields = ["created_at", "updated_at"]
    # Exclude raw ciphertext fields from detail view by default —
    # admins only see metadata, never plaintext (there is none stored).
    fields = [
        "conversation",
        "sender",
        "receiver",
        "message_type",
        "algorithm",
        "sender_key_version",
        "receiver_key_version",
        "ciphertext",
        "nonce",
        "auth_tag",
        "status",
        "created_at",
        "updated_at",
        "deleted_at",
    ]


@admin.register(GroupMessage)
class GroupMessageAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "conversation",
        "sender",
        "message_type",
        "status",
        "created_at",
    ]
    list_filter = ["message_type", "status"]
    search_fields = ["conversation__id", "sender__username"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(GroupMessageRecipient)
class GroupMessageRecipientAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "group_message",
        "receiver",
        "algorithm",
        "status",
        "created_at",
    ]
    list_filter = ["status", "algorithm"]
    search_fields = ["group_message__id", "receiver__username"]
    readonly_fields = ["created_at"]
    fields = [
        "group_message",
        "receiver",
        "algorithm",
        "sender_key_version",
        "receiver_key_version",
        "ciphertext",
        "nonce",
        "auth_tag",
        "status",
        "created_at",
    ]
