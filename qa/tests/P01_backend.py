"""P01 independent parser regression. Uses real Poppler; no external Python deps."""
import importlib.util
import json
import hashlib
import sys
from pathlib import Path
from unittest.mock import patch
import subprocess

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('ledger_parsers', ROOT / 'backend/parsers.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
checks = []

def check(name, test):
    try:
        assert test() is True
        checks.append({'name': name, 'pass': True})
    except Exception as e:
        checks.append({'name': name, 'pass': False, 'error': repr(e)})

def rejected(content, name, expected=''):
    try:
        p.parse_document(content, name)
        return False
    except p.DocumentParseError as e:
        return expected in str(e)

def csv(text):
    return p.parse_document(text.encode(), 'test.csv')

def make_pdf(texts, count=1):
    objects = [None, b'<< /Type /Catalog /Pages 2 0 R >>', None,
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    page_ids = []
    for _ in range(count):
        stream = b'BT /F1 10 Tf 40 760 Td 14 TL\n'
        for text in texts:
            literal = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            stream += ('(' + literal + ') Tj T*\n').encode()
        stream += b'ET'
        page_id = len(objects)
        stream_id = page_id + 1
        objects += [f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {stream_id} 0 R >>'.encode(),
                    b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
        page_ids.append(page_id)
    objects[2] = f'<< /Type /Pages /Kids [{" ".join(str(i)+" 0 R" for i in page_ids)}] /Count {len(page_ids)} >>'.encode()
    data = b'%PDF-1.4\n'
    offsets = [0]
    for i, obj in enumerate(objects[1:], 1):
        offsets.append(len(data)); data += str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    xref = len(data)
    data += f'xref\n0 {len(objects)}\n0000000000 65535 f \n'.encode()
    for offset in offsets[1:]: data += f'{offset:010d} 00000 n \n'.encode()
    data += f'trailer\n<< /Size {len(objects)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    return data

bank = p.parse_document((ROOT/'fixtures/pdf/bank-statement.pdf').read_bytes(), 'bank.pdf')
card = p.parse_document((ROOT/'fixtures/pdf/credit-card-statement.pdf').read_bytes(), 'card.pdf')
pay = p.parse_document((ROOT/'fixtures/pdf/payslip.pdf').read_bytes(), 'pay.pdf')
check('real bank PDF all 5 rows with second-page provenance', lambda: len(bank['transactions']) == 5 and bank['transactions'][-1]['page']==2 and bank['assets'][0]['value']==134400)
check('real card PDF debt and 3 charges reconcile; statement date is not payment deadline', lambda: card['debts'][0]['balance']==3800 and card['debts'][0]['due']=='2026-11-10' and card['debts'][0]['as_of']=='2026-10-31' and card['debts'][0]['minimum_payment']==380 and card['debts'][0]['source']=='card.pdf' and len(card['transactions'])==3)
check('real payslip generates only net pay', lambda: len(pay['transactions'])==1 and pay['transactions'][0]['amount']==65000 and pay['metadata']['reconciled'])
check('bank and payslip salary share exact dedupe identity', lambda: tuple(bank['transactions'][0][k] for k in ['date','name','amount']) == tuple(pay['transactions'][0][k] for k in ['date','name','amount']))
check('CSV multiline exact physical row', lambda: [r['line'] for r in csv('date,description,amount\n2026-10-01,"multi\nline",-0.10\n2026-10-02,next,-0.20')['transactions']]==[2,4])
check('CSV extra columns rejected', lambda: rejected(b'date,description,amount\n2026-10-01,test,1,234', 'test.csv', '欄數'))
check('CSV duplicate amount rejected', lambda: rejected(b'date,description,amount,amount\n2026-10-01,test,1,234','test.csv','重複'))
check('CSV alias duplicate rejected', lambda: rejected('date,日期,description,amount\n2026-10-01,2026-10-01,test,1'.encode(),'test.csv','歧義'))
check('CSV hex/exponent/fractional cent rejected', lambda: all(rejected(('date,description,amount\n2026-10-01,test,'+v).encode(),'test.csv') for v in ['0x10','1e3','0.001','NaN','Infinity']))
check('CSV grouping exact Decimal cents', lambda: csv('date,description,amount,category\n2026-10-01,test,"-1,234.56",Food')['transactions'][0]['amount_cents']==-123456)
check('invalid dates rejected', lambda: rejected(b'date,description,amount\n2026-02-29,test,12','test.csv','日期'))
check('unknown PDF rejected with no fake OCR', lambda: rejected((ROOT/'fixtures/pdf/unknown-document.pdf').read_bytes(),'unknown.pdf','尚未支援'))
check('pure blank PDF explicitly rejected', lambda: rejected(make_pdf([]),'blank.pdf','OCR'))
check('over 20-page PDF rejected', lambda: rejected(make_pdf(['BANK STATEMENT'],21),'huge.pdf','20 頁'))
check('file larger than 5MB rejected', lambda: rejected(b'a'*(5*1024*1024+1),'large.csv','5 MB'))
check('wrong extension/content rejected', lambda: rejected(b'%PDF-1.4\n','wrong.csv','不符'))
check('image unsupported instead of fake OCR', lambda: rejected(b'png','scan.png','OCR'))
bank_text=['BANK STATEMENT','Currency: TWD','Account: TEST 1','Opening balance: 100','Closing balance: 90','Date | Description | Amount | Category','2026-10-01 | Food | -10 | Food']
check('local generated real PDF parses supported bank', lambda: p.parse_document(make_pdf(bank_text),'bank.pdf')['assets'][0]['value']==90)
check('bank reconcile mismatch rejected', lambda: rejected(make_pdf([t.replace('Closing balance: 90','Closing balance: 91') for t in bank_text]),'bad.pdf','核對不符'))
check('unknown table date cannot silently drop', lambda: rejected(make_pdf(bank_text+['10-10-2026 | Zero | 0 | Food']),'bad-date.pdf','日期格式'))
check('conflicting balance field rejected', lambda: rejected(make_pdf(bank_text+['Closing balance: 10']),'conflict.pdf','矛盾'))
check('non-TWD does not invent conversion', lambda: rejected(make_pdf([t.replace('Currency: TWD','Currency: USD') for t in bank_text]),'usd.pdf','TWD'))
check('missing account does not overwrite another asset', lambda: not p.parse_document(make_pdf([t for t in bank_text if not t.startswith('Account:')]),'no-account.pdf')['assets'])
salary=['PAYSLIP','Currency: TWD','Pay date: 2026-10-28','Gross pay: 72000','Tax: 5000','Insurance: 2000','Net pay: 65000']
check('salary mismatch refuses fake income', lambda: rejected(make_pdf([t.replace('Net pay: 65000','Net pay: 66000') for t in salary]),'bad-pay.pdf','核對不符'))
with patch.object(p.subprocess,'run',side_effect=subprocess.TimeoutExpired('pdfinfo',10)):
    check('timeout explicitly bounded/refused', lambda: rejected(b'%PDF-1.4\n','slow.pdf','10 秒'))
with patch.object(p.shutil,'which',return_value=None):
    check('missing Poppler explained', lambda: rejected(b'%PDF-1.4\n','document.pdf','Poppler'))
report={'reviewer':'P01','stage':'backend_parser','sha256':hashlib.sha256((ROOT/'backend/parsers.py').read_bytes()).hexdigest(),'evidence':'Real Poppler PDF extraction + Python stdlib; only timeout/missing dependency checks are mocks','passed':sum(c['pass'] for c in checks),'total':len(checks),'checks':checks}
(ROOT/'qa/reports/P01_backend_raw.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))
sys.exit(0 if all(c['pass'] for c in checks) else 1)
