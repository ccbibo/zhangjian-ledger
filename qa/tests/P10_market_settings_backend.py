"""Independent market configure/status contract; no secret file or network."""
import contextlib,hashlib,io,json,os,sys,tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from app import Application
checks=[];DUMMY='P10_DUMMY_MARKET_KEY_NOT_REAL'
def test(name,fn):
    try:checks.append({'name':name,**fn()})
    except Exception as e:checks.append({'name':name,'passed':False,'error_type':type(e).__name__,'error':str(e).replace(DUMMY,'[DUMMY]')})
with tempfile.TemporaryDirectory(prefix='ledger-p10-market-config-') as temp:
    with patch.dict(os.environ,{},clear=True):app=Application(Path(temp)/'test.sqlite3')
    def status():
        code,r=app.dispatch('GET','/api/markets/status')
        return {'passed':code==200 and r['configured'] is False and 'scope' in r and '連線成功' not in r['scope'],'configured':r['configured'],'scope':r['scope']}
    test('Unconfigured GET status reports configuration only, not live market success',status)
    def configure():
        code,r=app.dispatch('POST','/api/markets/configure',{'key':DUMMY})
        _,s=app.dispatch('GET','/api/markets/status')
        return {'passed':code==200 and r['configured'] is True and s['configured'] is True and DUMMY not in json.dumps(r) and DUMMY not in json.dumps(s),'has_secret_in_response':DUMMY in json.dumps(r)}
    test('Configure keeps dummy credential in process memory and omits response/status',configure)
    def backup():
        _,snapshot=app.dispatch('GET','/api/backup');_,market=app.dispatch('GET','/api/markets')
        with app.store._connect() as con:dump='\n'.join(con.iterdump())
        raw=Path(app.store.db_path).read_bytes()
        return {'passed':DUMMY not in json.dumps(snapshot) and DUMMY not in json.dumps(market) and DUMMY not in dump and DUMMY.encode() not in raw,'secret_in_backup':DUMMY in json.dumps(snapshot),'secret_in_market_overview':DUMMY in json.dumps(market),'secret_in_sqlite':DUMMY in dump}
    test('Dummy key absent from financial backup, market overview, all SQLite tables and raw bytes',backup)
    for name,value in [('newline','x\ny'),('CR','x\ry'),('TAB','x\ty'),('NUL','x\x00y'),('DEL','x\x7fy'),('C1-NEL','x\x85y'),('Cf-ZWSP','x\u200by'),('Cf-Bidi','x\u202ey'),('Cs-surrogate','x\ud800y')]:
        def control(value=value):
            before=app.market_key
            try:app.dispatch('POST','/api/markets/configure',{'key':value})
            except ValueError:return {'passed':app.market_key==before,'rejected':True,'prior_config_preserved':app.market_key==before}
            return {'passed':False,'rejected':False,'prior_config_preserved':app.market_key==before}
        test('Control character '+name+' rejected without altering prior key',control)
    def clear():
        _,r=app.dispatch('POST','/api/markets/configure',{'key':''});_,s=app.dispatch('GET','/api/markets/status')
        return {'passed':app.market_key=='' and not r['configured'] and not s['configured']}
    test('Empty key explicitly clears memory configuration and status',clear)
    def prompt():
        captured={}
        def chat(data,context):
            captured.update(question=data['question'],consent=data['consent'],context=context)
            return {'provider':'DeepSeek','answer':'unit fixture only','model':'dummy','scope':'unit only'}
        with patch.object(app.services,'chat',side_effect=chat):code,r=app.dispatch('POST','/api/ai/summary',{'period':'month','consent':True})
        q=captured['question']
        return {'passed':code==200 and captured['consent'] is True and '不固定下週' in q and all(x in q for x in ['發現','數據依據','適用時機','追蹤方式','先判斷資料量']) and 'statistics' in r,'route':'/api/ai/summary','prompt_requires_data_based_timing':('適用時機' in q and '不固定下週' in q),'real_model_called':False}
    test('Production summary prompt requires evidence/action/timing/tracking without fixed next-week prescription',prompt)
report={'reviewer':'P10','method':'Production Application dispatcher + temporary real SQLite, environment cleared before construction; model stub captures prompt only. No secret configuration file, live key, network or browser.','ui':None,'checks':checks,'passed':sum(c['passed'] for c in checks),'total':len(checks),'source_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'backend/app.py',ROOT/'backend/markets.py']}}
out=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'qa/reports/P10_market_settings_backend_r1.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'passed':report['passed'],'total':report['total'],'failures':[c['name'] for c in checks if not c['passed']],'output':str(out)},ensure_ascii=False,indent=2))
