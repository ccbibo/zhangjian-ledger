"""Independent market truth/privacy cases; synthetic provider bodies only."""
import copy,hashlib,json,sys,tempfile,urllib.error
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
import markets as mk
from app import Application

fixed=datetime(2026,11,1,0,0,tzinfo=timezone.utc)
cases=[];requests=[];response_mode='good'
quote={'symbol':'META','currency':'USD','datetime':'2026-10-30','close':'100.00','previous_close':'90.00','change':'10.00','percent_change':'11.11111','is_market_open':False}
class Response:
 def __init__(self,data):self.data=data
 def __enter__(self):return self
 def __exit__(self,*_):return False
 def read(self,*_):return json.dumps(self.data).encode()
def mocked_open(request,timeout):
 global response_mode
 u=urlsplit(request.full_url);requests.append({'endpoint':u.scheme+'://'+u.netloc+u.path,'params':{k:v for k,v in parse_qs(u.query).items() if k!='apikey'}})
 if response_mode=='forbidden':raise urllib.error.HTTPError(request.full_url,403,'P08 error contains DUMMY-KEY-NEVER-PERSIST',None,None)
 if response_mode=='failure':raise OSError('P08 error contains DUMMY-KEY-NEVER-PERSIST')
 if u.path.endswith('/quote'):
  q=copy.deepcopy(quote);q['symbol']=parse_qs(u.query)['symbol'][0]
  if response_mode=='future':q['datetime']='2099-01-01'
  return Response(q)
 if u.path.endswith('/time_series'):return Response({'meta':{'symbol':'META','currency':'USD'},'values':[{'datetime':'2026-10-29','close':'90.00'},{'datetime':'2026-10-30','close':'100.00'}]})
 if 'STOCK_DAY_ALL' in u.path:return Response([{'Date':'1151030','Code':'2330','ClosingPrice':'100.00','Change':'5.00'}])
 return Response([])
def check(id,name,fn):
 try:
  d=fn();cases.append(dict(id=id,name=name,pass_=bool(d.pop('pass')),**d))
 except Exception as e:cases.append(dict(id=id,name=name,pass_=False,error=type(e).__name__+': '+str(e)))
def rejected_quote(q):
 try:mk.parse_us_snapshot(q,mk.REGISTRY['US:META']);return False
 except mk.MarketDataError:return True

with tempfile.TemporaryDirectory(prefix='p08-markets-') as folder:
 app=Application(Path(folder)/'test.sqlite3');app.market_key='';app.markets._now=lambda:fixed
 def no_key():
  before=len(requests);data=app.dispatch('POST','/api/markets/refresh',{'symbols':['US:META']})[1];q=next(q for q in data['quotes'] if q['id']=='US:META')
  return {'pass':q['status']=='missing_key' and q['price'] is None and len(requests)==before,'quote':q}
 with patch('markets.safe_open',side_effect=mocked_open):
  check('P08-M-01','No market key means no provider call and no fake price',no_key)
  def config():
   data=app.dispatch('POST','/api/markets/configure',{'key':'DUMMY-KEY-NEVER-PERSIST'})[1]
   return {'pass':data['configured'] is True and '尚未代表行情連線成功' in data['scope'] and 'DUMMY-KEY-NEVER-PERSIST' not in json.dumps(data),'status':data}
  check('P08-M-02','Configure is configuration status, not successful quote validation',config)
  app.markets._now=lambda:fixed+timedelta(seconds=61)
  def good_quote():
   data=app.dispatch('POST','/api/markets/refresh',{'symbols':['US:META'],'history':True})[1];q=next(q for q in data['quotes'] if q['id']=='US:META')
   return {'pass':q['price']=='100.00' and q['as_of']=='2026-10-30' and q['closed'] is True and '個人持倉盈虧' in q['not_personal_pnl'] and len(q['history'])==2,'quote':q}
  check('P08-M-03','Price/source date, closed flag, actual history and non-PnL claim preserved',good_quote)
  response_mode='forbidden';app.markets._now=lambda:fixed+timedelta(seconds=122)
  def denied():
   data=app.dispatch('POST','/api/markets/refresh',{'symbols':['US:SPX']})[1];q=next(q for q in data['quotes'] if q['id']=='US:SPX')
   return {'pass':q['status']=='permission_denied' and q['price'] is None and q['symbol']=='SPX','quote':q}
  check('P08-M-04','Index permission failure does not substitute an ETF or synthetic value',denied)
  response_mode='failure';app.markets._now=lambda:fixed+timedelta(seconds=183)
  def stale():
   data=app.dispatch('POST','/api/markets/refresh',{'symbols':['US:META']})[1];q=next(q for q in data['quotes'] if q['id']=='US:META')
   return {'pass':q['price']=='100.00' and q['as_of']=='2026-10-30' and q['stale'] and q['status']!='ok','quote':q}
  check('P08-M-05','Provider failure retains original quote date/value but makes cache stale',stale)
  def secret_free():
   data=app.dispatch('GET','/api/markets')[1];status=app.dispatch('GET','/api/markets/status')[1]
   with app.markets._connect() as con:
    stored=''.join(str(tuple(r)) for table in ['markets_cache','markets_history','markets_attempts','markets_meta'] for r in con.execute('SELECT * FROM '+table))
   return {'pass':'DUMMY-KEY-NEVER-PERSIST' not in json.dumps(data)+json.dumps(status)+stored,'cache_contains_key':False}
  check('P08-M-06','Key and raw provider error strings do not leak through status/cache',secret_free)
  response_mode='good';app.markets._now=lambda:fixed+timedelta(seconds=244)
  def tw():
   data=app.dispatch('POST','/api/markets/refresh',{'symbols':['TW:2330']})[1];q=next(q for q in data['quotes'] if q['id']=='TW:2330')
   return {'pass':q['price']=='100.00' and q['as_of']=='2026-10-30' and q['change']=='5.00' and q['change_percent']=='5.2632' and '日收盤' in q['delay'],'quote':q}
  check('P08-M-07','TWSE Code/ClosingPrice/Change/ROC date mapping is preserved',tw)
  response_mode='future';app.markets._now=lambda:fixed+timedelta(seconds=305)
  def future_cache():
   data=app.dispatch('POST','/api/markets/refresh',{'symbols':['US:META']})[1];q=next(q for q in data['quotes'] if q['id']=='US:META')
   return {'pass':not(q['as_of']=='2099-01-01' and q['status']=='ok' and not q['stale']),'quote':q}
  check('P08-M-08','Future source date is never accepted as fresh cache',future_cache)
  def future_time():
   q=copy.deepcopy(quote);q['datetime']='2099-01-01T16:00:00-05:00'
   try:
    app.markets._store_quote('US:META',mk.parse_us_snapshot(q,mk.REGISTRY['US:META']));return {'pass':False}
   except mk.MarketDataError:return {'pass':True}
  check('P08-M-09','Future source timestamp is rejected by cache write gate',future_time)
  def inconsistent():
   q=copy.deepcopy(quote);q.update(change='1.00',percent_change='1.00')
   return {'pass':rejected_quote(q),'input':q}
  check('P08-M-10','Inconsistent close/previous-close/change values are rejected',inconsistent)
  def bad_percentage():
   q=copy.deepcopy(quote);q['percent_change']='50.00'
   return {'pass':rejected_quote(q)}
  check('P08-M-11','Contradictory percentage is not treated as verified change',bad_percentage)
  def currency():
   q=copy.deepcopy(quote);q.pop('currency')
   try:r=mk.parse_us_snapshot(q,mk.REGISTRY['US:META']);return {'pass':r.get('currency_verified') is False,'result':r}
   except mk.MarketDataError:return {'pass':True}
  check('P08-M-12','Missing stock currency is rejected or explicitly unverified',currency)
  def no_date():
   q=copy.deepcopy(quote);q.pop('datetime');return {'pass':rejected_quote(q)}
  check('P08-M-13','Missing source date is never replaced by fetch time',no_date)
  def mismatched():
   q=copy.deepcopy(quote);q['symbol']='SPY';return {'pass':rejected_quote(q)}
  check('P08-M-14','Requested instrument cannot be replaced by a different symbol',mismatched)
  def bad_symbols():
   try:app.dispatch('POST','/api/markets/refresh',{'symbols':['US:SPY','US:SPY']});return {'pass':False}
   except ValueError:return {'pass':True}
  check('P08-M-15','Duplicate symbol refresh list is rejected',bad_symbols)
report={'role':'P08 market truth/privacy independent review','evidence':'Local Application.dispatch, real temporary SQLite, fixed clock and mocked markets.safe_open. No real provider/key, secret file or browser.','hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'backend/markets.py',ROOT/'backend/app.py']},'cases':[{('pass' if k=='pass_' else k):v for k,v in c.items()} for c in cases],'passed':sum(c['pass_'] for c in cases),'total':len(cases),'primary_docs_read':['https://openapi.twse.com.tw/v1/swagger.json','https://twelvedata.com/docs','https://support.twelvedata.com/en/articles/5745849-timezones'],'docs_scope':'Public primary documentation read through search connector only. App market tests did not contact providers; no quoted entitlement or real key success claim.'}
out=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'qa/reports/P08_markets_r1_raw.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False,indent=2));print('\n'.join(str(c['pass_'])+' '+c['id']+' '+c['name'] for c in cases))
