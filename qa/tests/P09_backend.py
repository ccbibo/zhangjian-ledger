"""P09 independent PDF -> dispatcher -> real SQLite integration; no HTTP socket."""
import base64
import copy
import hashlib
import json
import sqlite3
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app import Application

ORACLE = json.loads((ROOT / 'fixtures/expected.json').read_text())
ROUND = sys.argv[1] if len(sys.argv) > 1 else 'r1'
REPORT = ROOT / ('qa/reports/P09_backend_' + ROUND + '.json')
results = []

def record(name, passed, observed=None, expected=None):
    results.append({'name': name, 'passed': bool(passed), 'observed': observed, 'expected': expected})

def upload(app, filename):
    data = (ROOT / 'fixtures/pdf' / filename).read_bytes()
    return app.dispatch('POST', '/api/upload', {'filename': filename, 'content_base64': base64.b64encode(data).decode()})

def stats(app, month='2026-10'):
    status, data = app.dispatch('GET', '/api/stats?month=' + month)
    assert status == 200
    return data

def no_change_rejection(app, name, fn):
    before = app.store.snapshot()
    try:
        result = fn()
        record(name, False, {'unexpected_success': result, 'unchanged': before == app.store.snapshot()}, 'ValueError with unchanged committed snapshot')
    except ValueError as error:
        record(name, before == app.store.snapshot(), {'error': str(error), 'unchanged': before == app.store.snapshot()}, 'ValueError with unchanged committed snapshot')
    except Exception as error:
        record(name, False, {'exception': type(error).__name__, 'error': str(error)}, 'ValueError with unchanged committed snapshot')

with tempfile.TemporaryDirectory(prefix='ledger-p09-sqlite-') as temporary:
    db = Path(temporary) / 'ledger.sqlite3'
    app = Application(db)
    record('fresh database zero committed statistics', stats(app) == {'month':'2026-10','income':'0.00','expense':'0.00','balance':'0.00','transaction_count':0,'excluded_count':0}, stats(app))
    documents = []
    for filename in ('bank-statement.pdf', 'credit-card-statement.pdf', 'payslip.pdf'):
        status, pending = upload(app, filename)
        expected = ORACLE['documents'][filename]
        record(filename + ' preview kind and row count', status == 200 and pending['kind'] == expected['kind'] and len(pending['transactions']) == expected['transaction_count'], {'status':status,'kind':pending['kind'],'rows':len(pending['transactions']),'reused':pending['reused']}, {'kind':expected['kind'],'rows':expected['transaction_count']})
        observed_rows = [{k: t[k] for k in ('date','name','amount','category','page')} for t in pending['transactions']]
        expected_rows = [{**{k:t[k] for k in ('date','name','category','page')},'amount':format(Decimal(str(t['amount'])),'.2f')} for t in expected['transactions']]
        record(filename + ' oracle financial fields and source page', observed_rows == expected_rows, observed_rows, expected_rows)
        record(filename + ' source filename retained', all(t['source'] == filename for t in pending['transactions']), [t['source'] for t in pending['transactions']], filename)
        before = stats(app)
        record(filename + ' preview contributes no unconfirmed statistics', before['transaction_count'] == (0 if not documents else sum(d['committed_count'] for d in documents)), before)
        rows = copy.deepcopy(pending['transactions'])
        for row in rows:
            row['keep'] = not row['duplicate']
        status, confirmed = app.dispatch('POST','/api/commit', {'document_id':pending['document_id'],'transactions':rows})
        record(filename + ' commit confirmed', status == 200 and confirmed['status'] == 'committed', {'count':confirmed['committed_count'],'duplicates':sum(t['duplicate'] for t in pending['transactions'])})
        documents.append(confirmed)
    combined = ORACLE['combined_after_review_and_confirm']
    actual = stats(app)
    expected_stats = {'month':'2026-10','income':'66200.00','expense':'21800.00','balance':'44400.00','transaction_count':8,'excluded_count':2}
    record('combined independent oracle cashflow and 8 unique transactions', actual == expected_stats, actual, expected_stats)
    snapshot = app.store.snapshot()
    record('asset and debt snapshots match source statements', sum(Decimal(a['value']) for a in snapshot['assets']) == Decimal(combined['account_assets']) and sum(Decimal(a['balance']) for a in snapshot['debts']) == Decimal(combined['credit_card_liabilities']), {'assets':snapshot['assets'],'debts':snapshot['debts']}, {'assets':combined['account_assets'],'debts':combined['credit_card_liabilities']})
    with sqlite3.connect(db) as connection:
        payroll_payloads = [json.loads(row[0]) for row in connection.execute("select payload from documents") if json.loads(row[0]).get('kind') == 'payslip']
    meta = payroll_payloads[0]['metadata']
    expected_payroll = combined['saved_salary_metadata']
    record('payslip metadata persisted without extra withholding expenditure', all(meta[k] == v for k,v in expected_payroll.items()) and actual['expense'] == '21800.00', meta, expected_payroll)
    restarted = Application(db)
    record('new Application on same SQLite preserves committed data', restarted.store.snapshot() == snapshot and stats(restarted) == expected_stats, stats(restarted), expected_stats)
    app = restarted
    for filename in ('bank-statement.pdf','credit-card-statement.pdf','payslip.pdf'):
        status, repeated = upload(app, filename)
        record(filename + ' same-byte reimport reused committed document', status == 200 and repeated['reused'] and repeated['status'] == 'committed' and stats(app) == expected_stats, {'reused':repeated['reused'],'status':repeated['status'],'statistics':stats(app)})
    for filename in ('unknown-document.pdf','blank-financial-content.pdf'):
        no_change_rejection(app, filename + ' rejected without financial changes', lambda filename=filename: upload(app, filename))
    csv_content=(ROOT/'fixtures/transactions.csv').read_bytes()
    status, csv_preview = app.dispatch('POST','/api/upload',{'filename':'transactions.csv','content_base64':base64.b64encode(csv_content).decode()})
    record('CSV overlap after PDFs flags all 8 duplicate records', len(csv_preview['transactions']) == 8 and all(t['duplicate'] and not t['keep'] for t in csv_preview['transactions']), {'count':len(csv_preview['transactions']),'duplicates':sum(t['duplicate'] for t in csv_preview['transactions'])})
    status, zero_commit=app.dispatch('POST','/api/commit',{'document_id':csv_preview['document_id'],'transactions':csv_preview['transactions']})
    record('all-excluded duplicate CSV commit inserts zero', zero_commit['committed_count']==0 and stats(app)==expected_stats, {'committed_count':zero_commit['committed_count'],'statistics':stats(app)})
    no_change_rejection(app,'invalid base64 rejected',lambda:app.dispatch('POST','/api/upload',{'filename':'invalid.pdf','content_base64':'###'}))
    no_change_rejection(app,'empty decoded file rejected',lambda:app.dispatch('POST','/api/upload',{'filename':'empty.pdf','content_base64':''}))
    no_change_rejection(app,'oversized decoded file rejected',lambda:app.dispatch('POST','/api/upload',{'filename':'large.pdf','content_base64':base64.b64encode(b'a'*(5*1024*1024+1)).decode()}))
    no_change_rejection(app,'invalid month rejected',lambda:app.dispatch('GET','/api/stats?month=2026-99'))
    meal = next(t for t in snapshot['transactions'] if t['name']=='Meals')
    for bad in ('0x10','1e3','NaN','Infinity','-0.001','1000000000000.01'):
        no_change_rejection(app,'invalid monetary edit rejected '+bad,lambda bad=bad:app.dispatch('POST','/api/transaction',{'id':meal['id'],'transaction':{'amount':bad}}))
    status, edited = app.dispatch('POST','/api/transaction',{'id':meal['id'],'transaction':{'amount':'-1300.25','source':'forged.pdf','page':999,'line':999}})
    after = stats(app)
    record('valid decimal edit changes exact cents',status==200 and after['expense']=='21900.25' and after['balance']=='44299.75', after)
    tx=edited['transaction']
    record('edit cannot rewrite source provenance',all(tx.get(k)==meal.get(k) for k in ('source','page','line','id','document_id')), {k:tx.get(k) for k in ('source','page','line','id','document_id')}, {k:meal.get(k) for k in ('source','page','line','id','document_id')})
    # Contract revised in final app: POST /api/transaction/delete.
    # Previous DELETE route failure remains in immutable P09_backend_r1.json.
    delete_status, delete_result=app.dispatch('POST','/api/transaction/delete',{'id':meal['id']})
    record('POST delete dispatcher route availability',delete_status==200,{'status':delete_status,'deleted':delete_result.get('deleted')},'200 for POST /api/transaction/delete')
    deleted=delete_result
    after_delete=stats(app)
    record('Store delete exact totals and durable tombstone',deleted['deleted'] and after_delete['transaction_count']==7 and after_delete['expense']=='20600.00' and after_delete['balance']=='45600.00',after_delete)
    with sqlite3.connect(db) as connection:
        audits=[{'action':row[0],'has_previous':row[1] is not None,'has_next':row[2] is not None} for row in connection.execute('select action,previous_payload,next_payload from transaction_audit where transaction_id=?',(meal['id'],))]
    record('SQLite edit and delete audit kept', [a['action'] for a in audits]==['edit','delete'],audits)
    final=Application(db)
    record('edit/delete durable after new Application',stats(final)==after_delete and len(final.store.snapshot()['transactions'])==7,stats(final))
    no_change_rejection(final,'second delete rejects missing id',lambda:final.store.delete_transaction(meal['id']))
    # New final-round scenarios. They are additive; original R1 raw evidence retained.
    payroll_id=documents[2]['document_id']
    doc_status, payroll_doc=final.dispatch('GET','/api/document?id='+payroll_id)
    record('payroll metadata obtainable through document dispatcher after restart',doc_status==200 and payroll_doc['filename']=='payslip.pdf' and payroll_doc['status']=='committed' and all(payroll_doc['metadata'][k]==v for k,v in expected_payroll.items()),payroll_doc,expected_payroll)
    missing_status, missing_doc=final.dispatch('GET','/api/document?id=nonexistent-synthetic-id')
    record('unknown source document returns explicit 404',missing_status==404,{'status':missing_status,'body':missing_doc})
    debt=next(d for d in final.store.snapshot()['debts'] if d['account']=='card:TEST Credit Card 4242')
    record('credit-card minimum payment and as_of survive normalization',debt.get('minimum_payment')=='380.00' and debt.get('as_of')=='2026-10-31',debt)
    # Store-level synthetic snapshot variant, distinguished from the actual PDF fixture path.
    latest_before=final.store.snapshot()
    old_doc={'kind':'synthetic-old-snapshot','filename':'synthetic-old-snapshot.json','transactions':[],
             'assets':[{'account':'bank:TEST Bank 001','name':'TEST Bank 001','value':'100.00','currency':'TWD','as_of':'2026-09-30'}],
             'debts':[{'account':'card:TEST Credit Card 4242','name':'TEST Credit Card 4242','balance':'99.00','payment':'99.00','as_of':'2026-09-30','due':'2026-10-10'}],
             'metadata':{},'warnings':[]}
    old_preview=final.store.preview(old_doc,hashlib.sha256(b'p09-synthetic-old-snapshot').hexdigest())
    record('older snapshot preview warns before confirmation',len(old_preview['warnings'])==2 and all('較舊' in w for w in old_preview['warnings']),old_preview['warnings'])
    _,old_commit=final.dispatch('POST','/api/commit',{'document_id':old_preview['document_id'],'transactions':[]})
    latest_after=final.store.snapshot()
    record('older as_of cannot roll back asset or debt snapshot',latest_before['assets']==latest_after['assets'] and latest_before['debts']==latest_after['debts'] and len(old_commit.get('warnings',[]))>=2,{'assets':latest_after['assets'],'debts':latest_after['debts'],'warnings':old_commit.get('warnings',[])})
    missing_dates=copy.deepcopy(old_doc);missing_dates['filename']='synthetic-undated-snapshot.json'
    for item in missing_dates['assets']+missing_dates['debts']:item.pop('as_of')
    undated=final.store.preview(missing_dates,hashlib.sha256(b'p09-synthetic-undated-snapshot').hexdigest())
    _,undated_commit=final.dispatch('POST','/api/commit',{'document_id':undated['document_id'],'transactions':[]})
    record('undated snapshot does not replace known dated records',latest_before['assets']==final.store.snapshot()['assets'] and latest_before['debts']==final.store.snapshot()['debts'] and all('缺少快照日期' in w for w in undated_commit.get('warnings',[])),undated_commit.get('warnings',[]))
    # Refund/correction oracle: independent arithmetic, API upload CSV -> commit -> stats.
    refund_app=Application(Path(temporary)/'refund.sqlite3')
    refund_csv=('date,description,amount,category\n2026-10-01,Salary,1000,薪資\n2026-10-02,Meals,-100,餐飲\n2026-10-03,Meal refund,25,餐飲\n2026-10-04,Salary correction,-100,薪資\n2026-09-30,Previous month refund,50,餐飲\n2026-10-05,Internal transfer,900,轉帳\n').encode()
    _,rp=refund_app.dispatch('POST','/api/upload',{'filename':'refunds.csv','content_base64':base64.b64encode(refund_csv).decode()})
    refund_app.dispatch('POST','/api/commit',{'document_id':rp['document_id'],'transactions':rp['transactions']})
    refund_stats=stats(refund_app)
    record('spending refund reduces spending and salary correction reduces income',refund_stats['income']=='900.00' and refund_stats['expense']=='75.00' and refund_stats['balance']=='825.00' and refund_stats['excluded_count']==1,refund_stats,{'income':'900.00','expense':'75.00','balance':'825.00','excluded_count':1})
    previous_refund=stats(refund_app,'2026-09')
    record('refund booked in its actual month without inventing income',previous_refund['income']=='0.00' and previous_refund['expense']=='-50.00' and previous_refund['balance']=='50.00',previous_refund,{'income':'0.00','expense':'-50.00','balance':'50.00'})
    # Persist only synthetic test evidence, not database paths or source document bytes.
    report={'reviewer':'P09','round':ROUND,'contract_note':'Final uses POST /api/transaction/delete; R1 DELETE-route failure retained unchanged. New old/undated snapshot variants use Store.preview synthetic documents; all main supported document flows use actual PDF bytes via API dispatcher.','evidence_type':'真PDF＋API dispatcher整合，非HTTP socket/瀏覽器','synthetic_fixtures':True,'oracle':'fixtures/expected.json','code_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'backend/app.py',ROOT/'backend/store.py',ROOT/'backend/parsers.py']},'fixture_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'fixtures/pdf').glob('*.pdf')},'results':results,'passed':sum(r['passed'] for r in results),'total':len(results),'limitations':['Text PDFs only; no scanned-image OCR.','Application.dispatch bypasses HTTP host/origin/header middleware; no socket server started.','Actual temporary on-disk SQLite exercised; test database removed after run.']}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))
