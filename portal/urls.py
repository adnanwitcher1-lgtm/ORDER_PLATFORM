from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("<str:token>/", views.portal_home, name="home"),
    path("<str:token>/order/<int:pk>/", views.portal_order, name="order"),
    path("<str:token>/report.pdf", views.portal_report, name="report"),
]
