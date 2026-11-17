from .models import Notification


def unread_notifications(request):
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        count = Notification.objects.filter(recipient=user, read_at__isnull=True).count()
    else:
        count = 0
    return {"unread_notifications": count}