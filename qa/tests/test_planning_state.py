"""P03 independent planning persistence/validation tests; SQLite, no sockets."""
import copy
import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app import Application

SOURCES = [ROOT / 'backend' / p for p in ('app.py', 'planning.py', 'store.py')]
SOURCE_BEFORE = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCES}
EVIDENCE = []


class PlanningState(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ledger-p03-v05-')
        self.path = Path(self.temp.name) / 'planner.sqlite3'
        self.app = Application(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def api(self, path, payload=None, app=None, method='POST'):
        code, value = (app or self.app).dispatch(method, path, payload)
        self.assertEqual(code, 200)
        return value

    def save(self, kind, item, app=None):
        return self.api('/api/items/save', {'kind': kind, 'item': item}, app)

    def db(self):
        with sqlite3.connect(self.path) as con:
            return {t: con.execute('SELECT * FROM '+t+' ORDER BY rowid').fetchall()
                    for t in ['planner_items', 'planner_settings', 'planner_snapshots']}

    def rejected_unchanged(self, path, data):
        before = self.db()
        error=None
        try:
            self.app.dispatch('POST', path, data)
        except Exception as exc:
            error=exc
        self.assertEqual(self.db(), before)
        self.assertIsInstance(error,ValueError,'invalid input must use controlled validation error, not '+type(error).__name__)

    def test_all_item_kinds_survive_new_application(self):
        samples = {
            'budget': {'name':'餐飲預算','month':'2026-11','category':'餐飲','limit':'5000.25'},
            'goal': {'name':'備用金','target':'100000.25','saved':'12.34','deadline':'2027-01-01'},
            'holding': {'name':'one lot','asset_class':'台股與ETF','quantity':'2.5','cost_price':'10.25','price':'12.5','currency':'TWD','as_of':'2026-11-01'},
            'debt': {'name':'小貸款','balance':'123.45','rate':'1.775','payment':'12.34','due':'2026-11-15'},
            'bill': {'name':'電話費','amount':'999.50','due':'2026-11-15','recurrence':'monthly'},
            'account': {'name':'bank A','asset_class':'現金與存款','value':'123.45','as_of':'2026-11-01'},
            'investment_flow': {'name':'first investment','date':'2026-11-01','amount':'-12.34'}}
        for k, v in samples.items():
            self.save(k, v)
        before = self.app.planning.state()
        reopened = Application(self.path)
        self.assertEqual(reopened.planning.state(), before)
        self.assertEqual(len(before['items']), 7)

    def test_stale_revision_update_and_delete_reject_without_writes(self):
        first = self.save('goal', {'name':'goal','target':'100','saved':'0'})
        second = self.save('goal', dict(first, saved='25'))
        self.assertEqual(second['revision'], 2)
        self.rejected_unchanged('/api/items/save', {'kind':'goal','item':dict(first,saved='50')})
        self.rejected_unchanged('/api/items/delete', {'id':first['id'],'revision':first['revision']})
        self.rejected_unchanged('/api/items/delete', {'id':str(uuid.uuid4()),'revision':1})

    def test_parallel_same_revision_allows_exactly_one_update(self):
        first = self.save('goal', {'name':'race','target':'100','saved':'0'})
        other = Application(self.path)
        barrier = threading.Barrier(2)
        def update(app, val):
            barrier.wait()
            try:
                return {'ok':True,'item':self.save('goal',dict(first,saved=val),app)}
            except ValueError as exc:
                return {'ok':False,'error':str(exc)}
        with ThreadPoolExecutor(max_workers=2) as pool:
            a,b=pool.submit(update,self.app,'20'),pool.submit(update,other,'30')
            results=[a.result(),b.result()]
        self.assertEqual(sum(r['ok'] for r in results),1)
        saved=self.app.planning.state()['items'][0]
        self.assertEqual(saved['revision'],2)
        self.assertIn(saved['saved'],['20.00','30.00'])
        EVIDENCE.append({'case':'concurrent_revision','results':results})

    def test_boolean_revision_is_not_valid_integer_version(self):
        first=self.save('goal',{'name':'bool revision','target':'100','saved':'0'})
        self.rejected_unchanged('/api/items/save',{'kind':'goal','item':dict(first,revision=True,saved='99')})

    def test_budget_copy_preserves_existing_and_is_idempotent(self):
        self.save('budget',{'name':'old food','month':'2026-10','category':'餐飲','limit':'100.25'})
        self.save('budget',{'name':'old transport','month':'2026-10','category':'交通','limit':'200.50'})
        current=self.save('budget',{'name':'current food','month':'2026-11','category':'餐飲','limit':'300.75'})
        self.assertEqual(self.api('/api/budgets/copy',{'month':'2026-11'})['created'],1)
        before=self.db()
        self.assertEqual(self.api('/api/budgets/copy',{'month':'2026-11'})['created'],0)
        self.assertEqual(before,self.db())
        found=next(i for i in self.app.planning.state()['items'] if i['id']==current['id'])
        self.assertEqual(found['limit'],'300.75')

    def test_budget_copy_capacity_failure_is_atomic(self):
        self.save('budget',{'name':'food','month':'2026-10','category':'餐飲','limit':'100'})
        self.save('budget',{'name':'transport','month':'2026-10','category':'交通','limit':'200'})
        # Fixture creates the reachable 4999-item boundary without 4997 API round trips.
        goal={'name':'capacity fixture','target':'1.00','saved':'0.00','deadline':''}
        with self.app.store._connect() as con:
            con.executemany('INSERT INTO planner_items VALUES(?,?,?,?,?)',
                [(str(uuid.uuid4()),'goal',json.dumps(goal),1,'2026-11-01T00:00:00Z') for _ in range(4997)])
        before=self.db()
        error=''
        try:self.app.dispatch('POST','/api/budgets/copy',{'month':'2026-11'})
        except ValueError as exc:error=str(exc)
        after=self.db()
        EVIDENCE.append({'case':'copy_capacity','error':error,'before_count':len(before['planner_items']),'after_count':len(after['planner_items'])})
        self.assertTrue(error)
        self.assertEqual(after,before,'copy failure must not leave one new budget behind')

    def test_invalid_items_and_kind_do_not_write(self):
        invalid=[('goal',{'name':'bad','target':'1.001'}),('goal',{'name':'bad','target':'1','deadline':'2026-02-30'}),
                 ('budget',{'name':'bad','month':'2026-13','category':'餐飲','limit':'1'}),
                 ('budget',{'name':'bad','month':'2026-11','category':'薪資','limit':'1'}),
                 ('account',{'name':'bad','asset_class':'invalid','value':'1','as_of':'2026-11-01'}),
                 ('holding',{'name':'bad','asset_class':'黃金','quantity':'0','cost_price':'1','price':'1','as_of':'2026-11-01'}),
                 ('bill',{'name':'bad','amount':'1','due':'2026-11-01','status':'unknown'}),
                 ('investment_flow',{'name':'bad','date':'2026-11-01','amount':'0'})]
        for kind,item in invalid:
            with self.subTest(kind=kind,item=item):self.rejected_unchanged('/api/items/save',{'kind':kind,'item':item})
        self.rejected_unchanged('/api/items/save',{'kind':{},'item':{}})

    def test_settings_roundtrip_and_invalid_atomicity(self):
        valid={'theme':'dark','widgets':['net','goals'],'targets':{'現金與存款':'60.25','黃金':'39.75'}}
        self.api('/api/settings',valid)
        self.assertEqual(Application(self.path).planning.state()['settings'],valid)
        invalid=[{'theme':'bad'}, {'widgets':[]}, {'widgets':['net','net']},{'widgets':['unknown']},
                 {'targets':{'黃金':'99.99'}}, {'targets':{'unknown':'100'}},{'targets':{'黃金':True}},
                 {'targets':{'黃金':'100.001'}}, {'theme':'light','widgets':['net'],'targets':{'黃金':'1'}},
                 {'unknown':'value'},{'widgets':[{}]}]
        for data in invalid:
            with self.subTest(data=data):self.rejected_unchanged('/api/settings',data)

    def test_snapshot_persistence_exact_values_and_invalid_no_write(self):
        self.save('account',{'name':'cash','asset_class':'現金與存款','value':'100.25','as_of':'2026-11-01'})
        self.save('debt',{'name':'loan','balance':'20.10','payment':'1','rate':'0','due':'2026-11-15'})
        self.api('/api/snapshot',{'day':'2026-11-01'})
        first=Application(self.path).planning.state()['snapshots']
        self.assertEqual(first,[{'day':'2026-11-01','assets':'100.25','liabilities':'20.10','net_worth':'80.15'}])
        self.rejected_unchanged('/api/snapshot',{'day':'2026-02-30'})

    def test_concurrent_budget_copy_returns_two_then_zero_without_errors(self):
        for name,category in [('food','餐飲'),('transport','交通')]:
            self.save('budget',{'name':name,'month':'2026-10','category':category,'limit':'100'})
        other=Application(self.path);barrier=threading.Barrier(2)
        def copy_budgets(app):
            barrier.wait()
            return self.api('/api/budgets/copy',{'month':'2026-11'},app)['created']
        with ThreadPoolExecutor(max_workers=2) as pool:
            a,b=pool.submit(copy_budgets,self.app),pool.submit(copy_budgets,other)
            counts=[a.result(),b.result()]
        self.assertEqual(sorted(counts),[0,2])
        EVIDENCE.append({'case':'concurrent_budget_copy','created_counts':counts,'items':len(self.app.planning.state()['items'])})

    def test_delete_rejects_noninteger_revision_without_write(self):
        item=self.save('goal',{'name':'delete revision','target':'100','saved':'0'})
        for revision in [True,1.0,'1',None,{},[]]:
            with self.subTest(revision=revision):
                self.rejected_unchanged('/api/items/delete',{'id':item['id'],'revision':revision})

    def test_delete_invalid_id_type_controlled_error_no_write(self):
        for item_id in [{},[],False,123,None]:
            with self.subTest(item_id=item_id):
                self.rejected_unchanged('/api/items/delete',{'id':item_id,'revision':1})

    def test_budget_invalid_category_type_controlled_error_no_write(self):
        for category in [{},[],False,None,123]:
            with self.subTest(category=category):
                self.rejected_unchanged('/api/items/save',{'kind':'budget','item':{'name':'bad category','month':'2026-11','category':category,'limit':'1'}})

    def test_aggregate_overflow_update_rejects_without_incrementing_revision(self):
        self.save('account',{'name':'big','asset_class':'現金與存款','value':'1000000000000','as_of':'2026-11-01'})
        second=self.save('account',{'name':'second','asset_class':'現金與存款','value':'0','as_of':'2026-11-01'})
        self.rejected_unchanged('/api/items/save',{'kind':'account','item':dict(second,value='1')})
        ov=self.api('/api/workspace?month=2026-11',method='GET')
        self.assertEqual(ov['assets'],'1000000000000.00')
        self.assertEqual(next(i for i in ov['items'] if i['id']==second['id'])['revision'],1)

    def test_reverse_import_same_manual_account_rejects_all_ledger_writes(self):
        self.save('account',{'name':'TEST Bank001','asset_class':'現金與存款','value':'134400','as_of':'2026-11-01'})
        doc=self.app.store.preview({'filename':'synthetic-bank.pdf','transactions':[{'date':'2026-11-01','name':'should not enter','amount':'20','category':'薪資'}],
                                    'assets':[{'name':'TEST Bank001','value':'134400','as_of':'2026-11-01'}],'debts':[]},'f'*64)
        before_ledger=self.app.store.snapshot();before_planning=self.app.planning.state()
        with self.assertRaises(ValueError):
            self.api('/api/commit',{'document_id':doc['document_id'],'transactions':doc['transactions']})
        self.assertEqual(self.app.store.snapshot(),before_ledger)
        self.assertEqual(self.app.planning.state(),before_planning)
        self.assertEqual(self.api('/api/workspace?month=2026-11',method='GET')['assets'],'134400.00')

    def test_same_symbol_holding_lots_remain_legal_and_additive(self):
        lot={'name':'same ETF','symbol':'TEST','asset_class':'台股與ETF','quantity':'1','cost_price':'10','price':'20','currency':'TWD','as_of':'2026-11-01'}
        self.save('holding',lot);self.save('holding',lot)
        self.assertEqual(self.api('/api/workspace?month=2026-11',method='GET')['assets'],'40.00')


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(PlanningState)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    after={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCES}
    report={'role':'P03','version':'v0.5','evidence':'Application.dispatch + real temporary SQLite; no socket/browser',
            'source_before':SOURCE_BEFORE,'source_after':after,'source_unchanged':SOURCE_BEFORE==after,
            'tests_run':result.testsRun,'failures':[{'test':str(t),'trace':e} for t,e in result.failures],
            'errors':[{'test':str(t),'trace':e} for t,e in result.errors],'observations':EVIDENCE,
            'ui':None,'passed':result.wasSuccessful()}
    suffix=sys.argv[1] if len(sys.argv)>1 and sys.argv[1] in ['r2','r3'] else ''
    name='P03_v05_state'+('_'+suffix if suffix else '')+'.json'
    (ROOT/'qa/reports'/name).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    sys.exit(not result.wasSuccessful())
