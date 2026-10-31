from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = 'Create explicit Harbor roles without granting memberships.'
    def handle(self, *args, **options):
        for name in ('viewer', 'operator', 'field', 'admin'):
            group, _ = Group.objects.get_or_create(name=name)
            if name == 'admin':
                group.permissions.add(*Permission.objects.filter(content_type__app_label='auth', content_type__model__in=['user', 'group']))
