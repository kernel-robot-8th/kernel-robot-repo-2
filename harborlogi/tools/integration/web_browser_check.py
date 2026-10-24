"""Optional visual checks for `python -m harbor.web --demo` (requires Playwright + Edge)."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8040')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        for name, width, height in [('desktop', 1440, 1000), ('tablet', 768, 1024), ('mobile', 390, 844)]:
            page = browser.new_page(viewport={'width': width, 'height': height}, reduced_motion='reduce')
            errors, failed = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('requestfailed', lambda request: failed.append(request.url))
            page.goto(args.url)
            page.wait_for_load_state('networkidle')
            assert page.locator('#mode').inner_text() == 'DEMO · 합성 데이터'
            assert page.locator('.task').count() == 3
            page.screenshot(path=str(args.output / f'{name}-list.png'), full_page=True)
            page.get_by_role('button', name='보류', exact=True).click()
            assert page.locator('.task').count() == 1
            page.locator('.task').click()
            page.locator('#detail').wait_for(state='visible')
            assert page.locator('#work-title').inner_text() == 'DEMO-1025'
            assert page.locator('#work-title').evaluate('(node) => node === document.activeElement')
            page.get_by_role('button', name='전체', exact=True).click()
            page.locator('#search').fill('1024')
            assert page.locator('.task').count() == 1
            page.locator('.task').click()
            page.locator('#detail').wait_for(state='visible')
            page.get_by_role('button', name='ROSY 상태 다시 조회').click()
            page.locator('#detail').wait_for(state='visible')
            assert page.locator('#fleet-status').inner_text() == '합성 상태 · 연결 안 함'
            assert page.locator('#receipt-status').inner_text() == '미확인'
            assert page.locator('#console').is_hidden()
            overflow = page.evaluate('document.documentElement.scrollWidth > innerWidth')
            assert not overflow, f'Horizontal overflow at {width}px'
            assert not errors, errors
            assert not failed, failed
            page.screenshot(path=str(args.output / f'{name}.png'), full_page=True)
            results.append({'viewport': [width, height], 'page_errors': len(errors), 'failed_requests': len(failed),
                            'horizontal_overflow': overflow, 'search_filter_detail_refresh': 'passed'})
            page.close()
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.route('**/api/work*', lambda route: route.fulfill(status=503, content_type='application/json',
            body=json.dumps({'message': 'synthetic unavailable'})))
        page.goto(args.url)
        page.locator('#list-error').wait_for(state='visible')
        assert page.locator('#detail').is_hidden()
        assert page.locator('#tasks .task').count() == 0
        assert page.locator('#list-count').inner_text() == '업무 수 미확인'
        assert '장부에 등록된 업무가 없습니다.' not in page.locator('#tasks').inner_text()
        page.locator('#search').fill('test')
        page.get_by_role('button', name='보류', exact=True).click()
        assert page.locator('#list-count').inner_text() == '업무 수 미확인'
        page.get_by_role('button', name='목록 새로고침').click()
        page.locator('#list-error').wait_for(state='visible')
        assert page.locator('#list-count').inner_text() == '업무 수 미확인'
        page.screenshot(path=str(args.output / 'unavailable.png'), full_page=True)
        browser.close()
    (args.output / 'browser-summary.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps(results))


if __name__ == '__main__':
    main()
