import ipaddress
import http.client
import json
import math
import os
import re
import uuid
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPSHandler, Request, build_opener

from harbor.cell_site_map import decode_cell_site_json
from harbor.fleet_discovery import _NoRedirect
from harbor.site_recognition import parse_site_recognition

# ROSY Wave 4 구현 경로 (`rosy.cell-site-recognition.v1`).
SITE_RECOGNITION_PATH_PROPOSED = '/api/fleet/cell-site-recognition'


class FleetIdentityError(ValueError):
    """The configured app identity was not authenticated; no operation was sent."""


class RosyClient:
    def __init__(self, base_url=None, token=None, *, timeout=10, transport=None, ca_file=None,
                 site_recognition_path=None):
        self._site_recognition_path = (site_recognition_path if site_recognition_path is not None else
            os.environ.get('HARBOR_ROSY_SITE_RECOGNITION_PATH', SITE_RECOGNITION_PATH_PROPOSED))
        if transport is not None:
            self._request = transport
            return
        parsed = urlsplit(base_url or "")
        try:
            loopback = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = False
        if (not isinstance(base_url, str) or any(c.isspace() or ord(c) < 32 for c in base_url)
                or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
                or parsed.port == 0
                or parsed.scheme == "http" and not loopback):
            raise ValueError("Expected an HTTPS Fleet origin, or loopback HTTP")
        if not isinstance(token, str) or not token or any(ord(c) < 32 for c in token):
            raise ValueError("Expected a configured bearer credential")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Expected a positive finite HTTP timeout")
        self._origin, self._token, self._timeout = base_url.rstrip("/"), token, timeout
        self._ca_file = ca_file
        self._discover = bool(ca_file and parsed.scheme == 'https' and parsed.hostname.endswith('.local'))
        handlers = [_NoRedirect()]
        if ca_file and not self._discover:
            from harbor.fleet_discovery import site_ssl_context
            handlers.append(HTTPSHandler(context=site_ssl_context(ca_file)))
        self._opener = None if self._discover else build_opener(*handlers)
        self._request = self._http

    def _http(self, method, path, body):
        data = json.dumps(body, allow_nan=False).encode() if body is not None else None
        headers = {
            "Authorization": "Bearer " + self._token,
            "Content-Type": "application/json",
            "X-Request-Id": uuid.uuid4().hex,
        }
        request = Request(self._origin + path, data=data, method=method, headers=headers)
        opener = self._opener
        if self._discover:
            from harbor.fleet_discovery import discovery_opener
            opener = discovery_opener(self._origin, self._ca_file, self._timeout)
        try:
            with opener.open(request, timeout=self._timeout) as response:
                raw = response.read(1024 * 1024 + 1)
        except http.client.HTTPException as error:
            raise ValueError('Invalid Fleet HTTP response') from error
        if len(raw) > 1024 * 1024:
            raise ValueError("Fleet response exceeds 1 MiB")
        if path == "/api/fleet/pipeline/cell-sites":
            return decode_cell_site_json(raw)
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Expected a Fleet response object")
        return value

    def session(self):
        return self._request('GET', '/api/fleet/session', None)

    def _call(self, method, path, body):
        principal = getattr(self, 'expected_principal', None)
        role = getattr(self, 'expected_role', None)
        if principal is not None or role is not None:
            identity = self.session()
            if (not isinstance(identity, dict)
                    or (principal is not None and identity.get('principal_id') != principal)
                    or identity.get('role') not in ('service', 'operator')
                    or (role is not None and identity.get('role') != role)):
                raise FleetIdentityError('Configured Fleet application identity was not verified')
        return self._request(method, path, body)

    def compile(self, binding):
        result = self._call("POST", "/api/fleet/cell-app/compile", binding.compile_request())
        candidate = result.get("candidate") if isinstance(result, dict) else None
        summary = result.get("summary") if isinstance(result, dict) else None
        if (not isinstance(candidate, dict) or not isinstance(summary, dict)
                or candidate.get("kind") != "cell_job" or candidate.get("recipe_sha256") != binding.recipe_digest
                or candidate.get("cell_sha256") != binding.cell_digest
                or result.get("process_artifact_digest") != binding.process_artifact_digest
                or type(summary.get("transfer_count")) is not int or summary["transfer_count"] != 1
                or not re.fullmatch(r"[0-9a-f]{64}", result.get("process_artifact_digest", ""))):
            raise ValueError("Installed documents do not compile to the bound single-transfer process")
        return result

    def propose(self, binding, *, request_key):
        return self._call("POST", "/api/fleet/cell-app/proposals", binding.proposal_request(request_key))

    def job(self, mission_id):
        return self._call("GET", "/api/fleet/cell-jobs/" + quote(mission_id, safe=""), None)

    def trip(self, candidates, steps, *, request_key):
        return self._call("POST", "/api/fleet/trip-jobs", {
            "candidate_robot_ids": list(candidates), "request_key": request_key, "steps": list(steps),
        })

    def trip_propose(self, candidates, steps, *, request_key, object_ref=None):
        # D-908: the app only proposes; a different named operator admits outside Harbor.
        cargo = {} if object_ref is None else {"object_ref": object_ref}  # ADR 0029 optional field
        return self._call("POST", "/api/fleet/trip-proposals", {
            "candidate_robot_ids": list(candidates), "request_key": request_key, "steps": list(steps), **cargo,
        })

    def trip_proposal_request(self, request_key):
        return self._call("GET", "/api/fleet/trip-proposals?request_key=" + quote(request_key, safe=""), None)

    def trip_job(self, mission_id):
        return self._call("GET", "/api/fleet/trip-jobs/" + quote(mission_id, safe=""), None)

    def trip_request(self, request_key):
        return self._call("GET", "/api/fleet/trip-jobs?request_key=" + quote(request_key, safe=""), None)

    def propose_arm(self, binding, *, request_key):
        return self._arm_response(self._call("POST", "/api/fleet/arm-transfers/jobs", binding.proposal_request(request_key)))

    def _arm_response(self, result):
        job = result.get('job') if isinstance(result, dict) else None
        if not isinstance(job, dict):
            raise ValueError('Invalid ARM transfer response')
        principal = getattr(self, 'expected_principal', None)
        if principal is not None and job.get('proposal_principal_id') != principal:
            raise ValueError('Fleet proposal principal differs')
        return result

    def arm_job(self, mission_id):
        return self._arm_response(self._call("GET", "/api/fleet/arm-transfers/jobs/" + quote(mission_id, safe=""), None))

    def arm_request(self, request_key):
        return self._arm_response(self._call("GET", "/api/fleet/arm-transfers/jobs?request_key=" + quote(request_key, safe=""), None))

    def physical_receipt(self, mission_id):
        return self._call("GET", "/api/fleet/arm-transfers/jobs/" + quote(mission_id, safe="") + "/physical-receipt", None)

    def physical_evidence(self, mission_id):
        return self._call("GET", "/api/fleet/arm-transfers/jobs/" + quote(mission_id, safe="") + "/physical-evidence", None)

    def recheck_read(self, mission_id, recheck_key):
        return self._call("GET", "/api/fleet/arm-transfers/jobs/" + quote(mission_id, safe="") + "/rechecks/" + quote(recheck_key, safe=""), None)

    def port_events(self, *, after=None, limit=100):
        """ADR 0029 read on R; `after` is R's opaque cursor, passed back unparsed."""
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("Port event page size is 1..200")
        query = f"?limit={limit}" if after is None else f"?after={quote(after, safe='')}&limit={limit}"
        return self._call("GET", "/api/fleet/port-events" + query, None)

    def execution_profile(self):
        # Read-only platform signal (ADR0022). The platform route exists since v1.361; a build without it answers 404, which stays unmet.
        return self._call("GET", "/api/fleet/execution-profile", None)

    def workcell_observations(self):
        """Read-only D-971 workcell observation and open arm incident status."""
        return self._call("GET", "/api/fleet/workcell-observations", None)

    def cell_sites(self):
        """Read-only canonical cell site map (rosy.cell-site-map.v1)."""
        return self._call("GET", "/api/fleet/pipeline/cell-sites", None)

    def site_recognition(self, *, known_site_refs=None):
        """Read and validate a proposed ADR 0032 cell-site recognition snapshot; never updates the ledger."""
        path = self._site_recognition_path
        if (not isinstance(path, str) or not path.startswith('/') or path.startswith('//')
                or any(c.isspace() or ord(c) < 32 or c in '\\?#' for c in path)):
            raise ValueError('INVALID_SITE_RECOGNITION_PATH: expected an origin-relative path')
        return parse_site_recognition(self._call('GET', path, None), known_site_refs=known_site_refs)

    def site_places(self):
        """Read-only geometry-free port site places (rosy.port-site-places.v1)."""
        return self._call("GET", "/api/fleet/site-points?view=port", None)

    def fleet_state(self):
        """Read-only Fleet robot states, battery/power health, and per-robot E-STOP status."""
        return self._call("GET", "/api/fleet/state", None)

    def cell_tasks(self, *, cell_id=None):
        """Read-only OMX cell task statuses and human_safety indicators."""
        suffix = ("?cell_id=" + quote(str(cell_id), safe="")) if cell_id else ""
        return self._call("GET", "/api/fleet/pipeline/cell-tasks" + suffix, None)

    def _checked_key(self, value, label):
        if (not isinstance(value, str) or not value.strip() or len(value) > 128
                or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)):
            raise ValueError(f'Invalid {label}')
        return value.strip()

    def request_recheck(self, mission_id, recheck_key):
        return self._call("POST", "/api/fleet/arm-transfers/jobs/" + quote(mission_id, safe="") + "/rechecks",
                          {"recheck_key": self._checked_key(recheck_key, 'recheck_key')})

    def cancel_arm(self, mission_id, *, reason):
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 192 or any(ord(c) < 32 for c in reason):
            raise ValueError('Expected a short cancellation reason')
        return self._call('POST', '/api/fleet/arm-transfers/jobs/' + quote(mission_id, safe='') + '/cancel', {'reason': reason})

    def arm_recovery_preview(self, mission_id):
        """Read-only S3 ARM transfer recovery preview (`rosy` owns recovery grant)."""
        return self._call('GET', '/api/fleet/arm-transfers/jobs/' + quote(mission_id, safe='') + '/recovery', None)

    def arm_recovery_request(self, mission_id, *, request_key, expected_context_digest):
        """Propose S3 ARM transfer recovery; execution decision remains with a ROSY named operator."""
        key = self._checked_key(request_key, 'request_key')
        if not isinstance(expected_context_digest, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_context_digest):
            raise ValueError('Invalid expected_context_digest')
        return self._call(
            'POST',
            '/api/fleet/arm-transfers/jobs/' + quote(mission_id, safe='') + '/recovery',
            {'request_key': key, 'expected_context_digest': expected_context_digest},
        )

    def preview_cell_plan(self, payload):
        return self._call('POST', '/api/fleet/pipeline/cell-plans/dry-run', dict(payload))

    def propose_cell_plan(self, payload, *, request_key):
        key = self._checked_key(request_key, 'request_key')
        return self._call('POST', '/api/fleet/pipeline/cell-plans', {**dict(payload), 'request_key': key})

    def cell_plan(self, plan_id):
        return self._call('GET', '/api/fleet/pipeline/cell-plans/' + quote(plan_id, safe=''), None)

    def cell_plan_request(self, request_key):
        key = self._checked_key(request_key, 'request_key')
        return self._call('GET', '/api/fleet/pipeline/cell-plans?request_key=' + quote(key, safe=''), None)

    def cancel_cell_plan(self, plan_id, *, reason):
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 192 or any(ord(c) < 32 for c in reason):
            raise ValueError('Expected a short cancellation reason')
        return self._call('POST', '/api/fleet/pipeline/cell-plans/' + quote(plan_id, safe='') + '/cancel', {'reason': reason.strip()})

    def session_typed(self):
        from harbor.fleet_types import parse_fleet_session
        return parse_fleet_session(self.session())

    def execution_profile_typed(self):
        from harbor.fleet_types import parse_execution_profile
        return parse_execution_profile(self.execution_profile())

    def workcell_observations_typed(self):
        from harbor.fleet_types import parse_workcell_observations
        return parse_workcell_observations(self.workcell_observations())

    def site_places_typed(self):
        from harbor.site_places import parse_site_places
        return parse_site_places(self.site_places())

    def cell_sites_typed(self):
        from harbor.fleet_types import parse_cell_site_map
        return parse_cell_site_map(self.cell_sites())

    def port_events_typed(self, *, after=None, limit=100):
        from harbor.fleet_types import parse_port_event_page
        return parse_port_event_page(self.port_events(after=after, limit=limit))

    def preview_cell_plan_typed(self, payload, *, expected_order_source=None):
        from harbor.cell_plan import parse_cell_plan_dry_run
        raw = payload.to_wire() if hasattr(payload, 'to_wire') else payload
        return parse_cell_plan_dry_run(self.preview_cell_plan(raw), expected_order_source=expected_order_source)

    def propose_cell_plan_typed(self, payload, *, request_key, expected_order_source, expected_step_count=None):
        from harbor.cell_plan import parse_cell_plan_response
        raw = payload.to_wire() if hasattr(payload, 'to_wire') else payload
        return parse_cell_plan_response(
            self.propose_cell_plan(raw, request_key=request_key),
            expected_order_source=expected_order_source,
            expected_step_count=expected_step_count,
            expected_request_key=request_key,
        )

    def cell_plan_typed(self, plan_id, *, expected_order_source, expected_step_count=None):
        from harbor.cell_plan import parse_cell_plan_response
        return parse_cell_plan_response(
            self.cell_plan(plan_id),
            expected_order_source=expected_order_source,
            expected_step_count=expected_step_count,
            expected_plan_id=plan_id,
        )

    def cell_plan_request_typed(self, request_key, *, expected_order_source, expected_step_count=None):
        from harbor.cell_plan import parse_cell_plan_response
        return parse_cell_plan_response(
            self.cell_plan_request(request_key),
            expected_order_source=expected_order_source,
            expected_step_count=expected_step_count,
            expected_request_key=request_key,
        )

    def cancel_cell_plan_typed(self, plan_id, *, reason, expected_order_source=None):
        from harbor.cell_plan import parse_cell_plan_cancel
        return parse_cell_plan_cancel(
            self.cancel_cell_plan(plan_id, reason=reason),
            expected_order_source=expected_order_source,
            expected_plan_id=plan_id,
        )


def load_site_recognition(client=None, *, known_site_refs=None):
    """Optional read entry point for future UI use: (snapshot or None, status text)."""
    if os.environ.get('HARBOR_ROSY_SITE_RECOGNITION') != '1':
        return None, 'ROSY 인식 연동 전'
    if client is None:
        origin, token = os.environ.get('HARBOR_FLEET_ORIGIN'), os.environ.get('HARBOR_FLEET_TOKEN')
        if not origin or not token:
            return None, 'ROSY 인식 연동 전'
        client = RosyClient(origin, token, ca_file=os.environ.get('HARBOR_FLEET_CA_FILE') or None)
    try:
        return client.site_recognition(known_site_refs=known_site_refs), ''
    except HTTPError as error:
        if error.code != 404:
            error.close()
            return None, 'ROSY 인식 연결 오류'
        detail_code = None
        try:
            raw = error.read(4097)  # synthetic HTTPError may carry fp=None
            detail = json.loads(raw).get('detail', {}) if len(raw) <= 4096 else {}
            if isinstance(detail, dict):
                detail_code = detail.get('code')
        except (http.client.HTTPException, OSError, ValueError, TypeError, AttributeError):
            detail_code = None
        finally:
            error.close()
        return None, ('ROSY 인식 스냅샷 수신 대기' if detail_code == 'CELL_SITE_RECOGNITION_NOT_YET_OBSERVED'
                      else 'ROSY에 아직 인식 API 없음')
    except FleetIdentityError:
        return None, 'ROSY 인식 연결 오류'
    except ValueError:
        return None, 'ROSY 인식 응답 형식 오류'
    except OSError:
        return None, 'ROSY 인식 연결 오류'


def _clean_text(value, limit=200):
    if not isinstance(value, str):
        return ""
    return " ".join("".join(" " if ord(c) < 32 or ord(c) == 127 else c for c in value).split())[:limit]


def open_arm_incident_holds(snapshot):
    """Extract held D-971 arm incidents from GET /api/fleet/workcell-observations; raises ValueError if malformed."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("workcells"), list):
        raise ValueError("Invalid workcell observations payload")
    holds = []
    for cell in snapshot["workcells"]:
        if not isinstance(cell, dict) or not isinstance(cell.get("arms"), list):
            raise ValueError("Invalid workcell entry")
        cell_id = _clean_text(cell.get("workcell_id") or "", 96)
        for arm in cell["arms"]:
            if not isinstance(arm, dict):
                raise ValueError("Invalid arm entry")
            incidents = arm.get("incidents", [])
            if not isinstance(incidents, list):
                raise ValueError("Invalid arm incidents entry")
            held = []
            for inc in incidents:
                if not isinstance(inc, dict):
                    raise ValueError("Invalid arm incident entry")
                if inc.get("state") == "cleared" or inc.get("held") is False:
                    continue
                decision = inc.get("decision")
                # Fail closed like Fleet's ArmIncidentStore._holds: missing or malformed decision stays held.
                if not isinstance(decision, dict) or decision.get("hold") is not False:
                    held.append(inc)
            if held:
                first_decision = held[0].get("decision") if isinstance(held[0].get("decision"), dict) else {}
                reason = _clean_text(first_decision.get("reason")) or "arm incident not cleared"
                explanation = held[0].get("explanation") if isinstance(held[0].get("explanation"), dict) else None
                assessment = explanation.get("assessment") if explanation and isinstance(explanation.get("assessment"), dict) else None
                entry = {
                    "workcell_id": cell_id,
                    "source_id": _clean_text(arm.get("source_id") or "", 96),
                    "arm_id": _clean_text(arm.get("arm_id") or "", 96),
                    "incident_ids": [_clean_text(inc.get("incident_id") or "", 64) for inc in held[:16] if inc.get("incident_id")],
                    "reason": reason,
                }
                if assessment and isinstance(assessment.get("type"), str):
                    entry["assessment"] = {"type": _clean_text(assessment["type"], 64),
                                           "direction": _clean_text(assessment.get("direction") or "", 64)}
                holds.append(entry)
                if len(holds) >= 16:
                    return holds
    return holds
