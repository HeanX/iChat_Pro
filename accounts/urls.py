from django.urls import path

from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),
    path('api/keys/upload/', views.upload_public_key_view, name='upload-public-key'),
    path('api/keys/batch/', views.batch_public_keys_view, name='batch-public-keys'),
    path('api/keys/fingerprint/<int:user_id>/', views.public_key_fingerprint_view, name='public-key-fingerprint'),
    path('api/keys/<int:user_id>/<int:key_version>/', views.public_key_version_view, name='public-key-version'),
    path('api/keys/<int:user_id>/', views.public_key_view, name='public-key'),
]
