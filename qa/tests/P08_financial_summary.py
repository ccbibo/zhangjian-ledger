"""Independent pure-summary boundaries; synthetic data only."""
import hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from financial_summary import summarize

cases=[]
def row(day,amount,category='餐飲'):
 return {'id':'P08-'+day+amount,'date':day,'amount':amount,'category':category,'name':'P08_PRIVATE_MERCHANT','source':'P08_PRIVATE_DOCUMENT.pdf','account':'P08_PRIVATE_ACCOUNT'}
def test(id,name,fn):
 try:
  detail=fn();cases.append(dict(id=id,name=name,pass_=bool(detail.pop('pass')),**detail))
 except Exception as exc:cases.append(dict(id=id,name=name,pass_=False,error=type(exc).__name__+': '+str(exc)))
fixed='2026-11-01'
rows=[row('2026-11-01','1000.00','薪資'),row('2026-11-01','-100.00','薪資'),row('2026-10-26','-200.00'),row('2026-10-30','50.00'),row('2026-11-01','-999.00','轉帳'),row('2026-10-19','-100.00'),row('2026-10-25','300.00','薪資'),row('2026-11-02','9999.00','薪資'),row('2026-10-18','-888.00')]
def ranges(period,start,end,prior_start,prior_end,days):
 s=summarize(rows,[],period,as_of=fixed)
 return {'pass':s['current']['start']==start and s['current']['end']==end and s['prior']['start']==prior_start and s['prior']['end']==prior_end and s['current']['days']==days,'current':s['current'],'prior':s['prior']}
test('P08-F-01','Today range is Taipei day, prior yesterday',lambda:ranges('today','2026-11-01','2026-11-01','2026-10-31','2026-10-31',1))
test('P08-F-02','Week includes today plus six days; prior contiguous same length',lambda:ranges('week','2026-10-26','2026-11-01','2026-10-19','2026-10-25',7))
test('P08-F-03','Month means rolling thirty days, not calendar month',lambda:ranges('month','2026-10-03','2026-11-01','2026-09-03','2026-10-02',30))
def numeric():
 s=summarize(rows,[],'week',as_of=fixed)
 return {'pass':s['income']=='900.00' and s['expense']=='150.00' and s['balance']=='750.00' and s['prior']['expense']=='100.00','income':s['income'],'expense':s['expense'],'balance':s['balance'],'prior_expense':s['prior']['expense']}
test('P08-F-04','Salary reversal/refund and excluded transfers use net category semantics',numeric)
def refund():
 s=summarize([row(fixed,'50.00')],[],'today',as_of=fixed)
 return {'pass':s['income']=='0.00' and s['expense']=='-50.00' and s['balance']=='50.00','summary':s}
test('P08-F-05','Spending refund does not become ordinary income',refund)
def zero():
 s=summarize([row(fixed,'-10.00')],[],'week',as_of=fixed)
 perc=s['change']['percent']
 return {'pass':all(perc.get(k) is None for k in ['income','expense','balance']) and 'Infinity' not in json.dumps(s),'percent':perc,'coverage':s['coverage']}
test('P08-F-06','Prior zero baseline has no infinite percentage comparison',zero)
def empty():
 s=summarize([],[],'week',as_of=fixed)
 return {'pass':s['income']=='0.00' and s['expense']=='0.00' and not s['coverage']['has_current_data'] and s['coverage']['complete'] is False and bool(s['coverage']['note']),'coverage':s['coverage'],'attention':s['attention']}
test('P08-F-07','No records is missing coverage, not proof of no spending',empty)
def sparse():
 s=summarize([row(fixed,'-10.00')],[],'week',as_of=fixed)
 return {'pass':s['coverage']['observed_days']==1 and s['coverage']['expected_days']==7 and s['coverage']['complete'] is False and bool(s['coverage']['note']),'coverage':s['coverage']}
test('P08-F-08','Sparse coverage explicitly remains incomplete',sparse)
def timezonecheck():
 s=summarize(rows,[],'today',as_of=datetime(2026,10,31,16,30,tzinfo=timezone.utc))
 return {'pass':s['as_of']==fixed and s['current']['start']==fixed,'as_of':s['as_of']}
test('P08-F-09','Timezone-aware UTC cutoff is converted to Taipei before selecting day',timezonecheck)
def sensitive():
 bill={'id':'P08-bill','kind':'bill','name':'P08_PRIVATE_BILL','amount':'500.00','due':'2026-11-02','status':'unpaid','recurrence':'none'}
 s=summarize(rows,[bill],'week',as_of=fixed);serialized=json.dumps(s,ensure_ascii=False)
 return {'pass':not any(marker in serialized for marker in ['P08_PRIVATE_MERCHANT','P08_PRIVATE_DOCUMENT','P08_PRIVATE_ACCOUNT','P08_PRIVATE_BILL']),'attention':s['attention']}
test('P08-F-10','Financial summary contains no merchant/source/account/bill names',sensitive)
def no_income():
 s=summarize([row(fixed,'-10.00')],[],'today',as_of=fixed)
 return {'pass':s['income']=='0.00' and s.get('savings_rate') is None and 'Infinity' not in json.dumps(s),'summary':s}
test('P08-F-11','No-income summary does not invent savings ratio',no_income)
test('P08-F-12','Day alias and today produce equivalent output',lambda:{'pass':summarize(rows,[],'day',as_of=fixed)==summarize(rows,[],'today',as_of=fixed)})
report={'role':'P08 financial summary honesty/privacy pure boundaries','source_sha256':hashlib.sha256((ROOT/'backend/financial_summary.py').read_bytes()).hexdigest(),'evidence':'Pure function calls with fixed Taipei/date-aware synthetic data. No HTTP, cloud model, secret file or browser.','cases':[{('pass' if k=='pass_' else k):v for k,v in c.items()} for c in cases],'passed':sum(c['pass_'] for c in cases),'total':len(cases)}
target=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'qa/reports/P08_financial_summary_raw.json'
target.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
