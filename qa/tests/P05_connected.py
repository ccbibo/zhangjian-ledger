"""P05 direct backend/parser audit; no socket or browser is used."""
import hashlib,json,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from parsers import parse_document
from store import Store

results=[]
def check(id,expected,actual,passed,severity='critical'):
    results.append(dict(id=id,expected=expected,actual=actual,pass_=passed,severity=severity,evidence='Direct parser/SQLite execution in temporary folder; not HTTP or UI'))
with tempfile.TemporaryDirectory(prefix='ledger-p05-') as tmp:
    store=Store(Path(tmp)/'test.sqlite3')
    parsed={}
    for name in ['bank-statement.pdf','credit-card-statement.pdf','payslip.pdf']:
        data=(ROOT/'fixtures/pdf'/name).read_bytes()
        doc=parse_document(data,name);parsed[name]=doc
        staged=store.preview({'filename':name,**doc},hashlib.sha256(data).hexdigest())
        check('preview-'+name,'Uncommitted rows not in income',store.stats('2026-10'),store.stats('2026-10')['income']==('0.00' if name=='bank-statement.pdf' else '66200.00'))
        store.commit(staged['document_id'],staged['transactions'])
    snap=store.snapshot();stats=store.stats('2026-10')
    check('net-salary-income','66200.00 income, not gross72000 + deposits',stats,stats['income']=='66200.00' and stats['expense']=='21800.00' and stats['balance']=='44400.00')
    check('salary-dedup','8 unique rows, salary recorded once',snap['transactions'],len(snap['transactions'])==8 and len([t for t in snap['transactions'] if t['category']=='薪資'])==1)
    check('snapshot-not-cashflow-added','134400 assets minus 3800 debt =130600',{ 'assets':snap['assets'],'debts':snap['debts']},sum(float(a['value']) for a in snap['assets'])-sum(float(d['balance']) for d in snap['debts'])==130600)
    check('minimum-due-preservation','Minimum due 380 retained, not unknown interpreted as zero',snap['debts'][0],float(snap['debts'][0].get('minimum_payment',-1))==380)
    check('asset-source-preservation','Asset file source retained',snap['assets'][0],snap['assets'][0].get('source')=='bank-statement.pdf','major')
    check('debt-source-preservation','Debt file source retained',snap['debts'][0],snap['debts'][0].get('source')=='credit-card-statement.pdf','major')
    check('payroll-metadata','Net salary reconciled and deductions retained in parser metadata',parsed['payslip.pdf']['metadata'],parsed['payslip.pdf']['metadata']['net_pay']==65000 and parsed['payslip.pdf']['metadata']['tax']==5000 and parsed['payslip.pdf']['metadata']['insurance']==2000)
    check('transaction-provenance','Every committed row has actual source and PDF page',snap['transactions'],all(t.get('source') and t.get('page') for t in snap['transactions']))

text=(ROOT/'dist/connected.js').read_text()
static={
 'no_ai_claim': '尚未串接' in text and '生成式AI' in text,
 'ocr_scope_explicit':'尚不支援手機拍照與掃描 OCR' in text,
 'salary_basis_explicit':'薪資單只計實發薪資' in text,
 'strict_duplicate_disclosure':'日期、摘要與金額一致' in text,
 'unknown_min_due_not_zero': '最低應繳 ${fmt(d.minimum_payment||0)}' not in text,
 'snapshot_scope_disclosure':'不同日期快照合併' in text and '不是對你全部資產的認定' in text,
 'refund_handling_disclosed': '退款' in text,
}
report=dict(reviewer='P05',target='connected Python/SQLite edition',scope='statistical copy, salary basis, duplicate/provenance',tests=results,static_checks=static,counts=dict(passed=sum(r['pass_'] for r in results),total=len(results)),ui=None,findings=[r for r in results if not r['pass_']],notes=['No server, socket, browser or real personal data used.','Income is sum of all positive included-category transactions; refunds become income, not negative spending. This policy is currently not explained in connected UI.','Payroll metadata is held in the documents payload, but committed source dialog and exported snapshot do not expose deduction details; copy should distinguish retained DB metadata from accessible user details.'])
out=ROOT/'qa/reports/P05_connected_round3.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(dict(report=str(out),counts=report['counts'],failed=[r['id'] for r in report['findings']],static=static),ensure_ascii=False,indent=2))
