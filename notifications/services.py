from django.db.models import Q

from accounts.models import User

from .models import Notification


def managers_and_admins():
    return list(
        User.objects.filter(is_active=True).filter(
            Q(role=User.Role.MANAGER) | Q(is_superuser=True)
        )
    )


def notify(users, kind, message, link="", dedupe=False, exclude=None):
    """One notification per user. `exclude` is the user who performed the action.
    dedupe=True: if an unread notification with the same message already exists, none is created."""
    seen, new = set(), []
    for user in users:
        if user is None or user.pk in seen:
            continue
        if exclude is not None and user.pk == exclude.pk:
            continue
        seen.add(user.pk)
        if dedupe and Notification.objects.filter(
            recipient=user, kind=kind, message=message, read_at__isnull=True
        ).exists():
            continue
        new.append(Notification(recipient=user, kind=kind, message=message, link=link))
    Notification.objects.bulk_create(new)
    return len(new)