from django.urls import path

from . import views

urlpatterns = [
    path('', views.index_view, name='index'),
    path('settings/', views.settings_view, name='settings'),
]
