"""Create the clinic staff accounts shown on the login page.

The list comes from the STAFF_USERS setting in .env (kept out of the public
repository), format:  username:Display name:password, separated by commas.

    STAFF_USERS=ina:ინა:Secret1,miranda:მირანდა:Secret2

Existing users keep their password unless --reset-passwords is given.
Run again any time; it is safe to repeat.
"""
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError

from apps.mdf.models import StaffProfile

GROUP_NAME = 'ექიმები'
# What staff may manage in /admin/ (the clinic medicines list etc.).
PERMISSIONS = [
    ('mdf', 'medicine', ('view', 'add', 'change', 'delete')),
    ('mdf', 'prescriptiontemplate', ('view', 'add', 'change', 'delete')),
    ('mdf', 'staffprofile', ('view', 'change')),
]


def parse_staff(value):
    staff = []
    for item in (value or '').split(','):
        item = item.strip()
        if not item:
            continue
        parts = item.split(':', 2)
        if len(parts) != 3 or not all(p.strip() for p in parts):
            raise CommandError(f'Bad STAFF_USERS entry: "{item}" (need username:name:password)')
        staff.append(tuple(p.strip() for p in parts))
    return staff


class Command(BaseCommand):
    help = 'Create/refresh the staff accounts and their login-page profiles.'

    def add_arguments(self, parser):
        parser.add_argument('--reset-passwords', action='store_true',
                            help='Also set the password of users that already exist.')

    def handle(self, *args, **options):
        staff = parse_staff(os.environ.get('STAFF_USERS', ''))
        if not staff:
            raise CommandError('STAFF_USERS is empty. Add it to the .env file next to manage.py.')

        group, _ = Group.objects.get_or_create(name=GROUP_NAME)
        perms = []
        for app_label, model, actions in PERMISSIONS:
            for action in actions:
                perms.append(Permission.objects.get(
                    content_type__app_label=app_label, codename=f'{action}_{model}'))
        group.permissions.set(perms)

        User = get_user_model()
        for order, (username, display_name, password) in enumerate(staff, start=1):
            user, created = User.objects.get_or_create(username=username)
            if created or options['reset_passwords']:
                user.set_password(password)
            user.first_name = display_name
            user.is_active = True
            user.is_staff = True
            user.save()
            user.groups.add(group)
            profile, _ = StaffProfile.objects.get_or_create(
                user=user, defaults={'display_name': display_name})
            profile.display_name = display_name
            profile.order = order
            profile.save()
            state = 'created' if created else ('password reset' if options['reset_passwords'] else 'kept password')
            self.stdout.write(f'{username} ({display_name}): {state}')
        self.stdout.write(self.style.SUCCESS(f'{len(staff)} staff accounts ready.'))
