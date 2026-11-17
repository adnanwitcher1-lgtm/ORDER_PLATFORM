from django.core.management.base import BaseCommand, CommandError

from accounts import twofactor
from accounts.models import User


class Command(BaseCommand):
    help = "Turns off two-factor authentication for a user who lost their device and recovery codes."

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, **options):
        try:
            user = User.objects.get(username=options["username"])
        except User.DoesNotExist:
            raise CommandError("No user with that username.")
        twofactor.disable(user)
        self.stdout.write(f"Two-factor authentication reset for {user.username}. They can set it up again after signing in.")
