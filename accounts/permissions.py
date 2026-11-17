from functools import wraps
from .models import User
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def role_required(*roles):
    """Allow only users whose role is in `roles` (Super Admin always allowed)."""

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapper(request, *args, **kwargs):
            user = request.user
            if user.is_superuser or user.role in roles:
                return view_func(request, *args, **kwargs)
            raise PermissionDenied

        return wrapper

    return decorator
CUSTOMER_EDIT_ROLES = (User.Role.MANAGER, User.Role.SALES)


def can_manage_customers(user):
    return user.is_superuser or user.role in CUSTOMER_EDIT_ROLES
ORDER_EDIT_ROLES = (User.Role.MANAGER, User.Role.SALES)


def can_manage_orders(user):
    return user.is_superuser or user.role in ORDER_EDIT_ROLES
GOOGLE_MANAGE_ROLES = (User.Role.MANAGER,)
GOOGLE_VIEW_ROLES = (User.Role.MANAGER, User.Role.OPERATOR, User.Role.AUDITOR)


def can_manage_google(user):
    return user.is_superuser or user.role in GOOGLE_MANAGE_ROLES