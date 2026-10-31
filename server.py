"""EverAfter Studio v2 • pilot backend. Data controlled by participating operator.
Run: uvicorn server:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations
import os, re, io, json, sqlite3, time, hmac, hashlib, base64, secrets, uuid, urllib.request, urllib.error
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.responses import FileResponse, Response, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, ImageOps
import qrcode
from reportlab.pdfgen.canvas import Canvas

BASE=Path(__file__).resolve().parent
DATA=Path(os.getenv('DATA_DIR',str(BASE/'data'))).expanduser().resolve()
DATA.mkdir(parents=True, exist_ok=True)
BLOB=DATA/'blobs'; BLOB.mkdir(parents=True, exist_ok=True)
DB=DATA/'everafter.sqlite3'
BASE_URL=os.getenv('APP_BASE_URL','http://127.0.0.1:8000').rstrip('/')
ENV=os.getenv('APP_ENV','development')
APP_SECRET=os.getenv('APP_SECRET','')
if ENV == 'production' and (len(APP_SECRET)<32 or APP_SECRET=='replace-with-random-64-character-hex'):
    raise RuntimeError('Set a strong APP_SECRET before production deployment')
if not APP_SECRET: APP_SECRET='local-dev-only-secret-do-not-deploy'
S3_ENABLED=all(os.getenv(x) for x in ('S3_BUCKET','S3_ACCESS_KEY_ID','S3_SECRET_ACCESS_KEY'))

app=FastAPI(title='EverAfter Studio API',version='2.0.0')

# Single-process pilot throttle; use a shared backend before multi-worker public scale.
_THROTTLE={}
def throttle(req,group,limit,window=3600):
    key=(group,(req.client.host if req.client else 'unknown'))
    timestamps=[t for t in _THROTTLE.get(key,[]) if t>time.time()-window]
    if len(timestamps)>=limit:raise HTTPException(429,'Too many requests; please try later')
    timestamps.append(time.time());_THROTTLE[key]=timestamps

@app.middleware('http')
async def security_headers(req,call_next):
    if req.method in ('POST','PUT','PATCH') and int(req.headers.get('content-length','0') or 0)>17_000_000:
        return JSONResponse({'detail':'Request too large'},status_code=413)
    response=await call_next(req)
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['X-Content-Type-Options']='nosniff'
    allowed=os.getenv('PARTNER_FRAME_ANCESTORS','').strip()
    if allowed:
        response.headers['Content-Security-Policy']="frame-ancestors 'self' "+allowed
    else:response.headers['X-Frame-Options']='SAMEORIGIN'
    return response

@contextmanager
def connect():
    db=sqlite3.connect(DB,timeout=12)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL;')
    db.execute('PRAGMA busy_timeout=12000;')
    try:
        yield db
        db.commit()
    finally: db.close()

with connect() as db:
    db.executescript('''
    CREATE TABLE IF NOT EXISTS operators(id TEXT PRIMARY KEY,name TEXT,email TEXT UNIQUE,password_hash TEXT,created INTEGER,printer_id INTEGER);
    CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,operator_id TEXT,code TEXT UNIQUE,title TEXT,names TEXT,event_date TEXT,venue TEXT,retention_days INTEGER,created INTEGER);
    CREATE TABLE IF NOT EXISTS poses(event_id TEXT,pose TEXT,blob_key TEXT,PRIMARY KEY(event_id,pose));
    CREATE TABLE IF NOT EXISTS photos(id TEXT PRIMARY KEY,event_id TEXT,token_hash TEXT,blob_key TEXT,created INTEGER,consent INTEGER);
    CREATE TABLE IF NOT EXISTS bookings(id TEXT PRIMARY KEY,operator_id TEXT,contact_name TEXT,email TEXT,date TEXT,venue TEXT,notes TEXT,status TEXT,deposit_cents INTEGER,checkout_id TEXT,created INTEGER);
    CREATE TABLE IF NOT EXISTS stripe_events(event_id TEXT PRIMARY KEY,created INTEGER);
    CREATE TABLE IF NOT EXISTS partner_keys(id TEXT PRIMARY KEY,operator_id TEXT,label TEXT,key_hash TEXT UNIQUE,created INTEGER,revoked INTEGER DEFAULT 0);
    CREATE INDEX IF NOT EXISTS idx_photos_event ON photos(event_id);
    CREATE INDEX IF NOT EXISTS idx_bookings_op ON bookings(operator_id);
    ''')

def now():return int(time.time())
def rid():return uuid.uuid4().hex

def pw_hash(p,salt=None):
    salt=salt or secrets.token_bytes(16)
    return base64.urlsafe_b64encode(salt).decode()+':'+hashlib.pbkdf2_hmac('sha256',p.encode(),salt,350000).hex()
def pw_check(p,s):
    try:
        sl,h=s.split(':',1); salt=base64.urlsafe_b64decode(sl.encode())
        return hmac.compare_digest(pw_hash(p,salt),s)
    except Exception:return False

def sign_token(payload):
    raw=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).rstrip(b'=')
    sig=hmac.new(APP_SECRET.encode(),raw,hashlib.sha256).digest()
    return raw.decode()+'.'+base64.urlsafe_b64encode(sig).rstrip(b'=').decode()

def verify_token(t):
    try:
        raw,s=t.split('.',1)
        expected=hmac.new(APP_SECRET.encode(),raw.encode(),hashlib.sha256).digest()
        actual=base64.urlsafe_b64decode(s+'='*(-len(s)%4))
        if not hmac.compare_digest(expected,actual):raise ValueError('signature')
        p=json.loads(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)))
        if p['exp']<now():raise ValueError('expired')
        return p
    except Exception:raise HTTPException(401,'Invalid or expired session')

def operator(authorization:Optional[str]=Header(default=None)):
    if not authorization or not authorization.lower().startswith('bearer '):raise HTTPException(401,'Sign in required')
    p=verify_token(authorization[7:]); oid=p.get('sub')
    with connect() as db:
        row=db.execute('SELECT id,name,email,printer_id FROM operators WHERE id=?',(oid,)).fetchone()
    if not row:raise HTTPException(401,'Operator not found')
    return dict(row)

def event_owned(event_id,op):
    with connect() as db:
        row=db.execute('SELECT * FROM events WHERE id=? AND operator_id=?',(event_id,op['id'])).fetchone()
    if not row:raise HTTPException(404,'Event not found')
    return dict(row)

def event_by_code(code):
    with connect() as db:row=db.execute('SELECT * FROM events WHERE code=?',(code,)).fetchone()
    if not row:raise HTTPException(404,'Booth not found')
    return dict(row)

def s3_client():
    import boto3
    cfg=dict(aws_access_key_id=os.getenv('S3_ACCESS_KEY_ID'),aws_secret_access_key=os.getenv('S3_SECRET_ACCESS_KEY'),region_name=os.getenv('S3_REGION') or 'us-east-1')
    if os.getenv('S3_ENDPOINT_URL'):cfg['endpoint_url']=os.getenv('S3_ENDPOINT_URL')
    return boto3.client('s3',**cfg)

def blob_put(key,data):
    if S3_ENABLED:s3_client().put_object(Bucket=os.getenv('S3_BUCKET'),Key=key,Body=data,ContentType='image/jpeg')
    else:
        dest=BLOB/key;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)

def blob_get(key):
    if S3_ENABLED:return s3_client().get_object(Bucket=os.getenv('S3_BUCKET'),Key=key)['Body'].read()
    try:return (BLOB/key).read_bytes()
    except FileNotFoundError:raise HTTPException(404,'Image no longer available')

def blob_delete(key):
    if S3_ENABLED:s3_client().delete_object(Bucket=os.getenv('S3_BUCKET'),Key=key)
    else:(BLOB/key).unlink(missing_ok=True)

def clean_image(data):
    if len(data)>12_000_000:raise HTTPException(413,'Image exceeds 12 MB')
    try:
        im=Image.open(io.BytesIO(data));im=ImageOps.exif_transpose(im)
        if im.width*im.height>20_000_000:raise ValueError('Image dimensions too large')
        im=im.convert('RGB');out=io.BytesIO();im.save(out,'JPEG',quality=92,optimize=True)
        return out.getvalue()
    except Exception:raise HTTPException(400,'Invalid photo')

def from_data_url(s):
    if not isinstance(s,str) or not s.startswith('data:image/'):raise HTTPException(400,'Image data URL required')
    try:return base64.b64decode(s.split(',',1)[1],validate=True)
    except Exception:raise HTTPException(400,'Invalid base64 image')

def auth_public_photo(photo_id,token):
    with connect() as db:p=db.execute('SELECT p.*,e.retention_days FROM photos p JOIN events e ON e.id=p.event_id WHERE p.id=?',(photo_id,)).fetchone()
    if not p or not token or not hmac.compare_digest(p['token_hash'],hashlib.sha256(token.encode()).hexdigest()):raise HTTPException(404,'Photo not found')
    if p['created']+p['retention_days']*86400 < now():raise HTTPException(410,'Photo retention period has ended')
    return dict(p)

class Register(BaseModel):
    company:str=Field(min_length=2,max_length=120)
    email:str=Field(min_length=5,max_length=160)
    password:str=Field(min_length=10,max_length=200)
class Login(BaseModel):email:str; password:str
class EventInput(BaseModel):
    title:str=Field(min_length=2,max_length=150)
    names:str=Field(min_length=2,max_length=140)
    event_date:str=Field(default='',max_length=50)
    venue:str=Field(default='',max_length=160)
    retention_days:int=Field(default=30,ge=1,le=365)
class PoseInput(BaseModel):image:str
class PhotoInput(BaseModel):image:str;consent:bool=False
class EmailInput(BaseModel):token:str;email:str
class BookingInput(BaseModel):
    operator_id:str
    contact_name:str=Field(min_length=2,max_length=120)
    email:str=Field(min_length=5,max_length=160)
    date:str=Field(min_length=4,max_length=50)
    venue:str=Field(default='',max_length=160)
    notes:str=Field(default='',max_length=1500)
class DepositInput(BaseModel):amount_cents:int=Field(ge=100,le=1000000)
class PrinterInput(BaseModel):printer_id:int=Field(gt=0)
class PrintInput(BaseModel):photo_id:str;token:str

@app.get('/api/health')
def health():
    return {'ok':True,'version':'2.0.0','storage':'s3' if S3_ENABLED else 'local','stripe':bool(os.getenv('STRIPE_SECRET_KEY')),'email':bool(os.getenv('RESEND_API_KEY')),'printer':bool(os.getenv('PRINTNODE_API_KEY')),'mode':ENV}

@app.post('/api/auth/register')
def register(body:Register):
    if os.getenv('ENABLE_SIGNUP','true').lower()!='true':raise HTTPException(403,'Registration closed')
    email=body.email.strip().lower()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email):raise HTTPException(400,'Invalid email')
    op_id=rid()
    try:
        with connect() as db:db.execute('INSERT INTO operators VALUES(?,?,?,?,?,NULL)',(op_id,body.company.strip(),email,pw_hash(body.password),now()))
    except sqlite3.IntegrityError:raise HTTPException(409,'Email already registered')
    return {'token':sign_token({'sub':op_id,'exp':now()+43200}),'operator':{'id':op_id,'name':body.company,'email':email}}

@app.post('/api/auth/login')
def login(body:Login):
    with connect() as db:r=db.execute('SELECT * FROM operators WHERE email=?',(body.email.strip().lower(),)).fetchone()
    if not r or not pw_check(body.password,r['password_hash']):raise HTTPException(401,'Invalid email or password')
    return {'token':sign_token({'sub':r['id'],'exp':now()+43200}),'operator':{'id':r['id'],'name':r['name'],'email':r['email']}}

@app.get('/api/operator/me')
def me(op=Depends(operator)):return op

@app.post('/api/events')
def create_event(b:EventInput,op=Depends(operator)):
    eid=rid();code=secrets.token_urlsafe(9)
    with connect() as db:db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)',(eid,op['id'],code,b.title,b.names,b.event_date,b.venue,b.retention_days,now()))
    return {'id':eid,'code':code,'booth_url':f'{BASE_URL}/?booth={code}','title':b.title,'names':b.names,'event_date':b.event_date}

@app.get('/api/events')
def list_events(op=Depends(operator)):
    with connect() as db:
        rows=db.execute('''SELECT e.*, (SELECT COUNT(*) FROM photos p WHERE p.event_id=e.id) AS photo_count FROM events e WHERE operator_id=? ORDER BY created DESC''',(op['id'],)).fetchall()
    return [dict(r) for r in rows]

@app.delete('/api/events/{eid}')
def delete_event(eid:str,op=Depends(operator)):
    event_owned(eid,op)
    with connect() as db:
        keys=[r['blob_key'] for r in db.execute('SELECT blob_key FROM photos WHERE event_id=?',(eid,))]
        keys += [r['blob_key'] for r in db.execute('SELECT blob_key FROM poses WHERE event_id=?',(eid,))]
        db.execute('DELETE FROM photos WHERE event_id=?',(eid,));db.execute('DELETE FROM poses WHERE event_id=?',(eid,));db.execute('DELETE FROM events WHERE id=?',(eid,))
    for k in keys:blob_delete(k)
    return {'deleted':True}

@app.post('/api/events/{eid}/poses/{pose}')
def put_pose(eid:str,pose:str,body:PoseInput,op=Depends(operator)):
    event_owned(eid,op)
    if pose not in ('formal','fun','party'):raise HTTPException(400,'Unknown pose')
    data=clean_image(from_data_url(body.image));key=f'poses/{eid}/{pose}-{rid()}.jpg';blob_put(key,data)
    with connect() as db:
        old=db.execute('SELECT blob_key FROM poses WHERE event_id=? AND pose=?',(eid,pose)).fetchone()
        db.execute('INSERT OR REPLACE INTO poses VALUES(?,?,?)',(eid,pose,key))
    if old:blob_delete(old['blob_key'])
    return {'ok':True,'pose':pose}

@app.get('/api/booth/{code}')
def booth(code:str):
    e=event_by_code(code)
    with connect() as db:poses=[r['pose'] for r in db.execute('SELECT pose FROM poses WHERE event_id=?',(e['id'],))]
    return {'code':code,'title':e['title'],'names':e['names'],'date':e['event_date'],'venue':e['venue'],'poses':poses,'photo_urls':{p:f'/api/booth/{code}/poses/{p}' for p in poses}}

@app.get('/api/booth/{code}/poses/{pose}')
def pose_image(code:str,pose:str):
    e=event_by_code(code)
    with connect() as db:r=db.execute('SELECT blob_key FROM poses WHERE event_id=? AND pose=?',(e['id'],pose)).fetchone()
    if not r:raise HTTPException(404,'Pose missing')
    return Response(blob_get(r['blob_key']),media_type='image/jpeg',headers={'Cache-Control':'public, max-age=120'})

@app.post('/api/booth/{code}/photos')
def save_photo(code:str,body:PhotoInput,request:Request):
    throttle(request,'guest-photo',60,3600)
    e=event_by_code(code)
    if not body.consent:raise HTTPException(400,'Guest consent is required')
    image=clean_image(from_data_url(body.image));im=Image.open(io.BytesIO(image))
    if im.width/im.height<0.62 or im.width/im.height>0.72:raise HTTPException(400,'Photo must be portrait 4x6')
    pid=rid();token=secrets.token_urlsafe(24);key=f'gallery/{e["id"]}/{pid}.jpg'
    blob_put(key,image)
    with connect() as db:db.execute('INSERT INTO photos VALUES(?,?,?,?,?,?)',(pid,e['id'],hashlib.sha256(token.encode()).hexdigest(),key,now(),1))
    url=f'{BASE_URL}/gallery.html?id={pid}&t={token}'
    return {'id':pid,'url':url,'qr_url':f'{BASE_URL}/api/gallery/{pid}/qr?t={token}','image_url':f'{BASE_URL}/api/gallery/{pid}/image?t={token}','token':token}

@app.get('/api/gallery/{pid}/image')
def image(pid:str,t:str):
    p=auth_public_photo(pid,t)
    return Response(blob_get(p['blob_key']),media_type='image/jpeg',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

@app.get('/api/gallery/{pid}/qr')
def qr(pid:str,t:str):
    auth_public_photo(pid,t)
    img=qrcode.make(f'{BASE_URL}/gallery.html?id={pid}&t={t}');buf=io.BytesIO();img.save(buf,'PNG')
    return Response(buf.getvalue(),media_type='image/png',headers={'Cache-Control':'private, max-age=300'})

@app.get('/api/events/{eid}/photos')
def event_photos(eid:str,op=Depends(operator)):
    event_owned(eid,op)
    with connect() as db:rows=db.execute('SELECT id,created FROM photos WHERE event_id=? ORDER BY created DESC',(eid,)).fetchall()
    return [dict(r) for r in rows]

@app.get('/api/events/{eid}/photos/{pid}/image')
def operator_photo(eid:str,pid:str,op=Depends(operator)):
    event_owned(eid,op)
    with connect() as db:r=db.execute('SELECT blob_key FROM photos WHERE id=? AND event_id=?',(pid,eid)).fetchone()
    if not r:raise HTTPException(404,'Photo missing')
    return Response(blob_get(r['blob_key']),media_type='image/jpeg')

@app.delete('/api/events/{eid}/photos/{pid}')
def delete_photo(eid:str,pid:str,op=Depends(operator)):
    event_owned(eid,op)
    with connect() as db:
        r=db.execute('SELECT blob_key FROM photos WHERE id=? AND event_id=?',(pid,eid)).fetchone()
        if not r:raise HTTPException(404,'Photo missing')
        db.execute('DELETE FROM photos WHERE id=?',(pid,))
    blob_delete(r['blob_key']);return {'deleted':True}

@app.post('/api/gallery/{pid}/email')
def email_delivery(pid:str,body:EmailInput,request:Request):
    throttle(request,'email',12,3600)
    p=auth_public_photo(pid,body.token)
    key=os.getenv('RESEND_API_KEY')
    if not key:raise HTTPException(503,'Email delivery is not configured. Use the QR/download link.')
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',body.email):raise HTTPException(400,'Invalid email')
    gallery=f'{BASE_URL}/gallery.html?id={pid}&t={body.token}'
    payload={'from':os.getenv('RESEND_FROM','EverAfter <photos@example.com>'),'to':[body.email],
             'subject':'Your EverAfter wedding photo ✨','html':'<p>Your wedding memory is ready.</p><p><a href="'+gallery+'">Open and download your photograph</a></p><p>This photo may expire under the event’s retention policy.</p>'}
    req=urllib.request.Request('https://api.resend.com/emails',json.dumps(payload).encode(),{'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=12) as resp:return {'sent':True,'provider':json.loads(resp.read())}
    except urllib.error.HTTPError as err:raise HTTPException(502,'Email service rejected the request')

@app.get('/api/print/printers')
def printers(op=Depends(operator)):
    key=os.getenv('PRINTNODE_API_KEY')
    if not key:raise HTTPException(503,'PrintNode is not configured')
    req=urllib.request.Request('https://api.printnode.com/printers',headers={'Authorization':'Basic '+base64.b64encode((key+':').encode()).decode()})
    try:
        with urllib.request.urlopen(req,timeout=12) as resp:return json.loads(resp.read())
    except Exception:raise HTTPException(502,'Unable to retrieve printers')

@app.post('/api/operator/printer')
def set_printer(b:PrinterInput,op=Depends(operator)):
    with connect() as db:db.execute('UPDATE operators SET printer_id=? WHERE id=?',(b.printer_id,op['id']))
    return {'printer_id':b.printer_id}

@app.post('/api/print/{code}')
def print_photo(code:str,b:PrintInput,request:Request):
    throttle(request,'print',6,3600)
    e=event_by_code(code);p=auth_public_photo(b.photo_id,b.token)
    if p['event_id']!=e['id']:raise HTTPException(403,'Photo belongs to a different event')
    key=os.getenv('PRINTNODE_API_KEY')
    if not key:raise HTTPException(503,'Remote printer not connected; use Print in the browser instead')
    with connect() as db:op=db.execute('SELECT printer_id FROM operators WHERE id=?',(e['operator_id'],)).fetchone()
    if not op or not op['printer_id']:raise HTTPException(503,'Operator has not selected a printer')
    image_bytes=blob_get(p['blob_key'])
    buff=io.BytesIO();c=Canvas(buff,pagesize=(288,432))
    from reportlab.lib.utils import ImageReader
    c.drawImage(ImageReader(io.BytesIO(image_bytes)),0,0,width=288,height=432,preserveAspectRatio=False)
    c.showPage();c.save();doc=buff.getvalue()
    payload={'printerId':op['printer_id'],'title':'EverAfter Wedding Portrait','contentType':'pdf_base64','content':base64.b64encode(doc).decode(),'source':'EverAfter Studio'}
    req=urllib.request.Request('https://api.printnode.com/printjobs',json.dumps(payload).encode(),{'Authorization':'Basic '+base64.b64encode((key+':').encode()).decode(),'Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=20) as resp:return {'queued':True,'job_id':json.loads(resp.read())}
    except Exception:raise HTTPException(502,'Printer rejected the job')

@app.post('/api/bookings')
def booking(b:BookingInput,request:Request):
    throttle(request,'booking',4,3600)
    with connect() as db:
        op=db.execute('SELECT id FROM operators WHERE id=?',(b.operator_id,)).fetchone()
        if not op:raise HTTPException(404,'Vendor not found')
        bid=rid()
        db.execute('INSERT INTO bookings VALUES(?,?,?,?,?,?,?,?,?,?,?)',(bid,b.operator_id,b.contact_name,b.email,b.date,b.venue,b.notes,'inquiry',0,'',now()))
    return {'id':bid,'status':'inquiry','message':'Inquiry received. Booking is not confirmed until the operator accepts it.'}

@app.get('/api/bookings')
def bookings(op=Depends(operator)):
    with connect() as db:return [dict(r) for r in db.execute('SELECT * FROM bookings WHERE operator_id=? ORDER BY created DESC',(op['id'],))]

@app.post('/api/bookings/{bid}/checkout')
def checkout(bid:str,b:DepositInput,op=Depends(operator)):
    key=os.getenv('STRIPE_SECRET_KEY')
    if not key:raise HTTPException(503,'Stripe checkout is not configured')
    with connect() as db:r=db.execute('SELECT * FROM bookings WHERE id=? AND operator_id=?',(bid,op['id'])).fetchone()
    if not r:raise HTTPException(404,'Booking not found')
    from urllib.parse import urlencode
    form=[('mode','payment'),('success_url',BASE_URL+'/?payment=success'),('cancel_url',BASE_URL+'/?payment=cancel'),('client_reference_id',bid),('customer_email',r['email']),('line_items[0][price_data][currency]',os.getenv('STRIPE_CURRENCY','usd')),('line_items[0][price_data][unit_amount]',str(b.amount_cents)),('line_items[0][price_data][product_data][name]','EverAfter photo booth reservation deposit'),('line_items[0][quantity]','1')]
    req=urllib.request.Request('https://api.stripe.com/v1/checkout/sessions',urlencode(form).encode(),{'Authorization':'Bearer '+key,'Content-Type':'application/x-www-form-urlencoded','Idempotency-Key':'everafter-'+bid+'-'+str(b.amount_cents)},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=15) as resp:payment=json.loads(resp.read())
    except Exception:raise HTTPException(502,'Stripe could not create checkout')
    with connect() as db:db.execute('UPDATE bookings SET deposit_cents=?,checkout_id=?,status=? WHERE id=?',(b.amount_cents,payment.get('id',''),'checkout_sent',bid))
    return {'url':payment['url'],'session_id':payment['id']}

@app.post('/api/stripe/webhook')
async def stripe_webhook(req:Request):
    secret=os.getenv('STRIPE_WEBHOOK_SECRET')
    if not secret:raise HTTPException(503,'Webhook not configured')
    signature=req.headers.get('stripe-signature','');pairs=dict(x.split('=',1) for x in signature.split(',') if '=' in x)
    ts=pairs.get('t','')
    if not ts.isdigit() or abs(now()-int(ts))>300:raise HTTPException(400,'Invalid webhook timestamp')
    raw=await req.body();signed=(ts+'.').encode()+raw
    hashes=[s.split('=',1)[1] for s in signature.split(',') if s.startswith('v1=')]
    digest=hmac.new(secret.encode(),signed,hashlib.sha256).hexdigest()
    if not hashes or not any(hmac.compare_digest(h,digest) for h in hashes):raise HTTPException(400,'Invalid signature')
    event=json.loads(raw);event_id=event.get('id','');etype=event.get('type')
    with connect() as db:
        if db.execute('SELECT 1 FROM stripe_events WHERE event_id=?',(event_id,)).fetchone():return {'received':True,'duplicate':True}
        db.execute('INSERT INTO stripe_events VALUES(?,?)',(event_id,now()))
        if etype in ('checkout.session.completed','checkout.session.async_payment_succeeded'):
            obj=event.get('data',{}).get('object',{})
            if obj.get('payment_status')=='paid':
                bid=obj.get('client_reference_id');db.execute('UPDATE bookings SET status=? WHERE id=? AND checkout_id=?',('deposit_paid',bid,obj.get('id')))
    return {'received':True}

@app.get('/api/events/{eid}/export.zip')
def export_gallery(eid:str,op=Depends(operator)):
    import zipfile
    event_owned(eid,op)
    with connect() as db:photos=db.execute('SELECT id,blob_key FROM photos WHERE event_id=?',(eid,)).fetchall()
    bio=io.BytesIO()
    with zipfile.ZipFile(bio,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for p in photos:z.writestr('EverAfter-'+p['id']+'.jpg',blob_get(p['blob_key']))
    return Response(bio.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename=EverAfter-event-gallery.zip','Cache-Control':'no-store'})

class PartnerKeyInput(BaseModel):label:str=Field(min_length=2,max_length=80)

@app.post('/api/operator/partner-key')
def create_partner_key(b:PartnerKeyInput,op=Depends(operator)):
    k='ea_live_'+secrets.token_urlsafe(32);kid=rid()
    with connect() as db:db.execute('INSERT INTO partner_keys VALUES(?,?,?,?,?,0)',(kid,op['id'],b.label,hashlib.sha256(k.encode()).hexdigest(),now()))
    return {'id':kid,'label':b.label,'key':k,'note':'Copy now; this value cannot be retrieved again.'}

@app.get('/api/operator/partner-keys')
def partner_keys(op=Depends(operator)):
    with connect() as db:return [dict(r) for r in db.execute('SELECT id,label,created,revoked FROM partner_keys WHERE operator_id=? ORDER BY created DESC',(op['id'],))]

@app.delete('/api/operator/partner-key/{kid}')
def revoke_partner_key(kid:str,op=Depends(operator)):
    with connect() as db:db.execute('UPDATE partner_keys SET revoked=1 WHERE id=? AND operator_id=?',(kid,op['id']))
    return {'revoked':True}

def get_partner(x_api_key:Optional[str]=Header(default=None)):
    if not x_api_key:raise HTTPException(401,'Partner API key required')
    with connect() as db:r=db.execute('SELECT operator_id FROM partner_keys WHERE key_hash=? AND revoked=0',(hashlib.sha256(x_api_key.encode()).hexdigest(),)).fetchone()
    if not r:raise HTTPException(401,'Invalid or revoked partner key')
    return r['operator_id']

@app.get('/api/v1/events')
def partner_events(oid=Depends(get_partner)):
    with connect() as db:return [{'id':r['id'],'code':r['code'],'title':r['title'],'guest_url':BASE_URL+'/?booth='+r['code']} for r in db.execute('SELECT id,code,title FROM events WHERE operator_id=? ORDER BY created DESC',(oid,))]

@app.post('/api/v1/events')
def partner_create_event(b:EventInput,oid=Depends(get_partner)):
    eid=rid();code=secrets.token_urlsafe(9)
    with connect() as db:db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)',(eid,oid,code,b.title,b.names,b.event_date,b.venue,b.retention_days,now()))
    return {'id':eid,'code':code,'guest_url':BASE_URL+'/?booth='+code}

@app.get('/api/integration/{eid}')
def integration(eid:str,op=Depends(operator)):
    event=event_owned(eid,op)
    url=f'{BASE_URL}/?booth={event["code"]}'
    return {'url':url,'embed':f'<iframe src="{url}" allow="camera" style="width:100%;min-height:900px;border:0;border-radius:18px" title="EverAfter Photo Booth"></iframe>',
            'webhook_status':'Future partner events are not included in this pilot.'}

@app.get('/api/maintenance/expire')
def expire_help(op=Depends(operator)):
    return {'note':'Use a scheduled job to POST to /api/maintenance/expire with X-Maintenance-Key. Configure photo retention before commercial deployment.'}

@app.post('/api/maintenance/expire')
def expire(x_maintenance_key:Optional[str]=Header(default=None)):
    if not hmac.compare_digest(x_maintenance_key or '',APP_SECRET):raise HTTPException(403,'Invalid maintenance key')
    with connect() as db:
        old=db.execute('''SELECT p.id,p.blob_key FROM photos p JOIN events e ON e.id=p.event_id WHERE p.created + e.retention_days*86400 < ?''',(now(),)).fetchall()
        db.executemany('DELETE FROM photos WHERE id=?',[(r['id'],) for r in old])
    for r in old:blob_delete(r['blob_key'])
    return {'deleted':len(old)}

# No browser caching of user-specific photo pages
@app.get('/gallery.html')
def gallery():return FileResponse(BASE/'static'/'gallery.html',headers={'Cache-Control':'no-store'})
@app.get('/')
def index():return FileResponse(BASE/'static'/'index.html',headers={'Cache-Control':'no-cache'})
app.mount('/static',StaticFiles(directory=BASE/'static'),name='static')
