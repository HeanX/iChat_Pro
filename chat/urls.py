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
    # T19: Conversation management
    path(
        'api/conversations/<int:conversation_id>/pin/',
        views.pin_conversation_view,
        name='api_pin_conversation',
    ),
    path(
        'api/conversations/<int:conversation_id>/mute/',
        views.mute_conversation_view,
        name='api_mute_conversation',
    ),
    path(
        'api/conversations/<int:conversation_id>/archive/',
        views.archive_conversation_view,
        name='api_archive_conversation',
    ),
    path(
        'api/conversations/<int:conversation_id>/unarchive/',
        views.unarchive_conversation_view,
        name='api_unarchive_conversation',
    ),
    path(
        'api/conversations/<int:conversation_id>/clear/',
        views.clear_conversation_view,
        name='api_clear_conversation',
    ),
    path(
        'api/conversations/<int:conversation_id>/read/',
        views.read_conversation_view,
        name='api_read_conversation',
    ),
    path(
        'api/conversations/<int:conversation_id>/unread/',
        views.unread_conversation_view,
        name='api_unread_conversation',
    ),
    path(
        'api/conversations/<int:conversation_id>/',
        views.hide_conversation_view,
        name='api_hide_conversation',
    ),
    # T20: Message operations
    path(
        'api/conversations/<int:conversation_id>/messages/forward/',
        views.forward_message_view,
        name='api_forward_message',
    ),
    path(
        'api/conversations/<int:conversation_id>/messages/<int:message_id>/',
        views.delete_message_view,
        name='api_delete_message',
    ),
    path(
        'api/conversations/<int:conversation_id>/messages/<int:message_id>/recall/',
        views.recall_message_view,
        name='api_recall_message',
    ),
    path(
        'api/conversations/<int:conversation_id>/messages/<int:message_id>/status/',
        views.message_status_view,
        name='api_message_status',
    ),
    # Private chat history (keep after T20 routes to avoid conflicts)
    path(
        'api/conversations/<int:conversation_id>/messages/',
        views.conversation_messages_view,
        name='api_conversation_messages',
    ),
    # T22: Presence
    path(
        'api/users/<int:user_id>/presence/',
        views.user_presence_view,
        name='api_user_presence',
    ),
    path(
        'api/users/presence/',
        views.update_presence_view,
        name='api_update_presence',
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
]
