from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("", views.order_list, name="list"),
    path("add/", views.order_create, name="create"),
    path("<int:pk>/", views.order_detail, name="detail"),
    path("<int:pk>/edit/", views.order_update, name="update"),
    path("<int:pk>/action/<str:key>/", views.order_action, name="action"),
    path("<int:pk>/assign/", views.order_assign, name="assign"),
    path("<int:pk>/observations/add/", views.observation_create, name="observation_create"),
    path("<int:pk>/observations/fetch/", views.observation_fetch, name="observation_fetch"),
    path("customers/<int:customer_id>/channels/", views.customer_channels, name="customer_channels"),
]