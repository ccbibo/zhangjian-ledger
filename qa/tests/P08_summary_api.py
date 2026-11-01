"""Local dispatch and mocked cloud transport of deterministic financial summary."""
import hashlib,json,sys,tempfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
import app as module
from financial_summary import summarize
calls=[];cases=[]
def record(id,name,passed,**details):cases.append(dict(id=id,name=name,pass_=bool(passed),**details))
class Response:
 def __enter__(self):return self
 def __exit__(self,*_):return False
 def read(self,*_):return json.dumps({'choices':[{'message':{'content':'P08 mocked summary response'}}]}).encode()
def mock_open(request,timeout):calls.append(json.loads(request.data));return Response()
def fixed_summary(transactions,items,period='week',as_of=None):return summarize(transactions,items,period,as_of='2026-11-01')
with tempfile.TemporaryDirectory(prefix='p08-summary-api-') as folder:
 app=module.Application(Path(folder)/'test.sqlite3');app.services.configure({'key':'','model':'dummy-model'})
 for row in [{'date':'2026-11-01','name':'P08_PRIVATE_MERCHANT','amount':'-150.00','category':'餐飲'},{'date':'2026-10-26','name':'P08_PRIVATE_PAYROLL','amount':'1000.00','category':'薪資'}]:app.dispatch('POST','/api/transaction/create',{'transaction':row})
 app.dispatch('POST','/api/items/save',{'kind':'bill','item':{'name':'P08_PRIVATE_BILL','amount':'50.00','due':'2026-11-02','status':'unpaid','recurrence':'none'}})
 with patch('app.summarize',side_effect=fixed_summary),patch('services.safe_open',side_effect=mock_open):
  code,stats=app.dispatch('GET','/api/financial-summary?period=week&as_of=1900-01-01')
  record('P08-A-01','GET selects requested window and ignores forged client as_of',code==200 and stats['period']=='week' and stats['as_of']=='2026-11-01' and stats['current']['start']=='2026-10-26')
  try:app.dispatch('POST','/api/ai/summary',{'period':'week','consent':True});rejected=False
  except ValueError:rejected=True
  record('P08-A-02','Cloud financial summary requires a key with no transport call',rejected and not calls)
  app.services.configure({'key':'P08-DUMMY-NOT-REAL','model':'dummy-model'})
  for consent in [False,None,'true']:
   try:app.dispatch('POST','/api/ai/summary',{'period':'week','consent':consent});rejected=False
   except ValueError:rejected=True
   record('P08-A-03-'+str(consent),'Cloud summary requires exact true consent',rejected and not calls)
  code,result=app.dispatch('POST','/api/ai/summary',{'period':'week','consent':True,'question':'P08_PRIVATE_INJECTED_QUESTION','as_of':'1900-01-01'})
  sent=json.dumps(calls[-1],ensure_ascii=False)
  supplied=json.loads(calls[-1]['messages'][0]['content'].split('財務摘要：',1)[1])
  record('P08-A-04','Only this period deterministic summary is attached to model',supplied==stats and result['statistics']==stats and code==200)
  record('P08-A-05','No merchants, bills, source docs, injected question or credential in context',not any(v in sent for v in ['P08_PRIVATE_MERCHANT','P08_PRIVATE_PAYROLL','P08_PRIVATE_BILL','P08_PRIVATE_INJECTED_QUESTION','P08-DUMMY-NOT-REAL']))
  for period in ['day','week','month']:
   record('P08-A-06-'+period,'GET supports 1/7/30 day period contracts',app.dispatch('GET','/api/financial-summary?period='+period)[1]['period']==period)
  before=app.store.snapshot()
  with patch('services.safe_open',side_effect=OSError('P08 simulated transport failure')):
   try:app.dispatch('POST','/api/ai/summary',{'period':'week','consent':True});rejected=False
   except ValueError:rejected=True
  record('P08-A-07','Model failure does not alter financial ledger',rejected and app.store.snapshot()==before)
report={'role':'P08 financial-summary API privacy/honesty','evidence':'Direct local Application.dispatch with fixed pure summary and dummy model/key transport; no HTTP/browser/secret files/real provider call.','hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'backend/financial_summary.py',ROOT/'backend/app.py',ROOT/'backend/services.py']},'cases':[{('pass' if k=='pass_' else k):v for k,v in c.items()} for c in cases],'passed':sum(c['pass_'] for c in cases),'total':len(cases)}
target=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'qa/reports/P08_summary_api_raw.json'
target.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
