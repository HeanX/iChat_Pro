from django.contrib import admin

from .models import UserPublicKey


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
