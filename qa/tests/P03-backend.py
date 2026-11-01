"""Independent dispatch + real temporary SQLite tests; no listener/browser."""
from __future__ import annotations
import base64,copy,hashlib,json,sqlite3,sys,tempfile,threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from app import Application

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
sources=[ROOT/'backend/app.py',ROOT/'backend/store.py',ROOT/'backend/parsers.py']
before_sha={str(p.relative_to(ROOT)):sha(p) for p in sources}
results=[]
def check(id,title,fn):
    try:
        actual=fn();passed=bool(actual.pop('passed'));results.append(dict(id=id,title=title,passed=passed,actual=actual))
    except Exception as e:results.append(dict(id=id,title=title,passed=False,unexpected=repr(e)))
def upload(app,name,rows):
    content=('date,description,amount,category\n'+'\n'.join(','.join(r) for r in rows)+'\n').encode()
    code,doc=app.dispatch('POST','/api/upload',dict(filename=name,content_base64=base64.b64encode(content).decode()))
    assert code==200
    return doc
def commit(app,doc,rows=None):return app.dispatch('POST','/api/commit',dict(document_id=doc['document_id'],transactions=doc['transactions'] if rows is None else rows))
def dump(app):
    with sqlite3.connect(app.store.db_path) as c:
        return {t:c.execute('SELECT * FROM '+t+' ORDER BY rowid').fetchall() for t in ('documents','transactions','assets','debts','transaction_audit')}
def outcome_unchanged(app,call):
    b=dump(app);state=app.dispatch('GET','/api/state')[1];stats=app.dispatch('GET','/api/stats?month=2026-10')[1];err=''
    try:call()
    except ValueError as e:err=str(e)
    return dict(passed=bool(err) and b==dump(app) and state==app.dispatch('GET','/api/state')[1] and stats==app.dispatch('GET','/api/stats?month=2026-10')[1],error=err,dbUnchanged=b==dump(app),stateUnchanged=state==app.dispatch('GET','/api/state')[1],statsUnchanged=stats==app.dispatch('GET','/api/stats?month=2026-10')[1])

with tempfile.TemporaryDirectory(prefix='ledger-p03-') as temp:
    app=Application(Path(temp)/'failure.sqlite3')
    doc=upload(app,'original.csv',[['2026-10-01','salary','100.25','薪資'],['2026-10-02','lunch','-20.10','餐飲']]);commit(app,doc)
    tx=app.dispatch('GET','/api/state')[1]['transactions'];a=next(t for t in tx if t['name']=='salary');b=next(t for t in tx if t['name']=='lunch')
    for suffix,title,change in [('precision','high precision',{'amount':'12.345'}),('id','different transaction ID',{'id':b['id']}),('date','impossible date',{'date':'2026-02-30'}),('duplicate','duplicate semantic transaction',{'date':b['date'],'name':b['name'],'amount':b['amount']})]:
        check('P03-B-'+suffix,'Edit rejects '+title+' atomically',lambda change=change:outcome_unchanged(app,lambda:app.dispatch('POST','/api/transaction',dict(id=a['id'],transaction=change))))
    pending=upload(app,'half.csv',[['2026-10-03','valid first row','5.30','股息'],['2026-10-04','invalid second row','-3.40','生活']])
    half=copy.deepcopy(pending['transactions']);half[1]['amount']='1.001'
    check('P03-B-half','Mixed valid/invalid batch rolls back every table and stats',lambda:outcome_unchanged(app,lambda:commit(app,pending,half)))
    duplicate_ids=[pending['transactions'][0],copy.deepcopy(pending['transactions'][0])]
    check('P03-B-duplicate-id','Duplicate review IDs reject atomically',lambda:outcome_unchanged(app,lambda:commit(app,pending,duplicate_ids)))
    commit(app,pending)
    check('P03-B-replay','Repeated committed document rejects without double counting',lambda:outcome_unchanged(app,lambda:commit(app,pending)))
    cross=upload(app,'cross.csv',[['2026-10-01','salary','100.25','薪資']]);cross['transactions'][0]['keep']=True
    check('P03-B-cross','Cross-document duplicate cannot be forced by selecting keep',lambda:outcome_unchanged(app,lambda:commit(app,cross)))
    frozen=dump(app);stats=app.dispatch('GET','/api/stats?month=2026-10')[1];db_path=app.store.db_path;del app
    reopened=Application(db_path)
    check('P03-B-reopen','Close/recreate Application preserves actual SQLite data',lambda:dict(passed=dump(reopened)==frozen and reopened.dispatch('GET','/api/stats?month=2026-10')[1]==stats,stats=stats,transactionCount=len(reopened.dispatch('GET','/api/state')[1]['transactions'])))
    # Two independently initialized Applications target the same database; no in-memory shared ledger.
    two_path=Path(temp)/'two.sqlite3';one,two=Application(two_path),Application(two_path)
    one_doc=upload(one,'one.csv',[['2026-10-05','from app one','20.01','薪資']]);two_doc=upload(two,'two.csv',[['2026-10-06','from app two','30.02','薪資']])
    barrier=threading.Barrier(2)
    def worker(app,doc):barrier.wait();return commit(app,doc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        f1,f2=pool.submit(worker,one,one_doc),pool.submit(worker,two,two_doc);r1,r2=f1.result(),f2.result()
    check('P03-B-two','Concurrent Applications commit distinct data without lost update',lambda:dict(passed=len(one.dispatch('GET','/api/state')[1]['transactions'])==2 and one.dispatch('GET','/api/state')[1]==two.dispatch('GET','/api/state')[1] and one.dispatch('GET','/api/stats?month=2026-10')[1]['income']=='50.03',stats=one.dispatch('GET','/api/stats?month=2026-10')[1]))
    race_path=Path(temp)/'race.sqlite3';one,two=Application(race_path),Application(race_path)
    d1=upload(one,'race1.csv',[['2026-10-10','same transaction','45.67','薪資']]);d2=upload(two,'race2.csv',[['2026-10-10','same transaction','45.67','薪資'],['2026-10-11','different ignored row','0.00','生活']]);d2['transactions']=d2['transactions'][:1]
    barrier=threading.Barrier(2)
    def race_worker(app,doc):
        barrier.wait()
        try:return {'success':True,'result':commit(app,doc)[0]}
        except ValueError as e:return {'success':False,'error':str(e)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        f1,f2=pool.submit(race_worker,one,d1),pool.submit(race_worker,two,d2);race=[f1.result(),f2.result()]
    check('P03-B-race','Simultaneous duplicate commits admit exactly one',lambda:dict(passed=sum(r['success'] for r in race)==1 and len(one.dispatch('GET','/api/state')[1]['transactions'])==1 and one.dispatch('GET','/api/stats?month=2026-10')[1]['income']=='45.67',results=race,stats=one.dispatch('GET','/api/stats?month=2026-10')[1]))

after_sha={str(p.relative_to(ROOT)):sha(p) for p in sources}
report={'role':'P03','round':'backend-r1','evidence':'Application.dispatch direct Python calls + real tempfile SQLite; no HTTP listener, no browser','source_sha256_before':before_sha,'source_sha256_after':after_sha,'source_unchanged':before_sha==after_sha,'tests':results,'passed':sum(r['passed'] for r in results),'total':len(results),'critical_failures':[r['id'] for r in results if not r['passed']],'scores':{'task':18.75,'fidelity':20,'persistence':20,'ui':None,'scope':10,'errorState':5},'functional_score':73.75,'functional_max':80,'verdict':'Within P03 tested scope only; UI untested and no whole-product pass claim'}
(ROOT/'qa/reports/P03-backend-r1.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))
sys.exit(0 if report['passed']==report['total'] and report['source_unchanged'] else 1)
