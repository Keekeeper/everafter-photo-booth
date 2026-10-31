from playwright.sync_api import sync_playwright
from pathlib import Path
p=Path(__file__).resolve().parents[1]
with sync_playwright() as pw:
    b=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=b.new_page(accept_downloads=True,viewport={'width':1320,'height':900});errors=[]
    page.on('pageerror',lambda ex:errors.append(str(ex)))
    page.set_content((p/'static/index.html').read_text());page.add_style_tag(content=(p/'static/styles.css').read_text().split('\n',1)[1]);page.add_script_tag(content=(p/'static/app.js').read_text());
    page.locator('#route-home .hero-actions button[data-route=booth]').click()
    page.locator('#localCouple').set_input_files('/mnt/data/test_couple.jpg')
    page.wait_for_timeout(350)
    page.locator('#guestFile').set_input_files('/mnt/data/test_guest.jpg')
    page.wait_for_timeout(350)
    assert page.locator('#downloadPhoto').is_disabled(),'Raw rectangular selfie may not be downloadable.'
    page.locator('#cutoutSimple').click();page.wait_for_timeout(600)
    print('cutout status',page.locator('#cutoutStatus').inner_text())
    page.locator('#guestConsent').check();page.wait_for_timeout(400)
    assert page.locator('#downloadPhoto').is_enabled(),'Expected completed cutout + consent to enable export.'
    with page.expect_download() as d:
        page.locator('#downloadPhoto').click()
    file=d.value
    print('download suggested name',file.suggested_filename)
    file.save_as(str(p/'test-composite.jpg'))
    page.screenshot(path=str(p/'tested-guest-composite.png'),full_page=True)
    assert not errors,errors
    from PIL import Image
    im=Image.open(p/'test-composite.jpg');assert im.size==(1200,1800), im.size
    print('PASS: no rectangular image export, plain background cutout, guest consent, 1200x1800 download, browser errors', errors)
    b.close()
