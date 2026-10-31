"""Administrator-only creation of immutable condition versions from map drafts."""
import hmac
import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from harbor_web import services
from harbor_web.auth import require_roles
from harbor_web.map_config import build_map, editor_draft
from harbor_web.models import Audit, Condition, clean_text


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
