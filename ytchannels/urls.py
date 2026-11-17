from django.urls import path

from . import views

app_name = "channels"

urlpatterns = [
    path("", views.channel_list, name="list"),
    path("add/", views.channel_create, name="create"),
    path("<int:pk>/edit/", views.channel_update, name="update"),
    path("<int:pk>/archive/", views.channel_toggle_archive, name="archive"),
]