"""Scope/privacy service checks. No network and no real credential is used."""
import base64
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app import Application

cases = []
calls = []

class MockResponse:
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self, *_): return json.dumps({'choices':[{'message':{'content':'P08 mocked generated response'}}]}).encode()

def no_network_capture(request, timeout):
    calls.append({'url':request.full_url,'payload':json.loads(request.data),'timeout':timeout})
    return MockResponse()

with tempfile.TemporaryDirectory(prefix='ledger-p08-v05-') as folder:
    app = Application(Path(folder) / 'ledger.sqlite3')
    app.services.configure({'key':'','model':'deepseek-chat'})
    with patch('services.safe_open', no_network_capture):
        try:
            app.dispatch('POST','/api/ai/chat',{'month':'2026-10','question':'P08 question','consent':True})
            rejected = False
        except ValueError: rejected = True
        cases.append({'id':'P08-S-01','name':'No key prevents model network request','pass':rejected and not calls})
        app.services.configure({'key':'P08-SYNTHETIC-NOT-A-REAL-KEY','model':'deepseek-chat'})
        for consent in [False,None,'true']:
            try:
                app.dispatch('POST','/api/ai/chat',{'month':'2026-10','question':'P08 question','consent':consent})
                rejected = False
            except ValueError: rejected = True
            cases.append({'id':'P08-S-02-'+str(consent),'name':'Exact boolean consent required','pass':rejected and not calls})
        app.dispatch('POST','/api/transaction/create',{'transaction':{'date':'2026-10-01','name':'P08_PRIVATE_MERCHANT_MARKER','amount':'-123.45','category':'餐飲'}})
        code, result = app.dispatch('POST','/api/ai/chat',{'month':'2026-10','question':'P08_USER_QUESTION_MARKER','consent':True})
        sent = json.dumps(calls[-1]['payload'],ensure_ascii=False)
        cases.append({'id':'P08-S-03','name':'Only financial aggregates, not merchant rows, appended by app','pass':'P08_PRIVATE_MERCHANT_MARKER' not in sent and '123.45' in sent,'note':'Mocked transport, not a real provider call'})
        cases.append({'id':'P08-S-04','name':'User question transmitted as user message','pass':'P08_USER_QUESTION_MARKER' in sent,'implication':'UI must disclose the question itself is also sent'})
        backup = json.dumps(app.dispatch('GET','/api/backup')[1],ensure_ascii=False)
        status = json.dumps(app.dispatch('GET','/api/ai/status')[1],ensure_ascii=False)
        cases.append({'id':'P08-S-05','name':'Configured key is absent from backup/status','pass':'P08-SYNTHETIC-NOT-A-REAL-KEY' not in backup+status})
    with patch('newsroom.collect', side_effect=OSError('P08 simulated offline')):
        app.newsroom.refresh(asynchronous=False)
        news = app.dispatch('GET','/api/news')[1]
        cases.append({'id':'P08-S-06','name':'Actual RSS failure is explicit and has no fabricated article','pass':news['job']['status']=='failed' and len(news['channels'])==2 and all(not c['items'] and c['errors'] for c in news['channels'].values()),'result':news})
    holding = app.dispatch('POST','/api/items/save',{'kind':'holding','item':{'name':'P08 Manual USD','asset_class':'美股與ETF','currency':'USD','quantity':'100','cost_price':'90','price':'100','fx':'32','as_of':'2026-10-31'}})[1]
    cases.append({'id':'P08-S-07','name':'True SQLite manual holding is valued using entered prices and FX','pass':holding['value']=='320000.00' and holding['cost']=='288000.00'})
    app.dispatch('POST','/api/items/save',{'kind':'account','item':{'name':'P08 Account','asset_class':'現金與存款','value':'100','as_of':'2026-10-31'}})
    try:
        app.dispatch('POST','/api/items/save',{'kind':'account','item':{'name':' ｐ０８ ａｃｃｏｕｎｔ ','asset_class':'現金與存款','value':'100','as_of':'2026-10-31'}})
        rejected=False
    except ValueError: rejected=True
    cases.append({'id':'P08-S-08','name':'SQLite name duplicate rejects NFKC/case/space variant','pass':rejected})
    reopened=Application(Path(folder)/'ledger.sqlite3')
    cases.append({'id':'P08-S-09','name':'Planning values survive SQLite application reload','pass':len(reopened.planning.state()['items'])==2})

report={'role':'P08 v05 real SQLite plus mocked network service honesty checks','evidence':'Application.dispatch and temporary SQLite; service network replaced by deterministic mock. No HTTP server, browser, external RSS, paid model or real key.','hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'backend/services.py',ROOT/'backend/app.py',ROOT/'backend/planning.py']},'cases':cases,'passed':sum(bool(c['pass']) for c in cases),'total':len(cases),'model_request_shape':{'url':calls[0]['url'],'message_roles':[m['role'] for m in calls[0]['payload']['messages']],'user_question_included':True,'merchant_detail_included':False}}
target=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'qa/reports/P08_v05_services_raw.json'
target.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
