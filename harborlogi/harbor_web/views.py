from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from harbor_web import forms, services
from harbor_web.auth import has_role, require_roles
from harbor_web.models import Audit, Cargo, Condition, FieldEpisode, Observation, Order, Plan


def page(request, template, **context):
    return render(request, template, context)


def paginate(request, items):
    query = request.GET.copy()
    query.pop('page', None)
    return dict(page=Paginator(items, 25).get_page(request.GET.get('page')), query=query.urlencode())


def form_error(form, error):
    for message in error.messages:
        form.add_error(None, message)


def form_page(request, *, form_type, title, section, save, destination, description='', initial=None):
    form = form_type(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            obj = save(form.cleaned_data)
        except ValidationError as error:
            form_error(form, error)
        except services.Conflict:
            return render(request, 'error.html', dict(heading='기준이 변경되었습니다', reason='입력할 때와 현재 원본이 다릅니다. 최신 자료를 다시 확인하세요.', return_url=request.path), status=409)
        else:
            messages.success(request, '저장했습니다. 원래 자료와 변경 기록을 확인할 수 있습니다.')
            return redirect(destination(obj) if callable(destination) else destination)
    return page(request, 'form.html', form=form, title=title, section=section, description=description)


@require_roles('viewer', 'operator', 'admin', 'field')
@require_http_methods(['GET'])
def overview(request):
    if not has_role(request.user, 'viewer', 'operator', 'admin'):
        return redirect('/field/')
    requests = Order.objects.select_related('cargo').order_by('priority_group', 'sequence', 'id')[:12]
    plans = {p.order_id: p for p in Plan.objects.filter(order_id__in=[o.pk for o in requests]).order_by('id')}
    rows = []
    for order in requests:
        plan = plans.get(order.pk)
        action = '계획 확인' if plan else ('입고 인계 조건 확인' if order.kind == 'INBOUND' else '관측 선택·계획 검토')
        rows.append(dict(order=order, plan=plan, action=action, reason=reason_text(plan.result['reason']) if plan else ('입고 실행 인계 조건을 확인하세요.' if order.kind == 'INBOUND' else '관측 원본과 반출 순서를 검토하세요.')))
    return page(request, 'overview.html', section='overview', rows=rows,
                observation=Observation.objects.order_by('-id').first(), condition=Condition.objects.order_by('-id').first(),
                field_pending=FieldEpisode.objects.filter(state='OPEN').count())


@require_roles('viewer', 'operator', 'admin')
@require_http_methods(['GET'])
def cargo_list(request):
    q = request.GET.get('q', '')[:200]
    items = Cargo.objects.filter(Q(code__icontains=q) | Q(name__icontains=q) | Q(destination__icontains=q)).order_by('-id')
    return page(request, 'cargo.html', section='cargo', q=q, **paginate(request, items))


@require_roles('operator')
@require_http_methods(['GET', 'POST'])
def cargo_new(request):
    return form_page(request, form_type=forms.CargoForm, title='화물 등록', section='cargo', description='화물의 기본 정보를 등록합니다. 실제 위치와 내려놓기는 별도 관측으로 확인합니다.', save=lambda data: services.register_cargo(request.user, **data), destination='/cargo/')


@require_roles('viewer', 'operator', 'admin')
@require_http_methods(['GET'])
def order_list(request):
    q, kind = request.GET.get('q', '')[:200], request.GET.get('kind', '')
    items = Order.objects.select_related('cargo').filter(Q(cargo__code__icontains=q) | Q(cargo__name__icontains=q)).order_by('priority_group', 'sequence', 'id')
    if kind in {'INBOUND', 'OUTBOUND'}:
        items = items.filter(kind=kind)
    return page(request, 'requests.html', section='cargo', q=q, kind=kind, **paginate(request, items))


@require_roles('operator')
@require_http_methods(['GET', 'POST'])
def order_new(request):
    return form_page(request, form_type=forms.OrderForm, title='업무 요청 등록', section='cargo', description='업무 지시와 우선순위를 저장합니다. 요청 등록으로 실제 화물 위치가 바뀌지 않습니다.', save=lambda data: services.create_order(request.user, **data), destination=lambda obj: f'/requests/{obj.pk}/')


@require_roles('viewer', 'operator', 'admin')
@require_http_methods(['GET', 'POST'])
def order_detail(request, pk):
    order = get_object_or_404(Order.objects.select_related('cargo'), pk=pk)
    priority = forms.PriorityForm(initial=dict(expected_version=order.version, priority_group=order.priority_group, sequence=order.sequence, tie_policy=order.tie_policy, note=order.note))
    plan_form = forms.PlanForm(initial={'expected_version': order.version})
    if request.method == 'POST':
        if not has_role(request.user, 'operator'):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        action = request.POST.get('action')
        if action not in {'priority', 'plan'}:
            return HttpResponseBadRequest('지원하지 않는 요청입니다.')
        if action == 'priority':
            active = priority = forms.PriorityForm(request.POST)
        else:
            active = plan_form = forms.PlanForm(request.POST)
        if active.is_valid():
            try:
                if action == 'priority':
                    services.update_order(request.user, order_id=pk, **active.cleaned_data)
                else:
                    data = active.cleaned_data
                    plan = services.make_plan(request.user, order_id=pk, expected_version=data['expected_version'], observation_id=data['observation'].pk)
                    return redirect(f'/plans/{plan.pk}/')
            except services.Conflict:
                return render(request, 'error.html', dict(heading='요청이 변경되었습니다', reason='다른 사용자의 변경이 있습니다. 현재 revision을 다시 확인하세요.', return_url=f'/requests/{pk}/'), status=409)
            except ValidationError as error:
                form_error(active, error)
            else:
                messages.success(request, '요청 revision을 변경했습니다. 기존 계획은 원래 기준을 유지합니다.')
                return redirect(f'/requests/{pk}/')
    return page(request, 'request_detail.html', section='cargo', order=order, priority_form=priority, plan_form=plan_form, plans=Plan.objects.filter(order=order).order_by('-id'))


@require_roles('viewer', 'operator', 'admin')
@require_http_methods(['GET'])
def inventory(request):
    observations = Observation.objects.select_related('condition').order_by('-id')
    selected = get_object_or_404(observations, pk=request.GET['observation']) if request.GET.get('observation', '').isdigit() else observations.first()
    return page(request, 'inventory.html', section='inventory', observation=selected, observations=observations[:100])


@require_roles('admin')
@require_http_methods(['GET', 'POST'])
def observation_new(request):
    return form_page(request, form_type=forms.ObservationForm, title='관측 원본 등록', section='inventory', description='원본 출처와 시각을 함께 보존합니다. 수동 등록은 인증된 운영 producer를 대신하지 않습니다.', save=lambda data: services.record_observation(request.user, **data), destination=lambda obj: f'/inventory/?observation={obj.pk}')


@require_roles('admin')
@require_http_methods(['GET'])
def settings_page(request):
    return page(request, 'settings.html', section='settings', conditions=Condition.objects.order_by('-id'))


@require_roles('admin')
@require_http_methods(['GET', 'POST'])
def condition_new(request):
    return form_page(request, form_type=forms.ConditionForm, title='설치 기준 등록', section='settings', description='검증 기록의 참조와 계획 조건을 새 버전으로 보존합니다. 진행 중 작업의 기준은 바꾸지 않습니다.', save=lambda data: services.register_condition(request.user, **data), destination='/settings/')


def reason_text(code):
    return {
        'PROPOSAL_REQUIRES_ADMISSION_AND_EVIDENCE': '계획을 만들었습니다. 실행 전 설치 대응과 승인·근거 조건을 확인하세요.',
        'PRIORITY_ORDER_BLOCKED': '앞선 반출 지시가 있습니다. 우선순위와 그룹 안 허용 순서를 확인하세요.',
        'INBOUND_PLANNER_NOT_AVAILABLE': '입고 계획은 아직 연결되지 않았습니다. 입력한 요청은 보존됩니다.',
        'REVISION_CONFLICT': '검토한 관측 revision과 현재 기준이 다릅니다.',
        'OBSERVATION_NOT_CURRENT': '관측이 오래되었거나 시각을 확인할 수 없습니다. 최신 원본이 필요합니다.',
        'PHYSICAL_HOLDING_REQUIRES_RECONCILIATION': '들고 있는 화물이 있어 실제 인계 상태 확인이 필요합니다.',
        'OUTBOUND_NOT_OBSERVED': '목표 화물을 관측 원본에서 찾을 수 없습니다.',
        'OUTBOUND_NOT_IN_A_BAY': '목표 화물이 보관 칸에서 확인되지 않았습니다.',
        'OUTBOUND_ALREADY_ON_STAND_REQUIRES_EVIDENCE': '반출대에 있는 화물의 완료 근거를 확인해야 합니다.',
        'OUTBOUND_STAND_FULL': '반출대에 빈자리가 없습니다.',
        'OUTBOUND_MOVE_UNAVAILABLE': '보관 칸에서 반출대로 허용된 이동이 없습니다.',
        'NO_LEGAL_TEMPORARY_BAY': '방해 화물을 둘 유효한 임시 공간이 없습니다.',
        'ARM_REACH_UNVERIFIED': '팔의 도달 가능성 검증 참조가 없습니다.',
        'NO_ARM_REACHES_MOVE': '이 이동을 수행할 수 있는 팔 대응이 없습니다.',
    }.get(code, '원본과 연결 조건을 확인해야 합니다.')
