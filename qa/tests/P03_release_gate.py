"""Pure release-gate tests. Positive reports below are synthetic TEST FIXTURES.

They are never written as execution evidence or used to claim real E2E/live
success. The real current gate remains blocked until real evidence exists.
"""
import base64
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from release_gate import (SCHEMA,CORE_CASES,BROWSER_CASES,LIVE_CASES,SCORE_WEIGHTS,
                          evaluate_reports,source_manifest)

class ReleaseGate(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='ledger-gate-unit-fixture-')
        self.root=Path(self.tmp.name)
        for d in ['backend','dist','fixtures','qa/artifacts']:(self.root/d).mkdir(parents=True)
        (self.root/'backend/app.py').write_text('test fixture source')
        (self.root/'dist/index.html').write_text('<title>Gate unit fixture only</title>')
        (self.root/'fixtures/input.csv').write_text('fixture')
        (self.root/'build_web.py').write_text('fixture builder')
        self.now=datetime(2026,11,1,3,0,tzinfo=timezone.utc)
        self.manifest=source_manifest(self.root)
        stamp=self.now.isoformat()
        def report(kind):return {'schema':SCHEMA,'kind':kind,'run_id':'TEST-FIXTURE-'+kind,'at':stamp,'started_at':stamp,'finished_at':stamp,'source_hashes':self.manifest}
        core=report('core');core['cases']=[{'id':i,'status':'passed','method':'node_vm_handlers' if i=='browser_handlers' else 'python_dispatch_sqlite','exit_code':0,'counts':{'passed':1,'failed':0,'errors':0}} for i in sorted(CORE_CASES)]
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6fTcAAAAASUVORK5CYII=')
        (self.root/'qa/artifacts/test-only.png').write_bytes(png)
        browser=report('browser');browser.update(browser={'engine':'TEST-FIXTURE','transport':'real_browser','mode':'connected_backend','mocked':False,'origin':'http://127.0.0.1:8791'},backend_health={'ok':True,'storage':'SQLite'},artifacts=[{'path':'qa/artifacts/test-only.png','kind':'screenshot','sha256':hashlib.sha256(png).hexdigest()}],cases=[{'id':i,'status':'passed','method':'real_browser','events':[{'type':'click'},{'type':'reload'}],'assertions':[{'name':'synthetic observed value','actual':1,'expected':1}]}for i in sorted(BROWSER_CASES)])
        live=report('live');live['cases']=[]
        live['cases'].append({'id':'ai_financial_summary','status':'passed','method':'real_network','provider':'DeepSeek','http_status':200,'endpoint':'/api/ai/summary','generated':True,'answer':'Synthetic TEST FIXTURE answer, not an AI result','statistics':{'current':{'income':'10.00','expense':'3.00','balance':'7.00'}}})
        for channel in ['ai','finance']:
            live['cases'].append({'id':'news_'+channel,'status':'passed','method':'real_network','provider':'DeepSeek','http_status':200,'channel':channel,'ai_generated':True,'stale':False,'generated_at':stamp,'fetched_at':stamp,'items':[{'title':'TEST FIXTURE ONLY','url':'https://example.invalid/test-fixture','summary':'Synthetic test summary','why':'Synthetic test relevance','published':stamp}]})
        live['cases'].append({'id':'markets_live','status':'passed','method':'real_network','provider':'TEST FIXTURE provider','http_status':200,'stale':False,'fetched_at':stamp,'quotes':[{'symbol':'TEST','price':'1.00','as_of':stamp}],'charts':[{'symbol':'TEST','points':[{'at':(self.now-timedelta(days=1)).isoformat(),'price':'0.90'},{'at':stamp,'price':'1.00'}]}]})
        scores=report('scores');scores.update(major_failures=[],roles=[{'role_id':f'P{i:02}','scores':dict(SCORE_WEIGHTS),'major_failures':[],'ui_browser_run_id':browser['run_id']}for i in range(1,11)])
        self.reports={'core':core,'browser':browser,'live':live,'scores':scores}
    def tearDown(self):self.tmp.cleanup()
    def gate(self,**changes):
        reports=copy.deepcopy(self.reports);reports.update(changes)
        return evaluate_reports(self.root,now=self.now,**reports)
    def case(self,kind,ident):return next(c for c in self.reports[kind]['cases'] if c['id']==ident)
    def blocked(self,code):
        result=self.gate();self.assertFalse(result['production_ready']);self.assertIn(code,{b['code']for b in result['blockers']});return result
    def test_structurally_complete_test_fixture_passes_only_mechanical_gate(self):
        r=self.gate();self.assertTrue(r['production_ready'],r['blockers']);self.assertEqual(r['score_average'],100);self.assertFalse(r['full_project_complete']);self.assertEqual(r['scope']['ios_notifications'],'planned_not_delivered')
    def test_missing_all_evidence_never_passes(self):
        r=evaluate_reports(self.root,now=self.now);self.assertFalse(r['deliverable']);self.assertEqual(r['status'],'blocked')
    def test_overall_true_without_cases_never_passes(self):
        self.reports['core']={'passed':True,'overall':True};self.blocked('missing_cases')
    def test_core_positive_boolean_exit_code_is_not_zero(self):
        self.case('core','ledger')['exit_code']=False;self.blocked('runner_failed')
    def test_core_zero_tests_does_not_count(self):
        self.case('core','ledger')['counts']['passed']=0;self.blocked('invalid_test_counts')
    def test_core_missing_required_suite(self):
        self.reports['core']['cases']=[c for c in self.reports['core']['cases']if c['id']!='planning'];self.blocked('missing_case')
    def test_any_extra_failed_case_blocks_even_with_success_overall(self):
        self.reports['core']['overall']=True;self.reports['core']['cases'].append({'id':'extra','status':'failed','method':'python_unit','exit_code':0,'counts':{'passed':1,'failed':0,'errors':0}});self.blocked('case_not_passed')
    def test_changed_current_source_invalidates_every_report(self):
        (self.root/'backend/app.py').write_text('changed');self.blocked('source_mismatch')
    def test_extra_source_file_invalidates_evidence(self):
        (self.root/'dist/extra.js').write_text('new');self.blocked('source_mismatch')
    def test_verifier_and_qa_changes_do_not_self_invalidate(self):
        (self.root/'backend/verify_core.py').write_text('verifier');(self.root/'backend/release_gate.py').write_text('gate');(self.root/'qa/result.json').write_text('evidence');self.assertEqual(source_manifest(self.root),self.manifest)
    def test_stale_run_fails_without_rescaling(self):
        self.reports['browser']['started_at']=(self.now-timedelta(hours=25)).isoformat();self.blocked('stale_evidence')
    def test_future_and_naive_timestamps_block(self):
        self.reports['core']['at']=(self.now+timedelta(days=1)).isoformat();self.blocked('invalid_time_order');self.reports['core']['at']='2026-11-01T03:00:00';self.blocked('invalid_time')
    def test_report_admits_source_changed_during_run(self):
        self.reports['core']['source_unchanged']=False;self.blocked('source_changed_during_run')
    def test_blocked_browser_does_not_turn_into_pass(self):
        c=self.case('browser','app_load');c.update(status='blocked',blocker='Listener unavailable');self.blocked('case_not_passed')
    def test_vm_cannot_count_as_real_browser(self):
        self.case('browser','app_load')['method']='node_vm_handlers';self.blocked('invalid_method')
    def test_demo_backend_cannot_count_as_sqlite_e2e(self):
        self.reports['browser']['backend_health']['storage']='Browser experience';self.blocked('backend_unverified')
    def test_screenshot_missing_tampered_or_path_escape_block(self):
        self.reports['browser']['artifacts'][0]['sha256']='0'*64;self.blocked('missing_browser_artifact');self.reports['browser']['artifacts'][0]['path']='../outside.png';self.blocked('missing_browser_artifact')
    def test_assertion_pass_flag_cannot_override_actual_mismatch(self):
        self.case('browser','app_load')['assertions']=[{'name':'value','expected':1,'actual':2,'passed':True}];self.blocked('assertion_failed')
    def test_empty_or_null_assertions_not_evidence(self):
        self.case('browser','app_load')['assertions']=[{'name':'value','expected':None,'actual':None}];self.blocked('assertion_failed')
    def test_bool_not_same_observed_value_as_int(self):
        self.case('browser','app_load')['assertions']=[{'name':'value','expected':1,'actual':True}];self.blocked('assertion_failed')
    def test_persistence_without_reload_blocks(self):
        self.case('browser','account_persistence')['events']=[{'type':'click'}];self.blocked('persistence_not_reloaded')
    def test_malformed_browser_does_not_crash(self):
        self.case('browser','account_persistence')['events']=None;self.reports['browser']['browser']['origin']='https://[bad';self.blocked('not_real_browser')
    def test_mock_or_diagnostics_only_live_is_blocked(self):
        self.reports['live']['diagnostics_only']=True;self.blocked('nonexecution_evidence')
    def test_failed_summary_with_overall_true_blocks(self):
        self.case('live','ai_financial_summary')['generated']=False;self.reports['live']['passed']=True;self.blocked('summary_not_generated')
    def test_summary_legacy_chat_endpoint_cannot_replace_summary_route(self):
        self.case('live','ai_financial_summary')['endpoint']='/api/ai/chat';self.blocked('summary_not_generated')
    def test_summary_statistics_must_be_consistent(self):
        self.case('live','ai_financial_summary')['statistics']['current']['balance']='100';self.blocked('summary_statistics_invalid')
    def test_both_news_categories_required(self):
        self.reports['live']['cases']=[c for c in self.reports['live']['cases']if c['id']!='news_finance'];self.blocked('missing_case')
    def test_stale_news_cache_and_empty_items_block(self):
        c=self.case('live','news_ai');c['stale']=True;self.blocked('news_not_generated');c['stale']=False;c['items']=[];self.blocked('empty_news')
    def test_old_news_generation_and_old_publication_block(self):
        c=self.case('live','news_ai');c['generated_at']=(self.now-timedelta(hours=7)).isoformat();self.blocked('stale_live_content');c['generated_at']=self.now.isoformat();c['items'][0]['published']=(self.now-timedelta(hours=73)).isoformat();self.blocked('invalid_news_item')
    def test_market_missing_or_no_history_blocks(self):
        c=self.case('live','markets_live');c['charts']=[];self.blocked('market_charts_missing')
    def test_market_eod_precision_is_honest_and_supported(self):
        c=self.case('live','markets_live');c.pop('charts');c['data_delay']='daily_eod';c['quotes']=[{'symbol':'TEST','price':'1','as_of':'2026-10-30','as_of_precision':'date','history':[{'date':'2026-10-29','close':'0.9'},{'date':'2026-10-30','close':'1'}]}];self.assertTrue(self.gate()['production_ready'])
    def test_missing_ocr_scope_cannot_be_silently_reduced_to_templates(self):
        self.reports['browser']['cases']=[c for c in self.reports['browser']['cases']if c['id']!='scanned_pdf_ocr'];self.blocked('scope_incomplete')
    def test_null_ui_cannot_be_normalized_from_eighty(self):
        for r in self.reports['scores']['roles']:r['scores']['ui']=None
        self.blocked('invalid_score')
    def test_exactly_ten_unique_roles_required(self):
        self.reports['scores']['roles'][-1]=copy.deepcopy(self.reports['scores']['roles'][0]);self.blocked('invalid_role')
    def test_each_role_minimum_eighty(self):
        self.reports['scores']['roles'][0]['scores']={'task':18.75,'fidelity':15,'persistence':15,'ui':15,'scope':10,'error_state':5};self.blocked('role_below_80')
    def test_average_eightyfive_required(self):
        for r in self.reports['scores']['roles']:r['scores']={'task':25,'fidelity':20,'persistence':15,'ui':10,'scope':10,'error_state':0}
        self.blocked('average_below_85')
    def test_major_failure_not_averaged_away(self):
        self.reports['scores']['major_failures']=['financial double count'];self.blocked('major_failure')
    def test_arbitrary_score_and_unbound_ui_rejected(self):
        self.reports['scores']['roles'][0]['scores']['task']=24;self.blocked('invalid_score');self.reports['scores']['roles'][0]['scores']['task']=25;self.reports['scores']['roles'][0]['ui_browser_run_id']='unrelated';self.blocked('unbound_ui_score')
    def test_nonproduction_preview_is_explicit_when_core_only_passes(self):
        r=self.gate(browser=None,live=None,scores=None);self.assertFalse(r['production_ready']);self.assertEqual(r['experience_status'],'nonproduction_only')
    def test_exact_average_eightyfive_is_accepted_without_rounding(self):
        for r in self.reports['scores']['roles']:r['scores']={'task':18.75,'fidelity':20,'persistence':20,'ui':15,'scope':10,'error_state':1.25}
        result=self.gate();self.assertEqual(result['score_average'],85);self.assertTrue(result['production_ready'])
    def test_one_role_exactly_eighty_is_accepted_when_average_exceeds_eightyfive(self):
        self.reports['scores']['roles'][0]['scores']={'task':25,'fidelity':20,'persistence':15,'ui':10,'scope':10,'error_state':0}
        result=self.gate();self.assertEqual(result['score_totals']['P01'],80);self.assertTrue(result['production_ready'])
    def test_core_admitted_major_failure_not_hidden_by_score_report(self):
        self.reports['core']['major_failures']=['integrity'];self.blocked('major_failure')
    def test_market_null_history_is_rejected_not_crashed(self):
        c=self.case('live','markets_live');c.pop('charts');c['quotes'][0]['history']=None;self.blocked('market_chart_invalid')
    def test_challenge_numeric_mock_flag_cannot_be_false(self):
        self.reports['live']['mocked']=1;self.blocked('invalid_evidence_flag')
    def test_challenge_case_string_mock_flag_rejected(self):
        self.case('live','ai_financial_summary')['mocked']='true';self.blocked('invalid_evidence_flag')
    def test_challenge_numeric_source_unchanged_rejected(self):
        self.reports['live']['source_unchanged']=0;self.blocked('invalid_evidence_flag')
    def test_challenge_requested_symbols_all_need_observed_quote(self):
        self.case('live','markets_live')['requested_symbols']=['TW:TAIEX','TW:2330','US:SPY','US:META','US:DJI'];self.blocked('market_requested_quotes_missing')
    def test_challenge_chart_not_in_observed_quotes_rejected(self):
        self.case('live','markets_live')['charts'][0]['symbol']='UNRELATED';self.blocked('market_chart_unbound')
    def test_truthy_and_falsy_nonboolean_execution_flags_all_rejected(self):
        for flag in ['mocked','diagnostics_only']:
            for bad in [0,1,'false','true',None,[],{}]:
                with self.subTest(flag=flag,bad=bad):
                    original=copy.deepcopy(self.reports)
                    self.reports['live'][flag]=bad;self.blocked('invalid_evidence_flag');self.reports=original
        for bad in [0,1,'true',None,[],{}]:
            with self.subTest(source_unchanged=bad):
                original=copy.deepcopy(self.reports)
                self.case('browser','app_load')['source_unchanged']=bad;self.blocked('invalid_evidence_flag');self.reports=original
    def test_explicit_safe_boolean_flags_are_accepted(self):
        self.reports['live'].update(mocked=False,diagnostics_only=False,source_unchanged=True)
        self.case('live','markets_live').update(mocked=False,source_unchanged=True)
        self.assertTrue(self.gate()['production_ready'])
    def test_http_status_must_not_be_float_coercion(self):
        self.case('live','ai_financial_summary')['http_status']=200.0;self.blocked('provider_unverified')
    def test_requested_symbols_duplicate_and_nontext_rejected(self):
        for invalid in [['TEST','TEST'],[{}],[1],[],None,'TEST']:
            with self.subTest(requested=invalid):
                self.case('live','markets_live')['requested_symbols']=invalid;self.blocked('market_request_invalid')
    def test_complete_requested_quotes_with_only_one_real_chart_is_accepted(self):
        c=self.case('live','markets_live');q=c['quotes'][0];q.update(id='TW:2330',market='TW',symbol='2330')
        c['quotes'].append(dict(q,id='US:SPY',market='US',symbol='SPY'))
        c['requested_symbols']=['TW:2330','US:SPY'];c['charts'][0].update(instrument_id='TW:2330',symbol='2330')
        self.assertTrue(self.gate()['production_ready'])
    def test_quote_market_symbol_can_canonicalize_without_explicit_id(self):
        c=self.case('live','markets_live');c['quotes'][0].update(market='TW',symbol='2330');c['requested_symbols']=['TW:2330'];c['charts'][0].update(instrument_id='TW:2330',symbol='2330')
        self.assertTrue(self.gate()['production_ready'])
    def test_chart_instrument_and_symbol_cannot_disagree(self):
        c=self.case('live','markets_live');c['charts'][0].update(instrument_id='TEST',symbol='UNRELATED');self.blocked('market_chart_unbound')
    def test_duplicate_observed_identity_rejected(self):
        c=self.case('live','markets_live');c['quotes'].append(copy.deepcopy(c['quotes'][0]));self.blocked('market_identity_invalid')
    def test_quote_level_stale_or_old_fetch_not_hidden_by_top_freshness(self):
        q=self.case('live','markets_live')['quotes'][0];q['stale']=True;self.blocked('market_quotes_missing');q['stale']=0;self.blocked('market_quotes_missing');q['stale']=False;q['fetched_at']=(self.now-timedelta(hours=7)).isoformat();self.blocked('market_quotes_missing')
    def test_chart_future_duplicate_and_reverse_dates_rejected(self):
        c=self.case('live','markets_live');original=copy.deepcopy(c['charts'][0]['points'])
        c['charts'][0]['points'][1]['at']=(self.now+timedelta(days=1)).isoformat();self.blocked('market_chart_invalid')
        c['charts'][0]['points']=[copy.deepcopy(original[0]),copy.deepcopy(original[0])];self.blocked('market_chart_invalid')
        c['charts'][0]['points']=list(reversed(original));self.blocked('market_chart_invalid')
    def test_per_case_explicit_old_source_hash_is_rejected(self):
        self.case('core','ledger')['source_hashes']={'backend/app.py':'0'*64};self.blocked('source_mismatch')
    def test_role_explicit_mock_flag_is_rejected(self):
        self.reports['scores']['roles'][0]['mocked']='false';self.blocked('invalid_evidence_flag')

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(ReleaseGate)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'role':'P03','subject':'release gate mechanical unit verification','method':'synthetic test fixture reports only; no real browser/live success claimed','tests_run':result.testsRun,'passed':result.wasSuccessful(),'failures':[str(t) for t,_ in result.failures],'errors':[str(t) for t,_ in result.errors],'gate_sha256':hashlib.sha256((ROOT/'backend/release_gate.py').read_bytes()).hexdigest()}
    (ROOT/'qa/reports/P03_release_gate_tests.json').write_text(json.dumps(report,indent=2))
    sys.exit(not result.wasSuccessful())
