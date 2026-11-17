from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("settings/", views.site_settings, name="settings"),
    path("ai/summary/<str:scope>/", views.ai_summary, name="ai_summary"),
    path("ai/summary/<str:scope>/<int:pk>/", views.ai_summary, name="ai_summary_item"),
]