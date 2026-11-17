from django.urls import path

from . import views

app_name = "customers"

urlpatterns = [
    path("", views.customer_list, name="list"),
    path("add/", views.customer_create, name="create"),
    path("<int:pk>/", views.customer_detail, name="detail"),
    path("<int:pk>/edit/", views.customer_update, name="update"),
    path("<int:pk>/archive/", views.customer_toggle_archive, name="archive"),
    path("<int:pk>/portal/<str:action>/", views.customer_portal_action, name="portal_action"),
    path("<int:pk>/report.pdf", views.customer_report_pdf, name="report_pdf"),
    path("<int:pk>/report/email/", views.customer_report_email, name="report_email"),
    path("<int:pk>/report/whatsapp/", views.customer_whatsapp_send, name="report_whatsapp"),
]