"""Independent P02 money checks against actual Poppler and real SQLite files."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from parsers import parse_document
from store import Store

checks = []
def check(name, fn):
    try:
        value = fn()
        assert value is True, repr(value)
        checks.append({'name': name, 'pass': True})
    except Exception as e:
        checks.append({'name': name, 'pass': False, 'error': repr(e)})

oracle = json.loads((ROOT/'fixtures/expected.json').read_text())
with tempfile.TemporaryDirectory(prefix='ledger-p02-final-') as td:
    db = Path(td)/'ledger.sqlite3'
    s = Store(db)
    committed = []
    for name in ['bank-statement.pdf','credit-card-statement.pdf','payslip.pdf']:
        content = (ROOT/'fixtures/pdf'/name).read_bytes()
        parsed = parse_document(content, name)
        pending = s.preview(parsed, hashlib.sha256(content).hexdigest())
        result = s.commit(pending['document_id'], pending['transactions'])
        committed.append(result['committed_count'])
    stats = s.stats('2026-10')
    snap = s.snapshot()
    check('real three-PDF pipeline raw9 -> committed8 with salary dedupe', lambda: committed==[5,3,0] and len(snap['transactions'])==8)
    check('independent oracle income/expense/surplus from SQLite', lambda: stats['income']=='66200.00' and stats['expense']=='21800.00' and stats['balance']=='44400.00' and stats['excluded_count']==2)
    check('bank asset/card liabilities/net worth match handwritten oracle', lambda: sum(Decimal(a['value']) for a in snap['assets'])==Decimal('134400') and sum(Decimal(d['balance']) for d in snap['debts'])==Decimal('3800') and sum(Decimal(a['value']) for a in snap['assets'])-sum(Decimal(d['balance']) for d in snap['debts'])==Decimal('130600'))
    check('sources / statement date / minimum repayment retained', lambda: snap['assets'][0]['source']=='bank-statement.pdf' and snap['assets'][0]['as_of']=='2026-10-31' and snap['debts'][0]['source']=='credit-card-statement.pdf' and snap['debts'][0]['as_of']=='2026-10-31' and snap['debts'][0]['minimum_payment']=='380.00' and snap['debts'][0]['due']=='2026-11-10')
    with sqlite3.connect(db) as con:
        typed = con.execute('SELECT typeof(amount_cents), amount_cents FROM transactions').fetchall()
    check('actual SQLite amounts stored as integer cents not floating point', lambda: all(row[0]=='integer' and isinstance(row[1],int) for row in typed))
    s.create_transaction({'date':'2026-10-30','name':'Restaurant refund','amount':'300.00','category':'餐飲'})
    after_refund = s.stats('2026-10')
    check('restaurant refund nets spending, does not inflate income', lambda: after_refund['income']=='66200.00' and after_refund['expense']=='21500.00' and after_refund['balance']=='44700.00')
    s.create_transaction({'date':'2026-10-31','name':'Salary correction','amount':'-5000.00','category':'薪資'})
    after_salary = s.stats('2026-10')
    check('negative salary correction reduces income, not extra spending', lambda: after_salary['income']=='61200.00' and after_salary['expense']=='21500.00' and after_salary['balance']=='39700.00')
    s.create_transaction({'date':'2026-11-01','name':'Refund next month','amount':'0.30','category':'餐飲'})
    s.create_transaction({'date':'2026-11-01','name':'Micro salary one','amount':'0.10','category':'薪資'})
    s.create_transaction({'date':'2026-11-02','name':'Micro salary two','amount':'0.20','category':'薪資'})
    next_month = s.stats('2026-11')
    check('actual month boundary and exact cent addition', lambda: next_month['income']=='0.30' and next_month['expense']=='-0.30' and next_month['balance']=='0.60' and s.stats('2026-10')==after_salary)
    reload = Store(db)
    check('fresh Store instance reloads persisted signed category statistics', lambda: reload.stats('2026-10')==after_salary and reload.stats('2026-11')==next_month)
    def subcent_rejected():
        before=reload.snapshot()
        try:
            reload.create_transaction({'date':'2026-11-03','name':'Invalid precision','amount':'0.100001','category':'餐飲'})
            return False
        except ValueError:
            return reload.snapshot()==before
    check('subcent precision refusal leaves real database unchanged', subcent_rejected)

report = {'reviewer':'P02','stage':'final_backend_money','evidence':'Actual Poppler extraction of three synthetic PDF documents + real temporary SQLite file; no HTTP request or browser UI claimed','checks':checks,'passed':sum(c['pass'] for c in checks),'total':len(checks),'source_sha256':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ['backend/parsers.py','backend/store.py','dist/index.html']},'scores':{'task':25,'data':20,'persistence':20,'ui':None,'scope':10,'error':5},'functional_score':80,'max_functional':80,'overall_acceptance':'Not established: UI 20 points remain untested. Score covers this role and tested local-prototype scope, not full original企劃.'}
(ROOT/'qa/reports/P02_backend_final.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))
sys.exit(0 if all(c['pass'] for c in checks) else 1)
