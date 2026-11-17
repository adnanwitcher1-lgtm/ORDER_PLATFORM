from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.report_index, name="index"),
    path("<str:key>/", views.report_detail, name="detail"),
    path("<str:key>/export/<str:fmt>/", views.report_export, name="export"),
]