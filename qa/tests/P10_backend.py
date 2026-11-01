"""Independent dispatcher/real PDF/real SQLite + in-memory HTTP protocol checks.
No listening socket or browser is opened.
"""
import base64, hashlib, io, json, sys, tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from app import Application,make_handler

results=[]
def check(name,fn):
    try:
        details=fn();results.append({'test':name,**details})
    except Exception as e:
        results.append({'test':name,'pass':False,'error':repr(e)})

class MemorySocket:
    def __init__(self,request):self.request=io.BytesIO(request);self.sent=bytearray()
    def makefile(self,*args,**kwargs):return self.request
    def sendall(self,data):self.sent.extend(data)
    def settimeout(self,t):pass

with tempfile.TemporaryDirectory(prefix='ledger-p10-') as folder:
    app=Application(Path(folder)/'ledger.sqlite3')
    def upload(name):
        content=(ROOT/'fixtures/pdf'/name).read_bytes()
        return app.dispatch('POST','/api/upload',{'filename':name,'content_base64':base64.b64encode(content).decode()})[1]
    def full_pipeline():
        docs=[]
        for name in ['bank-statement.pdf','credit-card-statement.pdf','payslip.pdf']:
            d=upload(name);docs.append({'name':name,'count':len(d['transactions']),'duplicate':sum(t['duplicate'] for t in d['transactions'])})
            before=len(app.dispatch('GET','/api/state')[1]['transactions'])
            assert before in (0,5,8)
            app.dispatch('POST','/api/commit',{'document_id':d['document_id'],'transactions':d['transactions']})
        snap=app.dispatch('GET','/api/state')[1];stats=app.dispatch('GET','/api/stats?month=2026-10')[1]
        return {'pass':len(snap['transactions'])==8 and stats['income']=='66200.00' and stats['expense']=='21800.00' and stats['balance']=='44400.00','documents':docs,'stats':stats,'snapshot_assets':snap['assets'],'snapshot_debts':snap['debts']}
    check('Real PDF upload→preview→commit→SQLite→stats matches independent oracle',full_pipeline)
    def minimum():
        debt=app.dispatch('GET','/api/state')[1]['debts'][0]
        return {'pass':str(debt.get('minimum_payment')) in ('380','380.0','380.00'),'actual':debt.get('minimum_payment'),'expected':'380.00','frontend_fallback':'d.minimum_payment || 0'}
    check('Credit-card minimum payment survives parse→commit→state',minimum)
    def sources():
        snap=app.dispatch('GET','/api/state')[1]
        return {'pass':all(isinstance(a.get('source'),str) and a['source'] for a in snap['assets']+snap['debts']),'assets':snap['assets'],'debts':snap['debts']}
    check('Snapshot source survives to accounts screen',sources)
    def reimport():
        reused=[upload(n) for n in ['bank-statement.pdf','credit-card-statement.pdf','payslip.pdf']]
        return {'pass':all(d['status']=='committed' for d in reused) and len(app.dispatch('GET','/api/state')[1]['transactions'])==8,'statuses':[d['status'] for d in reused]}
    check('Reupload does not add duplicate transactions',reimport)
    def atomic_bad():
        content=b'date,description,amount,category\n2026-10-01,Test1,-1,Food\n2026-10-02,Test2,-2,Food\n'
        d=app.dispatch('POST','/api/upload',{'filename':'atomic.csv','content_base64':base64.b64encode(content).decode()})[1]
        d['transactions'][1]['amount']='NaN';before=app.dispatch('GET','/api/state')[1]
        try:app.dispatch('POST','/api/commit',{'document_id':d['document_id'],'transactions':d['transactions']})
        except ValueError:pass
        else:return {'pass':False,'reason':'invalid money accepted'}
        return {'pass':before==app.dispatch('GET','/api/state')[1]}
    check('Invalid batch is rejected without partial SQLite commit',atomic_bad)
    def year_boundary():
        content=b'date,description,amount,category\n1999-10-01,Old record,1,Salary\n'
        d=app.dispatch('POST','/api/upload',{'filename':'1999.csv','content_base64':base64.b64encode(content).decode()})[1]
        app.dispatch('POST','/api/commit',{'document_id':d['document_id'],'transactions':d['transactions']})
        try:s=app.dispatch('GET','/api/stats?month=1999-10')[1]
        except ValueError as e:return {'pass':False,'reason':'accepted transaction cannot be queried through API','error':str(e)}
        return {'pass':s['income']=='1.00','stats':s}
    check('Every accepted transaction year is queryable through stats API',year_boundary)
    def rejected_pdf():
        errors=[]
        for name in ['unknown-document.pdf','blank-financial-content.pdf']:
            try:upload(name)
            except ValueError as e:errors.append(str(e))
        return {'pass':len(errors)==2,'errors':errors}
    check('Unsupported PDF rejected instead of fabricated OCR',rejected_pdf)
    def request(raw):
        fake=MemorySocket(raw)
        handler=make_handler(app)
        handler(fake,('127.0.0.1',12345),SimpleNamespace(server_address=('127.0.0.1',8791)))
        response=bytes(fake.sent);head,body=response.split(b'\r\n\r\n',1)
        return int(head.split(b' ')[1]),body.decode(errors='replace'),head.decode()
    for label,raw,expected in [
        ('Host rejection',b'GET /api/state HTTP/1.1\r\nHost: attacker.test\r\n\r\n',403),
        ('Origin rejection',b'GET /api/state HTTP/1.1\r\nHost: 127.0.0.1:8791\r\nOrigin: https://attacker.test\r\n\r\n',403),
        ('Same-origin API',b'GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:8791\r\nOrigin: http://127.0.0.1:8791\r\n\r\n',200),
        ('Static traversal rejection',b'GET /../backend/app.py HTTP/1.1\r\nHost: 127.0.0.1:8791\r\n\r\n',404),
        ('Encoded traversal rejection',b'GET /%2e%2e/backend/app.py HTTP/1.1\r\nHost: 127.0.0.1:8791\r\n\r\n',404),
        ('Root serves connected entry',b'GET / HTTP/1.1\r\nHost: localhost:8791\r\n\r\n',200),
        ('JSON array rejected',b'POST /api/upload HTTP/1.1\r\nHost: 127.0.0.1:8791\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n[]',400),
        ('Bad Content-Length rejected',b'POST /api/upload HTTP/1.1\r\nHost: 127.0.0.1:8791\r\nContent-Type: application/json\r\nContent-Length: nope\r\n\r\n',400),
    ]:
        def run(raw=raw,expected=expected):
            status,body,head=request(raw)
            return {'pass':status==expected,'status':status,'expected':expected,'body':body[:300],'headers':head}
        check('In-memory HTTP: '+label,run)
report={'reviewer':'P10','evidence':'Real Poppler PDF extraction + Application dispatcher + temporary real SQLite; BaseHTTPRequestHandler over in-memory rfile/sendall, no real socket/browser','passed':sum(r['pass'] for r in results),'total':len(results),'sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'backend/app.py',ROOT/'backend/store.py',ROOT/'backend/parsers.py',ROOT/'dist/connected.js']},'results':results}
out=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'qa/reports/P10_backend_raw.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({'passed':report['passed'],'total':report['total'],'failed':[r['test'] for r in results if not r['pass']],'report':str(out)},ensure_ascii=False,indent=2))
