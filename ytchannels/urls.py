from django.urls import path

from . import views

app_name = "channels"

urlpatterns = [
    path("", views.channel_list, name="list"),
    path("add/", views.channel_create, name="create"),
    path("lookup/", views.channel_lookup, name="lookup"),
    path("<int:pk>/current-count/", views.channel_current_count, name="current_count"),
    path("<int:pk>/", views.channel_detail, name="detail"),
    path("<int:pk>/insights/refresh/", views.channel_insights_refresh, name="insights_refresh"),
    path("<int:pk>/analytics/refresh/", views.channel_analytics_refresh, name="analytics_refresh"),
    path("<int:pk>/insights/ai/", views.channel_insights_ai, name="insights_ai"),
    path("<int:pk>/edit/", views.channel_update, name="update"),
    path("<int:pk>/archive/", views.channel_toggle_archive, name="archive"),
]