"""P02 pure-function numeric contract checks. P08 reviews independently."""
import importlib.util
import json
from pathlib import Path
from datetime import datetime, timezone
import copy
import hashlib
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('summary',ROOT/'backend/financial_summary.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
summarize=m.summarize
checks=[]
def check(name,fn):
    try:assert fn() is True;checks.append({'name':name,'pass':True})
    except Exception as e:checks.append({'name':name,'pass':False,'error':repr(e)})
def tx(day,amount,category='餐飲',**kw):return dict(date=day,amount=amount,category=category,**kw)
def reject(fn):
    try:fn();return False
    except ValueError:return True

check('day current/prior inclusive one-day boundaries',lambda:(lambda r:r['current']['start']=='2026-11-01' and r['prior']['start']=='2026-10-31' and r['expense']=='100.00' and r['prior']['expense']=='200.00')(summarize([tx('2026-11-01','-100'),tx('2026-10-31','-200'),tx('2026-10-30','-300')],[],'day','2026-11-01')))
check('seven-day current/prior date windows',lambda:(lambda r:r['current']['start']=='2026-10-26' and r['current']['end']=='2026-11-01' and r['prior']['start']=='2026-10-19' and r['prior']['end']=='2026-10-25')(summarize([],[],'week','2026-11-01')))
check('month is rolling30 not calendar month',lambda:(lambda r:r['current']['days']==30 and r['current']['start']=='2026-10-03' and r['prior']['start']=='2026-09-03' and r['prior']['end']=='2026-10-02')(summarize([],[],'month','2026-11-01')))
check('Taipei date for UTC midnight boundary',lambda:summarize([],[],'day',datetime(2026,10,31,16,30,tzinfo=timezone.utc))['as_of']=='2026-11-01')
check('salary negative correction and dividend net income',lambda:summarize([tx('2026-11-01','1000','薪資'),tx('2026-11-01','-100','薪資'),tx('2026-11-01','50','股息')],[],'day','2026-11-01')['income']=='950.00')
check('spending refunds reduce expense not earned income',lambda:(lambda r:r['expense']=='80.00' and r['income']=='0.00')(summarize([tx('2026-11-01','-100'),tx('2026-11-01','20')],[],'day','2026-11-01')))
check('transfer/investment/card settlement excluded',lambda:(lambda r:r['income']=='0.00' and r['expense']=='0.00' and r['valid_count']==0 and r['current']['excluded_count']==3)(summarize([tx('2026-11-01','1000','轉帳'),tx('2026-11-01','-300','投資'),tx('2026-11-01','-200','信用卡繳款')],[],'day','2026-11-01')))
check('integer cents and decimal arithmetic exact',lambda:summarize([{'date':'2026-11-01','amount_cents':-10,'category':'餐飲'},{'date':'2026-11-01','amount_cents':-20,'category':'餐飲'}],[],'day','2026-11-01')['expense']=='0.30')
check('zero prior has null percentages not invented growth',lambda:summarize([tx('2026-11-01','-100')],[],'day','2026-11-01')['change']['percent']['expense'] is None)
check('negative prior expense percent uses absolute base',lambda:summarize([tx('2026-10-31','100'),tx('2026-11-01','-100')],[],'day','2026-11-01')['change']['percent']['expense']=='200.00')
check('sparse days never claim completeness',lambda:(lambda r:r['coverage']['observed_days']==1 and not r['coverage']['complete'] and '不代表' in r['coverage']['note'])(summarize([tx('2026-11-01','0')],[],'week','2026-11-01')))
check('invalid date/subcent/conflicting cents explicitly counted',lambda:(lambda r:r['coverage']['invalid_transactions']==3 and r['valid_count']==0)(summarize([tx('2026-02-30','-1'),tx('2026-11-01','0.001'),tx('2026-11-01','1',amount_cents=200)],[],'week','2026-11-01')))
check('privacy no names/account/file echoed',lambda:(lambda text:all(v not in text for v in ['SECRET MERCHANT','SECRET FILE','SECRET ACCOUNT']))(json.dumps(summarize([tx('2026-11-01','-10',name='SECRET MERCHANT',source='SECRET FILE',account='SECRET ACCOUNT')],[dict(kind='bill',name='SECRET MERCHANT',account='SECRET ACCOUNT',amount='20',due='2026-11-01',status='unpaid')],'week','2026-11-01'))))
def bills():
    rows=[dict(kind='bill',name='SECRET',amount='10',due=d,status=status) for d,status in [('2026-10-31','unpaid'),('2026-11-01','unpaid'),('2026-11-08','unpaid'),('2026-11-09','unpaid'),('2026-10-01','paid')]]
    r=summarize([],rows,'week','2026-11-01');a={v['type']:v for v in r['attention']}
    return a['overdue_bills']['count']==1 and a['overdue_bills']['amount']=='10.00' and a['upcoming_bills']['count']==2 and a['upcoming_bills']['amount']=='20.00'
check('bill overdue/today/seven-day boundary and paid exclusion',bills)
check('invalid period cleanly rejected',lambda:reject(lambda:summarize([],[],{},'2026-11-01')))
check('naive datetime rejected rather than guessing timezone',lambda:reject(lambda:summarize([],[],'day',datetime(2026,11,1))))
check('dates before year1 rejected without leaking payload',lambda:reject(lambda:summarize([],[],'month','0001-01-01')))
check('today alias is canonical day',lambda:summarize([],[],'today','2026-11-01')['period']=='day')
def immutable():
    ts=[tx('2026-11-01','-100')];items=[dict(kind='bill',name='SECRET',amount='10',due='2026-11-01')];before=copy.deepcopy((ts,items));r=summarize(ts,items,'week','2026-11-01');json.dumps(r,allow_nan=False);return before==(ts,items)
check('function has no mutation and output is strict JSON serializable',immutable)
report={'reviewer':'P02','scope':'Pure financial summary numeric/privacy contract; no HTTP/browser evidence','checks':checks,'passed':sum(c['pass'] for c in checks),'total':len(checks),'sha256':hashlib.sha256((ROOT/'backend/financial_summary.py').read_bytes()).hexdigest(),'ui_score':None,'total_score':None}
(ROOT/'qa/reports/P02_v05_summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
raise SystemExit(0 if all(c['pass'] for c in checks) else 1)
