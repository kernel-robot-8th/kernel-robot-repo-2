"""Read-only Harbor UI context for ROSY geometry-free port site places."""
import os
from urllib.error import HTTPError, URLError

from harbor.cell_site_map import compare_condition_with_rosy, normalize_cell_site_map
from harbor.site_places import (
    parse_site_places,
    resolve_intake_destination,
    resolve_outbound_dispatch_stops,
)
from harbor_web import fleet_connection

_VEHICLE_ROLE_LABELS = {
    'inbound_load': '수령구 상차',
    'intake_dropoff': '하역 정지선',
    'outbound_pickup': '반출 상차',
    'outbound_exit': '반출 전용 출구',
    'wait': '대기 정지선',
    'charge': '충전 칸',
}

_ZONE_ROLE_LABELS = {
    'storage': '장치 야드',
    'temporary': '임시 장치',
    'outbound': '반출대',
}

_STATUS_LABELS = {
    'verified': '검증 완료',
    'planned_unverified': '예정 · 미검증',
}


def site_places_context(*, condition=None):
    """Return read-only port site-place summary for the yard page; fail closed on any error."""
    config = fleet_connection.connection_settings()
    token = os.environ.get('HARBOR_FLEET_TOKEN', '').strip()
    if not config['origin'] or not token:
        return dict(available=False, status='ROSY 연동 전')
    if not config['allowed']:
        return dict(available=False, status='ROSY 허용 주소 아님')
    client = fleet_connection.fleet_client(config, token, timeout=1, verify_identity=True)
    try:
        payload = client.site_places()
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return dict(available=False, status='ROSY 위치 계약 확인 불가')
    try:
        places = parse_site_places(payload)
    except ValueError:
        return dict(available=False, status='ROSY 위치 계약 검증 실패')

    live_cell_site_map = None
    try:
        raw_sites = client.cell_sites()
        if isinstance(raw_sites, dict) and raw_sites.get('zones'):
            live_cell_site_map = normalize_cell_site_map(raw_sites)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        live_cell_site_map = None

    intake_stop, intake_reason = resolve_intake_destination(places)
    outbound_stops, outbound_reason = resolve_outbound_dispatch_stops(places)
    report = compare_condition_with_rosy(
        condition.payload if condition is not None else None,
        cell_site_map=live_cell_site_map,
        site_places=places,
    )
    alignment = dict(
        aligned=report['aligned'],
        summary='일치' if report['aligned'] else f"정합 점검 필요 ({len(report['issues'])}건)",
        issues=report['issues'],
    )
    return dict(
        available=True,
        schema=places.schema,
        digest=places.digest,
        intake_summary=f'사용 가능: {intake_stop}' if intake_stop else f'보류 ({intake_reason})',
        outbound_summary=(
            f'상차 {outbound_stops[0]} → 출구 {outbound_stops[1]}'
            if outbound_stops
            else f'보류 ({outbound_reason})'
        ),
        alignment=alignment,
        not_places=sorted(places.not_places),
        vehicle_places=[
            dict(
                place_id=vp.place_id,
                role_label=_VEHICLE_ROLE_LABELS.get(vp.port_role, vp.port_role),
                label=vp.label or vp.place_id,
                status_label=_STATUS_LABELS.get(vp.status, vp.status),
                dispatch_label='가능' if vp.dispatchable else '보류',
            )
            for vp in places.vehicle_places
        ],
        cell_zones=[
            dict(
                zone_id=cz.zone_id,
                role_label=_ZONE_ROLE_LABELS.get(cz.role, cz.role),
                label=cz.label or cz.zone_id,
                status_label=_STATUS_LABELS.get(cz.status, cz.status),
            )
            for cz in places.cell_zones
        ],
        intake_handoffs=[
            dict(
                handoff_id=ih.handoff_id,
                vehicle_stop_id=ih.vehicle_stop_id,
                target_cell_zones=list(ih.target_cell_zones),
                status_label=_STATUS_LABELS.get(ih.status, ih.status),
                dispatch_label='가능' if ih.dispatchable else '보류',
            )
            for ih in places.intake_handoffs
        ],
        outbound_handoffs=[
            dict(
                handoff_id=oh.handoff_id,
                cell_zone_id=oh.cell_zone_id,
                wire_slot_ref=oh.wire_slot_ref,
                vehicle_pickup_stop_id=oh.vehicle_pickup_stop_id or '미지정',
                vehicle_exit_stop_id=oh.vehicle_exit_stop_id or '미지정',
                status_label=_STATUS_LABELS.get(oh.status, oh.status),
                dispatch_label='가능' if oh.dispatchable else '보류',
            )
            for oh in places.outbound_handoffs
        ],
    )
