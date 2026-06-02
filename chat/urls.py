from django.urls import path

from . import views

urlpatterns = [
    path('', views.index_view, name='index'),
    path('settings/', views.settings_view, name='settings'),
    # Private chat history
    path(
        'api/conversations/<int:conversation_id>/messages/',
        views.conversation_messages_view,
        name='api_conversation_messages',
    ),
    # Group management
    path('api/groups/', views.create_group_view, name='api_create_group'),
    path('api/groups/<int:conversation_id>/', views.update_group_view, name='api_update_group'),
    path('api/groups/<int:conversation_id>/invite/', views.invite_member_view, name='api_invite_member'),
    path('api/groups/<int:conversation_id>/remove/', views.remove_member_view, name='api_remove_member'),
    path('api/groups/<int:conversation_id>/disband/', views.disband_group_view, name='api_disband_group'),
    path('api/groups/<int:conversation_id>/messages/', views.group_messages_view, name='api_group_messages'),
]
