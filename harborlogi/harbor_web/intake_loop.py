"""Inbound trips and ROSY port events for the executor (ADR 0029); nothing here admits or moves a device.

`prepare_departure` saves a PLANNED trip that declares the departing cargo code (`object_ref`) and
adds the cargo to the intake manifest. The executor proposes that trip once (a different named
operator admits it in ROSY), records TRIP_ARRIVED when Fleet confirms the trip, and reads R's port
events into the intake ledger. Both are off until HARBOR_TRIP_DESTINATION names the intake place.
"""
import logging
import os

from urllib.error import HTTPError, URLError

from django.db.models import Max

from harbor.port_events import RANK, IntakeLedger
from harbor.site_places import parse_site_places, resolve_intake_destination
from harbor.transport import TransportLedger, TripIntent

logger = logging.getLogger(__name__)


def trip_key(departure):
    return 'harbor-trip:' + str(departure.key)


def _destination():
    return os.environ.get('HARBOR_TRIP_DESTINATION', '')


def _resolve_tick_destination(client):
    configured = _destination().strip()
    if configured == 'auto':
        try:
            places = parse_site_places(client.site_places())
            stop_id, _ = resolve_intake_destination(places)
            return stop_id or ''
        except (HTTPError, URLError, OSError, ValueError):
            return ''
    return configured


def prepare_trip(departure, *, now, destination=None):
    """Declare the departing cargo on a PLANNED trip; None while trips are not configured."""
    path = os.environ.get('HARBOR_LEDGER_PATH', '')
    target = destination if destination is not None else _destination()
    if not path or not target:
        return None
    intent = TripIntent(trip_key(departure), (departure.vehicle_id,), target,
                        object_ref=departure.cargo.code)
    TransportLedger(path).prepare(intent)
    IntakeLedger(path).expect(intent.object_ref, now=now)
    return intent


def tick(registry, *, now):
    """Propose planned trips, mark arrivals, then read one page of port events. Returns the poll result."""
    from harbor_web.models import DepartureIntent
    path, client = registry.config['path'], registry.client
    transport, intake = TransportLedger(path), IntakeLedger(path)
    dest = _resolve_tick_destination(client)
    if dest:
        # shortcut: scans every departure each tick and skips arrived ones; add an index when departures grow large.
        for departure in DepartureIntent.objects.select_related('cargo').order_by('pk'):
            key, cargo = trip_key(departure), departure.cargo.code
            try:
                # 멱등 준비로 intent만 남거나 manifest 저장이 실패한 회차도 복구한다.
                prepare_trip(departure, now=now, destination=dest)
                row = transport.read(key)
                if RANK.get(intake.read(cargo)['state'], len(RANK)) >= RANK['TRIP_ARRIVED']:
                    continue  # arrived, further along, or HOLD
                if row['state'] == 'PLANNED':
                    transport.propose(key, client)
                elif row['state'] != 'SUBMITTING':
                    job = transport.reconcile_proposal(key, client)
                    if job is not None and job.get('status') == 'GOAL_CONFIRMED':
                        intake.arrive(cargo, now=now)
            except Exception:
                logger.exception('출발 준비 %s의 이송 처리 실패; 다음 틱에 재시도', departure.pk)
                continue
    intake.expire(now=now, connection=registry.config['origin'])  # An R that answered before, then went silent.
    result = intake.poll(client, connection=registry.config['origin'], now=now)
    try:
        from harbor_web.port_event_projection import project_stored_events
        project_stored_events(intake)
    except Exception:
        logger.exception('Port event inventory projection failed; poll cursor preserved')
    from harbor_web.field_episodes import prepare_departure_from_release
    from harbor_web.models import FieldEpisode
    latest = FieldEpisode.objects.exclude(state='REASSIGNED').values('vehicle_id').annotate(pk_max=Max('pk')).values('pk_max')
    for episode in FieldEpisode.objects.filter(pk__in=latest, departureintent__isnull=True).order_by('pk'):
        try:
            prepare_departure_from_release(episode, now=now)
        except Exception:
            logger.exception('회차 %s의 ROSY 출발 준비 실패', episode.pk)
    return result
