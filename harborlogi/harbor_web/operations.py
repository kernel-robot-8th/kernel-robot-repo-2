"""Read-only operations facts, keeping observations, proposals and handoffs separate."""
from django.db.models import Count, Max
from django.utils import timezone

from harbor_web import services
from harbor_web.models import Command, Observation, Order, Plan
from harbor_web.workspace import COMMAND_LABELS

ATTENTION_STATES = ('UNKNOWN', 'DISPATCHING', 'CANCEL_PENDING', 'BLOCKED')


def _inventory(observation):
    if observation is None:
        return dict(known=False, cargo_count=None, occupied=None, capacity=None,
                    storage_count=None, temporary_count=None, outbound_count=None,
                    holding_count=None, areas=[])
    policy = observation.condition.payload
    temporary = set(policy['temporary_bays'])
    outbound = policy['outbound_stand']
    spatial = observation.payload.get('schema') == 'grid-placement-v1'
    snapshot = services.snapshot(observation)
    areas = []
    presentation = services.area_presentation(policy) if spatial else {}
    if spatial:
        for area in snapshot.areas:
            placements = [p for p in snapshot.placements if p.area_id == area.area_id]
            areas.append(dict(id=area.area_id, cargo_count=len(placements),
                occupied=sum(len(snapshot.cells(p)) for p in placements),
                capacity=area.rows * area.columns * area.levels))
    else:
        areas = [dict(id=b.bay_id, cargo_count=len(b.cargo), occupied=len(b.cargo),
                      capacity=b.capacity) for b in snapshot.bays]
    for area in areas:
        area['label'] = presentation.get(area['id'], {}).get('label', area['id'])
        area['role'] = '임시 장치' if area['id'] in temporary else '반출대' if area['id'] == outbound else '보관'
    occupied, capacity = sum(a['occupied'] for a in areas), sum(a['capacity'] for a in areas)
    return dict(known=True, spatial=spatial, cargo_count=sum(a['cargo_count'] for a in areas),
        occupied=occupied, capacity=capacity,
        occupancy_percent=round(occupied * 100 / capacity) if capacity else None,
        unit='격자 칸' if spatial else '적재 자리', areas=areas,
        storage_count=sum(a['cargo_count'] for a in areas if a['role'] == '보관'),
        temporary_count=sum(a['cargo_count'] for a in areas if a['role'] == '임시 장치'),
        outbound_count=sum(a['cargo_count'] for a in areas if a['role'] == '반출대'),
        holding_count=len(snapshot.holdings), holdings=snapshot.holdings)


def operations_context(observation, *, now=None):
    """Summarize one exact observation pin; registered orders and handoffs are global."""
    now = timezone.now() if now is None else now
    inventory = _inventory(observation)
    freshness = dict(stale=None, superseded=None, age_seconds=None)
    if observation is not None:
        age = now - observation.observed_at
        freshness = dict(age_seconds=int(age.total_seconds()),
            stale=not timezone.timedelta(0) <= age <= services.constraints(observation.condition.payload).max_observation_age,
            superseded=Observation.objects.filter(source_id=observation.source_id, pk__gt=observation.pk).exists())
    counts = dict(Order.objects.values_list('kind').annotate(count=Count('pk')))
    commands = list(Command.objects.select_related('plan__observation__condition', 'order__cargo').order_by('-pk'))
    commanded = {c.order_id for c in commands}
    handoffs = [dict(command=c, plan=c.plan, label=COMMAND_LABELS.get(c.state, c.state)) for c in commands]
    latest_ids = Plan.objects.values('order_id').annotate(latest_id=Max('pk')).values('latest_id')
    latest = Plan.objects.filter(pk__in=latest_ids).select_related(
        'order__cargo', 'observation__condition', 'condition').order_by('-pk')
    rows, rehandled = [], set()
    phases = {'재취급': 0, '반출': 0, '복귀': 0, '입고': 0}
    ready = hold = excluded = move_count = outbound_plans = outbound_rehandles = 0
    digest = services.queue_digest()
    for plan in latest:
        if plan.order_id in commanded or observation is None or plan.observation_id != observation.pk or plan.condition_id != observation.condition_id:
            continue
        stale = (freshness['stale'] or freshness['superseded'] or
                 plan.order_version != plan.order.version or plan.queue_digest != digest)
        status = plan.result['status']
        included = status == 'READY' and not stale
        moves = plan.result.get('moves', [])
        primary = sorted({m['primary_arm'] for m in moves if m.get('primary_arm')})
        alternate = sorted({a for m in moves for a in m.get('alternate_arms', [])} - set(primary))
        rows.append(dict(plan=plan, stale=stale, included=included, status=status,
                         primary_arms=primary, alternate_arms=alternate))
        if status == 'HOLD':
            hold += 1
        elif not included:
            excluded += 1
        if not included:
            continue
        ready += 1
        move_count += len(moves)
        outbound = plan.order.kind == 'OUTBOUND'
        outbound_plans += outbound
        target_index = next((i for i, m in enumerate(moves) if m['cargo_id'] == plan.order.cargo.code), -1)
        for i, move in enumerate(moves):
            phase = move.get('phase_label') or ('입고' if plan.order.kind == 'INBOUND' else
                '재취급' if i < target_index else '반출' if i == target_index else '복귀')
            phases[phase] = phases.get(phase, 0) + 1
            outbound_rehandles += outbound and phase == '재취급'
            if move['cargo_id'] != plan.order.cargo.code:
                rehandled.add(move['cargo_id'])
    # Existing fields only (ADR 0020): no new state interpretation, no resend action.
    attention = []
    if observation is None:
        attention.append(dict(label='관측 없음', count=1, action='관측 연결 상태 확인', href='/inventory/'))
    if freshness['stale']:
        attention.append(dict(label='관측 유효 시간 밖', count=1, action='관측 선택', href='#observation-select'))
    if freshness['superseded']:
        attention.append(dict(label='같은 출처에 새 관측 있음', count=1, action='관측 선택', href='#observation-select'))
    if hold:
        attention.append(dict(label='HOLD 계획', count=hold, action='현재 검토 보기', href='#proposal-title'))
    if excluded and not (freshness['stale'] or freshness['superseded']):  # stale pin already explains them
        attention.append(dict(label='재검토로 이동 집계 제외', count=excluded, action='현재 검토 보기', href='#proposal-title'))
    for c in commands:
        if c.state in ATTENTION_STATES:
            attention.append(dict(label=f"전체 인계 · {COMMAND_LABELS[c.state]} · {c.order.cargo.code}", count=1,
                                  action='원래 작업 대조', href=f'/work/{c.pk}/'))
    return dict(observation=observation, inventory=inventory, freshness=freshness, attention=attention,
        attention_total=sum(item['count'] for item in attention),
        orders=dict(inbound=counts.get('INBOUND', 0), outbound=counts.get('OUTBOUND', 0), scope='전체 등록 지시'),
        workload=dict(plans=rows, ready_count=ready, hold_count=hold, excluded_count=excluded,
            rehandled_cargo_count=len(rehandled) if observation else None,
            move_count=move_count if observation else None, phase_counts=phases,
            rehandled_cargo=sorted(rehandled), outbound_plan_count=outbound_plans if observation else None,
            outbound_rehandle_moves=outbound_rehandles if observation else None,
            rehandle_per_outbound=round(outbound_rehandles / outbound_plans, 1) if observation and outbound_plans else None),
        handoffs=handoffs, actual=dict(completed=None, throughput=None))
