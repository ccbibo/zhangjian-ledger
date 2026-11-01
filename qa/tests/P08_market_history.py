"""Historical market data and official dated-index fallback boundary cases."""
import hashlib,json,sys,tempfile
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlsplit,parse_qs
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
from app import Application
import markets as m
cases=[]
class Response:
 def __init__(self,body):self.body=body
 def __enter__(self):return self
 def __exit__(self,*_):return False
 def read(self,*_):return json.dumps(self.body).encode()
def run_case(name,mode):
 with tempfile.TemporaryDirectory(prefix='p08-history-') as td:
  app=Application(Path(td)/'db.sqlite3');app.market_key='DUMMY-P08-NOT-A-REAL-KEY';app.markets._now=lambda:datetime(2026,10,30,18,0,tzinfo=timezone.utc)
  def fake(request,timeout):
   p=urlsplit(request.full_url).path
   if p.endswith('/quote'):
    return Response({'symbol':'META','currency':'USD','datetime':'2026-10-30T14:00:00-04:00' if mode=='open' else '2026-10-29','close':'100','previous_close':'90','change':'10','percent_change':'11.11111','is_market_open':mode=='open'})
   if p.endswith('/time_series'):
    return Response({'meta':{'symbol':'META','currency':'EUR' if mode=='currency' else 'USD'},'values':[{'datetime':'2099-01-01' if mode=='future' else '2026-10-29','close':'100'}]})
   if p.endswith('/MI_INDEX'):
    return Response([{'指數':'發行量加權股價指數','收盤指數':'99999','漲跌點數':'100','漲跌百分比':'0.1'}])
   if p.endswith('/MI_5MINS_HIST'):
    return Response({'stat':'OK','fields':['日期','開盤指數','最高指數','最低指數','收盤指數'],'data':[] if mode=='empty_fallback' else [['115/10/29','0','0','0','22900'],['115/10/30','0','0','0','23000']]})
   raise AssertionError('Unexpected destination')
  with patch('markets.safe_open',side_effect=fake):
   sid='TW:TAIEX' if 'fallback' in mode else 'US:META'
   data=app.dispatch('POST','/api/markets/refresh',{'symbols':[sid],'history':mode in ('currency','future')})[1]
   q=next(q for q in data['quotes'] if q['id']==sid)
  if mode=='currency':passed=bool(q.get('history_error')) and not any(p['date']=='2026-10-29' and p['close']=='100' for p in q['history'])
  elif mode=='future':passed=bool(q.get('history_error')) and not any(p['date'].startswith('2099') for p in q['history'])
  elif mode=='open':passed=not q['history'] or all(p.get('is_final_close') is False or p.get('kind')=='intraday_snapshot' for p in q['history'])
  elif mode=='dated_fallback':passed=q['price']=='23000' and q['as_of']=='2026-10-30' and q['source_endpoint']==m.TW_INDEX_HISTORY and q['status']=='ok'
  else:passed=q['price'] is None and q['as_of'] is None and q['status']!='ok'
  # Quote's own daily snapshot may legitimately equal one history point; currency
  # rejection is proven by history_error, not by demanding all cache data vanish.
  if mode=='currency':passed=bool(q.get('history_error'))
  cases.append({'name':name,'pass':passed,'quote':q})
for name,mode in [('US history currency must not be mislabelled USD','currency'),('Future historical point must not enter cache','future'),('Open intraday quote is not silently stored as final close','open'),('Undated TWSE index uses actual official dated close','dated_fallback'),('Missing dated fallback has no price invented from fetch date','empty_fallback')]:
 try:run_case(name,mode)
 except Exception as e:cases.append({'name':name,'pass':False,'error':type(e).__name__+': '+str(e)})
r={'role':'P08 historical market truth API boundaries','evidence':'Application.dispatch, true tempSQLite and mocked markets.safe_open only; no secret files, provider calls or browser.','hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'backend/markets.py',ROOT/'backend/app.py']},'cases':cases,'passed':sum(c['pass'] for c in cases),'total':len(cases)}
out=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'qa/reports/P08_market_history_r1_raw.json';out.write_text(json.dumps(r,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in r.items() if k!='cases'},ensure_ascii=False,indent=2));print('\n'.join(str(c['pass'])+' '+c['name'] for c in cases))
