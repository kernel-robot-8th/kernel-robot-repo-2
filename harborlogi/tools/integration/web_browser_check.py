"""Optional real-browser checks for Harbor demo navigation (Playwright + Edge)."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8050')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []

    def loaded(page):
        page.wait_for_load_state('networkidle')
        page.locator('#mode').filter(has_text='DEMO').wait_for()

    def nav(page, name):
        if page.locator('#menu-toggle').is_visible():
            page.locator('#menu-toggle').click()
        page.locator('#nav-' + name).click()

    def no_overflow(page):
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), page.url

    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        for name, width, height in [('desktop', 1440, 1000), ('desktop-1280', 1280, 800),
                                     ('tablet', 768, 1024), ('mobile', 390, 844)]:
            page = browser.new_page(viewport={'width': width, 'height': height}, reduced_motion='reduce')
            errors, failed = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('requestfailed', lambda request: failed.append(request.url))
            page.goto(args.url)
            loaded(page)
            assert page.url.endswith('/overview')
            assert page.locator('#attention-count').inner_text() == '3'
            assert page.locator('.attention-row').count() == 3
            assert 'DEMO-1024' not in page.locator('#attention').inner_text()
            sizes = page.evaluate("({body: parseFloat(getComputedStyle(document.body).fontSize), heading: parseFloat(getComputedStyle(document.getElementById('overview-title')).fontSize), skipColor: getComputedStyle(document.querySelector('.skip')).color})")
            assert sizes['heading'] >= sizes['body'] * 1.25, sizes
            assert sizes['skipColor'] != 'rgb(0, 0, 0)', sizes
            no_overflow(page)
            page.screenshot(path=str(args.output / (name + '-overview.png')), full_page=True)

            if width < 1280:
                page.locator('#menu-toggle').click()
                assert page.locator('#navigation').get_attribute('aria-modal') == 'true'
                page.keyboard.press('Escape')
                assert page.locator('#menu-toggle').evaluate('(n) => n === document.activeElement')
                assert page.locator('#menu-toggle').get_attribute('aria-expanded') == 'false'
            nav(page, 'work')
            page.locator('#tasks .task').first.wait_for()
            assert page.locator('.work-list h2').evaluate('(n) => n === document.activeElement')
            assert page.locator('.task').count() == 6
            nav(page, 'overview')
            page.locator('.attention-ref a').filter(has_text='DEMO-1025').click()
            page.locator('#detail').wait_for(state='visible')
            assert page.locator('#business-key').inner_text() == 'DEMO-1025'
            assert page.locator('#work-title').evaluate('(n) => n === document.activeElement')
            assert page.locator('#tab-overview').get_attribute('aria-selected') == 'true'
            no_overflow(page)
            page.screenshot(path=str(args.output / (name + '-work.png')), full_page=True)
            page.get_by_role('tab', name='계획', exact=True).click()
            page.locator('#pane-plan').wait_for(state='visible')
            assert page.locator('#moves li').count() == 3
            assert '/plan' in page.url
            page.screenshot(path=str(args.output / (name + '-plan.png')), full_page=True)
            page.get_by_role('tab', name='실행·근거', exact=True).click()
            page.locator('#pane-execution').wait_for(state='visible')
            assert page.locator('#fleet-status').inner_text() == '합성 상태 · 연결 안 함'
            assert page.locator('#receipt-status').inner_text() == '미확인'
            assert page.locator('#console').is_hidden()
            page.reload()
            loaded(page)
            page.locator('#pane-execution').wait_for(state='visible')
            page.get_by_role('button', name='ROSY 상태 다시 조회').click()
            page.locator('#detail').wait_for(state='visible')
            no_overflow(page)
            page.screenshot(path=str(args.output / (name + '-execution.png')), full_page=True)
            page.go_back()
            page.locator('#pane-plan').wait_for(state='visible')
            if width < 1280:
                page.locator('#back-work').click()
            page.locator('#view').select_option('all')
            page.locator('#search').fill('1024')
            assert page.locator('.task').count() == 1
            page.locator('.task').click()
            page.locator('#detail').wait_for(state='visible')
            assert page.locator('#business-key').inner_text() == 'DEMO-1024'
            page.go_back()
            page.wait_for_load_state('networkidle')
            assert page.locator('#search').input_value() == '1024'
            assert page.locator('#view').input_value() == 'all'
            nav(page, 'help')
            page.locator('#help-page').wait_for(state='visible')
            assert page.locator('#help-page img').evaluate('(n) => n.complete && n.naturalWidth > 0')
            page.screenshot(path=str(args.output / (name + '-help.png')), full_page=True)
            nav(page, 'planning')
            page.locator('#planning-result').wait_for(state='visible')
            assert page.locator('#planning-status').inner_text() == '계획안 생성'
            assert page.locator('#planning-moves li').count() == 3
            assert '재취급' in page.locator('#planning-moves li').nth(0).inner_text()
            assert '반출' in page.locator('#planning-moves li').nth(1).inner_text()
            assert '복귀' in page.locator('#planning-moves li').nth(2).inner_text()
            assert 'C1' in page.locator('#observed-bays .bay').nth(0).inner_text()
            assert 'C1' not in page.locator('#projected-bays .bay').nth(0).inner_text()
            assert 'C1' in page.locator('#projected-bays .bay').nth(2).inner_text()
            no_overflow(page)
            page.screenshot(path=str(args.output / (name + '-planning.png')), full_page=True)
            page.locator('#planning-cargo').select_option('C2')
            assert page.locator('#planning-result').is_hidden()
            page.get_by_role('button', name='계획 미리보기', exact=True).click()
            page.locator('#planning-result').wait_for(state='visible')
            assert page.locator('#planning-moves li').count() == 1
            page.reload()
            loaded(page)
            page.locator('#planning-result').wait_for(state='visible')
            assert page.locator('#planning-cargo').input_value() == 'C2'
            page.locator('#planning-cargo').select_option('C1')
            for scenario in ('no-space', 'stale'):
                page.locator('#planning-scenario').select_option(scenario)
                assert page.locator('#planning-result').is_hidden()
                page.get_by_role('button', name='계획 미리보기', exact=True).click()
                page.locator('#planning-result').wait_for(state='visible')
                assert page.locator('#planning-status').inner_text() == '계획 보류'
                assert page.locator('#planning-moves li').count() == 0
                assert page.locator('#projected-bays .bay').count() == 0
                assert page.locator('#planning-reason').bounding_box()['y'] < page.locator('#observed-bays').bounding_box()['y']
                no_overflow(page)
                page.screenshot(path=str(args.output / (name + '-planning-' + scenario + '.png')), full_page=True)
            assert not errors, errors
            assert not failed, failed
            results.append({'viewport': [width, height], 'page_errors': len(errors), 'failed_requests': len(failed),
                            'navigation_tabs_back_filters_focus_drawer': 'passed', 'planning_ready_hold_projection': 'passed', 'horizontal_overflow': False,
                            'computed_styles': sizes})
            page.close()

        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.route('**/api/work*', lambda route: route.fulfill(status=503, content_type='application/json',
            body=json.dumps({'message': 'synthetic unavailable'})))
        page.goto(args.url + '/overview')
        page.locator('#overview-error').wait_for(state='visible')
        assert page.locator('#attention-count').inner_text() == '미확인'
        assert '장부 기준으로 조치가 필요한 업무가 없습니다.' not in page.locator('#attention').inner_text()
        page.screenshot(path=str(args.output / 'unavailable-overview.png'), full_page=True)
        nav(page, 'work')
        assert page.locator('#list-count').inner_text() == '업무 수 미확인'
        page.locator('#search').fill('test')
        page.locator('#view').select_option('hold')
        page.get_by_role('button', name='목록 새로고침').click()
        page.locator('#list-error').wait_for(state='visible')
        assert page.locator('#list-count').inner_text() == '업무 수 미확인'
        assert page.locator('.task').count() == 0
        page.close()

        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.route('**/api/work*', lambda route: route.fulfill(content_type='application/json',
            body=json.dumps({'mode': 'demo', 'tasks': []})))
        page.goto(args.url + '/overview')
        loaded(page)
        assert page.locator('#attention-count').inner_text() == '0'
        page.close()

        page = browser.new_page()
        page.goto(args.url + '/work?view=constructor')
        loaded(page)
        assert page.locator('#view').input_value() == 'all'
        assert page.locator('.task').count() == 6
        page.close()

        page = browser.new_page()
        page.route('**/api/work*', lambda route: route.fulfill(content_type='application/json',
            body=json.dumps({'mode': 'live', 'tasks': []})))
        page.route('**/api/planning*', lambda route: route.fulfill(status=503, content_type='application/json',
            body=json.dumps({'error': 'INVENTORY_NOT_CONFIGURED', 'message': '실제 재고 source 미연동'})))
        page.goto(args.url + '/planning')
        page.locator('#planning-error').wait_for(state='visible')
        assert page.locator('#nav-planning').is_hidden()
        assert page.locator('#planning-result').is_hidden()
        assert page.locator('#planning-form').is_hidden()
        assert page.locator('#observed-bays .bay').count() == 0
        page.close()

        page = browser.new_page()
        page.goto(args.url + '/planning?cargo=&scenario=normal')
        page.locator('#planning-error').wait_for(state='visible')
        assert page.locator('#planning-result').is_hidden()
        assert page.locator('#planning-cargo').input_value() == ''
        page.close()

        page = browser.new_page()
        page.goto(args.url + '/planning?cargo=C1&scenario=normal')
        page.locator('#planning-result').wait_for(state='visible')
        page.route('**/api/planning*', lambda route: route.fulfill(status=503, content_type='application/json',
            body=json.dumps({'message': 'synthetic unavailable'})))
        page.locator('#planning-cargo').select_option('C2')
        page.locator('#planning-scenario').select_option('stale')
        page.get_by_role('button', name='계획 미리보기', exact=True).click()
        page.locator('#planning-error').wait_for(state='visible')
        assert 'cargo=C2' in page.url and 'scenario=stale' in page.url
        page.reload()
        page.locator('#planning-error').wait_for(state='visible')
        assert page.locator('#planning-cargo').input_value() == 'C2'
        assert page.locator('#planning-scenario').input_value() == 'stale'
        page.close()

        with urlopen(args.url + '/api/work') as response:
            original_listing = json.load(response)
        with urlopen(args.url + '/api/work?key=DEMO-1024') as response:
            original_detail = json.load(response)
        for key in ('.', '..', 'a/b', 'a\\b'):
            special_listing, special_detail = deepcopy(original_listing), deepcopy(original_detail)
            special_listing['tasks'] = [special_listing['tasks'][0]]
            special_listing['tasks'][0]['request_key'] = key
            special_detail['business']['request_key'] = key
            page = browser.new_page()
            page.route('**/api/work*', lambda route: route.fulfill(content_type='application/json',
                body=json.dumps(special_detail if '?' in route.request.url else special_listing)))
            page.goto(args.url + '/work')
            loaded(page)
            page.locator('.task').click()
            page.locator('#detail').wait_for(state='visible')
            assert page.locator('#business-key').inner_text() == key
            page.get_by_role('tab', name='계획', exact=True).click()
            page.locator('#pane-plan').wait_for(state='visible')
            page.locator('#search').fill(key)
            page.reload()
            loaded(page)
            page.locator('#pane-plan').wait_for(state='visible')
            assert page.locator('#business-key').inner_text() == key
            page.close()

        with urlopen(args.url + '/api/work') as response:
            listing = json.load(response)
        with urlopen(args.url + '/api/work?key=DEMO-1024') as response:
            detail = json.load(response)
        long_id = 'DEMO-' + 'LONG-IDENTIFIER-' * 8
        listing = deepcopy(listing)
        listing['tasks'][0]['request_key'] = long_id
        detail['business']['request_key'] = long_id
        detail['business']['mission_id'] = long_id
        detail['business']['cargo_id'] = 'CARGO-' + 'LONG-' * 24
        for move in detail['plan']:
            move['source'] = 'SOURCE-' + 'LOCATION-' * 12
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        page.route('**/api/work*', lambda route: route.fulfill(content_type='application/json',
            body=json.dumps(detail if '?' in route.request.url else listing)))
        page.goto(args.url + '/work/' + long_id)
        loaded(page)
        page.locator('#detail').wait_for(state='visible')
        no_overflow(page)
        for tab in ('plan', 'execution'):
            page.locator('#tab-' + tab).click()
            page.locator('#pane-' + tab).wait_for(state='visible')
            no_overflow(page)
        page.screenshot(path=str(args.output / 'mobile-long-id.png'), full_page=True)
        browser.close()
    (args.output / 'browser-summary.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps(results))


if __name__ == '__main__':
    main()
