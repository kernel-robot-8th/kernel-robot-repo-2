from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_http_methods

from harbor_web.auth import has_role, require_roles
from harbor_web.commercial import PROFILE_FIELDS, goods_digest, update_commercial, update_contents
from harbor_web.models import Cargo, CargoCommercial, Order
from harbor_web.page_helpers import page
from harbor_web.service_common import Conflict


class CommercialForm(forms.ModelForm):
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    class Meta:
        model = CargoCommercial
        fields = PROFILE_FIELDS
        labels = dict(nominal_length_ft='컨테이너 길이 (ft)', container_type='컨테이너 종류',
                      color='컨테이너 색', color_confirmed='실물 색 확인')


class ContentsForm(forms.Form):
    expected_digest = forms.CharField(widget=forms.HiddenInput)
    goods_contents = forms.CharField(label='입고 품목 (한 줄에 한 품목)', max_length=4000, required=False,
                                     widget=forms.Textarea(attrs={'rows': 6}))


def _edit(request, *, form, save, title, details, back):
    status = 200
    if request.method == 'POST':
        if not has_role(request.user, 'operator'):
            raise PermissionDenied
        if form.is_valid():
            try:
                save(dict(form.cleaned_data))
            except Conflict as exc:
                form.add_error(None, str(exc))
                status = 409
            except ValidationError as exc:
                form.add_error(None, ' '.join(exc.messages))
            else:
                return redirect(request.path)
    response = page(request, 'commercial_detail.html', section='cargo', title=title, form=form, details=details, back=back)
    response.status_code = status
    return response


@require_roles('viewer', 'operator', 'admin')
@require_http_methods(['GET', 'POST'])
def cargo_commercial(request, pk):
    cargo = get_object_or_404(Cargo.objects.select_related('commercial_profile'), pk=pk)
    current = cargo.current_commercial
    form = CommercialForm(request.POST if request.method == 'POST' else None,
                          initial={**{f: getattr(current, f) for f in PROFILE_FIELDS}, 'expected_revision': current.revision})
    color = current.get_color_display() if current.color else '미입력'
    if current.color:
        color += ' (확인)' if current.color_confirmed else ' (확인 전)'
    details = [('길이', f'{current.nominal_length_ft} ft' if current.nominal_length_ft else '미입력'),
               ('종류', current.container_type or '미입력'), ('색상', color)]
    return _edit(request, form=form, title=f'{cargo.code} 컨테이너 제원', details=details, back=f'/cargo/{pk}/history/',
                 save=lambda fields: update_commercial(request.user, pk, **fields))


@require_roles('viewer', 'operator', 'admin')
@require_http_methods(['GET', 'POST'])
def order_contents(request, pk):
    order = get_object_or_404(Order.objects.select_related('cargo'), pk=pk, kind='INBOUND')
    form = ContentsForm(request.POST if request.method == 'POST' else None,
                        initial={'goods_contents': order.goods_contents, 'expected_digest': goods_digest(order.goods_contents)})
    return _edit(request, form=form, title=f'{order.cargo.code} · 입고 요청 #{pk} 품목',
                 details=[('품목', order.goods_contents or '미입력')], back=f'/requests/{pk}/',
                 save=lambda fields: update_contents(request.user, pk, **fields))
