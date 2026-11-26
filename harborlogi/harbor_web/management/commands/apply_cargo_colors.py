"""Fill the physical color of team containers whose color is still blank (one-off, audited).

Cargo rows are immutable history. This is the single, narrow exception: a blank color recorded before the
color field existed may be filled once from the team record (the seed table, Confluence SP 41287681).
A color that is already set is never changed. Dry run by default; `--apply` writes.
"""
from pathlib import Path
import tempfile

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from harbor.ownership import WriterLease
from harbor_web.management.commands.seed_site_dataset import SEED_CONTAINERS
from harbor_web.models import Audit, Cargo

TEAM_COLORS = {row[0]: (row[8], row[9]) for row in SEED_CONTAINERS}


class Command(BaseCommand):
    help = 'Fill blank container colors from the team record (dry run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Write the colors (default: only show what would change).')

    def handle(self, *args, **options):
        db_path = settings.DATABASES['default'].get('NAME')
        lease_target = (Path(db_path) if isinstance(db_path, (str, Path)) and str(db_path) != ':memory:'
                        else Path(tempfile.gettempdir()) / 'harbor_apply_cargo_colors.lock')
        filled, kept = [], []
        with WriterLease(lease_target), transaction.atomic():
            for cargo in Cargo.objects.filter(code__in=TEAM_COLORS).order_by('pk'):
                color, confirmed = TEAM_COLORS[cargo.code]
                if cargo.color:
                    kept.append(f'{cargo.code}={cargo.color}')
                    continue
                filled.append(f'{cargo.code}={color}{"" if confirmed else "(예정)"}')
                if not options['apply']:
                    continue
                # Immutable manager refuses update(); the base manager writes only while the color is still blank.
                changed = Cargo._base_manager.filter(pk=cargo.pk, color='').update(color=color, color_confirmed=confirmed)
                if changed:
                    Audit.objects.create(actor=None, action='cargo.color_filled', object_type='Cargo', object_id=str(cargo.pk),
                                         detail=dict(code=cargo.code, color=color, color_confirmed=confirmed,
                                                     source='Confluence SP 41287681'))
        verb = '채움' if options['apply'] else '채울 예정(--apply 없음)'
        self.stdout.write(f'{verb} {len(filled)}개: {", ".join(filled) or "-"}')
        self.stdout.write(f'이미 색 있음(그대로) {len(kept)}개: {", ".join(kept) or "-"}')
