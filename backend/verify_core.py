"""Run current local tests; this never substitutes for browser/live acceptance."""
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from release_gate import source_manifest

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

def utc():
    return datetime.now(timezone.utc).isoformat()

def counts(completed):
    try:
        value,_=json.JSONDecoder().raw_decode(completed.stdout.lstrip())
        total=value.get('total')
        passed=value.get('passed',value.get('pass'))
        failed=value.get('failed',value.get('fail'))
        if type(passed) is not int:
            rows=value.get('checks',value.get('cases',value.get('results',[])))
            passed=sum(row.get('pass',row.get('passed',row.get('status')=='passed')) is True for row in rows)
            total=len(rows)
        if type(total) is int:
            failed=total-passed
        if type(failed) is not int:
            raise ValueError('Missing failed count')
        return {'passed':passed,'failed':failed,'errors':0}
    except (ValueError,TypeError,AttributeError):
        output=completed.stderr+'\n'+completed.stdout
        match=re.search(r'Ran (\d+) tests?',output)
        if match and completed.returncode==0 and re.search(r'^OK\s*$',output,re.M):
            return {'passed':int(match[1]),'failed':0,'errors':0}
        return {'passed':0,'failed':0,'errors':1}

def run():
    started=utc();run_id=uuid.uuid4().hex;folder=ROOT/'outputs/core-verification'/run_id;folder.mkdir(parents=True)
    before=source_manifest(ROOT)
    env=os.environ.copy()
    for key in list(env):
        if key.startswith('LEDGER_DEEPSEEK_'):
            env.pop(key)
    env['PYTHONWARNINGS']='ignore::ResourceWarning'
    node=shutil.which('node')
    groups=[
      ('ledger','python_dispatch_sqlite',[[PY,'-m','unittest','discover','-s','qa/tests','-p','test_backend_store.py','-q'],[PY,'qa/tests/P09_backend.py','release_core_'+run_id]]),
      ('parsers','python_unit',[[PY,'qa/tests/P01_backend.py']]),
      ('planning','python_dispatch_sqlite',[[PY,'-m','unittest','discover','-s','qa/tests','-p','test_planning_state.py','-q'],[PY,'qa/tests/test_planning_money.py']]),
      ('summary','python_dispatch_sqlite',[[PY,'-m','unittest','discover','-s','qa/tests','-p','test_summary_api.py','-q'],[PY,'-m','unittest','discover','-s','qa/tests','-p','test_api_payloads.py','-q'],[PY,'qa/tests/test_financial_summary_money.py'],[PY,'qa/tests/P08_financial_summary.py'],[PY,'qa/tests/P08_summary_api.py',str(folder/'summary-api.json')]]),
      ('newsroom','python_dispatch_sqlite',[[PY,'-m','unittest','discover','-s','qa/tests','-p','test_newsroom_schedule.py','-q'],[PY,'-m','unittest','discover','-s','qa/tests','-p','test_news_coverage.py','-q'],[PY,'qa/tests/P10_newsroom.py',str(folder/'newsroom.json')]]),
      ('services','python_unit',[[PY,'qa/tests/test_services.py']]),
      ('markets','python_dispatch_sqlite',[[PY,'qa/tests/test_market_adapters.py'],[PY,'qa/tests/P08_markets.py',str(folder/'markets-independent.json')],[PY,'qa/tests/P08_market_history.py',str(folder/'market-history-independent.json')],[PY,'qa/tests/P10_market_settings_backend.py']]),
      ('browser_handlers','node_vm_handlers',[[node or 'node','qa/tests/P03_v05_actions.cjs'],[node or 'node','qa/tests/P06_v05_button_audit.cjs'],[node or 'node','qa/tests/P10_button_audit.cjs',str(folder/'buttons.json')],[node or 'node','qa/tests/P07_market_charts.cjs'],[node or 'node','qa/tests/P10_market_settings.cjs'],[node or 'node','qa/tests/P06_news_cache_refresh_r1.cjs'],[node or 'node','qa/tests/P07_landing.cjs']])]
    cases=[]
    for case_id,method,commands in groups:
        aggregate={'passed':0,'failed':0,'errors':0};steps=[];exit_code=0
        for index,command in enumerate(commands):
            try:
                result=subprocess.run(command,cwd=ROOT,env=env,text=True,capture_output=True,timeout=60)
                measured=counts(result);code=result.returncode;log=result.stdout+'\n'+result.stderr
            except (OSError,subprocess.TimeoutExpired) as exc:
                code=1;measured={'passed':0,'failed':0,'errors':1};log=type(exc).__name__
            log_path=folder/(case_id+'-'+str(index)+'.log');log_path.write_text(log)
            for key in aggregate:aggregate[key]+=measured[key]
            exit_code=exit_code or code
            steps.append({'command':command,'exit_code':code,'counts':measured,'log':str(log_path.relative_to(ROOT))})
        status='passed' if exit_code==0 and aggregate['passed']>0 and not aggregate['failed'] and not aggregate['errors'] else 'failed'
        cases.append({'id':case_id,'method':method,'status':status,'exit_code':exit_code,'counts':aggregate,'steps':steps})
        print(case_id+': '+status,flush=True)
    after=source_manifest(ROOT)
    finished=utc()
    report={'schema':'ledger-release-evidence-v1','kind':'core','run_id':run_id,'at':finished,'started_at':started,'finished_at':finished,'source_hashes':after,'source_unchanged':before==after,'cases':cases,'passed':before==after and all(c['status']=='passed' for c in cases),'limitations':['These checks include mocks and Node VM handlers; no browser or real external AI/news execution.','Counts across scripts may overlap; not an independent-user or whole-product score.']}
    output=ROOT/'outputs/core-verification.json';output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('Core evidence: '+str(output))
    return 0 if report['passed'] else 1
if __name__=='__main__':
    raise SystemExit(run())
