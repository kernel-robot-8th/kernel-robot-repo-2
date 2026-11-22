"""Administrator-only creation of immutable condition versions from map drafts."""
import hmac
import json
import os
from urllib.error import HTTPError, URLError

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from harbor.cell_site_map import MAX_DOCUMENT_BYTES, decode_cell_site_json, normalize_cell_site_map
from harbor_web import fleet_connection, services
from harbor_web.auth import require_roles
from harbor_web.map_config import build_cell_site_import, build_map, editor_draft
from harbor_web.models import Audit, Condition, clean_text

IMPORT_TOKEN_SALT = 'harbor_web.cell_site_import'
IMPORT_TOKEN_MAX_AGE = 900


def _json(request, keys):
    if request.content_type != 'application/json':
        raise ValidationError('Expected application/json.')
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValidationError('Invalid JSON.') from error
    if not isinstance(data, dict) or set(data) != keys:
        raise ValidationError('Unexpected request fields.')
    return data


def _source(pk):
    return get_object_or_404(Condition, pk=pk) if pk is not None else None


@require_roles('admin')
@require_http_methods(['GET', 'POST'])
@ensure_csrf_cookie
def map_editor(request, pk=None):
    source = _source(pk)
    payload = source.payload if source else None
    if request.method == 'GET':
        try:
            draft = editor_draft(payload)
        except ValidationError as error:
            return render(request, 'error.html', dict(heading='맵 초안을 만들 수 없습니다',
                reason=' '.join(error.messages), return_url='/settings/'), status=400)
        return render(request, 'map_editor.html', dict(section='settings', source_condition=source,
            editor_data=dict(map=draft, source_id=source.pk if source else None,
                validate_url=request.path + 'validate/', save_url=request.path)))
    try:
        data = _json(request, {'map', 'fingerprint', 'name', 'calibration_ref'})
        result = build_map(payload, data['map'], source_id=source.pk if source else None)
        fingerprint = data['fingerprint']
        if (not isinstance(fingerprint, str) or not fingerprint.isascii() or
                not hmac.compare_digest(result['fingerprint'], fingerprint)):
            raise ValidationError('Validate this map draft before saving it.')
        for field, limit in (('name', 120), ('calibration_ref', 192)):
            if not isinstance(data[field], str) or len(data[field]) > limit:
                raise ValidationError('Name and calibration reference are required.')
            clean_text(data[field])
            try:
                data[field].encode('utf-8')
            except UnicodeError as error:
                raise ValidationError('Name and calibration reference require valid Unicode text.') from error
        with transaction.atomic():
            condition = services.register_condition(request.user, name=data['name'],
                calibration_ref=data['calibration_ref'], payload=result['payload'])
            Audit.objects.create(actor=request.user, action='condition.map.register', object_type='Condition',
                object_id=str(condition.pk), detail=dict(parent_condition_id=source.pk if source else None,
                    fingerprint=result['fingerprint'], summary=result['summary']))
        return JsonResponse(dict(condition_id=condition.pk, redirect=f'/inventory/?condition={condition.pk}'))
    except ValidationError as error:
        return JsonResponse(dict(errors=error.messages), status=400)


@require_roles('admin')
@require_http_methods(['POST'])
def map_validate(request, pk=None):
    source = _source(pk)
    try:
        data = _json(request, {'map'})
        result = build_map(source.payload if source else None, data['map'], source_id=source.pk if source else None)
        return JsonResponse(result)
    except ValidationError as error:
        return JsonResponse(dict(errors=error.messages), status=400)


def _import_page_context():
    fleet_cfg = fleet_connection.connection_settings()
    fleet_ready = bool(fleet_cfg['origin'] and fleet_cfg['allowed'] and os.environ.get('HARBOR_FLEET_TOKEN', '').strip())
    return dict(section='settings', fleet_cfg=fleet_cfg, fleet_ready=fleet_ready)


def _validate_text_fields(name, calibration_ref):
    for value, limit in ((name, 120), (calibration_ref, 192)):
        if not isinstance(value, str) or len(value) > limit:
            raise ValidationError('Name and calibration reference are required.')
        clean_text(value)
        try:
            value.encode('utf-8')
        except UnicodeError as error:
            raise ValidationError('Name and calibration reference require valid Unicode text.') from error


def _fetch_fleet_cell_sites():
    config = fleet_connection.connection_settings()
    token = os.environ.get('HARBOR_FLEET_TOKEN', '').strip()
    if not config['origin']:
        raise ValidationError('FLEET_ORIGIN_NOT_CONFIGURED: Fleet 주소가 설정되지 않았습니다.')
    if not config['allowed']:
        raise ValidationError('FLEET_ORIGIN_NOT_ALLOWED: 허용되지 않은 Fleet 주소입니다.')
    if not token:
        raise ValidationError('FLEET_TOKEN_NOT_CONFIGURED: HARBOR_FLEET_TOKEN 자격이 설정되지 않았습니다.')
    try:
        return fleet_connection.fleet_client(config, token, timeout=10).cell_sites()
    except HTTPError as error:
        raise ValidationError(f'FLEET_HTTP_{error.code}: Fleet 응답 오류 (HTTP {error.code})') from error
    except (URLError, TimeoutError, OSError) as error:
        reason = fleet_connection._network_reason(error)
        raise ValidationError(f'{reason}: Fleet에 연결할 수 없습니다.') from error
    except ValueError as error:
        raise ValidationError(f'INVALID_FLEET_RESPONSE: {error}') from error


@require_roles('admin')
@require_http_methods(['GET', 'POST'])
def cell_site_import(request):
    context = _import_page_context()
    if request.method == 'GET':
        return render(request, 'cell_site_import.html', {**context, 'preview': None, 'errors': []})
    try:
        source = request.POST.get('source', '').strip()
        if source == 'fleet':
            doc = _fetch_fleet_cell_sites()
        elif source == 'file':
            uploaded = request.FILES.get('map_file')
            if uploaded is None:
                raise ValidationError('JSON 파일을 선택하세요.')
            if uploaded.size is not None and uploaded.size > MAX_DOCUMENT_BYTES:
                raise ValidationError('INVALID_MAP_DOCUMENT: 파일 크기가 1 MiB를 넘습니다.')
            raw = uploaded.read(MAX_DOCUMENT_BYTES + 1)
            doc = decode_cell_site_json(raw)
        else:
            raise ValidationError('불러오기 출처(fleet 또는 file)가 올바르지 않습니다.')

        normalized = normalize_cell_site_map(doc)
        built = build_cell_site_import(normalized)
        existing_revisions = list(
            Audit.objects.filter(action='condition.import.register', detail__revision=normalized['revision'])
            .values_list('object_id', flat=True)[:5]
        )
        summary = dict(built['summary'])
        warnings = list(summary['warnings'])
        if existing_revisions:
            warnings.insert(0, f"같은 rev{normalized['revision']} 맵에서 등록한 설치 기준이 이미 있습니다.")
        summary['warnings'] = warnings
        summary['source'] = source

        import_token = signing.dumps(
            {
                'user_id': request.user.pk,
                'source': source,
                'payload': built['payload'],
                'audit_detail': {**built['audit_detail'], 'source': source},
            },
            salt=IMPORT_TOKEN_SALT,
            compress=True,
        )
        return render(request, 'cell_site_import.html', {
            **context,
            'preview': summary,
            'import_token': import_token,
            'source': source,
            'default_name': built['default_name'],
            'default_calibration_ref': built['default_calibration_ref'],
            'existing_revisions': existing_revisions,
            'errors': [],
        })
    except (ValidationError, ValueError) as error:
        messages = error.messages if isinstance(error, ValidationError) else [str(error)]
        return render(request, 'cell_site_import.html', {**context, 'preview': None, 'errors': messages}, status=400)


@require_roles('admin')
@require_http_methods(['POST'])
def cell_site_import_save(request):
    context = _import_page_context()
    token = request.POST.get('import_token', '')
    name = request.POST.get('name', '').strip()
    calibration_ref = request.POST.get('calibration_ref', '').strip()
    try:
        try:
            signed = signing.loads(token, salt=IMPORT_TOKEN_SALT, max_age=IMPORT_TOKEN_MAX_AGE)
        except signing.SignatureExpired as error:
            raise ValidationError('미리보기 유효 시간이 만료되었습니다. 다시 불러오세요.') from error
        except signing.BadSignature as error:
            raise ValidationError('유효하지 않은 미리보기 토큰입니다. 다시 불러오세요.') from error
        if not isinstance(signed, dict) or signed.get('user_id') != request.user.pk:
            raise ValidationError('현재 사용자와 일치하지 않는 미리보기 토큰입니다.')
        payload = signed.get('payload')
        audit_detail = signed.get('audit_detail')
        if not isinstance(payload, dict) or not isinstance(audit_detail, dict):
            raise ValidationError('손상된 미리보기 토큰입니다.')
        _validate_text_fields(name, calibration_ref)
        with transaction.atomic():
            services.constraints(payload)
            condition = services.register_condition(request.user, name=name, calibration_ref=calibration_ref, payload=payload)
            Audit.objects.create(
                actor=request.user,
                action='condition.import.register',
                object_type='Condition',
                object_id=str(condition.pk),
                detail=audit_detail,
            )
        return redirect(f'/inventory/?condition={condition.pk}')
    except (ValidationError, ValueError) as error:
        messages = error.messages if isinstance(error, ValidationError) else [str(error)]
        return render(request, 'cell_site_import.html', {**context, 'preview': None, 'errors': messages}, status=400)
