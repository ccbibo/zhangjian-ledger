"""P10 v0.5 service-contract tests. All external HTTP is intercepted in memory.
Uses only an invented dummy key. No real model, feed or listening socket.
"""
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from app import Application,make_handler
from services import Services

FAKE_KEY='P10_TEST_ONLY_NOT_A_REAL_CREDENTIAL'
CONTEXT={'month':'2026-10','assets':'100.00','liabilities':'10.00','net_worth':'90.00','cashflow':{'income':'30.00','expense':'10.00','balance':'20.00'},'spending':{'餐飲':'10.00'},'investment':{'cost':'0.00','value':'0.00','unrealized':'0.00'}}
RSS=b'<rss><channel><item><title>Official test item</title><link>https://www.federalreserve.gov/newsevents/pressreleases/test.htm</link><description>Test only.</description><pubDate>Mon, 01 Nov 2026</pubDate></item></channel></rss>'
ANSWER=json.dumps({'choices':[{'message':{'content':'Test explanation only.'}}]}).encode()
results=[]

class Response:
    def __init__(self,body,url='https://api.deepseek.com/chat/completions'):self.body=body;self.url=url;self.status=200;self.headers={}
    def read(self,n=-1):return self.body if n<0 else self.body[:n]
    def geturl(self):return self.url
    def getcode(self):return 200
    def __enter__(self):return self
    def __exit__(self,*args):return False

@contextlib.contextmanager
def network(body=ANSWER,error=None,redirect=None,redirect_code=302):
    calls=[]
    def fake_open(opener,request,data=None,timeout=None,**kwargs):
        req=request if isinstance(request,urllib.request.Request) else urllib.request.Request(request,data=data)
        row={'url':req.full_url,'timeout':timeout,'has_authorization':bool(req.get_header('Authorization')),
             'payload':json.loads(req.data) if req.data and req.get_header('Content-type','').startswith('application/json') else None}
        calls.append(row)
        if error:raise error
        if redirect:
            handlers=[h for h in opener.handlers if isinstance(h,urllib.request.HTTPRedirectHandler)]
            if not handlers:raise urllib.error.HTTPError(req.full_url,302,'Redirect blocked',{},io.BytesIO())
            try:
                nxt=handlers[0].redirect_request(req,io.BytesIO(),redirect_code,'Found',{},redirect)
            except (urllib.error.HTTPError,ValueError):
                row['redirect_blocked']=True
                raise
            row['redirect_blocked']=nxt is None
            row['redirect_url']=nxt.full_url if nxt else None
            row['authorization_forwarded']=bool(nxt and nxt.get_header('Authorization'))
            row['authorization_same_as_original']=bool(nxt and nxt.get_header('Authorization')==req.get_header('Authorization') and req.get_header('Authorization'))
            if nxt:
                calls.append({'url':nxt.full_url,'simulated_redirect_follow':True,'has_authorization':row['authorization_forwarded']})
                return Response(body,redirect)
            raise urllib.error.HTTPError(req.full_url,302,'Redirect blocked',{},io.BytesIO())
        return Response(body,req.full_url)
    # Patch at the common opener dispatch, so either urlopen or an explicit
    # restricted opener is intercepted. No network path remains in this scope.
    with patch.object(urllib.request.OpenerDirector,'open',autospec=True,side_effect=fake_open):yield calls

def service():
    with patch.dict('os.environ',{},clear=True):s=Services()
    s.configure({'key':FAKE_KEY,'model':'deepseek-chat'})
    return s

def test(name,fn):
    try:results.append({'test':name,**fn()})
    except Exception as exc:results.append({'test':name,'pass':False,'error_type':type(exc).__name__,'error_redacted':str(exc).replace(FAKE_KEY,'[DUMMY SECRET]')})

def expected_failure(fn):
    try:fn()
    except ValueError as exc:return str(exc)
    raise AssertionError('operation unexpectedly succeeded')

def consent():
    s=service()
    with network() as calls:
        messages=[expected_failure(lambda c=c:s.chat({'question':'本月收支？','consent':c},CONTEXT)) for c in [False,None,1,'true']]
    return {'pass':not calls and all('同意' in m for m in messages),'outbound_requests':len(calls)}
test('Explicit true consent required before any outbound model request',consent)

def status():
    s=service();out=json.dumps(s.status())
    return {'pass':FAKE_KEY not in out and s.status()['configured'] is True,'status':s.status()}
test('Status reflects configuration but does not contain dummy key',status)

def chat_success():
    s=service()
    with network() as calls:r=s.chat({'question':'本月收支？','consent':True},CONTEXT)
    sent=calls[0]['payload']
    return {'pass':r['answer']=='Test explanation only.' and calls[0]['url']=='https://api.deepseek.com/chat/completions' and calls[0]['has_authorization'] and CONTEXT['cashflow']['income'] in sent['messages'][0]['content'],'endpoint':calls[0]['url'],'timeout':calls[0]['timeout'],'has_auth':calls[0]['has_authorization'],'provider':r['provider']}
test('Consented chat uses fixed HTTPS endpoint with summary and bounded timeout',chat_success)

for name,exc in [('timeout',TimeoutError('timeout '+FAKE_KEY)),('HTTP401',urllib.error.HTTPError('https://api.deepseek.com/chat/completions',401,FAKE_KEY,{},io.BytesIO())),('HTTP402',urllib.error.HTTPError('https://api.deepseek.com/chat/completions',402,FAKE_KEY,{},io.BytesIO())),('HTTP503',urllib.error.HTTPError('https://api.deepseek.com/chat/completions',503,FAKE_KEY,{},io.BytesIO()))]:
    def check(exc=exc):
        s=service()
        with network(error=exc):message=expected_failure(lambda:s.chat({'question':'測試','consent':True},CONTEXT))
        return {'pass':FAKE_KEY not in message and bool(message),'message':message}
    test('Model '+name+' fails honestly without exposing secret',check)

for name,body in [('nonJSON',b'not json'),('emptychoices',b'{"choices":[]}'),('emptycontent',b'{"choices":[{"message":{"content":""}}]}'),('oversize',b'x'*(2*1024*1024+2))]:
    def check(body=body):
        s=service()
        with network(body=body):message=expected_failure(lambda:s.chat({'question':'測試','consent':True},CONTEXT))
        return {'pass':FAKE_KEY not in message,'message':message}
    test('Model '+name+' rejected instead of false answer',check)

def model_redirect():
    s=service()
    with network(redirect='https://not-deepseek.example/steal') as calls:
        error=None
        try:s.chat({'question':'測試','consent':True},CONTEXT)
        except ValueError as exc:error=str(exc)
    return {'pass':len(calls)==1 and not calls[0].get('authorization_forwarded',False) and error is not None,'simulated_requests':calls,'error':error,'note':'Stdlib redirect handler invoked in memory; no actual request/key transmission'}
test('Model HTTP redirect cannot forward Authorization to another origin',model_redirect)

def feed_redirect():
    s=service()
    with network(body=RSS,redirect='http://127.0.0.1:8791/api/state') as calls:r=s.news(refresh=True)
    return {'pass':len(calls)==1 and not r['articles'] and bool(r['errors']),'simulated_requests':calls,'article_count':len(r['articles']),'error_count':len(r['errors'])}
test('RSS fixed allowlist blocks redirect to loopback',feed_redirect)

def redirect_variants():
    details=[]
    for code in [301,302,303,307,308]:
        for target in ['https://not-deepseek.example/steal','http://127.0.0.1:8791/api/state']:
            s=service()
            with network(redirect=target,redirect_code=code) as calls:
                error=expected_failure(lambda:s.chat({'question':'test','consent':True},CONTEXT))
            details.append({'code':code,'target':target,'requests':len(calls),'blocked':calls[0].get('redirect_blocked',False),'secret_in_error':FAKE_KEY in error})
    return {'pass':all(d['requests']==1 and d['blocked'] and not d['secret_in_error'] for d in details),'variants':details}
test('All common redirect codes block cross-origin and HTTPS downgrade variants',redirect_variants)

def key_clear():
    s=service();out=s.configure({'key':''})
    with network() as calls:message=expected_failure(lambda:s.chat({'question':'test','consent':True},CONTEXT))
    return {'pass':out['configured'] is False and not calls and 'Key' in message,'outbound_requests':len(calls),'configured':out['configured']}
test('Clearing memory key disables outbound model calls',key_clear)

def invalid_configuration_atomic():
    s=service()
    for data in [{'key':None},{'key':'x','model':'https://evil.example/'},{'key':'x'*513}]:
        expected_failure(lambda data=data:s.configure(data))
    return {'pass':s.key==FAKE_KEY and s.model=='deepseek-chat','configuration_unchanged':s.key==FAKE_KEY}
test('Invalid service configuration leaves prior memory configuration unchanged',invalid_configuration_atomic)


def feed_cache():
    s=service()
    with network(body=RSS) as calls:a=s.news();b=s.news()
    return {'pass':len(calls)==1 and len(a['articles'])==1 and b['cached'] is True,'requests':len(calls),'cached':b['cached']}
test('RSS caches actual returned items and labels cached state',feed_cache)

for name,body in [('DOCTYPE',b'<!DOCTYPE rss [<!ENTITY e SYSTEM "file:///etc/passwd">]><rss/>'),('invalidXML',b'bad xml'),('oversize',b'x'*(2*1024*1024+2))]:
    def check(body=body):
        s=service()
        with network(body=body):r=s.news(refresh=True)
        return {'pass':not r['articles'] and bool(r['errors']),'articles':len(r['articles']),'errors':len(r['errors'])}
    test('RSS '+name+' returns explicit failure without fabricated news',check)

class MemorySocket:
    def __init__(self,raw):self.r=io.BytesIO(raw);self.sent=bytearray()
    def makefile(self,*a,**kw):return self.r
    def sendall(self,data):self.sent.extend(data)
    def settimeout(self,t):pass

with tempfile.TemporaryDirectory(prefix='ledger-p10-services-') as temp:
    with patch.dict('os.environ',{},clear=True):app=Application(Path(temp)/'ledger.sqlite3')
    def app_secret():
        app.dispatch('POST','/api/ai/configure',{'key':FAKE_KEY,'model':'deepseek-chat'})
        before=app.dispatch('GET','/api/backup')[1]
        with network():answer=app.dispatch('POST','/api/ai/chat',{'month':'2026-10','question':'本月？','consent':True})[1]
        after=app.dispatch('GET','/api/backup')[1]
        with app.store._connect() as con:db_dump='\n'.join(con.iterdump())
        raw_key=FAKE_KEY.encode() in Path(app.store.db_path).read_bytes()
        return {'pass':before==after and FAKE_KEY not in json.dumps(after) and FAKE_KEY not in db_dump and not raw_key,'backup_changed':before!=after,'secret_in_database':FAKE_KEY in db_dump,'secret_in_sqlite_bytes':raw_key,'provider':answer['provider']}
    test('Configure/chat does not persist key or modify financial data',app_secret)
    def logs_secret():
        payload=json.dumps({'key':FAKE_KEY}).encode();raw=b'POST /api/ai/configure HTTP/1.1\r\nHost: 127.0.0.1:8791\r\nContent-Type: application/json\r\nContent-Length: '+str(len(payload)).encode()+b'\r\n\r\n'+payload
        sock=MemorySocket(raw);log=io.StringIO()
        with contextlib.redirect_stdout(log):make_handler(app)(sock,('127.0.0.1',1234),SimpleNamespace(server_address=('127.0.0.1',8791)))
        return {'pass':FAKE_KEY not in log.getvalue() and FAKE_KEY.encode() not in sock.sent,'status':int(bytes(sock.sent).split(b' ')[1]),'log':log.getvalue()}
    test('HTTP configure response and request log omit secret body',logs_secret)
    def currency():
        base={'name':'Test holding','asset_class':'美股與ETF','symbol':'TEST','quantity':'2','cost_price':'10','price':'12','currency':'USD','fx':'32','as_of':'2026-10-31'}
        r=app.dispatch('POST','/api/items/save',{'kind':'holding','item':base})[1]
        bad=dict(base,currency='EUR');message=expected_failure(lambda:app.dispatch('POST','/api/items/save',{'kind':'holding','item':bad}))
        return {'pass':r['value']=='768.00' and r['cost']=='640.00' and '不支援' in message,'value':r['value'],'cost':r['cost'],'rejected_currency':message}
    test('Holding uses explicit manual USD fx and rejects unsupported currency',currency)
    def overaggregate():
        try:
            for n in ['P10-A','P10-B']:
                app.dispatch('POST','/api/items/save',{'kind':'account','item':{'name':n,'asset_class':'現金與存款','value':'1000000000000','as_of':'2026-10-31'}})
        except ValueError as exc:
            ov=app.dispatch('GET','/api/workspace?month=2026-10')[1]
            return {'pass':True,'reason':'aggregate rejected before corrupting readable workspace','error':str(exc),'assets':ov['assets']}
        try:app.dispatch('GET','/api/workspace?month=2026-10')
        except ValueError as exc:
            persisted=[i for i in app.planning.state()['items'] if i['name'].startswith('P10-')]
            return {'pass':False,'reason':'save returned success twice but workspace now unavailable','persisted':len(persisted),'error':str(exc)}
        return {'pass':True}
    test('Planning aggregate limit cannot persist a workspace-breaking state',overaggregate)

out=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'qa/reports/P10_v05_raw.json'
report={'reviewer':'P10','evidence':'urllib OpenerDirector.open fully mocked; stdlib redirect objects only; invented dummy key; real temporary SQLite; in-memory HTTP protocol; no real network/model/socket/browser','passed':sum(r['pass'] for r in results),'total':len(results),'sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'backend/services.py',ROOT/'backend/planning.py',ROOT/'backend/app.py']},'results':results}
out.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'passed':report['passed'],'total':report['total'],'failed':[r['test'] for r in results if not r['pass']],'report':str(out)},ensure_ascii=False,indent=2))
