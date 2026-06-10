from django.contrib import admin

from .models import (
    BlockedUser,
    Contact,
    FriendRequest,
    UserNotificationSettings,
    UserPrivacySettings,
    UserProfile,
    UserPublicKey,
)
# Group & GroupMember consolidated into chat.Conversation (T22)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'nickname', 'created_at', 'updated_at')
    search_fields = ('user__username', 'nickname')


@admin.register(FriendRequest)
class FriendRequestAdmin(admin.ModelAdmin):
    list_display = ('sender', 'receiver', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('sender__username', 'receiver__username')


@admin.register(UserPublicKey)
class UserPublicKeyAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'algorithm',
        'key_version',
        'key_fingerprint',
        'is_active',
        'created_at',
    )
    list_filter = ('algorithm', 'is_active')
    search_fields = ('user__username', 'key_fingerprint')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('user', 'contact', 'created_at')
    search_fields = ('user__username', 'contact__username')


@admin.register(UserPrivacySettings)
class UserPrivacySettingsAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'last_seen_visibility', 'profile_photo_visibility',
        'phone_number_visibility', 'two_step_verification_enabled',
        'updated_at',
    )
    list_filter = (
        'last_seen_visibility', 'two_step_verification_enabled',
        'passcode_lock_enabled',
    )
    search_fields = ('user__username',)


@admin.register(BlockedUser)
class BlockedUserAdmin(admin.ModelAdmin):
    list_display = ('blocker', 'blocked', 'created_at')
    search_fields = ('blocker__username', 'blocked__username')


@admin.register(UserNotificationSettings)
class UserNotificationSettingsAdmin(admin.ModelAdmin):
    list_display = ('user', 'notification_sound', 'volume', 'created_at')
    search_fields = ('user__username',)