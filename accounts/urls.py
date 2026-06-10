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
    path(
        'contacts/<int:contact_id>/chat/',
        views.contact_chat_view,
        name='contact_chat',
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

    # Public-key management (multi-version E2EE API)
    path('api/keys/upload/', views.upload_public_key_view, name='upload-public-key'),
    path('api/keys/batch/', views.batch_public_keys_view, name='batch-public-keys'),
    path(
        'api/keys/fingerprint/<int:user_id>/',
        views.public_key_fingerprint_view,
        name='public-key-fingerprint',
    ),
    path(
        'api/keys/<int:user_id>/<int:key_version>/',
        views.public_key_version_view,
        name='public-key-version',
    ),
    path('api/keys/<int:user_id>/', views.public_key_view, name='public-key'),
    # T38: Key trust management
    path(
        'api/keys/fingerprints/',
        views.my_fingerprints_view,
        name='my-fingerprints',
    ),
    path(
        'api/keys/contacts/<int:user_id>/fingerprints/',
        views.contact_fingerprints_view,
        name='contact-fingerprints',
    ),
    path(
        'api/keys/contacts/<int:user_id>/trust/',
        views.key_trust_view,
        name='key-trust',
    ),
    path(
        'api/keys/trust/',
        views.key_trust_list_view,
        name='key-trust-list',
    ),
    # Notification settings (P2 T23)
    path(
        'api/settings/notifications/',
        views.notification_settings_view,
        name='notification-settings',
    ),
    path(
        'api/settings/notifications/update/',
        views.notification_settings_update_view,
        name='notification-settings-update',
    ),
    # Storage, privacy, and blocked-user endpoints have been consolidated
    # into chat/urls.py (ketter1024's P2 T05/T06/T19-T40 views).
    # QR code card (P2 T30)
    path('api/qr-card/', views.qr_card_view, name='qr-card'),
    # Multi-account context (P2 T35)
    path('api/account/context/', views.multi_account_view, name='multi-account'),
    path('api/account/context/update/', views.multi_account_update_view, name='multi-account-update'),
    # Session management (P2 T36)
    path('api/sessions/', views.session_list_view, name='session-list'),
    path('api/sessions/terminate/', views.session_terminate_view, name='session-terminate'),
    # Profile sync events (P2 T39)
    path('api/profile/updates/', views.profile_updates_view, name='profile-updates'),
]
