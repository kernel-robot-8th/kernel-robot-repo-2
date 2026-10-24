from django import forms as django_forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from harbor_web import forms, services
from harbor_web.auth import has_role, require_roles
from harbor_web.models import DepartureIntent, FieldConfirmation, FieldEpisode
from harbor_web.views import form_error, form_page, page, paginate


class ReassignForm(django_forms.Form):
    expected_version = django_forms.IntegerField(widget=django_forms.HiddenInput, min_value=1)
    assigned_to = django_forms.ModelChoiceField(label='새 현장 확인 담당', queryset=get_user_model().objects.filter(is_active=True, groups__name='field').distinct())


@require_roles('operator', 'admin', 'field')
@require_http_methods(['GET'])
def field_list(request):
    items = FieldEpisode.objects.select_related('order__cargo', 'assigned_to').order_by('-id')
    if not has_role(request.user, 'operator', 'admin'):
        items = items.filter(assigned_to=request.user)
    state = request.GET.get('state', '')
    if state in {'OPEN', 'CONFIRMED', 'MISMATCH', 'UNABLE', 'REASSIGNED'}:
        items = items.filter(state=state)
    return page(request, 'field.html', section='field', state=state, **paginate(request, items))


@require_roles('operator')
@require_http_methods(['GET', 'POST'])
def field_new(request):
    def save(data):
        return services.assign_episode(request.user, order_id=data['order'].pk, vehicle_id=data['vehicle_id'], assigned_to=data['assigned_to'])
    return form_page(request, form_type=forms.EpisodeForm, title='현장 확인 배정', section='field', description='입고 요청·화물·차량을 특정한 공급 인계 회차를 만듭니다.', save=save, destination=lambda obj: f'/field/{obj.pk}/')


@require_roles('operator', 'admin', 'field')
@require_http_methods(['GET', 'POST'])
def field_detail(request, pk):
    episode = get_object_or_404(FieldEpisode.objects.select_related('order__cargo', 'assigned_to'), pk=pk)
    if not has_role(request.user, 'operator', 'admin') and episode.assigned_to_id != request.user.pk:
        raise PermissionDenied
    form = forms.ConfirmForm(initial={'expected_version': episode.version, 'observed_at': timezone.localtime()})
    reassign = ReassignForm(initial={'expected_version': episode.version})
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'confirm':
                form = forms.ConfirmForm(request.POST)
                if form.is_valid():
                    services.confirm_episode(request.user, episode_id=pk, **form.cleaned_data)
                    messages.success(request, '이 회차의 확인 결과를 저장했습니다. 차량 실행 허가와는 별개입니다.')
                    return redirect(f'/field/{pk}/')
            elif action == 'reassign':
                reassign = ReassignForm(request.POST)
                if reassign.is_valid():
                    new = services.reassign_episode(request.user, episode_id=pk, **reassign.cleaned_data)
                    return redirect(f'/field/{new.pk}/')
            elif action == 'departure' and request.POST.get('confirmation_id', '').isdigit():
                services.prepare_departure(request.user, episode_id=pk, confirmation_id=int(request.POST['confirmation_id']))
                messages.info(request, '차량과 회차에 결속된 출발 전제를 기록했습니다. Fleet 운영자에게 같은 차량과 원본을 인계하세요.')
                return redirect(f'/field/{pk}/')
            else:
                return render(request, 'error.html', dict(reason='지원하지 않는 현장 확인 요청입니다.', return_url=f'/field/{pk}/'), status=400)
        except services.Conflict:
            return render(request, 'error.html', dict(heading='현장 배정이 변경되었습니다', reason='이전 화면의 확인을 재사용하지 마세요. 현재 대상과 회차를 다시 확인하세요.', return_url='/field/'), status=409)
        except ValidationError as error:
            form_error(reassign if action == 'reassign' else form, error)
    return page(request, 'field_detail.html', section='field', episode=episode, form=form, reassign_form=reassign,
                confirmations=FieldConfirmation.objects.filter(episode=episode).select_related('actor').order_by('-id'),
                departure=DepartureIntent.objects.filter(episode=episode).first(),
                can_confirm=has_role(request.user, 'field') and episode.assigned_to_id == request.user.pk and episode.state == 'OPEN')
