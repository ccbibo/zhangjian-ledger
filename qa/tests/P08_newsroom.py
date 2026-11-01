"""Honesty boundaries for newsroom. Synthetic sources and model transport only."""
import hashlib,json,sys,tempfile
from pathlib import Path
from datetime import datetime,timedelta
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
import newsroom as nr
from store import Store
from planning import Planning

cases=[];captured=[]
fixed=datetime(2026,10,31,8,0,tzinfo=nr.TZ)
def add(case,name,passed,**details):cases.append(dict(id=case,name=name,pass_=bool(passed),**details))
def candidates(source,now):
 return [{'id':source['category']+str(i),'title':source['category']+' test '+str(i),'url':'https://example.com/'+source['category']+str(i),'source':source['name'],'published':now.isoformat(),'excerpt':'Synthetic excerpt '+str(i)} for i in range(7)]
class Response:
 def __init__(self,data):self.data=data
 def __enter__(self):return self
 def __exit__(self,*_):return False
 def read(self,*_):return self.data
def model_ok(request,timeout):
 payload=json.loads(request.data);captured.append(payload)
 source=json.loads(payload['messages'][1]['content'])
 content=json.dumps({'items':[{'id':a['id'],'summary':'Synthetic selected summary','why':'Synthetic reason'} for a in source['candidates'][:3]]})
 return Response(json.dumps({'choices':[{'message':{'content':content}}]}).encode())

with tempfile.TemporaryDirectory(prefix='ledger-p08-news-') as folder:
 store=Store(Path(folder)/'news.sqlite3');planning=Planning(store);provider=SimpleNamespace(key='',model='deepseek-chat');room=nr.NewsRoom(store,planning,provider)
 with patch('newsroom.now_local',return_value=fixed),patch('newsroom.collect',side_effect=candidates),patch.object(room,'_summarize',side_effect=AssertionError('No key must not call model')):
  room.refresh(asynchronous=False);data=room.overview()
 add('P08-N-01','Each category caps fallback at five with no AI claim',all(len(c['items'])==5 and not c['ai_generated'] for c in data['channels'].values()),counts={k:len(c['items']) for k,c in data['channels'].items()})
 provider.key='P08-FAKE-NOT-A-REAL-KEY'
 with patch('newsroom.now_local',return_value=fixed),patch('newsroom.collect',side_effect=candidates),patch.object(room,'_summarize',side_effect=ValueError('mock invalid model')):
  room.refresh(asynchronous=False);data=room.overview()
 add('P08-N-02','AI failure retains source flashes with ai_generated false',all(not c['ai_generated'] and len(c['items'])==5 and any(e['source']=='DeepSeek' for e in c['errors']) for c in data['channels'].values()))
 def bad_model(request,timeout):
  content=json.dumps({'items':[{'id':'fabricated-id','summary':'Fake event','why':'Fake reason'}]})
  return Response(json.dumps({'choices':[{'message':{'content':content}}]}).encode())
 with patch('newsroom.now_local',return_value=fixed),patch('newsroom.collect',side_effect=candidates),patch('newsroom.safe_open',side_effect=bad_model):
  room.refresh(asynchronous=False);data=room.overview()
 add('P08-N-03','Model cannot attach a fabricated candidate ID',all(not c['ai_generated'] and all(i['id']!='fabricated-id' for i in c['items']) for c in data['channels'].values()))
 with patch('newsroom.now_local',return_value=fixed),patch('newsroom.collect',side_effect=candidates),patch('newsroom.safe_open',side_effect=model_ok):
  room.refresh(asynchronous=False);good=room.overview()
 add('P08-N-04','Validated mock model selection is marked generated',all(c['ai_generated'] and len(c['items'])==3 for c in good['channels'].values()))
 sent=json.dumps(captured,ensure_ascii=False)
 add('P08-N-05','News model payload contains public candidate fields only',all(set(json.loads(p['messages'][1]['content']))=={'category','candidates'} for p in captured) and 'transactions' not in sent and 'liabilities' not in sent)
 later=fixed+timedelta(days=1)
 with patch('newsroom.now_local',return_value=later),patch('newsroom.collect',side_effect=OSError('Synthetic offline')):
  room.refresh(asynchronous=False);stale=room.overview()
 add('P08-N-06','Failure cache preserves original date/update and marks stale',all(c['stale'] and c['date']==good['channels'][k]['date'] and c['updated_at']==good['channels'][k]['updated_at'] and c['last_attempt']==later.isoformat() for k,c in stale['channels'].items()),dates={k:c['date'] for k,c in stale['channels'].items()})
 add('P08-N-07','Old generated summaries remain old rather than current-generation claims',all(c['ai_generated'] and c['stale'] for c in stale['channels'].values()) and stale['job']['status']=='failed')
 with patch('newsroom.now_local',return_value=later),patch.object(room,'refresh',return_value={'started':True}) as refresh:
  result=room.tick(later)
 add('P08-N-08','Failed daily attempt is not endlessly retried as a success',result is False and not refresh.called)
 freshstore=Store(Path(folder)/'fresh.sqlite3');freshplanning=Planning(freshstore);fresh=nr.NewsRoom(freshstore,freshplanning,SimpleNamespace(key='',model='dummy'))
 add('P08-N-09','Default scheduler is Taipei 08:00',fresh.schedule()=={'enabled':True,'hour':8,'minute':0})
 with patch.object(fresh,'refresh',return_value={'started':True}) as refresh:
  before=fresh.tick(fixed.replace(hour=7,minute=59));at=fresh.tick(fixed)
 add('P08-N-10','Schedule does not run before selected time',before is False and at is True and refresh.call_count==1)
 freshplanning.settings({'news_schedule':{'enabled':False,'hour':8,'minute':0}})
 with patch.object(fresh,'refresh',return_value={'started':True}) as refresh:
  disabled=fresh.tick(fixed)
 add('P08-N-11','Disabled schedule has no next-run and no job start',disabled is False and not refresh.called and fresh.overview()['next_run'] is None)
 xml=('''<rss><channel><item><title>fresh</title><link>https://example.com/fresh</link><pubDate>Sat, 31 Oct 2026 00:00:00 GMT</pubDate></item><item><title>old</title><link>https://example.com/old</link><pubDate>Mon, 26 Oct 2026 00:00:00 GMT</pubDate></item><item><title>untimed</title><link>https://example.com/untimed</link></item><item><title>future</title><link>https://example.com/future</link><pubDate>Sun, 1 Nov 2026 00:00:00 GMT</pubDate></item></channel></rss>''').encode()
 with patch('newsroom.safe_open',return_value=Response(xml)):
  rows=nr.collect(nr.SOURCES[0],fixed)
 add('P08-N-12','Collect rejects over-72h, future and untimed candidates',[r['title'] for r in rows]==['fresh'])
 source=(ROOT/'dist/workspace.js').read_text()
 add('P08-N-13','UI explicitly labels retained stale data and continuous backend dependency','上次內容，不是今日更新' in source and '後端必須持續執行' in source and '未滿5則不湊數' in source)
 report={'role':'P08 newsroom honesty boundaries','evidence':'Temporary SQLite + fake sources/clock/model transport; no browser, HTTP server or real external API/key. No secret files read.','hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'backend/newsroom.py',ROOT/'dist/workspace.js']},'cases':[{('pass' if k=='pass_' else k):v for k,v in c.items()} for c in cases],'passed':sum(c['pass_'] for c in cases),'total':len(cases),'stale_ui_fixture':stale,'limits':['ID validation cannot independently prove semantic truth of generated summary; user must inspect original source.','Scheduler not observed for a real overnight run; direct tick time-boundary tests only.']}
 target=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'qa/reports/P08_newsroom_raw.json'
 target.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='stale_ui_fixture'},ensure_ascii=False,indent=2))
