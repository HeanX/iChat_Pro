from django.contrib import admin

from .models import (
    Contact,
    FriendRequest,
    Group,
    GroupMember,
    UserProfile,
    UserPublicKey,
)


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


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'creator', 'member_count', 'created_at')
    search_fields = ('name', 'creator__username')


@admin.register(GroupMember)
class GroupMemberAdmin(admin.ModelAdmin):
    list_display = ('group', 'user', 'role', 'joined_at')
    list_filter = ('role',)
    search_fields = ('group__name', 'user__username')


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('user', 'contact', 'created_at')
    search_fields = ('user__username', 'contact__username')