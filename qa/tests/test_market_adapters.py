"""P02 market adapter contracts: synthetic provider fixtures + actual SQLite.

All provider traffic is mocked. Passing these tests is not live market evidence.
"""
import copy
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import urllib.error
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
import markets as m
from store import Store

NOW=datetime(2026,11,2,8,tzinfo=timezone.utc)
TW=[{'Date':'1151030','Code':'2330','ClosingPrice':'1000.00','Change':'-10.00'},
    {'Date':'1151030','Code':'0050','ClosingPrice':'200.50','Change':'0.50'},
    {'Date':'1151030','Code':'0056','ClosingPrice':'30.10','Change':'0.10'}]
INDEX=[{'日期':'1151030','指數':'發行量加權股價指數','收盤指數':'20000.12','漲跌':'-','漲跌點數':'100.00','漲跌百分比':'0.50'}]
def us(symbol='META'):
    return {'symbol':symbol,'currency':'USD','close':'100.12','change':'1.12','percent_change':'1.1313',
            'datetime':'2026-10-30 16:00:00','exchange_timezone':'America/New_York','is_market_open':False}
CHECKS=[]
def check(name,fn):
    with tempfile.TemporaryDirectory(prefix='p02-markets-') as tmp:
        db=Path(tmp)/'test.sqlite3';store=Store(db);obj=m.Markets(store,now=lambda:NOW)
        try:assert fn(obj,db) is True;CHECKS.append({'name':name,'pass':True})
        except Exception as e:CHECKS.append({'name':name,'pass':False,'error':repr(e)})
def rejects(fn):
    try:fn();return False
    except ValueError:return True
def find(data,key):return next(q for q in data['quotes'] if q['id']==key)

def readonly(obj,db):
    with patch.object(m,'request_json',side_effect=AssertionError('Must not fetch')):
        r=obj.overview();return len(r['quotes'])==17 and all(q['price'] is None for q in r['quotes']) and find(r,'US:META')['status']=='missing_key'
check('read-only overview never fetches or invents initial quotes',readonly)
check('TW ROC date/sign/index point parsing',lambda obj,db:(lambda q:q['price']=='20000.12' and q['change']=='-100.00' and q['change_percent']=='-0.50' and q['as_of']=='2026-10-30' and q['closed'] is None)(m.parse_tw_snapshot(INDEX,m.REGISTRY['TW:TAIEX'])))
check('TW percent derived only from valid previous close',lambda obj,db:m.parse_tw_snapshot(TW,m.REGISTRY['TW:2330'])['change_percent']=='-0.9901')
check('missing source date is not replaced with fetch date',lambda obj,db:rejects(lambda:m.parse_tw_snapshot([{'Code':'2330','ClosingPrice':'100','Change':'1'}],m.REGISTRY['TW:2330'])))
check('invalid or suspended stock does not become zero price',lambda obj,db:all(rejects(lambda v=v:m.parse_tw_snapshot([{'Date':'1151030','Code':'2330','ClosingPrice':v}],m.REGISTRY['TW:2330'])) for v in ['0','-1','NaN','--','1e99']))
check('US timestamp timezone and provider closed state retained',lambda obj,db:(lambda q:q['as_of']=='2026-10-30T16:00:00-04:00' and q['as_of_precision']=='timestamp' and q['closed'] is True)(m.parse_us_snapshot(us(),m.REGISTRY['US:META'])))
check('US symbol mismatch never substitutes another stock/ETF',lambda obj,db:rejects(lambda:m.parse_us_snapshot(us('SPY'),m.REGISTRY['US:SPX'])))
check('US currency mismatch explicitly refused',lambda obj,db:rejects(lambda:m.parse_us_snapshot(dict(us(),currency='EUR'),m.REGISTRY['US:META'])))
check('US missing or unknown timezone not guessed',lambda obj,db:rejects(lambda:m.parse_us_snapshot(dict(us(),exchange_timezone='UNKNOWN'),m.REGISTRY['US:META'])))

def no_key(obj,db):
    with patch.object(m,'request_json',side_effect=AssertionError('no network without key')) as fetch:
        q=find(obj.refresh(['US:META']),'US:META');return q['status']=='missing_key' and q['price'] is None and fetch.call_count==0
check('missing Twelve Data key sends no model/market request',no_key)
def entitlement(obj,db):
    with patch.object(m,'request_json',return_value={'status':'error','code':403,'message':'SECRET KEY MAY APPEAR HERE'}):
        r=obj.refresh(['US:SPX'],key='SECRET-TWELVE');q=find(r,'US:SPX')
        return q['status']=='permission_denied' and q['price'] is None and 'SECRET' not in json.dumps(r) and not find(r,'US:SPY')['price']
check('index plan failure separate and no ETF fallback/secret echo',entitlement)
def persist(obj,db):
    with patch.object(m,'request_json',return_value=TW):obj.refresh(['TW:2330','TW:0050','TW:0056'])
    r=m.Markets(db,now=lambda:NOW).overview();q=find(r,'TW:2330')
    return q['price']=='1000.00' and q['as_of']=='2026-10-30' and q['history']==[{'date':'2026-10-30','close':'1000.00'}]
check('real SQLite persists successful quote and only actual one-point history',persist)
def shared(obj,db):
    with patch.object(m,'request_json',side_effect=lambda url:INDEX if url==m.TW_INDEX else TW) as f:
        r=obj.refresh(['TW:TAIEX','TW:2330','TW:0050','TW:0056']);return f.call_count==2 and r['refresh']['network_requests']==2
check('TW selected stocks share a single source feed',shared)
def cached_failure(obj,db):
    with patch.object(m,'request_json',return_value=TW):obj.refresh(['TW:2330'])
    obj._now=lambda:NOW+timedelta(minutes=2)
    err=urllib.error.URLError(socket.gaierror(-2,'name lookup failure SECRET'))
    with patch.object(m,'request_json',side_effect=err):r=obj.refresh(['TW:2330'])
    q=find(r,'TW:2330');return q['price']=='1000.00' and q['stale'] and q['error']['code']=='dns_error' and 'SECRET' not in json.dumps(r)
check('DNS failure retains last good quote but marks stale/error',cached_failure)
def stale(obj,db):
    with patch.object(m,'request_json',return_value=TW):obj.refresh(['TW:2330'])
    obj._now=lambda:NOW+timedelta(days=5)
    return find(obj.overview(),'TW:2330')['stale'] is True
check('old fetch/source dates marked stale, never relabeled current',stale)
def cooldown(obj,db):
    with patch.object(m,'request_json',return_value=TW) as f:
        obj.refresh(['TW:2330']);r=obj.refresh(['TW:2330']);return f.call_count==1 and r['refresh_error']['code']=='cooldown'
check('persistent60second cooldown avoids repeated network quota use',cooldown)
def us_quota(obj,db):
    with patch.object(m,'request_json',side_effect=lambda url:us(m.parse_qs(m.urlsplit(url).query)['symbol'][0])) as f:
        r=obj.refresh([key for key in m.REGISTRY if key.startswith('US:')],key='SECRET-TWELVE')
        return f.call_count==6 and r['refresh']['us_requests']==6 and any(q['status']=='quota_deferred' for q in r['quotes'])
check('US explicit refresh bounded to six provider requests',us_quota)
check('SSRF URL host/scheme/path/symbol whitelist',lambda obj,db:all(rejects(lambda url=url:m._safe_url(url)) for url in ['http://api.twelvedata.com/quote','https://localhost/quote','https://api.twelvedata.com.evil.test/quote','https://api.twelvedata.com@127.0.0.1/quote','https://api.twelvedata.com/anything','https://api.twelvedata.com/quote?symbol=EVIL','https://www.twse.com.tw/exchangeReport/STOCK_DAY?stockNo=evil']))
check('unknown selection/type/history fields rejected',lambda obj,db:all(rejects(fn) for fn in [lambda:obj.refresh(['https://evil.test']),lambda:obj.refresh([{}]),lambda:obj.refresh(['US:META','US:META']),lambda:obj.refresh(['US:META'],history='true')]))

def no_key_storage(obj,db):
    err=urllib.error.HTTPError('https://api.twelvedata.com/quote?apikey=SECRET-TWELVE',403,'SECRET-TWELVE',{},None)
    with patch.object(m,'request_json',side_effect=err):r=obj.refresh(['US:META'],key='SECRET-TWELVE')
    return 'SECRET-TWELVE' not in json.dumps(r) and b'SECRET-TWELVE' not in db.read_bytes()
check('provider HTTP errors do not leak key to JSON or actual SQLite bytes',no_key_storage)
def history(obj,db):
    data={'stat':'OK','fields':['日期','成交股數','收盤價'],'data':[['115/10/28','100','99.90'],['115/10/29','200','100.10']]}
    with patch.object(m,'request_json',side_effect=lambda url:data if m.TW_HISTORY in url else TW):r=obj.refresh(['TW:2330'],history=True)
    return find(r,'TW:2330')['history']==[{'date':'2026-10-28','close':'99.90'},{'date':'2026-10-29','close':'100.10'},{'date':'2026-10-30','close':'1000.00'}]
check('official history points added exactly without filling missing dates',history)
def history_error(obj,db):
    with patch.object(m,'request_json',side_effect=lambda url:{'stat':'No data'} if m.TW_HISTORY in url else TW):obj.refresh(['TW:2330'],history=True)
    q=find(obj.overview(),'TW:2330');return q['price']=='1000.00' and q['status']=='ok' and q['history_error']['code']=='history_unavailable'
check('history error remains independently visible without invalidating good quote',history_error)
def history_us(obj,db):
    payload={'meta':{'symbol':'META','currency':'USD','interval':'1day'},'values':[{'datetime':'2026-10-29','close':'99.01'},{'datetime':'2026-10-30','close':'100.12'}],'status':'ok'}
    return m.parse_history(payload,m.REGISTRY['US:META'])==[{'date':'2026-10-29','close':'99.01'},{'date':'2026-10-30','close':'100.12'}]
check('US daily history validates symbol and exact dated closes',history_us)
check('conflicting same-day history points rejected',lambda obj,db:rejects(lambda:m.parse_history({'meta':{'symbol':'META','currency':'USD'},'values':[{'datetime':'2026-10-29','close':'99'},{'datetime':'2026-10-29','close':'100'}]},m.REGISTRY['US:META'])))

class Response:
    def __init__(self,data):self.data=data
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,n):return self.data[:n]
def raw_reader(obj,db):
    with patch.object(m,'safe_open',return_value=Response(json.dumps(TW).encode())) as f:
        got=m.request_json(m.TW_STOCKS)
        return got==TW and f.call_count==1
check('JSON reader exercises fixed URL validation and bounded safe_open transport',raw_reader)
def body_limit(obj,db):
    with patch.object(m,'safe_open',return_value=Response(b'x'*(m.MAX_BODY+1))):return rejects(lambda:m.request_json(m.TW_STOCKS))
check('oversized source body refused before decode/cache',body_limit)
def nondestructive(obj,db):
    s=Store(db);s.create_transaction({'date':'2026-10-31','name':'SECRET PRIVATE MERCHANT','amount':'-10','category':'餐飲'})
    before=s.snapshot()
    with patch.object(m,'request_json',return_value=TW):out=obj.refresh(['TW:2330'])
    return before==s.snapshot() and 'SECRET PRIVATE MERCHANT' not in json.dumps(out)
check('market refresh neither reads private ledger content into output nor edits holdings',nondestructive)

def index_fallback(obj,db):
    feed=[{k:v for k,v in INDEX[0].items() if k!='日期'}]
    hist={'stat':'OK','fields':['日期','收盤指數'],'data':[['115/10/29','20000.00'],['115/10/30','20010.00']]}
    with patch.object(m,'request_json',side_effect=lambda url:feed if url==m.TW_INDEX else hist) as f:r=obj.refresh(['TW:TAIEX'])
    q=find(r,'TW:TAIEX')
    return f.call_count==2 and q['price']=='20010.00' and q['change']=='10.00' and q['change_percent']=='0.0500' and q['as_of']=='2026-10-30' and q['source_endpoint']==m.TW_INDEX_HISTORY and len(q['history'])==2
check('MI_INDEX noDate uses dated official index history with exact source date',index_fallback)
def one_index(obj,db):
    feed=[{k:v for k,v in INDEX[0].items() if k!='日期'}];hist={'stat':'OK','fields':['日期','收盤指數'],'data':[['115/10/30','20010.00']]}
    with patch.object(m,'request_json',side_effect=lambda url:feed if url==m.TW_INDEX else hist):r=obj.refresh(['TW:TAIEX'])
    q=find(r,'TW:TAIEX');return q['price']=='20010.00' and q['change'] is None and q['change_percent'] is None and len(q['history'])==1
check('single dated index point shows price without fabricated change',one_index)
def previous_month(obj,db):
    feed=[{k:v for k,v in INDEX[0].items() if k!='日期'}];hist={'stat':'OK','fields':['日期','收盤指數'],'data':[['115/10/30','20010.00']]}
    def fixture(url):
        if url==m.TW_INDEX:return feed
        return {'stat':'No data'} if 'date=20261101' in url else hist
    with patch.object(m,'request_json',side_effect=fixture) as f:r=obj.refresh(['TW:TAIEX'])
    return f.call_count==3 and find(r,'TW:TAIEX')['as_of']=='2026-10-30'
check('empty first-month index history can use bounded previous-month official source',previous_month)
def future_quote(obj,db):
    with patch.object(m,'request_json',return_value=dict(us(),datetime='2099-01-01')):r=obj.refresh(['US:META'],key='DUMMY')
    q=find(r,'US:META');return q['price'] is None and q['error']['code']=='future_date' and q['stale']
check('future provider date cannot enter fresh quote cache',future_quote)
def future_time(obj,db):
    with patch.object(m,'request_json',return_value=dict(us(),datetime='2026-11-02 14:00:00')):r=obj.refresh(['US:META'],key='DUMMY')
    return find(r,'US:META')['price'] is None and find(r,'US:META')['error']['code']=='future_date'
check('future timestamp on same market date rejected at runtime clock gate',future_time)
check('stock missing currency is rejected not labeledUSD',lambda obj,db:rejects(lambda:m.parse_us_snapshot({k:v for k,v in us().items() if k!='currency'},m.REGISTRY['US:META'])))
check('close previous-close and change contradiction refused',lambda obj,db:rejects(lambda:m.parse_us_snapshot(dict(us(),close='100',previous_close='90',change='1',percent_change='1'),m.REGISTRY['US:META'])))
check('change percent contradiction refused at reported precision',lambda obj,db:rejects(lambda:m.parse_us_snapshot(dict(us(),close='100',previous_close='90',change='10',percent_change='50'),m.REGISTRY['US:META'])))
check('malformed grouping cannot turn1comma2 into12',lambda obj,db:rejects(lambda:m._number('1,2',True)))
check('stock history foreign currency mismatch rejected',lambda obj,db:rejects(lambda:m.parse_history({'meta':{'symbol':'META','currency':'EUR'},'values':[{'datetime':'2026-10-30','close':'100'}]},m.REGISTRY['US:META'])))
def future_history(obj,db):
    q=us();hist={'meta':{'symbol':'META','currency':'USD'},'values':[{'datetime':'2099-01-01','close':'100'}]}
    with patch.object(m,'request_json',side_effect=lambda url:hist if m.US_HISTORY in url else q):r=obj.refresh(['US:META'],key='DUMMY',history=True)
    row=find(r,'US:META');return row['price']=='100.12' and row['history_error']['code']=='future_date' and all(pt['date']!='2099-01-01' for pt in row['history'])
check('future historical points cannot enter SQLite graph cache',future_history)
def open_bar(obj,db):
    with patch.object(m,'request_json',return_value=dict(us(),is_market_open=True)):r=obj.refresh(['US:META'],key='DUMMY')
    q=find(r,'US:META');return q['price']=='100.12' and q['closed'] is False and q['history']==[]
check('open intraday USquote not misrecorded as final daily close',open_bar)
check('DEL NEL bidi and surrogate key characters are refused',lambda obj,db:all(rejects(lambda c=c:obj.refresh(['US:META'],key='DUMMY'+c)) for c in ['\x7f','\x85','\u202e','\ud800']))
check('registry static sectors are stock labels not invented sector indexes',lambda obj,db:m.REGISTRY['US:META']['sector']=='通訊服務' and m.REGISTRY['US:NVDA']['sector']=='半導體' and m.REGISTRY['US:QQQ']['sector'] is None and m.REGISTRY['TW:TAIEX']['currency'] is None)

report={'reviewer':'P02','scope':'market adapter contract + realSQLite persistence; all remote provider responses synthetic mocked fixtures, zero live network evidence',
        'checks':CHECKS,'passed':sum(c['pass'] for c in CHECKS),'total':len(CHECKS),
        'source_sha256':hashlib.sha256((ROOT/'backend/markets.py').read_bytes()).hexdigest(),
        'live_status':'blocked/unverified: no DNS workaround and no live API Key used','ui_score':None,'total_score':None}
(ROOT/'qa/reports/P02_markets_contract.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
sys.exit(0 if all(c['pass'] for c in CHECKS) else 1)
