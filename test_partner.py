import requests,time
base='http://127.0.0.1:8765'
def call(method,path,**kwargs):return requests.request(method,base+path,timeout=10,**kwargs)
r=call('POST','/api/auth/register',json={'company':'Pilot Co','email':'partner'+str(int(time.time()))+'@test.net','password':'verystrongpassword123'})
assert r.status_code==200,r.text
h={'Authorization':'Bearer '+r.json()['token']}
r=call('POST','/api/operator/partner-key',headers=h,json={'label':'Premium booth software'})
assert r.status_code==200,r.text
kid=r.json()['id'];pk=r.json()['key'];assert pk.startswith('ea_live_')
r=call('POST','/api/v1/events',headers={'X-API-Key':pk},json={'title':'Test partner wedding','names':'Partner Couple'});assert r.status_code==200,r.text
r=call('GET','/api/v1/events',headers={'X-API-Key':pk});assert r.status_code==200 and len(r.json())>0
r=call('DELETE','/api/operator/partner-key/'+kid,headers=h);assert r.status_code==200
r=call('GET','/api/v1/events',headers={'X-API-Key':pk});assert r.status_code==401,r.text
print('PASS: partner API key issued, event created, list works, revoked key denied')
