"""Integration tests using the API and a real temporary event gallery."""
import io,os,requests,base64,hashlib,hmac,json,time
from PIL import Image,ImageDraw
base='http://127.0.0.1:8765'

def img_data(bg='green',portrait=False):
    im=Image.new('RGB',(600,900),(94,142,93) if bg=='green' else (214,203,190));d=ImageDraw.Draw(im)
    if portrait:
        d.ellipse((195,100,402,310),fill='#a98469');d.rectangle((150,290,455,810),fill='#b05047')
    else:
        d.ellipse((100,160,240,330),fill='#ae9878');d.ellipse((360,160,500,330),fill='#bb9c84')
        d.rectangle((120,310,220,805),fill='#e4c1b0');d.rectangle((380,310,480,805),fill='#333f4d')
    b=io.BytesIO();im.save(b,format='JPEG');return 'data:image/jpeg;base64,'+base64.b64encode(b.getvalue()).decode()

def req(method,path,**kwargs):
    r=requests.request(method,base+path,timeout=15,**kwargs)
    return r

def test_complete_flow():
    email='operator'+str(int(time.time()))+'@example.org'
    r=req('POST','/api/auth/register',json={'company':'Studio Test','email':email,'password':'secure-passphrase-123'});assert r.status_code==200,r.text
    token=r.json()['token'];oid=r.json()['operator']['id'];h={'Authorization':'Bearer '+token}
    r=req('POST','/api/events',json={'title':'Our Day','names':'Test & Couple','event_date':'2026-12-10','venue':'Sterling'},headers=h);assert r.status_code==200,r.text
    event=r.json();eid=event['id'];code=event['code']
    r=req('POST',f'/api/events/{eid}/poses/formal',json={'image':img_data()},headers=h);assert r.status_code==200,r.text
    r=req('GET','/api/booth/'+code);assert r.status_code==200 and r.json()['poses']==['formal'],r.text
    r=req('GET',f'/api/booth/{code}/poses/formal');assert r.status_code==200 and r.headers['content-type'].startswith('image/jpeg')
    r=req('POST',f'/api/booth/{code}/photos',json={'image':img_data(portrait=True),'consent':False});assert r.status_code==400
    r=req('POST',f'/api/booth/{code}/photos',json={'image':img_data(portrait=True),'consent':True});assert r.status_code==200,r.text
    p=r.json();assert p['url'].startswith('http')
    r=req('GET',f'/api/gallery/{p["id"]}/image?t={p["token"]}');assert r.status_code==200
    r=req('GET',f'/api/gallery/{p["id"]}/qr?t={p["token"]}');assert r.status_code==200 and r.content[:8]==b'\x89PNG\r\n\x1a\n'
    r=req('GET',f'/api/gallery/{p["id"]}/qr?t=wrong');assert r.status_code==404
    r=req('GET',f'/api/events/{eid}/photos',headers=h);assert r.status_code==200 and len(r.json())==1
    r=req('POST',f'/api/gallery/{p["id"]}/email',json={'token':p['token'],'email':'guest@example.org'});assert r.status_code==503
    r=req('POST',f'/api/print/{code}',json={'photo_id':p['id'],'token':p['token']});assert r.status_code==503
    r=req('POST','/api/bookings',json={'operator_id':oid,'contact_name':'Happy Guest','email':'guest@example.org','date':'2026-12-10','venue':'Sterling','notes':'Test request'});assert r.status_code==200
    bid=r.json()['id'];r=req('GET','/api/bookings',headers=h);assert r.status_code==200 and any(x['id']==bid for x in r.json())
    r=req('POST',f'/api/bookings/{bid}/checkout',json={'amount_cents':15000},headers=h);assert r.status_code==503
    r=req('DELETE',f'/api/events/{eid}/photos/{p["id"]}',headers=h);assert r.status_code==200
    r=req('GET',f'/api/gallery/{p["id"]}/image?t={p["token"]}');assert r.status_code==404
    r=req('DELETE',f'/api/events/{eid}',headers=h);assert r.status_code==200
    print('PASS: sign up, private event, pose upload, consent, photo save, QR, photo access control, print/email unavailable states, booking, deletion')

if __name__=='__main__':test_complete_flow()
