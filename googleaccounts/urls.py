from django.urls import path

from . import views

app_name = "google"

urlpatterns = [
    path("", views.connection_list, name="list"),
    path("connect/", views.connect_start, name="connect"),
    path("callback/", views.oauth_callback, name="callback"),
    path("<int:pk>/", views.connection_detail, name="detail"),
    path("<int:pk>/edit/", views.connection_edit, name="edit"),
    path("<int:pk>/check/", views.connection_check, name="check"),
    path("<int:pk>/reauthorize/", views.connection_reauthorize, name="reauthorize"),
    path("<int:pk>/disconnect/", views.connection_disconnect, name="disconnect"),
]