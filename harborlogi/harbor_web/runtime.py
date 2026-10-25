"""Single writer SQLite deployment and matched, private recovery artifacts."""
from contextlib import closing, ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import uuid
from urllib.parse import urlsplit
from harbor.ownership import WriterLease


def require_profile(path, mode, *, allow_initial=False):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        owned = 'harbor_web_database' in tables or ('django_migrations' in tables and connection.execute("SELECT 1 FROM django_migrations WHERE app='harbor_web' LIMIT 1").fetchone())
        if not owned:
            raise ValueError('Existing database lacks Harbor web ownership.')
        if 'harbor_web_deploymentstate' not in tables:
            if allow_initial:
                return
            raise ValueError('Run web migrations before startup.')
        columns = {r[1] for r in connection.execute('PRAGMA table_info(harbor_web_deploymentstate)')}
        if not allow_initial and 'relocation' not in columns:
            raise ValueError('Run web migrations before startup or backup.')
        row = connection.execute('SELECT mode FROM harbor_web_deploymentstate WHERE id=1').fetchone()
        if not row or row[0] != mode:
            raise ValueError('Web database deployment mode differs.')


def execution_block_reason():
    from harbor_web.runtime_models import DeploymentState
    state = DeploymentState.objects.filter(pk=1).first()
    if state is None:
        return 'DEPLOYMENT_PROFILE_REQUIRED'
    return 'RESTORE_RECONCILIATION_REQUIRED' if state.restored_at else ''


def ledger_identity(path):
    return hashlib.sha256(str(Path(path).resolve()).casefold().encode()).hexdigest()


def _verified_record(record):
    return (isinstance(record, dict)
            and set(record) == {'source', 'target', 'manifest_sha256', 'ledger_sha256'}
            and all(isinstance(v, str) and re.fullmatch(r'[0-9a-f]{64}', v) for v in record.values()))


def ledger_identity_matches(saved, current):
    if saved == current:
        return True
    from harbor_web.runtime_models import DeploymentState
    state = DeploymentState.objects.filter(pk=1).first()
    record = state.relocation if state else {}
    return bool(state and state.restored_at and _verified_record(record)
                and record['source'] == saved and record['target'] == current)


def _original_identity(web, current):
    with closing(sqlite3.connect(Path(web).resolve().as_uri() + '?mode=ro', uri=True)) as c:
        restored_at, raw = c.execute('SELECT restored_at, relocation FROM harbor_web_deploymentstate WHERE id=1').fetchone()
    record = json.loads(raw)
    if not record:
        return current
    if not restored_at or not _verified_record(record) or record['target'] != current:
        raise ValueError('Backup ledger differs from verified restored pair.')
    return record['source']


def _copy(source, target):
    with closing(sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite integrity check failed.')
    os.chmod(target, 0o600)


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _ledger_owned(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'business', 'evidence'}.issubset(tables) or 'django_migrations' in tables:
            raise ValueError('Unknown legacy ledger ownership.')


def backup_product(output, web, ledger, mode):
    output, web = Path(output), Path(web).resolve()
    ledger = Path(ledger).resolve() if ledger else None
    if not output.is_absolute() or output.exists():
        raise ValueError('Backup output must be a new absolute directory.')
    if ledger == web:
        raise ValueError('Web and ledger must be distinct.')
    origin = os.environ.get('HARBOR_FLEET_ORIGIN', '')
    principal = os.environ.get('HARBOR_FLEET_PRINCIPAL', '')
    parsed = urlsplit(origin)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Fleet origin must not contain credentials or query data.')
    with ExitStack() as stack:
        for path in sorted([web] + ([ledger] if ledger else []), key=str):
            stack.enter_context(WriterLease(path))
        require_profile(web, mode)
        if not origin:
            with closing(sqlite3.connect(web.as_uri() + '?mode=ro', uri=True)) as source:
                tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'harbor_web_fleetconnection' in tables:
                    row = source.execute('SELECT origin,principal FROM harbor_web_fleetconnection WHERE id=1').fetchone()
                    if row:
                        origin, principal = row
                        parsed = urlsplit(origin)
                        if parsed.username or parsed.password or parsed.query or parsed.fragment:
                            raise ValueError('Fleet origin must not contain credentials or query data.')
        if ledger:
            _ledger_owned(ledger)
        output.mkdir(mode=0o700)
        files = {}
        for name, source in [('web.sqlite3', web), ('ledger.sqlite3', ledger)]:
            if source:
                _copy(source, output / name)
                files[name] = _hash(output / name)
        manifest = dict(schema=1, mode=mode, created_at=datetime.now(timezone.utc).isoformat(), files=files,
                        ledger_present=bool(ledger), ledger_identity=_original_identity(web, ledger_identity(ledger)) if ledger else None,
                        backup_ledger_identity=ledger_identity(ledger) if ledger else None, fleet_origin=origin,
                        fleet_principal=principal)
        (output / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True), encoding='utf-8')
        os.chmod(output / 'manifest.json', 0o600)


def restore_product(backup, web, ledger, mode):
    backup, web, ledger = Path(backup), Path(web), Path(ledger)
    if not backup.is_absolute() or not web.is_absolute() or not ledger.is_absolute() or web.resolve() == ledger.resolve():
        raise ValueError('Restore requires distinct absolute target paths.')
    if web.exists() or ledger.exists():
        raise ValueError('Restore targets must be new.')
    manifest_bytes = (backup / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict):
        raise ValueError('Invalid backup manifest.')
    expected = {'web.sqlite3', 'ledger.sqlite3'} if manifest.get('ledger_present') is True else {'web.sqlite3'}
    if (type(manifest.get('schema')) is not int or manifest['schema'] != 1 or manifest.get('mode') != mode
            or type(manifest.get('ledger_present')) is not bool or not isinstance(manifest.get('files'), dict)
            or set(manifest['files']) != expected
            or not all(isinstance(v, str) and re.fullmatch(r'[0-9a-f]{64}', v) for v in manifest['files'].values())
            or (manifest['ledger_present'] and not (isinstance(manifest.get('ledger_identity'), str) and re.fullmatch(r'[0-9a-f]{64}', manifest['ledger_identity'])))
            or (manifest['ledger_present'] and not (isinstance(manifest.get('backup_ledger_identity'), str) and re.fullmatch(r'[0-9a-f]{64}', manifest['backup_ledger_identity'])))
            or (not manifest['ledger_present'] and (manifest.get('ledger_identity') is not None or manifest.get('backup_ledger_identity') is not None))
            or not all(isinstance(manifest.get(k), str) for k in ('created_at', 'fleet_origin', 'fleet_principal'))):
        raise ValueError('Invalid backup manifest schema, files, or mode.')
    try:
        datetime.fromisoformat(manifest['created_at'])
        origin = urlsplit(manifest['fleet_origin'])
        if origin.username or origin.password or origin.query or origin.fragment:
            raise ValueError('Unsafe backup origin.')
    except (TypeError, ValueError) as error:
        raise ValueError('Invalid backup identity metadata.') from error
    for name, digest in manifest['files'].items():
        path = backup / name
        if path.is_symlink() or not path.is_file() or _hash(path) != digest:
            raise ValueError('Backup file hash differs.')
    require_profile(backup / 'web.sqlite3', mode)
    if 'ledger.sqlite3' in expected:
        _ledger_owned(backup / 'ledger.sqlite3')
        if _original_identity(backup / 'web.sqlite3', manifest['backup_ledger_identity']) != manifest['ledger_identity']:
            raise ValueError('Backup lineage differs from matched web restore record.')
    stages, publications, adopted = [], [], []
    with ExitStack() as stack:
        for target in sorted([web, ledger], key=str):
            stack.enter_context(WriterLease(target))
        if web.exists() or ledger.exists():
            raise ValueError('Restore targets must be new.')
        try:
            for name, target in [('web.sqlite3', web), ('ledger.sqlite3', ledger)]:
                if name not in expected:
                    continue
                stage = target.with_name(target.name + '.' + uuid.uuid4().hex + '.restore-temporary')
                stages.append(stage)
                _copy(backup / name, stage)
                if name == 'web.sqlite3':
                    with closing(sqlite3.connect(stage)) as c, c:
                        record = dict(source=manifest['ledger_identity'], target=ledger_identity(ledger),
                                      manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                                      ledger_sha256=manifest['files']['ledger.sqlite3']) if manifest['ledger_present'] else {}
                        c.execute('UPDATE harbor_web_deploymentstate SET restored_at=?, relocation=? WHERE id=1',
                                  (datetime.now(timezone.utc).isoformat(), json.dumps(record)))
                publications.append((stage, target))
            # Publish only after both staged databases validate; never overwrite.
            for stage, target in publications:
                os.link(stage, target)
                adopted.append(target)
            for stage in stages:
                stage.unlink()
        except BaseException:
            for path in adopted + stages:
                path.unlink(missing_ok=True)
            raise
