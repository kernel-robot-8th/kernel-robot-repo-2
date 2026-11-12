import json
import os
import subprocess
import sys

import pytest

from harbor.workflow import BusinessLedger


def test_business_ledger_connection_pragmas(tmp_path):
    ledger = BusinessLedger(tmp_path / 'business.sqlite3', placement_sources=('camera',),
                            retreat_sources=('fleet',))
    with ledger._connect() as connection:
        assert connection.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert connection.execute('PRAGMA busy_timeout').fetchone()[0] == 10000
        assert connection.execute('PRAGMA synchronous').fetchone()[0] == 1


@pytest.mark.parametrize('database, expected_journal', [(':memory:', 'memory'), ('file', 'wal')])
def test_django_default_connection_pragmas(tmp_path, database, expected_journal):
    path = str(tmp_path / 'web.sqlite3') if database == 'file' else ':memory:'
    env = dict(os.environ, DJANGO_SETTINGS_MODULE='harbor_web.settings', HARBOR_WEB_MODE='test',
               HARBOR_WEB_SECRET_KEY='x' * 50, HARBOR_WEB_DATABASE=path)
    script = '''
import django
django.setup()
from django.db import connection, transaction
with connection.cursor() as cursor:
    result = {name: cursor.execute('PRAGMA ' + name).fetchone()[0]
              for name in ('journal_mode', 'foreign_keys', 'busy_timeout', 'synchronous')}
result['transaction_mode'] = connection.transaction_mode
statements = []
connection.connection.set_trace_callback(statements.append)
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
result['began_immediate'] = any(statement == 'BEGIN IMMEDIATE' for statement in statements)
print(__import__('json').dumps(result))
'''
    run = subprocess.run([sys.executable, '-c', script], env=env, text=True,
                         capture_output=True, check=True)
    result = json.loads(run.stdout)
    assert result == {'journal_mode': expected_journal, 'foreign_keys': 1,
                      'busy_timeout': 20000, 'synchronous': 1,
                      'transaction_mode': 'IMMEDIATE', 'began_immediate': True}
