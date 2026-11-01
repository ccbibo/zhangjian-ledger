"""Unit fixtures for diagnostics/prod verification helpers, never live evidence."""
import io,json,socket,sys,urllib.error
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from network_diagnostics import classify_failure,diagnose_network
from verify_live import summary_case,news_cases,market_case,MARKET_SYMBOLS
checks=[]
def test(name,fn):
    try:checks.append({'name':name,'passed':bool(fn())})
    except Exception as e:checks.append({'name':name,'passed':False,'error_type':type(e).__name__})

test('DNS wrapper remains pre-auth stage',lambda:classify_failure(urllib.error.URLError(socket.gaierror(8,'dummy')))['api_key_verdict']=='not_checked')
test('No key distinguishes configuration from invalid key',lambda:classify_failure(ValueError('尚未設定DeepSeek API Key'))['category']=='no_key')
test('HTTP401 distinguishes rejected authentication',lambda:classify_failure(urllib.error.HTTPError('https://api.deepseek.com/',401,'dummy',{},io.BytesIO()))['api_key_verdict']=='rejected')
test('HTTP400 is model/request, not asserted wrong key',lambda:classify_failure(urllib.error.HTTPError('https://api.deepseek.com/',400,'dummy',{},io.BytesIO()))['category']=='model_or_request')
test('HTTP402 distinct billing class',lambda:classify_failure(urllib.error.HTTPError('https://api.deepseek.com/',402,'dummy',{},io.BytesIO()))['category']=='quota')
test('Malformed model JSON distinct from DNS',lambda:classify_failure(json.JSONDecodeError('dummy','x',0))['category']=='invalid_model_response')
test('Feed exception distinct from model auth',lambda:classify_failure(ValueError('unsupported XML'),'feed')['category']=='feed_unavailable_or_format')
test('Diagnostic error never contains arbitrary secret text',lambda:'DUMMY_SECRET_TEXT' not in json.dumps(classify_failure(TimeoutError('DUMMY_SECRET_TEXT'))))

def dns_no_key_no_network():
    # Name resolver itself is mocked; this is a unit assertion, never release evidence.
    with patch('socket.getaddrinfo',side_effect=socket.gaierror(8,'dummy')),patch('urllib.request.getproxies',return_value={'https':'https://user:DUMMY_SECRET_TEXT@proxy.invalid:443'}),patch.dict('os.environ',{'HTTPS_PROXY':'https://user:DUMMY_SECRET_TEXT@proxy.invalid:443'},clear=True):
        r=diagnose_network(['api.deepseek.com'])
    return not r['dns']['api.deepseek.com']['resolved'] and 'DUMMY_SECRET_TEXT' not in json.dumps(r) and r['proxy_configuration']['HTTPS_PROXY']['present']
test('Diagnostic records proxy presence/type without proxy values',dns_no_key_no_network)

def production_route():
    calls=[]
    def dispatch(method,path,payload):
        calls.append((method,path,payload))
        return 200,{'answer':'unit fixture only','provider':'DeepSeek','model':'dummy','statistics':{'current':{'income':'1','expense':'0','balance':'1'}},'scope':'fixture'}
    r=summary_case(SimpleNamespace(dispatch=dispatch))
    return calls==[('POST','/api/ai/summary',{'period':'month','consent':True})] and r['endpoint']=='/api/ai/summary' and r['statistics']['current']['income']=='1'
test('Summary verifier invokes exact production dispatcher route and consent payload',production_route)

def news_current(stale=False,date_override=None):
    now=datetime.now(timezone.utc).isoformat();today=datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat()
    chan={'date':date_override or today,'updated_at':now,'ai_generated':True,'stale':stale,'errors':[],
          'items':[{'title':'Unit only','url':'https://example.test/source','published':now,'summary':'Unit summary','why':'Unit reason'}]}
    app=SimpleNamespace(services=SimpleNamespace(key='DUMMY_ONLY'),newsroom=SimpleNamespace(refresh=lambda **k:None),dispatch=lambda *a:(200,{'channels':{'ai':chan,'finance':chan}}))
    cases,_=news_cases(app)
    return cases
test('Both fresh non-stale news categories pass helper structural check',lambda:all(c['status']=='passed' for c in news_current()))
test('Stale news cannot satisfy live-generation helper',lambda:all(c['status']=='blocked' for c in news_current(stale=True)))
test('Previous-date news cannot satisfy current-day helper',lambda:all(c['status']=='blocked' for c in news_current(date_override='2001-01-01')))

def market_fixture_case(bad=None,no_history=False,no_key=False):
    now=datetime.now(timezone.utc);stamp=now.isoformat();day=now.astimezone(ZoneInfo('Asia/Taipei')).date().isoformat()
    quotes=[]
    for i,sid in enumerate(MARKET_SYMBOLS):
        quotes.append({'id':sid,'symbol':sid.split(':')[1],'price':'100.00','as_of':day,'as_of_precision':'date','status':'ok','stale':False,'fetched_at':stamp,'history':[{'date':(now-timedelta(days=1)).date().isoformat(),'close':'99.00'},{'date':day,'close':'100.00'}] if i==0 and not no_history else []})
    if bad:quotes[-1].update(status='permission_denied',stale=True,error={'code':'permission_denied','message':'fixture entitlement unavailable'})
    calls=[]
    def dispatch(method,path,payload):calls.append((method,path,payload));return 200,{'quotes':quotes,'refresh':{'requested':MARKET_SYMBOLS}}
    case=market_case(SimpleNamespace(market_key='' if no_key else 'DUMMY_ONLY',dispatch=dispatch))
    return case,calls,day

test('No independent market key blocks without attempting market network',lambda:(lambda x:x[0]['status']=='blocked' and x[0]['failure_category']=='no_key' and not x[1])(market_fixture_case(no_key=True)))
test('Markets verifier uses production requested symbols/history payload',lambda:(lambda x:x[1]==[('POST','/api/markets/refresh',{'symbols':MARKET_SYMBOLS,'history':True})])(market_fixture_case()))
test('All requested quotes plus one real source history can satisfy helper shape',lambda:market_fixture_case()[0]['status']=='passed')
test('A requested premium index permission failure prevents live pass',lambda:market_fixture_case(bad=True)[0]['status']=='blocked')
test('No source history cannot be replaced with fabricated chart points',lambda:market_fixture_case(no_history=True)[0]['status']=='blocked')
test('Daily quote date precision preserved without invented timestamp',lambda:(lambda x:all(q['as_of']==x[2] and q['as_of_precision']=='date' and q['data_delay']=='daily_eod' for q in x[0]['quotes']))(market_fixture_case()))

report={'kind':'unit_test','mocked':True,'method':'unit fixtures only; no secrets file/environment real key/model/feed/network/socket/browser','passed':sum(c['passed'] for c in checks),'total':len(checks),'checks':checks,'release_evidence':False}
out=ROOT/'qa/reports/P10_live_diagnostics_unit.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'passed':report['passed'],'total':report['total'],'failed':[c['name'] for c in checks if not c['passed']]},ensure_ascii=False))
