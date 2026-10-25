"""Role-scoped operating guidance; reading guidance never probes or controls Fleet."""
from harbor_web.auth import require_roles
from harbor_web.views import page
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods


class ConnectionVersionForm(forms.Form):
    expected_version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class ConnectionForm(ConnectionVersionForm):
    origin = forms.ChoiceField(label='연결할 Fleet 주소')
    principal = forms.CharField(label='Fleet 서비스 신원', max_length=192,
        help_text='Fleet에서 발급한 토큰의 principal ID와 정확히 일치해야 합니다.')
    arm_console_enabled = forms.BooleanField(label='이 Fleet에 ARM 승인 화면을 설치했습니다', required=False,
        help_text='화면 설치 선언입니다. API 연결 검사와 실행 승인을 대신하지 않습니다.')

    def __init__(self, *args, origins, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['origin'].choices = [(value, value) for value in origins]


CONNECTION_REASONS = {
    'READ_API_VERIFIED': '서비스 신원과 ARM 조회 API를 확인했습니다. 제출 권한·설치 대응·실물 준비는 별도로 검증해야 합니다.',
    'PRINCIPAL_MISMATCH': '토큰의 신원이 설정과 다릅니다. 배포 자격과 서비스 신원을 확인하세요.',
    'ROLE_NOT_SUITABLE': '조회된 역할로는 작업 제안용 연결을 확인할 수 없습니다.',
    'AUTHENTICATION_FAILED': 'Fleet이 인증을 거절했습니다. 서버에 설치한 서비스 토큰을 확인하세요.',
    'AUTHORIZATION_FAILED': 'Fleet이 조회 권한을 거절했습니다.',
    'ARM_NOT_INSTALLED': 'Fleet의 ARM 실행 설치가 준비되지 않았습니다.',
    'ARM_API_UNCONFIRMED': '이 Fleet에서 호환되는 ARM 조회 API를 확인하지 못했습니다.',
    'SESSION_API_UNCONFIRMED': 'Fleet 세션 조회 API를 확인하지 못했습니다.',
    'SESSION_SCHEMA_UNCONFIRMED': 'Fleet 신원 응답 형식이 계약과 다릅니다.',
    'FLEET_UNREACHABLE': 'Fleet 응답을 받지 못했습니다. 연결 주소·네트워크를 확인하세요.',
    'INVALID_FLEET_RESPONSE': 'Fleet 응답을 검증할 수 없습니다.',
    'CONNECTION_INCOMPLETE': '연결 주소·서비스 신원·서버 토큰이 필요합니다.',
    'ORIGIN_NOT_ALLOWED': '서버가 허용한 Fleet 주소만 사용할 수 있습니다.',
    'CONNECTION_CHANGED': '연결 설정이 변경됐습니다. 새로고침 후 확인하세요.',
    'DEPLOYMENT_MANAGED_CONNECTION': '배포 환경에서 관리하는 연결입니다. 서버 설정에서 변경해야 합니다.',
    'EXISTING_EXECUTION_IDENTITY_LOCKED': '원래 실행이 있어 연결 신원을 바꿀 수 없습니다. 원래 요청 대조를 유지하세요.',
    'INVALID_FLEET_PRINCIPAL': '서비스 신원의 길이와 문자를 확인하세요.',
}


@require_roles('viewer', 'operator', 'field', 'admin')
def workflow(request):
    return page(request, 'workflow.html', section='workflow')


@require_roles('admin')
@require_http_methods(['GET', 'POST'])
def integration(request):
    import os
    from harbor_web import fleet_connection as bridge
    from harbor_web.pipeline import connection_summary
    config = bridge.connection_settings()
    origins = bridge.allowed_origins()
    form = ConnectionForm(initial=dict(config, expected_version=config['version']), origins=origins)
    if request.method == 'POST':
        action = request.POST.get('action')
        form = ConnectionForm(request.POST, origins=origins) if action == 'save' else form
        command = form if action == 'save' else ConnectionVersionForm(request.POST)
        if action not in {'save', 'check'}:
            from django.http import HttpResponseBadRequest
            return HttpResponseBadRequest('Unknown connection action.')
        if command.is_valid():
            try:
                if action == 'save':
                    bridge.save_connection(request.user, **command.cleaned_data)
                    messages.success(request, 'Fleet 연결 설정을 저장했습니다. 연결 확인을 실행하세요.')
                else:
                    result = bridge.check_connection(request.user, **command.cleaned_data)
                    messages.info(request, CONNECTION_REASONS.get(result['reason'], '연결을 확인할 수 없습니다.'))
                return redirect('/settings/integration/')
            except ValidationError as error:
                messages.error(request, CONNECTION_REASONS.get(error.messages[0], '연결 설정을 확인하세요.'))
        else:
            messages.error(request, '연결 입력 또는 설정 버전을 확인한 뒤 다시 시도하세요.')
    check = bridge.last_check()
    return page(request, 'integration.html', section='settings', connection=connection_summary(),
        connection_config=config, connection_form=form, connection_check=check,
        check_message=CONNECTION_REASONS.get(check.detail['reason'], '') if check else '',
        origins_available=bool(origins), server_inputs=[
            dict(label=label, present=bool(os.environ.get('HARBOR_' + key)), variable='HARBOR_' + key)
            for label, key in [('Fleet 서비스 토큰', 'FLEET_TOKEN'), ('업무 실행 장부', 'LEDGER_PATH'), ('설치된 이동 대응', 'BINDINGS_PATH')]])
