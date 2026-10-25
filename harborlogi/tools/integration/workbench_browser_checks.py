"""Real viewer/editor gestures on the isolated multicell fixture; no mocked renderer."""


def exercise_workbench(page, output):
    canvas = page.locator('canvas')
    figure = page.locator('[data-yard-3d]')
    cargo = page.locator('[data-scene-cargo]')
    source = page.locator('#yard-scene-data').text_content()
    requests = []
    page.on('request', lambda request: requests.append(request.method) if request.method != 'GET' else None)

    def rendered_after(revision):
        page.wait_for_function('(r) => Number(document.querySelector("canvas").dataset.renderRevision || 0) > r', arg=revision)

    def revision():
        return int(canvas.get_attribute('data-render-revision') or 0)

    assert figure.get_attribute('data-renderer') == 'webgl'
    page.locator('[data-yard-stage="0"]').click()
    cargo.select_option('')
    old = revision()
    page.locator('[data-scene-action="pan"]').click()
    box = canvas.bounding_box()
    page.mouse.move(box['x'] + box['width'] * .5, box['y'] + box['height'] * .5)
    page.mouse.down()
    page.mouse.move(box['x'] + box['width'] * .62, box['y'] + box['height'] * .56, steps=8)
    page.mouse.up()
    rendered_after(old)
    assert cargo.input_value() == '', 'Camera drag must not select a box'
    page.locator('[data-scene-action="orbit"]').click()
    old = revision()
    page.locator('[data-scene-zoom="0.15"]').click()
    rendered_after(old)
    for name in ('front', 'top', 'iso'):
        old = revision()
        page.locator('[data-scene-camera="' + name + '"]').click()
        rendered_after(old)
    cargo.select_option('C1')
    page.locator('[data-scene-action="focus"]').click()
    page.locator('[data-scene-action="isolate"]').click()
    assert page.locator('[data-scene-action="isolate"]').get_attribute('aria-pressed') == 'true'
    page.screenshot(path=str(output / 'selected-model.png'), full_page=True)
    page.locator('[data-scene-action="isolate"]').click()

    edit = page.locator('[data-scene-action="edit"]')
    edit.click()
    assert figure.get_attribute('data-editing') == 'true'
    assert page.locator('[data-yard-stage="1"]').is_disabled()
    assert page.locator('[data-scene-play]').is_disabled()
    assert page.locator('[data-yard-view="flat"]').is_disabled()

    def move(code, area, row, column, level, orientation=0):
        cargo.select_option(code)
        page.locator('[data-scene-editor] [name=area]').select_option(area)
        for name, value in dict(row=row, column=column, level=level).items():
            page.locator('[data-scene-editor] [name=' + name + ']').fill(str(value))
        page.locator('[data-scene-editor] [name=orientation]').select_option(str(orientation))
        page.locator('[data-scene-editor] button[type=submit]').click()

    move('C1', 'stand', 1, 1, 1)
    assert page.locator('[data-scene-error]').inner_text()
    assert figure.get_attribute('data-draft-changes') == '0', 'Cannot remove a supporting cargo'
    move('C2', 'temp', 1, 1, 1)
    assert not page.locator('[data-scene-error]').inner_text()
    move('C1', 'stand', 1, 1, 1)
    assert figure.get_attribute('data-draft-changes') == '2'
    assert '가상:' in page.locator('[data-scene-info]').inner_text()
    move('C1', 'stand', 1, 1, 1, 90)
    assert page.locator('[data-scene-error]').inner_text()
    assert figure.get_attribute('data-draft-changes') == '2'
    move('C7', 'yard', 2, 3, 1)
    assert '겹' in page.locator('[data-scene-error]').inner_text()
    move('C7', 'yard', 2, 2, 2)
    assert '지지' in page.locator('[data-scene-error]').inner_text()
    page.screenshot(path=str(output / 'draft-validation.png'), full_page=True)
    edit.click()
    assert figure.get_attribute('data-editing') == 'false'
    assert figure.get_attribute('data-draft-changes') == '0'
    assert page.locator('[data-yard-stage="1"]').is_enabled()
    assert page.locator('#yard-scene-data').text_content() == source

    edit.click()
    move('C1', 'yard', 1, 1, 1, 90)
    assert figure.get_attribute('data-draft-changes') == '1'
    assert not page.locator('[data-scene-error]').inner_text()
    assert page.locator('[data-scene-editor] [name=orientation]').input_value() == '90'
    edit.click()

    # Select a free floor cargo and drag the actual red X handle in the rendered viewport.
    cargo.select_option('C4')
    page.locator('[data-scene-action="focus"]').click()
    page.locator('[data-scene-action="isolate"]').click()
    page.locator('[data-scene-camera="front"]').click()
    edit.click()
    old = revision()
    page.locator('[data-scene-action="focus"]').click()
    rendered_after(old)
    canvas.scroll_into_view_if_needed()
    # Capture before reading pixels: scrolling a screenshot can move the gesture's target.
    canvas.screenshot(path=str(output / 'gizmo-before-canvas.png'))
    pixels = canvas.evaluate('''(c) => {
      const p=document.createElement('canvas');p.width=c.width;p.height=c.height;
      const g=p.getContext('2d');g.drawImage(c,0,0);const d=g.getImageData(0,0,p.width,p.height).data;
      const red=[];let lo=Infinity,hi=-Infinity;
      for(let y=0;y<p.height;y++)for(let x=0;x<p.width;x++){
        const i=(y*p.width+x)*4,r=d[i],a=d[i+1],b=d[i+2];
        if(r>190&&a<40&&b<40)red.push([x,y]);
        if(a>70&&b>60&&a>r*1.2&&b>r*1.15){lo=Math.min(lo,x);hi=Math.max(hi,x);}
      }
      red.sort((a,b)=>a[0]-b[0]);const point=red[Math.floor(red.length*.85)];
      const ratio=c.clientWidth/p.width;
      return point&&Number.isFinite(lo)?{x:point[0]*ratio,y:point[1]*ratio,distance:(hi-lo)*ratio}:null;
    }''')
    assert pixels, 'Visible translation handle and model were not found'
    box = canvas.bounding_box()
    page.mouse.move(box['x'] + pixels['x'], box['y'] + pixels['y'])
    page.mouse.down()
    page.mouse.move(box['x'] + pixels['x'] - pixels['distance'], box['y'] + pixels['y'], steps=18)
    page.mouse.up()
    figure.screenshot(path=str(output / 'gizmo-after.png'))
    assert figure.get_attribute('data-draft-changes') == '1', (pixels, page.locator('[data-scene-editor] [name=column]').input_value(), page.locator('[data-scene-error]').inner_text())
    assert page.locator('[data-scene-editor] [name=column]').input_value() in ('1', '2')
    page.screenshot(path=str(output / 'gizmo-moved.png'), full_page=True)
    edit.click()
    page.locator('[data-scene-action="isolate"]').click()
    cargo.select_option('')
    canvas.scroll_into_view_if_needed()
    touch = page.context.new_cdp_session(page)
    touch.send('Emulation.setTouchEmulationEnabled', dict(enabled=True, maxTouchPoints=2))
    box = canvas.bounding_box()
    x, y = box['x'] + box['width'] * .5, box['y'] + box['height'] * .5
    touch.send('Input.dispatchTouchEvent', dict(type='touchStart', touchPoints=[dict(x=x-25, y=y, id=1), dict(x=x+25, y=y, id=2)]))
    touch.send('Input.dispatchTouchEvent', dict(type='touchMove', touchPoints=[dict(x=x-65, y=y-15, id=1), dict(x=x+25, y=y, id=2)]))
    touch.send('Input.dispatchTouchEvent', dict(type='touchEnd', touchPoints=[]))
    touch.send('Emulation.setTouchEmulationEnabled', dict(enabled=False))
    touch.detach()
    assert cargo.input_value() == '', 'Two-finger camera gesture must not select a box on the stationary finger'
    page.locator('[data-scene-action="fit"]').click()
    if page.locator('[data-scene-action="fullscreen"]').is_visible():
        page.evaluate('''() => {
          window.__fullscreenFrame = null;
          const c = document.querySelector('canvas');
          const observer = new ResizeObserver(() => {
            if (!document.fullscreenElement) return;
            const p=document.createElement('canvas');p.width=c.width;p.height=c.height;
            const g=p.getContext('2d');g.drawImage(c,0,0);const d=g.getImageData(0,0,p.width,p.height).data;
            let colored=0;for(let i=0;i<d.length;i+=4)if(Math.max(d[i],d[i+1],d[i+2])-Math.min(d[i],d[i+1],d[i+2])>50)colored++;
            window.__fullscreenFrame = {colored, width:c.width, height:c.height};observer.disconnect();
          });observer.observe(c);
        }''')
        page.locator('[data-scene-action="fullscreen"]').click()
        page.wait_for_function('() => Boolean(document.fullscreenElement)')
        page.wait_for_function('() => window.__fullscreenFrame !== null')
        assert page.evaluate('() => window.__fullscreenFrame.colored') > 1000, 'Fullscreen resize exposed an empty WebGL frame'
        page.screenshot(path=str(output / 'fullscreen-rendered.png'))
        page.locator('[data-scene-action="fullscreen"]').click()
        page.wait_for_function('() => !document.fullscreenElement')
    assert page.locator('#yard-scene-data').text_content() == source
    assert not requests, requests
    return ['WebGL camera pan/presets/zoom, selection focus/isolation and fullscreen work; two-finger motion does not select cargo',
            'Local drafts enforce footprint boundaries, overlaps, full support and restoration; original JSON and server stay unchanged',
            'Actual rendered translation handle moves and snaps one cargo; no injected transform or mocked renderer']
