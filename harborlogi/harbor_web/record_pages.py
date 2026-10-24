from django import forms as django_forms
from django.views.decorators.http import require_http_methods
from harbor_web import forms, services
from harbor_web.auth import require_roles
from harbor_web.models import Audit, Measurement
from harbor_web.views import form_page, page, paginate


class ComparisonForm(django_forms.Form):
    left = django_forms.ModelChoiceField(label='기준 자료 A', queryset=Measurement.objects.none())
    right = django_forms.ModelChoiceField(label='비교 자료 B', queryset=Measurement.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('left', 'right'):
            self.fields[name].queryset = Measurement.objects.order_by('-id')
            self.fields[name].label_from_instance = lambda item: f'{item.label} · {item.scenario} · {item.provenance}'


@require_roles('viewer', 'operator', 'admin')
@require_http_methods(['GET'])
def records(request):
    q = request.GET.get('q', '')[:96]
    items = Audit.objects.select_related('actor').order_by('-id')
    if q:
        items = items.filter(object_id=q)
    return page(request, 'records.html', section='records', q=q, **paginate(request, items))


@require_roles('operator')
@require_http_methods(['GET', 'POST'])
def measurement_new(request):
    return form_page(request, form_type=forms.MeasurementForm, title='측정 자료 등록', section='records', description='같은 조건을 비교할 수 있도록 원본 참조를 보존합니다. 실제 미측정 값은 비워 두세요.', save=lambda data: services.record_measurement(request.user, **data), destination='/records/compare/')


@require_roles('viewer', 'operator', 'admin')
@require_http_methods(['GET'])
def comparison(request):
    form = ComparisonForm(request.GET or None)
    result = None
    if form.is_bound and form.is_valid():
        result = services.compare_measurements(form.cleaned_data['left'], form.cleaned_data['right'])
    return page(request, 'comparison.html', section='records', form=form, comparison=result)
