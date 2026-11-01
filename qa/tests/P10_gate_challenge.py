"""Adversarial mechanical-gate tests; generated reports are UNIT FIXTURES ONLY."""
import copy,importlib.util,json,sys,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('p03_gate_unit',ROOT/'qa/tests/P03_release_gate.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
rows=[]
def run(name,mutate):
    suite=mod.ReleaseGate('test_structurally_complete_test_fixture_passes_only_mechanical_gate');suite.setUp()
    try:
        mutate(suite);result=suite.gate();rows.append({'case':name,'expected_production_ready':False,'observed_production_ready':result['production_ready'],'passed':not result['production_ready'],'blocker_codes':sorted({b['code'] for b in result['blockers']})})
    except Exception as exc:rows.append({'case':name,'passed':False,'exception':type(exc).__name__})
    finally:suite.tearDown()
run('Top mocked numeric truthy must not become real execution evidence',lambda t:t.reports['live'].update(mocked=1))
run('Per-case mocked string true must not become real network evidence',lambda t:t.case('live','ai_financial_summary').update(mocked='true'))
run('Source unchanged numeric false should not certify unchanged production bytes',lambda t:t.reports['live'].update(source_unchanged=0))
run('Requested market symbols absent from observed quotes cannot pass',lambda t:t.case('live','markets_live').update(requested_symbols=['TW:TAIEX','TW:2330','US:SPY','US:META','US:DJI']))
run('Market chart for a different unquoted symbol cannot validate quote chart evidence',lambda t:t.case('live','markets_live')['charts'][0].update(symbol='UNRELATED'))
run('Plain overall true cannot replace missing per-case execution',lambda t:t.reports['live'].update(overall=True,cases=[]))
run('Explicit stale AI news cannot pass',lambda t:t.case('live','news_ai').update(stale=True))
run('Explicit mocked boolean true cannot pass',lambda t:t.reports['live'].update(mocked=True))
report={'kind':'unit_test','mocked':True,'release_evidence':False,'method':'Mechanical gate challenged with P03 synthetic unit fixture report generator; no live evidence/browser/network/secret','passed':sum(r['passed'] for r in rows),'total':len(rows),'cases':rows}
report['gate_sha256']=hashlib.sha256((ROOT/'backend/release_gate.py').read_bytes()).hexdigest()
out=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'qa/reports/P10_gate_challenge_r1.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
