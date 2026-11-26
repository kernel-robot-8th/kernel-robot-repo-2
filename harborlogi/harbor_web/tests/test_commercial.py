from django.contrib.auth.models import Group, User
from django.test import TestCase

from harbor_web import services
from harbor_web.commercial import goods_digest
from harbor_web.models import Audit, Cargo, Order
from harbor_web.yard_scene import cargo_scene_metadata


class CommercialManifestTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user('manifest-op')
        self.viewer = User.objects.create_user('manifest-reader')
        for user, role in ((self.operator, 'operator'), (self.viewer, 'viewer')):
            user.groups.add(Group.objects.get_or_create(name=role)[0])
        self.client.force_login(self.operator)
        self.cargo = services.register_cargo(self.operator, code='C-5', name='Declared', weight_kg=3,
                                            volume_m3=2, destination='Depot', color='orange')

    def test_existing_cargo_update_is_audited_and_overrides_registration_color(self):
        url = f'/cargo/{self.cargo.pk}/commercial/'
        self.assertEqual(self.client.get(url).context['form'].initial['color'], 'orange')
        new = dict(expected_revision=0, nominal_length_ft=40, container_type='일반 화물',
                   color='sky', color_confirmed='on')
        self.assertRedirects(self.client.post(url, new), url)
        self.assertEqual(self.client.post(url, {**new, 'color': 'pink'}).status_code, 409)
        self.assertEqual(Audit.objects.filter(action='cargo.commercial').count(), 1)
        cargo = Cargo.objects.get(pk=self.cargo.pk)
        self.assertEqual(cargo.color, 'orange')
        self.assertEqual(cargo.current_commercial.nominal_length_ft, 40)
        self.assertEqual(cargo_scene_metadata(['C-5'])['C-5']['color'], 'sky')
        self.assertContains(self.client.get(f'/cargo/{cargo.pk}/history/'), '하늘')
        self.client.force_login(self.viewer)
        self.assertContains(self.client.get(url), '40 ft')
        self.assertEqual(self.client.post(url, {**new, 'expected_revision': 1}).status_code, 403)

    def test_new_cargo_and_inbound_order_accept_declared_values(self):
        result = self.client.post('/cargo/new/', dict(code='C-8', name='Cargo', weight_kg='3',
                                  volume_m3='1', destination='Depot', length_cells='1',
                                  nominal_length_ft='20', container_type='냉장', color='yellow'))
        self.assertEqual(result.status_code, 302)
        cargo = Cargo.objects.get(code='C-8')
        self.assertEqual(cargo.current_commercial.nominal_length_ft, 20)
        self.assertEqual(cargo.current_commercial.container_type, '냉장')
        post = dict(cargo=cargo.pk, kind='INBOUND', priority_group=3, tie_policy='FIXED',
                    goods_contents='부품 A\n부품 B')
        self.assertEqual(self.client.post('/requests/new/', post).status_code, 302)
        old, = Order.objects.filter(cargo=cargo)
        self.assertEqual(old.goods_contents, post['goods_contents'])
        self.assertEqual(self.client.post('/requests/new/', {**post, 'goods_contents': ''}).status_code, 302)
        new = Order.objects.get(cargo=cargo, sequence=2)
        self.assertEqual(new.goods_contents, '')
        url = f'/requests/{old.pk}/contents/'
        fields = dict(expected_digest=goods_digest(old.goods_contents), goods_contents='갱신 품목\n다른 품목')
        self.assertRedirects(self.client.post(url, fields), url)
        self.assertEqual(self.client.post(url, {**fields, 'goods_contents': 'stale'}).status_code, 409)
        old.refresh_from_db()
        new.refresh_from_db()
        self.assertEqual(old.goods_contents, fields['goods_contents'])
        self.assertEqual(new.goods_contents, '')
        self.client.force_login(self.viewer)
        self.assertContains(self.client.get(url), '갱신 품목')
        self.assertEqual(self.client.post(url, fields).status_code, 403)
