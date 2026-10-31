import os
import sqlite3
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from harbor_web.runtime import backup_product


class Command(BaseCommand):
    help = 'Create a private matched backup while all writers are stopped.'

    def add_arguments(self, parser):
        parser.add_argument('--output', required=True)

    def handle(self, **options):
        try:
            backup_product(options['output'], settings.DATABASES['default']['NAME'], os.environ.get('HARBOR_LEDGER_PATH'), settings.MODE)
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
            raise CommandError(str(error)) from error
        self.stdout.write('Completed; protect backup authentication data and reconcile restored execution externally.')
