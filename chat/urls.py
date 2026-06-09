from django.urls import path

from . import views

urlpatterns = [
    path('', views.index_view, name='index'),
    path('settings/', views.settings_view, name='settings'),
    # Conversation list & creation
    path('api/conversations/', views.conversations_list_view, name='api_conversations'),
    path(
        'api/conversations/create/',
        views.get_or_create_single_conversation_view,
        name='api_conversation_create',
    ),
    # Private chat history
    path(
        'api/conversations/<int:conversation_id>/messages/',
        views.conversation_messages_view,
        name='api_conversation_messages',
    ),
    path(
        'api/conversations/<int:conversation_id>/messages/send/',
        views.send_private_message_view,
        name='api_conversation_message_send',
    ),
    # Group management
    path('api/groups/', views.create_group_view, name='api_create_group'),
    path('api/groups/<int:conversation_id>/', views.update_group_view, name='api_update_group'),
    path('api/groups/<int:conversation_id>/invite/', views.invite_member_view, name='api_invite_member'),
    path('api/groups/<int:conversation_id>/remove/', views.remove_member_view, name='api_remove_member'),
    path('api/groups/<int:conversation_id>/disband/', views.disband_group_view, name='api_disband_group'),
    path('api/groups/<int:conversation_id>/members/', views.group_members_view, name='api_group_members'),
    path('api/groups/<int:conversation_id>/messages/', views.group_messages_view, name='api_group_messages'),
    # P2 T05: Data & Storage
    path('api/storage/stats/', views.storage_stats_view, name='api_storage_stats'),
    path('api/storage/clear/', views.storage_clear_view, name='api_storage_clear'),
    path('api/storage/settings/', views.storage_settings_view, name='api_storage_settings'),
    # P2 T06: Privacy & Security
    path('api/privacy/settings/', views.privacy_settings_view, name='api_privacy_settings'),
    path('api/privacy/blocked/', views.blocked_users_list_view, name='api_blocked_users_list'),
    path('api/privacy/block/', views.block_user_view, name='api_block_user'),
    path('api/privacy/unblock/', views.unblock_user_view, name='api_unblock_user'),
    path('api/privacy/delete-contacts/', views.delete_synced_contacts_view, name='api_delete_synced_contacts'),
    path('api/privacy/delete-account/', views.delete_account_view, name='api_delete_account'),
]
