"""Single process execution owner; database migration is a separate operation."""
import argparse
import os


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8060)
    args = parser.parse_args()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'harbor_web.settings')
    import django
    django.setup()
    from django.conf import settings
    from harbor_web.commands import acquire_registry, registry_close
    from harbor_web.wsgi import application
    configured = any(os.environ.get('HARBOR_' + key) for key in (
        'LEDGER_PATH', 'FLEET_ORIGIN', 'FLEET_TOKEN', 'FLEET_PRINCIPAL', 'BINDINGS_PATH'))
    from harbor.ownership import WriterLease
    from harbor_web.runtime import require_profile
    from pathlib import Path
    path = settings.DATABASES['default']['NAME']
    if str(path) == ':memory:' or not Path(path).is_absolute():
        raise RuntimeError('Server requires an absolute file-backed web database.')
    require_profile(path, settings.MODE)
    lease = WriterLease(path)
    lease.__enter__()
    try:
        if configured:
            acquire_registry()
        from waitress import serve
        serve(application, host='127.0.0.1', port=args.port, threads=4)
    finally:
        registry_close()
        lease.__exit__()


if __name__ == '__main__':
    main()
