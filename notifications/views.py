from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Notification


@login_required
def notification_list(request):
    only_unread = request.GET.get("filter") == "unread"
    items = Notification.objects.filter(recipient=request.user)
    if only_unread:
        items = items.filter(read_at__isnull=True)

    page_obj = Paginator(items, 20).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "notifications/list.html", {
        "page_obj": page_obj,
        "only_unread": only_unread,
        "querystring": params.urlencode(),
    })


@login_required
def notification_open(request, pk):
    item = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if item.read_at is None:
        item.read_at = timezone.now()
        item.save(update_fields=["read_at"])
    if item.link.startswith("/") and not item.link.startswith("//"):
        return redirect(item.link)
    return redirect("notifications:list")


@login_required
@require_POST
def mark_all_read(request):
    Notification.objects.filter(recipient=request.user, read_at__isnull=True).update(
        read_at=timezone.now()
    )
    messages.success(request, "All notifications marked as read.")
    return redirect("notifications:list")