from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _

from .models import (
    Conversation,
    ConversationMember,
    EncryptedMessage,
    GroupMessage,
    GroupMessageRecipient,
)

# ---------------------------------------------------------------------------
# Admin site branding
# ---------------------------------------------------------------------------
admin.site.site_header = "iChat Pro 管理后台"
admin.site.site_title = "iChat Pro"
admin.site.index_title = _("注意：后台不存储、不展示消息明文 — 所有消息以密文形式保存。")

# ---------------------------------------------------------------------------
# User admin — manage accounts and enable/disable status
# ---------------------------------------------------------------------------
admin.site.unregister(User)


@admin.register(User)
class CustomUserAdmin(BaseUserAdmin):
    list_display = [
        "id",
        "username",
        "email",
        "is_active",
        "is_staff",
        "date_joined",
    ]
    list_filter = ["is_active", "is_staff", "is_superuser"]
    search_fields = ["username", "email"]
    actions = ["activate_users", "deactivate_users"]

    @admin.action(description=_("Activate selected users"))
    def activate_users(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, _(f"{updated} user(s) activated."))

    @admin.action(description=_("Deactivate selected users"))
    def deactivate_users(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, _(f"{updated} user(s) deactivated."))


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
