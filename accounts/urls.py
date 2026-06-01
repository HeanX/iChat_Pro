from django.urls import path

from . import views

urlpatterns = [
    # Auth
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),

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
]
