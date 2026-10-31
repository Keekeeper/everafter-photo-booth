from playwright.sync_api import sync_playwright
import requests, pathlib
base='http://127.0.0.1:8765';folder=pathlib.Path(__file__).resolve().parents[1]

def route_handler(route):
    from urllib.parse import urlparse
    req=route.request;u=urlparse(req.url);p=u.path
    if u.hostname!='everafter.test':
        route.abort();return
    try:
        if p.startswith('/api/'):
            resp=requests.request(req.method,base+p+('?' + u.query if u.query else ''),data=req.post_data,headers={k:v for k,v in req.headers.items() if k.lower() not in ['host','content-length']},timeout=15)
            route.fulfill(status=resp.status_code,body=resp.content,headers={'content-type':resp.headers.get('content-type','application/json'),'access-control-allow-origin':'*'});return
        if p=='/':f=folder/'static/index.html'
        else:f=folder/'static'/p.lstrip('/').removeprefix('static/')
        if not f.is_file():route.fulfill(status=404,body='not found');return
        ct='text/css' if f.suffix=='.css' else 'text/javascript' if f.suffix=='.js' else 'text/html'
        route.fulfill(status=200,body=f.read_bytes(),headers={'content-type':ct});return
    except Exception as e:route.fulfill(status=500,body=str(e))

with sync_playwright() as p:
    b=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    for mobile in (False,True):
        page=b.new_page(viewport={'width':390,'height':844} if mobile else {'width':1440,'height':900},device_scale_factor=1,is_mobile=mobile,has_touch=mobile)
        errors=[];page.route('**/*',route_handler)
        page.on('pageerror',lambda x:errors.append(str(x)))
        page.set_content((folder/'static/index.html').read_text(),wait_until='domcontentloaded')
        page.add_style_tag(content=(folder/'static/styles.css').read_text().split('\n',1)[1])
        page.add_script_tag(content=(folder/'static/app.js').read_text())
        page.wait_for_timeout(600)
        print('PAGE', 'mobile' if mobile else 'desktop',page.title(), 'headline',page.locator('.hero h1').inner_text().strip().replace('\n',' '))
        page.screenshot(path=str(folder/('preview-mobile.png' if mobile else 'preview-desktop.png')),full_page=True)
        page.locator('#route-home .hero-actions button[data-route="booth"]').click()
        page.wait_for_timeout(400)
        print('BOOTH',page.locator('#route-booth').is_visible(),'download disabled:',page.locator('#downloadPhoto').is_disabled())
        page.screenshot(path=str(folder/('booth-mobile.png' if mobile else 'booth-desktop.png')),full_page=True)
        print('ERRORS',errors[:3]);assert not errors,errors
        page.close()
    b.close()
