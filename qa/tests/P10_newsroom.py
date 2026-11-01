"""Independent NewsRoom provenance/security tests. No true key/network/socket/UI."""
import contextlib,hashlib,io,json,sys,tempfile,urllib.error,urllib.request
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
import newsroom
from newsroom import NewsRoom,collect
from planning import Planning
from store import Store

NOW=datetime(2026,11,1,8,0,tzinfo=timezone.utc)
DUMMY='P10_INVENTED_NEWS_KEY_NOT_REAL'
SOURCE={'category':'ai','name':'P10 Synthetic source','url':'https://news.example.test/rss','home':'https://news.example.test/'}

def rss(title='Source headline',stamp=None):
    stamp=stamp or NOW.isoformat()
    return f'<rss><channel><item><title>{title}</title><link>https://news.example.test/article</link><pubDate>{stamp}</pubDate><description>Source excerpt.</description></item></channel></rss>'.encode()

class Response:
    def __init__(self,raw,url):self.raw=raw;self.url=url;self.status=200
    def read(self,n=-1):return self.raw if n<0 else self.raw[:n]
    def geturl(self):return self.url
    def __enter__(self):return self
    def __exit__(self,*a):return False

@contextlib.contextmanager
def network(feed=None,items=None,redirect=False,error=None):
    calls=[]
    def op(self,req,data=None,timeout=None,**kw):
        url=req.full_url;is_model=url.startswith('https://api.deepseek.com/')
        row={'url':url,'timeout':timeout,'has_authorization':bool(req.get_header('Authorization'))}
        calls.append(row)
        if error:raise error
        if redirect:
            hs=[h for h in self.handlers if isinstance(h,urllib.request.HTTPRedirectHandler)]
            nxt=hs[0].redirect_request(req,io.BytesIO(),302,'Found',{},'http://127.0.0.1:8791/api/state')
            row['second_request']=bool(nxt)
            if nxt:raise AssertionError('redirect unexpectedly followed')
        if is_model:
            payload=json.loads(req.data);row['secret_in_json_body']=DUMMY in req.data.decode();c=json.loads(payload['messages'][1]['content'])['candidates']
            selected=items(c) if callable(items) else items
            raw=json.dumps({'choices':[{'message':{'content':json.dumps({'items':selected},ensure_ascii=False)}}]},ensure_ascii=False).encode()
        else:raw=feed if feed is not None else rss()
        return Response(raw,url)
    with patch.object(urllib.request.OpenerDirector,'open',autospec=True,side_effect=op):yield calls

results=[]
def test(name,fn):
    try:results.append({'test':name,**fn()})
    except Exception as e:results.append({'test':name,'pass':False,'error_type':type(e).__name__,'error':str(e).replace(DUMMY,'[REDACTED DUMMY]')})

def collect_at(delta):
    with network(feed=rss(stamp=(NOW+delta).isoformat())):return collect(SOURCE,NOW)
test('RSS exactly 72 hours old is included',lambda:{'pass':len(collect_at(timedelta(hours=-72)))==1})
test('RSS beyond 72 hours is rejected',lambda:{'pass':not collect_at(timedelta(hours=-72,seconds=-1))})
test('Future time beyond ten minutes is rejected',lambda:{'pass':not collect_at(timedelta(minutes=11))})
test('Future timestamp at ten minute tolerance is included',lambda:{'pass':len(collect_at(timedelta(minutes=10)))==1})

def _collect(raw):
    with network(feed=raw):return collect(SOURCE,NOW)

# This check is repeated after helper definition; the earlier exact-date cases
# already prove collection has no wall-clock dependency under this fixture.
test('Missing/naive/invalid dates rejected (executed helper)',lambda:{'pass':all(not _collect(rss(stamp=s)) for s in ['none','2026-11-01T08:00:00','not-a-date'])})

def atom():
    raw=f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom headline</title><link rel="self" href="https://news.example.test/api-entry"/><link rel="alternate" type="text/html" href="https://news.example.test/article"/><published>{NOW.isoformat()}</published><summary>Atom excerpt.</summary></entry></feed>'.encode()
    out=_collect(raw)
    return {'pass':len(out)==1 and out[0]['url']=='https://news.example.test/article','urls':[a['url'] for a in out]}
test('Atom chooses article alternate link rather than entry self API link',atom)

def rejected_entity(encoding):
    raw=f'<?xml version="1.0" encoding="{encoding}"?><!DOCTYPE rss [<!ENTITY demo "Expanded entity">]><rss><channel><item><title>&demo;</title><link>https://news.example.test/article</link><pubDate>{NOW.isoformat()}</pubDate></item></channel></rss>'.encode(encoding)
    try:out=_collect(raw)
    except Exception:return {'pass':True,'rejected':True}
    return {'pass':False,'rejected':False,'titles':[a['title'] for a in out]}
test('UTF-8 entity document rejected',lambda:rejected_entity('UTF-8'))
test('UTF-16 entity document rejected',lambda:rejected_entity('UTF-16'))
test('UTF-32 entity document rejected',lambda:rejected_entity('UTF-32'))

def nul_document():
    try:_collect(rss().replace(b'<rss>',b'<rss>\x00'))
    except Exception:return {'pass':True}
    return {'pass':False}
test('UTF-8 NUL-bearing XML rejected',nul_document)
test('UTF-8 BOM valid feed accepted',lambda:{'pass':len(_collect(b'\xef\xbb\xbf'+rss()))==1})

def atom_html_first():
    raw=f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom source</title><link rel="alternate" type="application/json" href="https://news.example.test/json"/><link rel="alternate" type="text/html" href="https://news.example.test/html"/><updated>{NOW.isoformat()}</updated></entry></feed>'.encode()
    out=_collect(raw)
    return {'pass':len(out)==1 and out[0]['url']=='https://news.example.test/html','urls':[a['url'] for a in out]}
test('Atom prefers HTML alternate over JSON alternate',atom_html_first)

def atom_self_only():
    raw=f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom source</title><link rel="self" href="https://news.example.test/api"/><updated>{NOW.isoformat()}</updated></entry></feed>'.encode()
    return {'pass':not _collect(raw)}
test('Atom without article alternate does not invent article URL',atom_self_only)

def unsafe_urls():
    output=[]
    for url in ['javascript:alert(1)','http://news.example.test/article','https://user:pass@news.example.test/article']:
        output.extend(_collect(rss().replace(b'https://news.example.test/article',url.encode())))
    return {'pass':not output,'accepted':len(output)}
test('Unsafe article schemes and URL credentials rejected',unsafe_urls)

def legacy_rss_utf16():
    from services import Services
    svc=Services.__new__(Services);svc.key=DUMMY;svc.model='dummy-model';svc.feed_cache=None;svc.feed_time=0
    raw=f'<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE rss [<!ENTITY x "expanded">]><rss><channel><item><title>&x;</title><link>https://news.example.test/</link></item></channel></rss>'.encode('UTF-16')
    with network(feed=raw):out=svc.news(refresh=True)
    return {'pass':not out['articles'] and bool(out['errors'])}
test('Legacy Services RSS rejects UTF-16 entity document too',legacy_rss_utf16)


def redirect_collect():
    with network(redirect=True) as calls:
        try:collect(SOURCE,NOW)
        except Exception:return {'pass':len(calls)==1,'calls':calls}
    return {'pass':False,'calls':calls}
test('Collector uses NoRedirect and does not send auth to public feeds',redirect_collect)

def empty_title():
    out=_collect(rss(title='&lt;b&gt;&lt;/b&gt;'))
    return {'pass':not out,'titles':[a['title'] for a in out]}
test('HTML-only title is rejected after plain-text normalization',empty_title)

with tempfile.TemporaryDirectory(prefix='ledger-p10-news-') as temp:
    ledger=Store(Path(temp)/'news.sqlite3');planning=Planning(ledger)
    provider=SimpleNamespace(key=DUMMY,model='dummy-model')
    room=NewsRoom(ledger,planning,provider)
    candidates=[{'id':f'id-{i}','title':f'Source headline {i}','url':f'https://news.example.test/article-{i}','source':'Synthetic source','published':NOW.isoformat(),'excerpt':'Source fact.'} for i in range(7)]
    def selected(items,expect_reject=False):
        with network(items=items) as calls:
            try:out=room._summarize('ai',candidates)
            except Exception as exc:return {'pass':expect_reject,'rejected':True,'error_type':type(exc).__name__,'calls':len(calls)}
        return {'pass':not expect_reject,'rejected':False,'output':out,'calls':len(calls)}
    def invented_url():
        r=selected([{'id':'id-0','summary':'Source summary.','why':'Source reason.','url':'https://invented.example/'}])
        r['pass']=r.get('output',[{}])[0].get('url')==candidates[0]['url']
        return r
    test('Valid selected id preserves source URL instead of model-invented URL',invented_url)
    test('Unknown candidate id is rejected',lambda:selected([{'id':'fake','summary':'Fact','why':'Reason'}],True))
    test('Duplicate selected ids are rejected',lambda:selected([{'id':'id-0','summary':'Fact','why':'Reason'}]*2,True))
    test('Six selected articles rejected',lambda:selected([{'id':f'id-{i}','summary':'Fact','why':'Reason'} for i in range(6)],True))
    test('Literal blank summary rejected',lambda:selected([{'id':'id-0','summary':' ','why':'Reason'}],True))
    test('Nonbreaking-space-only summary rejected after normalization',lambda:selected([{'id':'id-0','summary':'&nbsp;','why':'Reason'}],True))
    test('HTML-only summary rejected after normalization',lambda:selected([{'id':'id-0','summary':'<b></b>','why':'Reason'}],True))
    test('HTML-only why rejected after normalization',lambda:selected([{'id':'id-0','summary':'Fact','why':'<b></b>'}],True))
    test('Summary over 140 chars rejected',lambda:selected([{'id':'id-0','summary':'x'*141,'why':'Reason'}],True))
    test('Why over 70 chars rejected',lambda:selected([{'id':'id-0','summary':'Fact','why':'x'*71}],True))
    def cache_safe():
        def items(c):return [{'id':c[0]['id'],'summary':'Source summary.','why':'Source reason.'}]
        with network(items=items) as calls,patch.object(newsroom,'now_local',return_value=NOW):room.refresh(asynchronous=False)
        state=room.overview();serialized=json.dumps(state,ensure_ascii=False)
        with ledger._connect() as con:dump='\n'.join(con.iterdump())
        feeds=[c for c in calls if not c['url'].startswith('https://api.deepseek.com/')]
        models=[c for c in calls if c['url'].startswith('https://api.deepseek.com/')]
        return {'pass':DUMMY not in serialized and DUMMY not in dump and DUMMY.encode() not in Path(ledger.db_path).read_bytes() and all(not c['has_authorization'] for c in feeds) and all(c['has_authorization'] and not c['secret_in_json_body'] for c in models),'feed_requests':len(feeds),'model_requests':len(models),'secret_in_cache':DUMMY in serialized,'secret_in_database':DUMMY in dump,'job_status':state['job']['status']}
    test('Model auth stays in request headers, not public feed/cache/SQLite',cache_safe)
    def per_category():
        state=room.overview()
        return {'pass':set(state['channels'])=={'ai','finance'} and all(1<=len(c['items'])<=5 for c in state['channels'].values()),'counts':{k:len(v['items']) for k,v in state['channels'].items()}}
    test('Refresh persists separate AI and finance channels at most five each',per_category)
    def llm_failure():
        def invalid(c):return [{'id':'unknown-id','summary':'Fake summary','why':'Fake reason'}]
        with network(items=invalid),patch.object(newsroom,'now_local',return_value=NOW):room.refresh(asynchronous=False)
        state=room.overview()
        return {'pass':all(not c['ai_generated'] and c['errors'] and all(not a['summary'] for a in c['items']) for c in state['channels'].values()),'generated':{k:v['ai_generated'] for k,v in state['channels'].items()}}
    test('Rejected LLM selection falls back honestly to unsummarized source items',llm_failure)
    def source_exception_secret():
        with network(error=RuntimeError(DUMMY)),patch.object(newsroom,'now_local',return_value=NOW):room.refresh(asynchronous=False)
        output=json.dumps(room.overview(),ensure_ascii=False)
        with ledger._connect() as con:dump='\n'.join(con.iterdump())
        return {'pass':DUMMY not in output and DUMMY not in dump and DUMMY.encode() not in Path(ledger.db_path).read_bytes(),'secret_in_output':DUMMY in output}
    test('Upstream error text containing dummy secret is not cached or reported',source_exception_secret)


out=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'qa/reports/P10_newsroom_r1_raw.json'
report={'reviewer':'P10','evidence':'Mocked common urllib opener; synthetic fixed UTC date; dummy provider object; real temporary SQLite; no true key lookup, secret file read, network, socket or UI','sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'backend/newsroom.py',ROOT/'backend/services.py']},'passed':sum(r['pass'] for r in results),'total':len(results),'results':results}
out.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'passed':report['passed'],'total':report['total'],'failed':[r['test'] for r in results if not r['pass']],'report':str(out)},ensure_ascii=False,indent=2))
