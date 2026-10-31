"""Actual browser checks for the shared icon navigation and accessible tooltips."""


def exercise_navigation(page):
    page.set_viewport_size(dict(width=1440, height=1000))
    density = page.locator('[data-nav-density]')
    rail = page.locator('#navigation')
    assert rail.bounding_box()['width'] == 76
    link = rail.get_by_role('link', name='반출 관제', exact=True)
    assert link.locator('svg[aria-hidden=true]').count() == 1
    link.hover()
    tip = page.get_by_role('tooltip')
    tip.wait_for(state='visible')
    assert '우선순위' in tip.inner_text()
    tip.hover()
    page.wait_for_timeout(180)
    assert tip.is_visible()
    page.keyboard.press('Escape')
    assert not tip.is_visible()
    link.focus()
    tip.wait_for(state='visible')
    assert link.get_attribute('aria-describedby') == 'nav-tooltip'
    page.keyboard.press('Escape')
    assert not tip.is_visible()
    density.click()
    assert rail.bounding_box()['width'] > 76
    assert density.get_attribute('aria-expanded') == 'true'
    page.reload()
    assert page.locator('[data-nav-density]').get_attribute('aria-expanded') == 'true'
    assert rail.bounding_box()['width'] > 76
    page.locator('[data-nav-density]').click()
    page.reload()
    assert rail.bounding_box()['width'] == 76
    for width in (768, 390):
        page.set_viewport_size(dict(width=width, height=1000))
        page.locator('#nav-toggle').click()
        assert page.locator('#nav-toggle').inner_text() == '메뉴 닫기'
        assert rail.is_visible()
        assert link.locator('.nav-label').bounding_box()['width'] > 1
        assert not tip.is_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.keyboard.press('Escape')
        assert page.locator('#nav-toggle').inner_text() == '메뉴 열기'
        assert not rail.is_visible()
    page.set_viewport_size(dict(width=1440, height=1000))
