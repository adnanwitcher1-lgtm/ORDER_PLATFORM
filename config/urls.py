from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("customers/", include("customers.urls")),
    path("channels/", include("ytchannels.urls")),
    path("orders/", include("orders.urls")),
    path("google-accounts/", include("googleaccounts.urls")),
    path("notifications/", include("notifications.urls")),
    path("reports/", include("reports.urls")),
    path("portal/", include("portal.urls")),
    path("", include("core.urls")),
]