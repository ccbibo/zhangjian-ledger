"""P02 independent money/oracle tests; real temporary SQLite, no browser claims."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from decimal import Decimal

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from store import Store
from planning import Planning, xirr
from parsers import parse_document

CHECKS=[]
def run(name,fn):
    with tempfile.TemporaryDirectory(prefix='ledger-planning-p02-') as d:
        store=Store(Path(d)/'test.sqlite3');p=Planning(store)
        try:
            value=fn(store,p)
            assert value is True,repr(value)
            CHECKS.append({'name':name,'pass':True})
        except Exception as e:
            CHECKS.append({'name':name,'pass':False,'error':repr(e)})

def rejects(fn):
    try:fn();return False
    except ValueError:return True

def account(name='Test bank',value='100.00'):
    return dict(name=name,asset_class='現金與存款',value=value,as_of='2026-10-31')

def holding(name='Test shares',currency='USD',fx='32',qty='2',cost='90',price='100'):
    return dict(name=name,asset_class='美股與ETF',symbol='TEST',quantity=qty,cost_price=cost,price=price,currency=currency,fx=fx,as_of='2026-10-31')

def debt(name='Test loan',balance='1000',rate='0',payment='300'):
    return dict(name=name,balance=balance,rate=rate,payment=payment,due='2026-11-10')

def add_tx(s,day,name,amount,category):
    return s.create_transaction(dict(date=day,name=name,amount=amount,category=category))

run('P02-01 negative account amount is refused',lambda s,p:rejects(lambda:p.save_item('account',account(value='-1'))))
run('P02-02 fractional cent account amount is refused',lambda s,p:rejects(lambda:p.save_item('account',account(value='0.001'))))
run('P02-03 negative debt interest is refused',lambda s,p:rejects(lambda:p.save_item('debt',debt(rate='-0.1'))))
run('P02-04 over100 debt interest is refused',lambda s,p:rejects(lambda:p.save_item('debt',debt(rate='100.0001'))))
run('P02-05 exponent FX is refused',lambda s,p:rejects(lambda:p.save_item('holding',holding(fx='1e3'))))
run('P02-06 zero FX is refused for foreign currency',lambda s,p:rejects(lambda:p.save_item('holding',holding(fx='0'))))
run('P02-07 holding product overflow rejected atomically',lambda s,p:rejects(lambda:p.save_item('holding',holding(qty='1000000000000',price='1000000000000')) ) and not p.state()['items'])

def usd(s,p):
    h=p.save_item('holding',holding());ov=p.overview('2026-10')
    return h['value']=='6400.00' and h['cost']=='5760.00' and ov['investment']=={'cost':'5760.00','value':'6400.00','unrealized':'640.00'} and ov['assets']=='6400.00'
run('P02-08 USD quantity * quote * FX and profit golden arithmetic',usd)
def hkd(s,p):
    h=p.save_item('holding',holding(currency='HKD',fx='4',qty='3',cost='4',price='5'))
    return h['value']=='60.00' and h['cost']=='48.00'
run('P02-09 HKD precise product',hkd)
def twd(s,p):
    h=p.save_item('holding',holding(currency='TWD',fx='999',qty='0.1',cost='1.15',price='1.25'))
    return h['fx']=='1' and h['value']=='0.13' and h['cost']=='0.12'
run('P02-10 TWD FX ignored and financial half-up rounding',twd)

def budgeting(s,p):
    add_tx(s,'2026-10-01','Meal','-1000','餐飲');add_tx(s,'2026-10-02','Meal refund','100','餐飲')
    p.save_item('budget',dict(name='Dining',month='2026-10',category='餐飲',limit='1200'))
    ov=p.overview('2026-10')
    return ov['budgets'][0]['used']=='900.00' and ov['budgets'][0]['remaining']=='300.00' and ov['spending']['餐飲']=='900.00' and ov['cashflow']['income']=='0.00' and ov['cashflow']['expense']=='900.00'
run('P02-11 refunds reconcile between budgets and ledger statistics',budgeting)
def budgetdup(s,p):
    b=dict(name='Dining',month='2026-10',category='餐飲',limit='100')
    p.save_item('budget',b)
    return rejects(lambda:p.save_item('budget',b)) and len(p.state()['items'])==1
run('P02-12 duplicate monthly category budget rejected',budgetdup)
def goal(s,p):
    g=p.save_item('goal',dict(name='Goal',target='100.01',saved='12.34',deadline='2026-12-31'))
    return g['target']=='100.01' and g['saved']=='12.34' and p.overview('2026-10')['assets']=='0.00'
run('P02-13 savings goal is not silently counted as extra assets',goal)

def loan_zero(s,p):
    g=p.save_item('debt',debt());r=p.schedule(g['id'])
    return len(r['rows'])==4 and r['paid_off'] and Decimal(r['interest'])==0 and Decimal(r['rows'][-1]['payment'])==100 and sum(Decimal(x['principal']) for x in r['rows'])==1000
run('P02-14 zero-interest final installment capped exactly',loan_zero)
def loan_rate(s,p):
    g=p.save_item('debt',debt(rate='12',payment='100'));r=p.schedule(g['id'])
    return r['paid_off'] and Decimal(r['rows'][0]['interest'])==10 and Decimal(r['rows'][0]['principal'])==90 and sum(Decimal(x['principal']) for x in r['rows'])==1000 and sum(Decimal(x['payment']) for x in r['rows'])-1000==Decimal(r['interest']) and all(Decimal(x['remaining'])>=0 for x in r['rows'])
run('P02-15 amortization conserves principal and interest cents',loan_rate)
def loan_insufficient(s,p):
    g=p.save_item('debt',debt(rate='12',payment='10'))
    return rejects(lambda:p.schedule(g['id']))
run('P02-16 payment not covering interest explicitly refused',loan_insufficient)
def loan_cap(s,p):
    g=p.save_item('debt',debt(balance='1000',payment='0.01'));r=p.schedule(g['id'])
    return len(r['rows'])==600 and not r['paid_off'] and Decimal(r['rows'][-1]['remaining'])==994
run('P02-17 capped600 period schedule remains explicitly unpaid',loan_cap)

def history(s,p):
    for m,i,e in [('07',1000,100),('08',2000,200),('09',3000,300)]:
        add_tx(s,'2026-'+m+'-01','Pay'+m,str(i),'薪資');add_tx(s,'2026-'+m+'-02','Spend'+m,str(-e),'生活')
    h=p.history('2026-10')
    return h['sample_months']==3 and [x['month'] for x in h['forecast']]==['2026-11','2026-12','2027-01'] and all(x['income']=='2000.00' and x['expense']=='200.00' and x['balance']=='1800.00' for x in h['forecast'])
run('P02-18 three completed months forecast golden average',history)
run('P02-19 insufficient forecast sample returns no invented forecast',lambda s,p:p.history('2026-10')['forecast']==[])
run('P02-20 valid earliest supported month history remains available',lambda s,p:isinstance(p.history('1900-01')['history'],list))

def duplicate_manual(s,p):
    p.save_item('account',account())
    return rejects(lambda:p.save_item('account',account())) and p.overview('2026-10')['assets']=='100.00'
run('P02-21 identical manual account snapshot not counted twice without confirmation',duplicate_manual)
def late_bank(s,p):
    p.save_item('account',account(name='TEST Bank 001',value='134400'))
    content=(ROOT/'fixtures/pdf/bank-statement.pdf').read_bytes();parsed=parse_document(content,'bank-statement.pdf');pending=s.preview(parsed,hashlib.sha256(content).hexdigest())
    # R2 safety contract: explicit whole-batch refusal is valid; do not require
    # silently merging independent source records. R1 original failure is retained.
    before=(s.snapshot(),p.state())
    try:
        s.commit(pending['document_id'],pending['transactions'])
        return p.overview('2026-10')['assets']=='134400.00'
    except ValueError as e:
        return '手動' in str(e) and before==(s.snapshot(),p.state()) and p.overview('2026-10')['assets']=='134400.00'
run('P02-22 late bank upload prevents double count by safe atomic refusal or correct merge',late_bank)
def duplicate_debt(s,p):
    content=(ROOT/'fixtures/pdf/credit-card-statement.pdf').read_bytes();parsed=parse_document(content,'credit-card-statement.pdf');pending=s.preview(parsed,hashlib.sha256(content).hexdigest());s.commit(pending['document_id'],pending['transactions'])
    before=(s.snapshot(),p.state())
    try:
        p.save_item('debt',debt(name='TEST Credit Card 4242',balance='3800',payment='3800'))
        return p.overview('2026-10')['liabilities']=='3800.00'
    except ValueError as e:
        return '重複' in str(e) and before==(s.snapshot(),p.state()) and p.overview('2026-10')['liabilities']=='3800.00'
run('P02-23 manual debt matching uploaded statement refused atomically or correctly merged',duplicate_debt)
def aggregate(s,p):
    p.save_item('account',account(name='A',value='1000000000000'))
    return rejects(lambda:p.save_item('account',account(name='B',value='1'))) and len(p.state()['items'])==1 and p.overview('2026-10')['assets']=='1000000000000.00'
run('P02-24 aggregate overflow rejected atomically at save',aggregate)
def rounding(s,p):
    p.save_item('account',account(value='0.01'))
    p.settings({'targets':{'現金與存款':'50','美股與ETF':'50'}})
    ov=p.overview('2026-10')
    return sum(Decimal(x['difference']) for x in ov['allocation'])==0
run('P02-25 rebalance cent rounding conserves total assets',rounding)

run('P02-26 XIRR 365day10percent golden',lambda s,p:abs(xirr([{'date':'2025-01-01','amount':'-1000'},{'date':'2026-01-01','amount':'1100'}])['rate']-.1)<1e-8)
run('P02-27 XIRR negative10percent golden',lambda s,p:abs(xirr([{'date':'2025-01-01','amount':'-1000'},{'date':'2026-01-01','amount':'900'}])['rate']+.1)<1e-8)
run('P02-28 XIRR same-date explicitly refused',lambda s,p:rejects(lambda:xirr([{'date':'2025-01-01','amount':'-1000'},{'date':'2025-01-01','amount':'1100'}])))
run('P02-29 XIRR textbook multiple roots refused',lambda s,p:rejects(lambda:xirr([{'date':'2023-01-01','amount':'-100'},{'date':'2024-01-01','amount':'230'},{'date':'2024-12-31','amount':'-132'}])))
def xirr_long(s,p):
    r=xirr([{'date':'1900-01-01','amount':'-100'},{'date':'2200-01-01','amount':'110'}])
    expected=(Decimal('1.1')**(Decimal(365)/Decimal(109573)))-1
    return abs(r['rate']-float(expected))<1e-6
run('P02-30 XIRR legal300year dates avoid exponent overflow',xirr_long)
run('P02-31 XIRR no positive flow refused',lambda s,p:rejects(lambda:xirr([{'date':'2025-01-01','amount':'-100'},{'date':'2026-01-01','amount':'-1'}])))
run('P02-32 XIRR one-cent amounts remain numerically meaningful',lambda s,p:abs(xirr([{'date':'2025-01-01','amount':'-0.01'},{'date':'2026-01-01','amount':'0.02'}])['rate']-1)<1e-8)

def combined(s,p):
    for name in ['bank-statement.pdf','credit-card-statement.pdf','payslip.pdf']:
        content=(ROOT/'fixtures/pdf'/name).read_bytes();parsed=parse_document(content,name);preview=s.preview(parsed,hashlib.sha256(content).hexdigest());s.commit(preview['document_id'],preview['transactions'])
    p.save_item('holding',holding())
    p.save_item('account',account(name='Other independent cash',value='1000'))
    p.save_item('debt',debt(name='Separate loan',balance='2000',payment='100'))
    ov=p.overview('2026-10')
    return ov['assets']=='141800.00' and ov['liabilities']=='5800.00' and ov['net_worth']=='136000.00' and ov['cashflow']['income']=='66200.00' and ov['cashflow']['expense']=='21800.00'
run('P02-33 oldPDF + newcash/holding/debt modules conserve golden combined totals',combined)

def snapshot(s,p):
    a=p.save_item('account',account(value='100'));p.snapshot('2026-10-31')
    a['value']='120';p.save_item('account',a);p.snapshot('2026-11-01')
    st=Planning(Store(s.db_path)).state()
    return [(v['day'],v['net_worth']) for v in st['snapshots']]==[('2026-10-31','100.00'),('2026-11-01','120.00')]
run('P02-34 real SQLite history snapshots preserve historical numeric values',snapshot)

def reverse_card(s,p):
    p.save_item('debt',debt(name='TEST Credit Card 4242',balance='3800',payment='3800'))
    content=(ROOT/'fixtures/pdf/credit-card-statement.pdf').read_bytes();parsed=parse_document(content,'credit-card-statement.pdf');pending=s.preview(parsed,hashlib.sha256(content).hexdigest())
    before=(s.snapshot(),p.state())
    return rejects(lambda:s.commit(pending['document_id'],pending['transactions'])) and before==(s.snapshot(),p.state()) and p.overview('2026-10')['liabilities']=='3800.00'
run('P02-35 reverse manual-card then PDF refusal is atomic across charges and debt',reverse_card)
def update_overflow(s,p):
    a=p.save_item('account',account(name='A',value='100'));p.save_item('account',account(name='B',value='1'))
    before=p.state();a['value']='1000000000000'
    return rejects(lambda:p.save_item('account',a)) and before==p.state() and p.overview('2026-10')['assets']=='101.00'
run('P02-36 aggregate overflow on revision update preserves previous item',update_overflow)
def duplicate_lots(s,p):
    p.save_item('holding',holding());p.save_item('holding',holding())
    return p.overview('2026-10')['assets']=='12800.00' and len(p.state()['items'])==2
run('P02-37 identical symbol holding lots stay permitted unlike full account snapshots',duplicate_lots)
def penny_allocation(s,p):
    a=p.save_item('account',account(value='0.01'))
    p.settings({'targets':{'現金與存款':'33.33','美股與ETF':'33.33','黃金':'33.34'}})
    for value in ['0.01','0.02','0.03','0.07','1.01','123.45']:
        a['value']=value;a=p.save_item('account',a)
        ov=p.overview('2026-10')
        if sum(Decimal(x['difference']) for x in ov['allocation'])!=0:return False
    return True
run('P02-38 largest-remainder rebalance conserves cents across uneven targets',penny_allocation)
run('P02-39 XIRR leap-year366day result matches independent day count',lambda s,p:abs(xirr([{'date':'2024-01-01','amount':'-1000'},{'date':'2025-01-01','amount':'1100'}])['rate']-float(Decimal('1.1')**(Decimal(365)/366)-1))<1e-8)
run('P02-40 XIRR unsorted inputs produce same10percent golden',lambda s,p:abs(xirr([{'date':'2026-01-01','amount':'1100'},{'date':'2025-01-01','amount':'-1000'}])['rate']-.1)<1e-8)

failed=[c for c in CHECKS if not c['pass']]
report={'reviewer':'P02','scope':'v0.5 planning financial correctness only; real SQLite and actual Poppler for PDF fixtures. Not HTTP/browser UI, not full ten-role score.',
        'contract_revision':'R2 P02-22/23 allow explicit safe atomic refusal for duplicate account identity, not only automatic dedupe. R1 original evidence retained in P02_v05_r1.json.',
        'source_sha256':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ['backend/planning.py','backend/store.py','backend/parsers.py']},
        'checks':CHECKS,'passed':len(CHECKS)-len(failed),'total':len(CHECKS),'ui_score':None,'total_score':None,'verdict':'FAIL; independent numerical or consistency defects remain' if failed else 'Role checks passed; full acceptance and UI not established'}
out=ROOT/'qa/reports/P02_v05_money.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
sys.exit(1 if failed else 0)
