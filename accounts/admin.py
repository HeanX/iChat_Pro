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
    list_display = ('user', 'fingerprint_short', 'algorithm', 'created_at')
    search_fields = ('user__username', 'fingerprint')
    readonly_fields = ('fingerprint', 'public_key')

    @admin.display(description='Fingerprint')
    def fingerprint_short(self, obj):
        return obj.fingerprint[:32] + '…'


class GroupMemberInline(admin.TabularInline):
    model = GroupMember
    extra = 0
    fields = ('user', 'role', 'joined_at')
    readonly_fields = ('joined_at',)


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'creator', 'member_count', 'created_at')
    search_fields = ('name', 'creator__username')
    inlines = [GroupMemberInline]


@admin.register(GroupMember)
class GroupMemberAdmin(admin.ModelAdmin):
    list_display = ('group', 'user', 'role', 'joined_at')
    list_filter = ('role',)
    search_fields = ('group__name', 'user__username')


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('user', 'contact', 'created_at')
    search_fields = ('user__username', 'contact__username')
