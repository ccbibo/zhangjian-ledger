"""Fixed-instrument market adapters; explicit refresh, persistent real-data cache.

TWSE: official daily closing snapshots, not live quotes. Twelve Data: separate
market-data key and plan entitlements. No account linking, trading, inferred
prices, fabricated dates or synthetic curves. Provider failures never echo URLs,
credentials or raw bodies. Network tests must mock safe_open when DNS is blocked.
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import socket
import sqlite3
import threading
import unicodedata
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit, parse_qs
from zoneinfo import ZoneInfo

from services import safe_open

TW_STOCKS='https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL'
TW_INDEX='https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX'
TW_HISTORY='https://www.twse.com.tw/exchangeReport/STOCK_DAY'
TW_INDEX_HISTORY='https://www.twse.com.tw/indicesReport/MI_5MINS_HIST'
US_QUOTE='https://api.twelvedata.com/quote'
US_HISTORY='https://api.twelvedata.com/time_series'
DOCS={'twse':'https://openapi.twse.com.tw/','twelvedata':'https://twelvedata.com/docs'}
MAX_BODY=4*1024*1024
TAIPEI=ZoneInfo('Asia/Taipei')


def _instrument(market,symbol,label,kind='stock'):
    return {'id':market+':'+symbol,'market':market,'symbol':symbol,'label':label,'kind':kind,
            'provider':'twse' if market=='TW' else 'twelvedata',
            'currency':None if kind=='index' else 'TWD' if market=='TW' else 'USD','unit':'index_points' if kind=='index' else 'price'}


REGISTRY={r['id']:r for r in [
    _instrument('TW','TAIEX','台灣加權指數','index'),_instrument('TW','2330','台積電'),
    _instrument('TW','0050','元大台灣50','etf'),_instrument('TW','0056','元大高股息','etf'),
    _instrument('US','DJI','道瓊工業指數','index'),_instrument('US','IXIC','NASDAQ綜合指數','index'),
    _instrument('US','SPX','S&P 500指數','index'),_instrument('US','SPY','SPDR S&P 500 ETF','etf'),
    _instrument('US','QQQ','Invesco QQQ ETF','etf'),_instrument('US','META','Meta Platforms'),
    _instrument('US','NVDA','NVIDIA'),_instrument('US','AAPL','Apple'),_instrument('US','TSM','台積電ADR'),
    _instrument('US','MSFT','Microsoft'),_instrument('US','AMZN','Amazon'),
    _instrument('US','GOOGL','Alphabet'),_instrument('US','AMD','AMD'),
]}
SECTORS={'2330':'半導體','TSM':'半導體','NVDA':'半導體','AMD':'半導體','AAPL':'科技硬體',
         'MSFT':'軟體','META':'通訊服務','GOOGL':'通訊服務','AMZN':'非必需消費'}
for _row in REGISTRY.values():
    _row['sector']=SECTORS.get(_row['symbol']) if _row['kind']=='stock' else None


class MarketDataError(ValueError):
    def __init__(self,code,message):
        super().__init__(message);self.code=code;self.message=message


def _number(value,positive=False):
    if isinstance(value,bool) or not isinstance(value,(str,int,float,Decimal)):
        raise MarketDataError('invalid_response','行情數字格式不正確，未更新價格')
    text=str(value).strip()
    if ',' in text and not re.fullmatch(r'[+-]?\d{1,3}(?:,\d{3})+(?:\.\d{1,10})?',text):
        raise MarketDataError('invalid_response','行情數字分組格式不正確，未更新價格')
    text=text.replace(',','')
    if not re.fullmatch(r'[+-]?\d+(?:\.\d{1,10})?',text):
        raise MarketDataError('invalid_response','行情數字格式不正確，未更新價格')
    n=Decimal(text)
    if not n.is_finite() or abs(n)>Decimal('1000000000000') or (positive and n<=0):
        raise MarketDataError('invalid_response','行情價格不是有效正數，未更新價格')
    return format(n,'f')


def _source_day(value):
    if not isinstance(value,str):
        raise MarketDataError('missing_date','來源未提供有效報價日期，未以抓取時間代替')
    text=value.strip()
    try:
        if re.fullmatch(r'\d{7}',text):
            return date(int(text[:3])+1911,int(text[3:5]),int(text[5:])).isoformat()
        if re.fullmatch(r'\d{3}/\d{2}/\d{2}',text):
            y,m,d=map(int,text.split('/'));return date(y+1911,m,d).isoformat()
        if re.fullmatch(r'\d{8}',text):
            return date(int(text[:4]),int(text[4:6]),int(text[6:])).isoformat()
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}',text):
            return date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    raise MarketDataError('missing_date','來源未提供有效報價日期，未以抓取時間代替')


def _safe_url(url):
    u=urlsplit(url)
    if u.scheme!='https' or u.username or u.password or u.port not in (None,443) or u.fragment:
        raise MarketDataError('destination_refused','行情目的地不在固定官方白名單')
    base=f'https://{u.hostname}{u.path}'
    if base not in (TW_STOCKS,TW_INDEX,TW_HISTORY,TW_INDEX_HISTORY,US_QUOTE,US_HISTORY):
        raise MarketDataError('destination_refused','行情目的地不在固定官方白名單')
    allowed={'symbol','apikey','interval','outputsize','format','country','exchange','response','date','stockNo'}
    query=parse_qs(u.query,keep_blank_values=True)
    if set(query)-allowed or any(len(v)!=1 for v in query.values()):
        raise MarketDataError('destination_refused','行情查詢參數不在固定白名單')
    if 'symbol' in query and query['symbol'][0] not in {r['symbol'] for r in REGISTRY.values() if r['market']=='US'}:
        raise MarketDataError('destination_refused','標的不在固定清單')
    if 'stockNo' in query and query['stockNo'][0] not in {'2330','0050','0056'}:
        raise MarketDataError('destination_refused','標的不在固定清單')
    if 'date' in query and not re.fullmatch(r'\d{8}',query['date'][0]):
        raise MarketDataError('destination_refused','歷史查詢日期格式不正確')
    return url


def _failure(exc):
    if isinstance(exc,MarketDataError):return {'code':exc.code,'message':exc.message}
    if isinstance(exc,urllib.error.HTTPError):
        if exc.code in (401,403):return {'code':'permission_denied','message':'行情Key或此標的權限不足；指數不會以ETF代替'}
        if exc.code==429:return {'code':'rate_limited','message':'行情供應商額度或速率限制，稍後再試'}
        if 300<=exc.code<400:return {'code':'redirect_refused','message':'行情來源重新導向已拒絕，未將Key轉送其他網址'}
        return {'code':'provider_error','message':'行情供應商暫時無法提供資料，保留既有快取'}
    reason=getattr(exc,'reason',exc)
    if isinstance(reason,socket.gaierror):return {'code':'dns_error','message':'目前執行環境無法解析行情網址，尚未驗證Key或取得報價'}
    if isinstance(reason,(TimeoutError,socket.timeout)):return {'code':'timeout','message':'行情來源連線逾時，保留既有快取'}
    return {'code':'network_error','message':'行情連線或來源格式暫時失敗，保留既有快取'}


def request_json(url):
    req=urllib.request.Request(_safe_url(url),headers={'User-Agent':'LedgerLocal/0.5 (explicit personal market reader)','Accept':'application/json'})
    with safe_open(req,timeout=8) as response:
        raw=response.read(MAX_BODY+1)
    if len(raw)>MAX_BODY:raise MarketDataError('response_too_large','行情回應超出大小限制')
    try:return json.loads(raw.decode('utf-8-sig'))
    except (UnicodeError,json.JSONDecodeError):raise MarketDataError('invalid_response','行情來源不是有效JSON') from None


def parse_tw_snapshot(payload,instrument):
    rows=payload if isinstance(payload,list) else payload.get('data',[]) if isinstance(payload,dict) else []
    for row in rows:
        if not isinstance(row,dict):continue
        if instrument['kind']=='index':
            if row.get('指數',row.get('IndexName'))!='發行量加權股價指數':continue
            price=_number(row.get('收盤指數',row.get('ClosingIndex')),True)
            change=row.get('漲跌點數',row.get('Change'));percent=row.get('漲跌百分比',row.get('ChangePercent'))
            sign=row.get('漲跌','')
            if sign=='-' and change is not None:change='-'+str(change).lstrip('+-')
            if sign=='-' and percent is not None:percent='-'+str(percent).lstrip('+-')
        else:
            if str(row.get('Code',row.get('證券代號','')))!=instrument['symbol']:continue
            price=_number(row.get('ClosingPrice',row.get('收盤價')),True)
            change=row.get('Change',row.get('漲跌價差'));percent=None
        day=_source_day(row.get('Date',row.get('日期',payload.get('Date') if isinstance(payload,dict) else None)))
        change=_number(change) if change not in (None,'','--') else None
        if percent not in (None,'','--'):percent=_number(percent)
        elif change is not None:
            previous=Decimal(price)-Decimal(change)
            percent=format((Decimal(change)/previous*100).quantize(Decimal('.0001')),'f') if previous>0 else None
        return {'price':price,'change':change,'change_percent':percent,'as_of':day,'as_of_precision':'date',
                'source':DOCS['twse'],'source_endpoint':TW_INDEX if instrument['kind']=='index' else TW_STOCKS,
                'delay':'官方日收盤資料，非盤中即時報價；可能延後更新','closed':None,'market_state':'unknown_daily_close_only'}
    raise MarketDataError('symbol_unavailable','官方來源本次沒有此標的有效收盤資料')


def _provider_status(payload):
    if not isinstance(payload,dict):raise MarketDataError('invalid_response','行情來源回應格式不正確')
    if payload.get('status')=='error' or ('code' in payload and payload.get('code') not in (200,'200',None)):
        code=payload.get('code')
        if code in (401,403,'401','403'):raise MarketDataError('permission_denied','行情Key或該標的權限不足；不以其他標的替代')
        if code in (429,'429'):raise MarketDataError('rate_limited','行情供應商額度或速率限制，稍後再試')
        raise MarketDataError('symbol_unavailable','供應商未提供此標的資料；未產生替代或示範行情')


def parse_us_snapshot(payload,instrument):
    _provider_status(payload)
    if payload.get('symbol')!=instrument['symbol']:
        raise MarketDataError('symbol_mismatch','供應商回傳標的與請求不一致，未更新行情')
    if instrument['kind']!='index' and payload.get('currency')!='USD':
        raise MarketDataError('currency_mismatch','供應商幣別與固定市場標的不一致，未更新行情')
    # Index entitlement/symbol aliases are deliberately not guessed.
    price=_number(payload.get('close'),True)
    raw_time=payload.get('datetime')
    if not isinstance(raw_time,str):raise MarketDataError('missing_date','行情來源缺少報價日期，未以抓取時間代替')
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',raw_time):
        as_of=_source_day(raw_time);precision='date'
    else:
        try:
            dt=datetime.fromisoformat(raw_time.replace('Z','+00:00'))
            if dt.tzinfo is None:
                zone=payload.get('exchange_timezone')
                if zone not in ('America/New_York','US/Eastern'):
                    raise ValueError('Unknown source timezone')
                dt=dt.replace(tzinfo=ZoneInfo('America/New_York'))
            as_of=dt.isoformat();precision='timestamp'
        except ValueError:raise MarketDataError('missing_date','來源日期或時區無法核實，未更新報價') from None
    change=_number(payload['change']) if payload.get('change') is not None else None
    percent=_number(payload['percent_change']) if payload.get('percent_change') is not None else None
    previous=_number(payload['previous_close'],True) if payload.get('previous_close') is not None else None
    if previous is not None:
        computed=Decimal(price)-Decimal(previous)
        if change is not None and abs(computed-Decimal(change))>Decimal(1).scaleb(Decimal(change).as_tuple().exponent)/2:
            raise MarketDataError('inconsistent_response','行情價格與來源漲跌金額不一致，未更新報價')
        if change is None:change=format(computed,'f')
    elif change is not None and Decimal(price)-Decimal(change)>0:
        previous=format(Decimal(price)-Decimal(change),'f')
    elif change is not None:
        raise MarketDataError('inconsistent_response','行情漲跌推算的前收盤價不是正數，未更新報價')
    if previous is not None and percent is not None:
        expected=(Decimal(price)-Decimal(previous))/Decimal(previous)*100
        tolerance=Decimal(1).scaleb(Decimal(percent).as_tuple().exponent)/2
        if abs(expected-Decimal(percent))>tolerance+Decimal('.00000001'):
            raise MarketDataError('inconsistent_response','行情價格與來源漲跌百分比不一致，未更新報價')
    open_state=payload.get('is_market_open')
    return {'price':price,'change':change,'change_percent':percent,
            'as_of':as_of,'as_of_precision':precision,'source':DOCS['twelvedata'],'source_endpoint':US_QUOTE,
            'delay':'取決於Twelve Data方案、交易所與標的權限；未保證即時','closed':not open_state if type(open_state) is bool else None,
            'market_state':'open' if open_state is True else 'closed' if open_state is False else 'unknown',
            'as_of_meaning':'供應商資料時間，不保證為最後一筆成交時間'}


def quote_from_tw_history(points,instrument):
    if not points:raise MarketDataError('history_unavailable','官方帶日期歷史資料本次為空，未產生替代報價')
    points=sorted(points,key=lambda p:p['date']);latest=points[-1];change=percent=None
    if len(points)>1:
        delta=Decimal(latest['close'])-Decimal(points[-2]['close'])
        change=format(delta,'f');percent=format((delta/Decimal(points[-2]['close'])*100).quantize(Decimal('.0001')),'f')
    return {'price':latest['close'],'change':change,'change_percent':percent,
            'as_of':latest['date'],'as_of_precision':'date','source':DOCS['twse'],'source_endpoint':TW_INDEX_HISTORY,
            'delay':'官方帶日期日收盤歷史，非盤中即時行情；漲跌比較最近兩個來源交易日',
            'closed':None,'market_state':'unknown_daily_close_only'}


def parse_history(payload,instrument):
    """Parse actual provider daily closes only; no interpolation or quote-time fill."""
    points=[]
    if instrument['market']=='US':
        _provider_status(payload)
        meta=payload.get('meta',{})
        if not isinstance(meta,dict) or meta.get('symbol')!=instrument['symbol']:
            raise MarketDataError('symbol_mismatch','歷史來源標的不一致')
        if instrument['kind']!='index' and meta.get('currency')!='USD':
            raise MarketDataError('currency_mismatch','歷史來源幣別與標的不一致，未更新曲線')
        if meta.get('interval') not in (None,'1day'):
            raise MarketDataError('invalid_response','歷史來源不是日資料，未當作每日收盤曲線')
        for row in payload.get('values',[]):
            if not isinstance(row,dict):raise MarketDataError('invalid_response','歷史行情格式不正確')
            points.append({'date':_source_day(row.get('datetime')),'close':_number(row.get('close'),True)})
    else:
        if not isinstance(payload,dict) or payload.get('stat')!='OK':
            raise MarketDataError('history_unavailable','官方歷史資料本次不可用，未補出曲線')
        fields=payload.get('fields',[])
        close_names=('收盤指數','收盤價')
        close_idx=next((fields.index(n) for n in close_names if n in fields),None)
        date_idx=fields.index('日期') if '日期' in fields else None
        if close_idx is None or date_idx is None:raise MarketDataError('invalid_response','官方歷史欄位無法核實')
        for row in payload.get('data',[]):
            if not isinstance(row,list) or max(close_idx,date_idx)>=len(row):raise MarketDataError('invalid_response','官方歷史欄位無法核實')
            if row[close_idx] in ('--','---',''):continue
            points.append({'date':_source_day(row[date_idx]),'close':_number(row[close_idx],True)})
    if len(points)>5000:raise MarketDataError('response_too_large','歷史行情點數超出限制')
    unique={}
    for point in points:
        if point['date'] in unique and unique[point['date']]!=point['close']:
            raise MarketDataError('invalid_response','同日歷史行情互相矛盾，未更新曲線')
        unique[point['date']]=point['close']
    return [{'date':d,'close':unique[d]} for d in sorted(unique)]


class Markets:
    def __init__(self,ledger_or_path,now=None):
        self.db_path=str(getattr(ledger_or_path,'db_path',ledger_or_path))
        if self.db_path==':memory:':raise ValueError('市場快取需要本機SQLite檔案')
        Path(self.db_path).parent.mkdir(parents=True,exist_ok=True)
        self._now=now or (lambda:datetime.now(timezone.utc))
        self._lock=threading.Lock()
        with self._connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS markets_cache (symbol TEXT PRIMARY KEY,payload TEXT NOT NULL,fetched_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS markets_history (symbol TEXT NOT NULL,day TEXT NOT NULL,close TEXT NOT NULL,source TEXT NOT NULL,PRIMARY KEY(symbol,day));
                CREATE TABLE IF NOT EXISTS markets_attempts (symbol TEXT PRIMARY KEY,status TEXT NOT NULL,error TEXT,attempted_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS markets_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);''')

    @contextmanager
    def _connect(self):
        con=sqlite3.connect(self.db_path,timeout=8);con.row_factory=sqlite3.Row
        try:
            with con:yield con
        finally:con.close()

    def status(self):
        return {'providers':{'twse':{'key_required':False,'source':DOCS['twse'],'mode':'daily_close'},
                             'twelvedata':{'key_required':True,'source':DOCS['twelvedata'],'mode':'separate_runtime_key'}},
                'scope':'只讀行情；不綁銀行、不下單、不自動改寫持倉價格或成本。Twelve Data Key獨立於DeepSeek。'}

    def overview(self):
        now=self._now()
        with self._connect() as c:
            cached={r['symbol']:r for r in c.execute('SELECT * FROM markets_cache')}
            attempts={r['symbol']:r for r in c.execute('SELECT * FROM markets_attempts')}
            history={k:[] for k in REGISTRY}
            for r in c.execute('SELECT * FROM markets_history ORDER BY day'):
                if r['symbol'] in history:history[r['symbol']].append({'date':r['day'],'close':r['close']})
            herrors={r['key'].split(':',1)[1]:json.loads(r['value']) for r in c.execute("SELECT * FROM markets_meta WHERE key LIKE 'history_error:%'")}
        quotes=[]
        for key,ins in REGISTRY.items():
            q={**ins,'price':None,'change':None,'change_percent':None,'as_of':None,'as_of_precision':None,
               'source':DOCS[ins['provider']],'source_endpoint':None,'delay':'尚未取得資料','closed':None,'market_state':'unknown',
               'status':'missing_key' if ins['market']=='US' else 'unavailable','stale':True,'error':None,'fetched_at':None}
            if key in cached:
                row=cached[key];q.update(json.loads(row['payload']));q['fetched_at']=row['fetched_at'];q['status']='ok'
                age=(now-datetime.fromisoformat(row['fetched_at'])).total_seconds()
                day=_source_day(q['as_of'][:10]);source_age=(now.astimezone(TAIPEI).date()-date.fromisoformat(day)).days
                q['stale']=age>24*3600 or source_age>3
                if q['stale']:q['status']='stale'
            attempt=attempts.get(key)
            if attempt and (key not in cached or attempt['attempted_at']>=cached[key]['fetched_at']) and attempt['error']:
                q['error']=json.loads(attempt['error']);q['status']=attempt['status'];q['stale']=True
            q['history']=history[key][-180:]
            if key in herrors:q['history_error']=herrors[key]
            q['not_personal_pnl']='標的價格／指數點數與市場漲跌，不是個人持倉盈虧。'
            quotes.append(q)
        return {**self.status(),'quotes':quotes,'generated_at':now.isoformat(),
                'history_note':'只顯示供應商真實歷史或已取得的每日快照；未補點、不保證涵蓋每個交易日。'}

    def _attempt(self,key,error):
        with self._connect() as c:c.execute('INSERT OR REPLACE INTO markets_attempts VALUES(?,?,?,?)',
            (key,error['code'],json.dumps(error,ensure_ascii=False),self._now().isoformat()))

    def _store_quote(self,key,quote):
        ins=REGISTRY[key];source_day=date.fromisoformat(quote['as_of'][:10])
        local_now=self._now().astimezone(TAIPEI if ins['market']=='TW' else ZoneInfo('America/New_York'))
        if source_day>local_now.date():
            raise MarketDataError('future_date','來源報價日期晚於目前市場日期，未更新快取')
        if quote['as_of_precision']=='timestamp' and datetime.fromisoformat(quote['as_of'])>self._now()+timedelta(minutes=5):
            raise MarketDataError('future_date','來源報價時間晚於目前時間，未更新快取')
        stamp=self._now().isoformat()
        with self._connect() as c:
            c.execute('INSERT OR REPLACE INTO markets_cache VALUES(?,?,?)',(key,json.dumps(quote,ensure_ascii=False),stamp))
            c.execute('INSERT OR REPLACE INTO markets_attempts VALUES(?,?,NULL,?)',(key,'ok',stamp))
            if ins['market']=='TW' or quote.get('closed') is True:
                c.execute('INSERT OR REPLACE INTO markets_history VALUES(?,?,?,?)',(key,quote['as_of'][:10],quote['price'],quote['source']))

    def _store_history(self,key,points,source,closed=None):
        ins=REGISTRY[key]
        today=self._now().astimezone(TAIPEI if ins['market']=='TW' else ZoneInfo('America/New_York')).date()
        if any(date.fromisoformat(pt['date'])>today for pt in points):
            raise MarketDataError('future_date','歷史來源包含未來日期，未更新曲線')
        # A US daily bar on the current date may still be in progress. Keep the
        # quote available, but do not relabel an open/unknown bar as daily close.
        usable=[pt for pt in points if ins['market']=='TW' or closed is True or date.fromisoformat(pt['date'])<today]
        with self._connect() as c:
            for pt in usable:c.execute('INSERT OR REPLACE INTO markets_history VALUES(?,?,?,?)',(key,pt['date'],pt['close'],source))
            c.execute('DELETE FROM markets_meta WHERE key=?',('history_error:'+key,))

    def refresh(self,symbols=None,key=None,history=False):
        if symbols is None:symbols=list(REGISTRY)
        if not isinstance(symbols,list) or not 1<=len(symbols)<=len(REGISTRY) or any(not isinstance(s,str) or s not in REGISTRY for s in symbols) or len(set(symbols))!=len(symbols):
            raise ValueError('請選擇固定市場清單中的不重複標的')
        if key is not None and (not isinstance(key,str) or len(key)>512 or any(unicodedata.category(c) in ('Cc','Cf','Cs') for c in key)):
            raise ValueError('Twelve Data Key格式不正確')
        if type(history) is not bool:raise ValueError('history須為布林值')
        key=(key or '').strip()
        if not self._lock.acquire(blocking=False):return dict(self.overview(),refresh_error={'code':'busy','message':'市場資料更新中'})
        try:
            now=self._now()
            with self._connect() as c:
                c.execute('BEGIN IMMEDIATE')
                last=c.execute("SELECT value FROM markets_meta WHERE key='last_refresh'").fetchone()
                if last and (now-datetime.fromisoformat(last['value'])).total_seconds()<60:
                    return dict(self.overview(),refresh_error={'code':'cooldown','message':'手動更新至少間隔60秒，避免消耗免費額度'})
                c.execute("INSERT OR REPLACE INTO markets_meta VALUES('last_refresh',?)",(now.isoformat(),))
            feeds={};requests=0;us_calls=0;history_done=False
            for sid in symbols:
                ins=REGISTRY[sid]
                try:
                    fallback_points=None
                    if ins['market']=='TW':
                        endpoint=TW_INDEX if ins['kind']=='index' else TW_STOCKS
                        if endpoint not in feeds:
                            try:feeds[endpoint]=request_json(endpoint);requests+=1
                            except Exception as exc:feeds[endpoint]=exc;requests+=1
                        if isinstance(feeds[endpoint],Exception):raise feeds[endpoint]
                        try:quote=parse_tw_snapshot(feeds[endpoint],ins)
                        except MarketDataError as exc:
                            if ins['kind']!='index' or exc.code!='missing_date':raise
                            # MI_INDEX may omit its date. A dated official index
                            # history endpoint supplies the actual source date.
                            history_url=TW_INDEX_HISTORY+'?'+urlencode({'response':'json','date':now.astimezone(TAIPEI).strftime('%Y%m01')})
                            requests+=1
                            try:fallback_points=parse_history(request_json(history_url),ins)
                            except MarketDataError as history_exc:
                                if history_exc.code!='history_unavailable':raise
                                fallback_points=[]
                            if not fallback_points:
                                previous_month=(now.astimezone(TAIPEI).date().replace(day=1)-timedelta(days=1)).strftime('%Y%m01')
                                requests+=1
                                fallback_points=parse_history(request_json(TW_INDEX_HISTORY+'?'+urlencode({'response':'json','date':previous_month})),ins)
                            quote=quote_from_tw_history(fallback_points,ins)
                            history_done=True
                    else:
                        if not key:raise MarketDataError('missing_key','尚未設定獨立Twelve Data行情Key；DeepSeek Key不適用')
                        if us_calls>=6:raise MarketDataError('quota_deferred','本輪最多6次美股請求，此標的未請求；可稍後單獨更新')
                        us_calls+=1;requests+=1
                        quote=parse_us_snapshot(request_json(US_QUOTE+'?'+urlencode({'symbol':ins['symbol'],'apikey':key})),ins)
                    self._store_quote(sid,quote)
                    if fallback_points is not None:
                        self._store_history(sid,fallback_points,quote['source'],quote.get('closed'))
                    if history and not history_done and (ins['market']=='TW' or us_calls<6):
                        history_done=True
                        try:
                            if ins['market']=='TW':
                                day=now.astimezone(TAIPEI).strftime('%Y%m01')
                                base=TW_INDEX_HISTORY if ins['kind']=='index' else TW_HISTORY
                                params={'response':'json','date':day}
                                if ins['kind']!='index':params['stockNo']=ins['symbol']
                            else:
                                base=US_HISTORY;params={'symbol':ins['symbol'],'apikey':key,'interval':'1day','outputsize':30};us_calls+=1
                            requests+=1;points=parse_history(request_json(base+'?'+urlencode(params)),ins)
                            self._store_history(sid,points,quote['source'],quote.get('closed'))
                        except Exception as exc:
                            # History failure is separate: a valid quote stays valid.
                            with self._connect() as c:c.execute('INSERT OR REPLACE INTO markets_meta VALUES(?,?)',('history_error:'+sid,json.dumps(_failure(exc),ensure_ascii=False)))
                except Exception as exc:self._attempt(sid,_failure(exc))
            result=self.overview();result['refresh']={'requested':symbols,'network_requests':requests,'us_requests':us_calls,
                'history_requested':history,'at':now.isoformat(),'scope':'明確手動更新；快取不寫入持倉或帳本。'}
            with self._connect() as c:
                herrors={r['key'].split(':',1)[1]:json.loads(r['value']) for r in c.execute("SELECT * FROM markets_meta WHERE key LIKE 'history_error:%'")}
            for q in result['quotes']:
                if q['id'] in herrors:q['history_error']=herrors[q['id']]
            return result
        finally:self._lock.release()
