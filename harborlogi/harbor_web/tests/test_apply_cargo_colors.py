from io import StringIO

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase

from harbor_web import services
from harbor_web.models import Audit, Cargo


class ApplyCargoColorsTests(TestCase):
    def setUp(self):
        self.op = User.objects.create_user('color-op')
        from django.contrib.auth.models import Group
        self.op.groups.add(Group.objects.get_or_create(name='operator')[0])

    def cargo(self, code, color=''):
        return services.register_cargo(self.op, code=code, name=code, weight_kg=2, volume_m3='0.012',
                                       destination='x', length_cells=3, color=color)

    def run_command(self, *args):
        out = StringIO()
        call_command('apply_cargo_colors', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        self.cargo('C-1')
        out = self.run_command()
        self.assertIn('채울 예정', out)
        self.assertEqual(Cargo.objects.get(code='C-1').color, '')
        self.assertFalse(Audit.objects.filter(action='cargo.color_filled').exists())

    def test_apply_fills_only_blank_team_colors_and_audits(self):
        self.cargo('C-1')                    # blank -> pink, confirmed
        self.cargo('C-14')                   # blank -> sky, planned
        self.cargo('C-6', color='yellow')    # already set: never changed
        self.cargo('X-9')                    # not a team container: ignored
        out = self.run_command('--apply')
        self.assertEqual((Cargo.objects.get(code='C-1').color, Cargo.objects.get(code='C-1').color_confirmed), ('pink', True))
        self.assertEqual((Cargo.objects.get(code='C-14').color, Cargo.objects.get(code='C-14').color_confirmed), ('sky', False))
        self.assertEqual(Cargo.objects.get(code='C-6').color, 'yellow')
        self.assertEqual(Cargo.objects.get(code='X-9').color, '')
        self.assertEqual(sorted(a.detail['code'] for a in Audit.objects.filter(action='cargo.color_filled')), ['C-1', 'C-14'])
        self.assertIn('C-6=yellow', out)
        # Running again changes nothing more.
        self.run_command('--apply')
        self.assertEqual(Audit.objects.filter(action='cargo.color_filled').count(), 2)

    def test_cargo_stays_immutable_through_the_normal_manager(self):
        self.cargo('C-1')
        with self.assertRaises(ValidationError):
            Cargo.objects.filter(code='C-1').update(color='pink')
