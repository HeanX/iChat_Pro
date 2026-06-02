from django.urls import path

from . import views

urlpatterns = [
    # Auth
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),

    # Profile
    path('profile/edit/', views.profile_edit_view, name='profile_edit'),

    # Contacts
    path('contacts/', views.contact_list_view, name='contacts'),
    path('contacts/search/', views.search_users, name='search_users'),
    path(
        'contacts/request/send/',
        views.friend_request_send,
        name='friend_request_send',
    ),
    path(
        'contacts/request/<int:request_id>/accept/',
        views.friend_request_accept,
        name='friend_request_accept',
    ),
    path(
        'contacts/request/<int:request_id>/reject/',
        views.friend_request_reject,
        name='friend_request_reject',
    ),
    path(
        'contacts/<int:contact_id>/delete/',
        views.contact_delete,
        name='contact_delete',
    ),

    # Groups
    path('groups/', views.group_list_view, name='groups'),
    path('groups/create/', views.group_create_view, name='group_create'),
    path(
        'groups/<int:group_id>/',
        views.group_detail_view,
        name='group_detail',
    ),
    path(
        'groups/<int:group_id>/add-member/',
        views.group_add_member_view,
        name='group_add_member',
    ),
    path(
        'groups/<int:group_id>/leave/',
        views.group_leave_view,
        name='group_leave',
    ),

    # Public-key management
    path(
        'keys/upload/',
        views.upload_public_key,
        name='upload_public_key',
    ),
    path(
        'keys/<str:username>/',
        views.get_public_key,
        name='get_public_key',
    ),
]
