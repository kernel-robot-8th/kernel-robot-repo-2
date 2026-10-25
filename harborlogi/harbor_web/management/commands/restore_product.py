import os
import sqlite3
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from harbor_web.runtime import restore_product


class Command(BaseCommand):
    help = 'Restore into new targets with execution reconciliation gate.'

    def add_arguments(self, parser):
        parser.add_argument('--backup', required=True)
        parser.add_argument('--web-database', required=True)
        parser.add_argument('--ledger-database', required=True)

    def handle(self, **options):
        try:
            restore_product(options['backup'], options['web_database'], options['ledger_database'], settings.MODE)
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
            raise CommandError(str(error)) from error
        self.stdout.write('Completed; protect backup authentication data and reconcile restored execution externally.')
