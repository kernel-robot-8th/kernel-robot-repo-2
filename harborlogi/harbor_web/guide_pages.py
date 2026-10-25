"""Role-scoped operating guidance; reading guidance never probes or controls Fleet."""
from harbor_web.auth import require_roles
from harbor_web.views import page


@require_roles('viewer', 'operator', 'field', 'admin')
def workflow(request):
    return page(request, 'workflow.html', section='workflow')


@require_roles('admin')
def integration(request):
    from harbor_web.pipeline import connection_summary
    return page(request, 'integration.html', section='settings', connection=connection_summary())
