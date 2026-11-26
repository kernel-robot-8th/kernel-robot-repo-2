"""Shared rules for the service modules: role checks, audited saves, domain-error mapping."""

from django.core.exceptions import PermissionDenied, ValidationError

from harbor_web.auth import has_role
from harbor_web.models import Audit

AUTO_DISPATCH_USERNAME = 'harbor-automatic'


def automatic_actor():
    """Non-interactive author of automatic receipt planning and cell proposals (ADR 0034)."""
    from django.contrib.auth import get_user_model
    user, created = get_user_model().objects.get_or_create(username=AUTO_DISPATCH_USERNAME, defaults={'is_active': False})
    if created:
        user.set_unusable_password()
        user.save(update_fields=['password'])
    return user


class Conflict(Exception):
    pass


def _role(actor, *roles):
    if not has_role(actor, *roles):
        raise PermissionDenied


def _save(actor, obj, action, *, detail=None):
    obj.full_clean()
    obj.save()
    Audit.objects.create(actor=actor, action=action, object_type=type(obj).__name__, object_id=str(obj.pk), detail=detail or {})
    return obj


def _domain(call):
    try:
        return call()
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        raise ValidationError('Invalid structured input: ' + str(error)) from error
