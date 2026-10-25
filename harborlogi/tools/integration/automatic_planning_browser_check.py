"""Isolated real-browser acceptance: placement changes with schedule and observations."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import traceback
from urllib.request import urlopen
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(Path(os.environ.get('LOCALAPPDATA',''))/'Temp/harbor-web-browser'))


def main():
    output=ROOT/'runs/automatic-planning-browser'/('run-'+uuid.uuid4().hex)
    output.mkdir(parents=True)
    for key in list(os.environ):
        if key.startswith('HARBOR_'):del os.environ[key]
    os.environ.update(DJANGO_SETTINGS_MODULE='harbor_web.settings',HARBOR_WEB_MODE='development',
        HARBOR_WEB_SECRET_KEY=secrets.token_urlsafe(64),HARBOR_WEB_DATABASE=str(output/'web.sqlite3'))
    import django
    django.setup()
    from django.core.management import call_command
    from django.contrib.auth.models import User,Group
    from django.utils import timezone
    from harbor_web import services
    from harbor_web.models import Plan,Command,Observation,CargoFootprint
    from playwright.sync_api import sync_playwright
    call_command('migrate',verbosity=0)
    call_command('bootstrap_roles',verbosity=0)
    password=secrets.token_urlsafe(30)
    operator=User.objects.create_user('planning-operator',password=password)
    admin=User.objects.create_user('planning-admin',password=password)
    operator.groups.add(Group.objects.get(name='operator'))
    admin.groups.add(Group.objects.get(name='admin'))
    cargo={code:services.register_cargo(operator,code=code,name='Synthetic '+code,weight_kg=1,
        volume_m3=1,destination='synthetic yard',length_cells=1) for code in ['C1','C2','C3']}
    shapes={code:CargoFootprint.objects.get(cargo=value).pk for code,value in cargo.items()}
    policy=dict(outbound_stand='stand',temporary_bays=['temp'],conveyor='conveyor',
        allowed_moves=[['Y1','stand'],['Y2','stand']],arm_reach=[['A',x] for x in ['Y1','Y2','temp','stand']],
        max_observation_age_seconds=1800,yard_layout=dict(rows=1,columns=2,levels=2,cells=[
            dict(bay_id='Y1',row=1,column=1),dict(bay_id='Y2',row=1,column=2)]),
        spatial_areas=[dict(area_id='temp',rows=1,columns=1,levels=2),dict(area_id='stand',rows=1,columns=1,levels=1)],
        spatial_moves=[['yard','temp'],['temp','yard'],['yard','stand']])
    condition=services.register_condition(admin,name='Synthetic placement map',calibration_ref='synthetic-only',payload=policy)
    payload=dict(schema='grid-placement-v1',observed_areas=['yard','temp','stand'],holdings=[],placements=[
        dict(cargo_id=code,area_id='yard',row=1,column=column,level=1,orientation=0,footprint_revision=shapes[code])
        for code,column in [('C1',1),('C2',2)]])
    observed=services.record_observation(admin,source_id='planning-source',source_revision='one',
        observed_at=timezone.now(),provenance='SYNTHETIC',condition=condition,payload=payload)
    outgoing={code:services.create_order(operator,cargo=cargo[code],kind='OUTBOUND',priority_group=group,
        sequence=1,tie_policy='FIXED') for code,group in [('C1',1),('C3',2),('C2',3)]}
    incoming=services.create_order(operator,cargo=cargo['C3'],kind='INBOUND',priority_group=9,sequence=1,tie_policy='FIXED')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    origin=f'http://127.0.0.1:{port}'
    result=dict(status='FAILED',output=str(output),checks=[],actual_fleet=False)
    child=None
    log=(output/'server.log').open('w',encoding='utf-8')
    try:
        child=subprocess.Popen([sys.executable,'-m','harbor_web','--port',str(port)],cwd=ROOT,
            env=os.environ.copy(),stdout=log,stderr=log)
        deadline=time.monotonic()+30
        while True:
            try:
                if urlopen(origin+'/accounts/login/',timeout=1).status==200:break
            except OSError:
                if time.monotonic()>deadline:raise AssertionError('Server readiness timeout')
                time.sleep(.2)
        os.environ['DJANGO_ALLOW_ASYNC_UNSAFE']='true'
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            page=browser.new_page(viewport=dict(width=1440,height=1000))
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            def login(page,user):
                page.goto(origin+'/accounts/login/')
                page.locator('[name=username]').fill(user.username)
                page.locator('[name=password]').fill(password)
                page.locator('button[type=submit]').click()
                page.wait_for_url(origin+'/')
            login(page,operator)
            page.goto(origin+f'/requests/{incoming.pk}/')
            form=page.locator('form:has(input[name=action][value=plan])')
            form.locator('[name=observation]').select_option(str(observed.pk))
            form.locator('[name=automatic]').check()
            form.locator('button[type=submit]').click()
            page.wait_for_url('**/plans/*/')
            first=Plan.objects.filter(order=incoming).latest('pk')
            assert first.result['status']=='READY' and first.result['recommendation']['column']==2
            original=json.loads(json.dumps(first.result))
            page.locator('[data-yard-3d][data-ready=true]').wait_for()
            page.screenshot(path=str(output/'01-initial-recommendation.png'))
            page.locator('[data-yard-stage="1"]').click()
            page.wait_for_function('() => document.querySelector("[data-yard-3d] canvas").dataset.moving === "false"')
            page.locator('[data-yard-3d]').scroll_into_view_if_needed()
            page.screenshot(path=str(output/'02-predicted-placement.png'))
            page.goto(origin+f'/requests/{outgoing["C1"].pk}/')
            page.get_by_text('우선순위·지시 메모 변경',exact=True).click()
            change=page.locator('form:has(input[name=action][value=priority])')
            change.locator('[name=priority_group]').fill('4')
            change.locator('button[type=submit]').click()
            page.wait_for_load_state('networkidle')
            second=Plan.objects.filter(order=incoming).latest('pk')
            assert second.pk>first.pk and second.result['trigger']=='order.update'
            assert second.result['recommendation']['column']==1
            page.goto(origin+f'/plans/{second.pk}/')
            page.locator('[data-yard-3d][data-ready=true]').wait_for()
            page.screenshot(path=str(output/'03-replanned-after-priority.png'))
            first.refresh_from_db();assert first.result==original
            page.goto(origin+f'/plans/{first.pk}/')
            assert page.get_by_text('계산 기준이 달라졌습니다',exact=True).is_visible()
            admin_page=browser.new_page()
            login(admin_page,admin)
            admin_page.goto(origin+'/inventory/import/')
            changed=json.loads(json.dumps(payload));changed['placements'][1].update(column=1,level=2)
            for name,value in [('source_id','planning-source'),('source_revision','two'),
                    ('observed_at',timezone.localtime().strftime('%Y-%m-%dT%H:%M')),('payload',json.dumps(changed))]:
                admin_page.locator(f'[name={name}]').fill(value)
            admin_page.locator('[name=condition]').select_option(str(condition.pk))
            admin_page.locator('[name=provenance]').select_option('DECLARED')
            admin_page.locator('form:has([name=payload]) button[type=submit]').click()
            admin_page.wait_for_url('**/inventory/?observation=*')
            third=Plan.objects.filter(order=incoming).latest('pk')
            assert third.pk>second.pk and third.result['trigger']=='observation.record'
            assert (third.result['recommendation']['column'],third.result['recommendation']['level'])==(2,1)
            page.goto(origin+f'/plans/{third.pk}/')
            page.locator('[data-yard-3d][data-ready=true]').wait_for()
            page.screenshot(path=str(output/'04-replanned-after-observation.png'))
            for width in (1440,1280,768,390):
                page.set_viewport_size(dict(width=width,height=1000))
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width
            page.goto(origin+f'/requests/{incoming.pk}/')
            form=page.locator('form:has(input[name=action][value=plan])')
            form.locator('[name=automatic]').uncheck()
            form.locator('button[type=submit]').click()
            page.wait_for_url('**/plans/*/')
            incoming.refresh_from_db();assert not incoming.auto_replan
            assert not Command.objects.exists()
            observed.refresh_from_db();assert observed.payload==payload
            assert Observation.objects.count()==2
            assert not errors,errors
            result.update(status='PASSED',checks=['native inbound automatic form','schedule reversal changes target',
                'new observed layout changes target','pinned old plans and actual observation unchanged',
                'native admin observation import triggers internal recalculation','four widths without overflow',
                'disable automatic mode','no command or fabricated observation'],errors=errors)
            browser.close()
    except Exception:
        result['traceback']=traceback.format_exc()
    finally:
        if child is not None:
            child.terminate();child.wait(timeout=10)
        log.close()
        (output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))
    return 0 if result['status']=='PASSED' else 1


if __name__=='__main__':raise SystemExit(main())
