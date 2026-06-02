from django.contrib import admin

from .models import Contact, FriendRequest, UserProfile, UserPublicKey


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


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('user', 'contact', 'created_at')
    search_fields = ('user__username', 'contact__username')
