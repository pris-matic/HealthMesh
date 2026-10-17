import os
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = 'Provision synthetic coordinator/reviewer accounts from environment passwords.'

    @transaction.atomic
    def handle(self, *args, **options):
        passwords = {role: os.environ.get(f'DEMO_{role.upper()}_PASSWORD') for role in ('coordinator', 'reviewer')}
        if not all(passwords.values()):
            raise CommandError('Set DEMO_COORDINATOR_PASSWORD and DEMO_REVIEWER_PASSWORD first.')
        for role, password in passwords.items():
            user, _ = get_user_model().objects.get_or_create(username=f'demo-{role}')
            user.set_password(password)
            user.is_active = True
            user.is_staff = user.is_superuser = False
            user.save()
            user.groups.set([Group.objects.get_or_create(name=role)[0]])
        self.stdout.write('Synthetic demo users provisioned.')
