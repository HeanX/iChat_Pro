from django.urls import path

from . import views

urlpatterns = [
    path('', views.index_view, name='index'),
    path('settings/', views.settings_view, name='settings'),
    path(
        'api/conversations/<int:conversation_id>/messages/',
        views.conversation_messages_view,
        name='api_conversation_messages',
    ),
]
