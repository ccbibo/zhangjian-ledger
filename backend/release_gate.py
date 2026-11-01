"""Fail-closed release gate. Never promotes mock/unit evidence to real E2E.

Reports are audit records, not cryptographic attestations: this verifier checks
their concrete observations, referenced bytes, source binding and freshness. It
does not make a fabricated report authentic. Keep execution logs for review.
No network, secrets, database, or application state is accessed by this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

SCHEMA = 'ledger-release-evidence-v1'
TZ = ZoneInfo('Asia/Taipei')
CORE_CASES = {'ledger', 'parsers', 'planning', 'summary', 'newsroom', 'services', 'browser_handlers'}
MAIN_BROWSER_CASES = {
    'app_load', 'pdf_import_review', 'transaction_edit_persistence',
    'budget_create_edit_delete', 'cashflow_month_drilldown', 'account_persistence',
    'portfolio_targets_xirr', 'debt_bill_goal', 'settings_backup', 'layout_reorder',
    'ai_financial_summary', 'news_ai', 'news_finance', 'markets_page',
}
SCOPE_CASES = {'scanned_pdf_ocr', 'image_receipt_ocr', 'non_template_pdf_import'}
BROWSER_CASES = MAIN_BROWSER_CASES | SCOPE_CASES
PERSISTENCE_CASES = {'transaction_edit_persistence', 'budget_create_edit_delete', 'account_persistence', 'settings_backup', 'layout_reorder'}
LIVE_CASES = {'ai_financial_summary', 'news_ai', 'news_finance', 'markets_live'}
SCORE_WEIGHTS = {'task': 25, 'fidelity': 20, 'persistence': 20, 'ui': 20, 'scope': 10, 'error_state': 5}
FRESH_HOURS = {'core': 24, 'browser': 24, 'live': 6, 'scores': 24}
PLANNED_NOT_DELIVERED = ['ios_notifications']


def source_manifest(root: str | Path) -> dict[str, str]:
    """Same production scope for core/browser/live; excludes verifier self-reference."""
    root = Path(root).resolve()
    candidates = []
    for folder in ['backend', 'dist', 'fixtures']:
        base = root / folder
        if not base.is_dir():
            raise ValueError('Missing source directory: ' + folder)
        for p in base.rglob('*'):
            rel = p.relative_to(root)
            if any(part.startswith('.') or part == '__pycache__' for part in rel.parts):
                continue
            if folder == 'backend' and (p.suffix != '.py' or p.name == 'release_gate.py' or p.name.startswith('verify')):
                continue
            if p.is_symlink():
                raise ValueError('Source symlink is not allowed: ' + str(rel))
            if p.is_file():
                candidates.append(p)
    if (root / 'build_web.py').is_file():
        candidates.append(root / 'build_web.py')
    if not candidates:
        raise ValueError('No production source found')
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(candidates)}


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _integer(value):
    return type(value) is int


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _url(value):
    try:
        return urlsplit(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _amount(value, positive=False):
    if type(value) not in (str, int, float):
        return None
    try:
        n = Decimal(str(value))
        return n if n.is_finite() and abs(n) <= Decimal('1000000000000') and (not positive or n > 0) else None
    except InvalidOperation:
        return None


def _market_date(value):
    try:
        return date.fromisoformat(value) if isinstance(value,str) and len(value)==10 else None
    except ValueError:
        return None


def _json_equal(a, b):
    # Python True == 1 is not acceptable assertion evidence.
    return json.dumps(a, sort_keys=True, allow_nan=False) == json.dumps(b, sort_keys=True, allow_nan=False)


class Gate:
    def __init__(self, root, now=None):
        self.root = Path(root).resolve()
        self.now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.blockers = []
        try:
            self.manifest = source_manifest(self.root)
        except (OSError, ValueError) as exc:
            self.manifest = {}
            self.add('source_unavailable', 'source', str(exc))

    def add(self, code, where, detail):
        self.blockers.append({'code': code, 'where': where, 'detail': detail})

    def flags(self, record, where):
        """JSON booleans are identities, never Python truthiness/coercion."""
        for flag in ('mocked', 'diagnostics_only'):
            if flag in record and record[flag] is not False:
                code='nonexecution_evidence' if record[flag] is True else 'invalid_evidence_flag'
                self.add(code,where+'.'+flag,'When present this execution flag must be the JSON boolean false')
        if 'source_unchanged' in record and record['source_unchanged'] is not True:
            code='source_changed_during_run' if record['source_unchanged'] is False else 'invalid_evidence_flag'
            self.add(code,where+'.source_unchanged','When present source_unchanged must be the JSON boolean true')
        if 'major_failures' in record and (not isinstance(record['major_failures'],list) or record['major_failures']):
            self.add('major_failure',where,'A major-failure record must be an empty JSON array')

    def common(self, report, kind):
        if not isinstance(report, dict):
            self.add('missing_evidence', kind, 'Required report is missing or not a JSON object')
            return False
        if report.get('schema') != SCHEMA or report.get('kind') != kind:
            self.add('invalid_schema', kind, 'Require current evidence schema and matching kind')
        if not _text(report.get('run_id')):
            self.add('missing_run_id', kind, 'Execution run_id is required')
        if not self.manifest or report.get('source_hashes') != self.manifest:
            self.add('source_mismatch', kind, 'Evidence does not bind every current production source file')
        at, start, end = (_time(report.get(k)) for k in ('at', 'started_at', 'finished_at'))
        if not at or not start or not end:
            self.add('invalid_time', kind, 'UTC-offset-aware at, started_at and finished_at are required')
        elif start > end or end > at or max(at, end) > self.now + timedelta(minutes=5):
            self.add('invalid_time_order', kind, 'Impossible or future execution timestamps')
        elif self.now - start > timedelta(hours=FRESH_HOURS[kind]) or self.now - at > timedelta(hours=FRESH_HOURS[kind]):
            self.add('stale_evidence', kind, 'Evidence is older than the fixed freshness window')
        self.flags(report,kind)
        return True

    def cases(self, report, kind, required):
        raw = report.get('cases') if isinstance(report, dict) else None
        if not isinstance(raw, list) or not raw:
            self.add('missing_cases', kind, 'Require per-case observations, not passed/overall flags')
            return {}
        found = {}
        for c in raw:
            if not isinstance(c, dict) or not _text(c.get('id')):
                self.add('malformed_case', kind, 'Case must contain a nonempty id')
                continue
            ident = c['id']
            if ident in found:
                self.add('duplicate_case', kind + '.' + ident, 'Duplicate case IDs are not allowed')
            found[ident] = c
            self.flags(c,kind+'.'+ident)
            if 'source_hashes' in c and c['source_hashes']!=self.manifest:
                self.add('source_mismatch',kind+'.'+ident,'Per-case source provenance contradicts the current source manifest')
            if c.get('status') != 'passed':
                self.add('case_not_passed', kind + '.' + ident, str(c.get('blocker') or c.get('message') or c.get('status') or 'missing status'))
        for ident in sorted(required - found.keys()):
            self.add('missing_case', kind + '.' + ident, 'Required case has no evidence')
        return found

    def core(self, report):
        before = len(self.blockers)
        if not self.common(report, 'core'):
            return False
        cases = self.cases(report, 'core', CORE_CASES)
        for ident, c in cases.items():
            counts = c.get('counts', {})
            if c.get('method') not in {'python_unit', 'python_dispatch_sqlite', 'node_vm_handlers'}:
                self.add('invalid_method', 'core.' + ident, 'Core evidence requires an executed test runner')
            if not _integer(c.get('exit_code')) or c.get('exit_code') != 0:
                self.add('runner_failed', 'core.' + ident, 'Runner exit_code must be integer zero')
            if not isinstance(counts, dict) or not all(_integer(counts.get(k)) for k in ('passed', 'failed', 'errors')) or counts.get('passed', 0) <= 0 or counts.get('failed', -1) != 0 or counts.get('errors', -1) != 0:
                self.add('invalid_test_counts', 'core.' + ident, 'Positive executed passed count, zero failures and zero errors are required')
        return len(self.blockers) == before

    def screenshot(self, artifact):
        if not isinstance(artifact, dict) or artifact.get('kind') != 'screenshot' or not _text(artifact.get('path')):
            return False
        p = Path(artifact['path'])
        if p.is_absolute() or '..' in p.parts:
            return False
        p = self.root / p
        try:
            if not p.resolve().is_relative_to(self.root) or p.is_symlink() or not p.is_file():
                return False
            data = p.read_bytes()
            image = data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8\xff') or (data.startswith(b'RIFF') and data[8:12] == b'WEBP')
            return len(data) > 64 and image and hashlib.sha256(data).hexdigest() == artifact.get('sha256')
        except OSError:
            return False

    def browser(self, report):
        before = len(self.blockers)
        if not self.common(report, 'browser'):
            return False, {}
        b = report.get('browser', {})
        origin = _url(b.get('origin')) if isinstance(b, dict) else None
        if not isinstance(b, dict) or b.get('transport') != 'real_browser' or b.get('mode') != 'connected_backend' or b.get('mocked') is not False or not _text(b.get('engine')) or not origin or origin.scheme not in {'http', 'https'} or not origin.netloc:
            self.add('not_real_browser', 'browser', 'Require a real browser connected to the backend, not VM/demo/file preview')
        health = report.get('backend_health', {})
        if not isinstance(health, dict) or health.get('ok') is not True or health.get('storage') != 'SQLite':
            self.add('backend_unverified', 'browser', 'Real E2E must observe the SQLite backend health')
        artifacts = report.get('artifacts', [])
        if not isinstance(artifacts, list) or not any(self.screenshot(a) for a in artifacts):
            self.add('missing_browser_artifact', 'browser', 'Require an existing screenshot with matching SHA and image bytes')
        cases = self.cases(report, 'browser', BROWSER_CASES)
        for ident, c in cases.items():
            if c.get('method') != 'real_browser':
                self.add('invalid_method', 'browser.' + ident, 'VM/static/synthetic DOM does not count as real browser E2E')
            events = c.get('events', [])
            if not isinstance(events, list) or not any(isinstance(e, dict) and e.get('type') in {'navigate', 'click', 'fill', 'submit', 'reload'} for e in events):
                self.add('missing_browser_actions', 'browser.' + ident, 'Require recorded browser actions')
            if not isinstance(events, list):
                events = []
            if ident in PERSISTENCE_CASES and not any(isinstance(e, dict) and e.get('type') == 'reload' for e in events):
                self.add('persistence_not_reloaded', 'browser.' + ident, 'Persistence must survive an actual page reload')
            assertions = c.get('assertions', [])
            if not isinstance(assertions, list) or not assertions:
                self.add('missing_assertions', 'browser.' + ident, 'Require raw expected/actual observations')
                continue
            for assertion in assertions:
                try:
                    valid = isinstance(assertion, dict) and _text(assertion.get('name')) and 'expected' in assertion and 'actual' in assertion and assertion['actual'] is not None and assertion['actual'] not in ('', [], {}) and _json_equal(assertion['expected'], assertion['actual'])
                except (TypeError, ValueError):
                    valid = False
                if not valid:
                    self.add('assertion_failed', 'browser.' + ident, 'Expected and observed JSON must match; pass flags are ignored')
        return len(self.blockers) == before, cases

    def fresh_live_time(self, value, where, same_day=False):
        stamp = _time(value)
        if not stamp or stamp > self.now + timedelta(minutes=5) or self.now - stamp > timedelta(hours=6):
            self.add('stale_live_content', where, 'Require a real content timestamp within six hours')
            return False
        if same_day and stamp.astimezone(TZ).date() != self.now.astimezone(TZ).date():
            self.add('wrong_news_day', where, 'News generation must be from the current Asia/Taipei day')
            return False
        return True

    def live(self, report):
        before = len(self.blockers)
        if not self.common(report, 'live'):
            return False
        for ident, c in self.cases(report, 'live', LIVE_CASES).items():
            where = 'live.' + ident
            if c.get('method') != 'real_network' or c.get('mocked') is True:
                self.add('invalid_method', where, 'Require real provider requests; mocks/diagnostics are not accepted')
            if (ident != 'markets_live' and c.get('provider') != 'DeepSeek') or not _text(c.get('provider')) or type(c.get('http_status')) is not int or c.get('http_status') != 200:
                self.add('provider_unverified', where, 'Require successful live DeepSeek production response')
            if ident == 'ai_financial_summary':
                stats = c.get('statistics', {})
                current = stats.get('current', {}) if isinstance(stats, dict) else None
                if c.get('endpoint') != '/api/ai/summary' or c.get('generated') is not True or not _text(c.get('answer')) or not isinstance(current, dict) or not all(k in current for k in ('income', 'expense', 'balance')):
                    self.add('summary_not_generated', where, 'Require production /api/ai/summary answer and its actual statistics')
                elif any(_amount(current[k]) is None for k in ('income','expense','balance')) or _amount(current['income']) - _amount(current['expense']) != _amount(current['balance']):
                    self.add('summary_statistics_invalid', where, 'Observed income minus expense must equal the observed balance')
            elif ident in {'news_ai', 'news_finance'}:
                if c.get('channel') != ident[5:] or c.get('ai_generated') is not True or c.get('stale') is not False:
                    self.add('news_not_generated', where, 'Require this category of newly generated AI news, not cached fallback')
                self.fresh_live_time(c.get('generated_at'), where, same_day=True)
                self.fresh_live_time(c.get('fetched_at'), where)
                rows = c.get('items')
                if not isinstance(rows, list) or not 1 <= len(rows) <= 5:
                    self.add('empty_news', where, 'Each category needs one to five real sourced items')
                    continue
                for item in rows:
                    if not isinstance(item, dict):
                        self.add('invalid_news_item', where, 'News item must be an object')
                        continue
                    url = _url(item.get('url'))
                    published = _time(item.get('published_at', item.get('published')))
                    if not _text(item.get('title')) or not url or url.scheme != 'https' or not url.netloc or not _text(item.get('summary')) or not _text(item.get('why')) or not published or published > self.now + timedelta(minutes=5) or self.now - published > timedelta(hours=72):
                        self.add('invalid_news_item', where, 'Require source HTTPS URL, title, AI summary/why and a real publication time within 72 hours')
            elif ident == 'markets_live':
                if c.get('stale') is not False:
                    self.add('market_stale', where, 'Market state must not be a stale fixture or cache fallback')
                self.fresh_live_time(c.get('fetched_at'), where)
                quotes, charts = c.get('quotes'), c.get('charts')
                def quote_identity(q):
                    if not isinstance(q,dict):return None
                    if _text(q.get('id')):return q['id']
                    if _text(q.get('instrument_id')):return q['instrument_id']
                    if _text(q.get('market')) and _text(q.get('symbol')):return q['market']+':'+q['symbol']
                    return q.get('symbol') if _text(q.get('symbol')) else None
                quote_rows=[q for q in quotes if isinstance(q,dict)] if isinstance(quotes,list) else []
                observed={quote_identity(q):q for q in quote_rows if quote_identity(q) is not None}
                if len(observed)!=len(quote_rows):
                    self.add('market_identity_invalid',where,'Observed quote identities must be nonempty and unique')
                if 'requested_symbols' in c:
                    requested=c['requested_symbols']
                    if not isinstance(requested,list) or not requested or any(not _text(s) for s in requested) or len(set(requested))!=len(requested):
                        self.add('market_request_invalid',where,'Requested symbols must be a nonempty list of unique instrument identities')
                    elif not set(requested).issubset(observed):
                        self.add('market_requested_quotes_missing',where,'Every requested instrument must have its own observed quote; unrelated cached prices do not satisfy it')
                def valid_quote(q):
                    if not isinstance(q,dict) or not _text(q.get('symbol')) or _amount(q.get('price'),True) is None:
                        return False
                    if ('status' in q and q['status']!='ok') or ('stale' in q and q['stale'] is not False):
                        return False
                    if 'fetched_at' in q:
                        fetched=_time(q['fetched_at'])
                        if not fetched or fetched>self.now+timedelta(minutes=5) or self.now-fetched>timedelta(hours=6):return False
                    stamp=_time(q.get('as_of'))
                    if stamp:
                        return stamp<=self.now+timedelta(minutes=5) and self.now-stamp<=timedelta(days=7)
                    day=_market_date(q.get('as_of'))
                    return bool(day and q.get('as_of_precision')=='date' and q.get('data_delay',c.get('data_delay'))=='daily_eod' and 0<=(self.now.astimezone(TZ).date()-day).days<=7)
                if not isinstance(quotes, list) or not quotes or any(not valid_quote(q) for q in quotes):
                    self.add('market_quotes_missing', where, 'Require observed priced symbols with quote timestamps')
                if charts is None and isinstance(quotes,list):
                    charts=[{'symbol':q.get('symbol'),'points':[{'at':p.get('date'),'price':p.get('close'),'precision':'date'} for p in (q.get('history') if isinstance(q.get('history'),list) else []) if isinstance(p,dict)]} for q in quotes if isinstance(q,dict)]
                if not isinstance(charts,list) or not charts:
                    self.add('market_charts_missing', where, 'Require an actual market chart response')
                else:
                    for chart in charts:
                        points=chart.get('points') if isinstance(chart,dict) else None
                        if not isinstance(chart,dict) or not _text(chart.get('symbol')) or not isinstance(points,list) or len(points)<2:
                            self.add('market_chart_invalid', where, 'Require at least two dated positive-price chart points')
                            continue
                        ident=chart.get('instrument_id',chart.get('id'))
                        matches=[q for q in quote_rows if (quote_identity(q)==ident if _text(ident) else q.get('symbol')==chart['symbol']) and q.get('symbol')==chart['symbol']]
                        if len(matches)!=1:
                            self.add('market_chart_unbound',where,'Chart identity and symbol must match exactly one observed quote')
                        times=[]
                        for point in points:
                            if not isinstance(point,dict) or _amount(point.get('price'),True) is None:
                                self.add('market_chart_invalid',where,'Every history point needs a positive observed price');continue
                            stamp=_time(point.get('at'))
                            day=_market_date(point.get('at')) if point.get('precision')=='date' else None
                            if stamp and stamp<=self.now+timedelta(minutes=5):times.append(stamp.isoformat())
                            elif day and day<=self.now.astimezone(TZ).date():times.append(day.isoformat())
                            else:self.add('market_chart_invalid',where,'History points must have valid nonfuture source dates')
                        if len(times)!=len(points) or len(set(times))<2 or times!=sorted(times):
                            self.add('market_chart_invalid',where,'History must contain at least two distinct dates in ascending order')
        return len(self.blockers) == before

    def scores(self, report, browser):
        before = len(self.blockers)
        totals = {}
        if not self.common(report, 'scores'):
            return False, totals
        if not isinstance(report.get('major_failures'), list) or report['major_failures']:
            self.add('major_failure', 'scores', 'Require zero measured major failures')
        roles = report.get('roles')
        if not isinstance(roles, list):
            self.add('missing_roles', 'scores', 'Ten independent role assessments are required')
            return False, totals
        seen = set()
        for r in roles:
            if not isinstance(r, dict) or not isinstance(r.get('role_id'),str) or r.get('role_id') not in {f'P{i:02}' for i in range(1, 11)} or r.get('role_id') in seen:
                self.add('invalid_role', 'scores', 'Role IDs must be exactly P01–P10 without repeats')
                continue
            ident = r['role_id'];seen.add(ident)
            self.flags(r,'scores.'+ident)
            if not isinstance(r.get('major_failures'), list) or r['major_failures']:
                self.add('major_failure', 'scores.' + ident, 'Role must have zero measured major failures')
            values = r.get('scores', {})
            valid = isinstance(values, dict) and set(values) == set(SCORE_WEIGHTS)
            if valid:
                for key, weight in SCORE_WEIGHTS.items():
                    v = values[key]
                    if type(v) not in (int, float) or not math.isfinite(v) or v not in {weight * q / 4 for q in range(5)}:
                        valid = False
            if not valid:
                self.add('invalid_score', 'scores.' + ident, 'All six observed scores are required in original quarter bands; null UI cannot pass')
                continue
            if not isinstance(browser, dict) or not _text(browser.get('run_id')) or r.get('ui_browser_run_id') != browser['run_id']:
                self.add('unbound_ui_score', 'scores.' + ident, 'UI score must refer to this real-browser evidence run')
            total = sum(values.values());totals[ident] = total
            if total < 80:
                self.add('role_below_80', 'scores.' + ident, 'Each role must reach 80/100; no rescaling from functional /80')
        if seen != {f'P{i:02}' for i in range(1, 11)}:
            self.add('missing_roles', 'scores', 'Require all ten role reports')
        if len(totals) == 10 and sum(totals.values()) / 10 < 85:
            self.add('average_below_85', 'scores', 'Ten-role average must reach 85/100')
        return len(self.blockers) == before, totals


def evaluate_reports(root, core=None, browser=None, live=None, scores=None, now=None):
    gate = Gate(root, now)
    core_ok = gate.core(core)
    browser_ok, browser_cases = gate.browser(browser)
    live_ok = gate.live(live)
    scores_ok, totals = gate.scores(scores, browser)
    scope = {}
    for ident in sorted(SCOPE_CASES):
        c = browser_cases.get(ident, {})
        scope[ident] = 'verified' if browser_ok and c.get('status') == 'passed' else 'not_verified_or_not_implemented'
        if scope[ident] != 'verified':
            gate.add('scope_incomplete', 'scope.' + ident, 'Original OCR/document scope is not satisfied by CSV or specified text templates')
    for ident in PLANNED_NOT_DELIVERED:
        scope[ident] = 'planned_not_delivered'
    scope['markets_live'] = 'verified' if live_ok else 'not_verified_or_not_implemented'
    scope['markets_page'] = 'verified' if browser_ok else 'not_verified_or_not_implemented'
    ready = not gate.blockers and core_ok and browser_ok and live_ok and scores_ok
    return {'schema': 'ledger-release-gate-v1', 'checked_at': gate.now.isoformat(),
            'production_ready': ready, 'deliverable': ready,
            'status': 'production_ready' if ready else 'blocked',
            'experience_status': 'production_verified' if ready else 'nonproduction_only' if core_ok else 'unverified',
            'full_project_complete': ready and not PLANNED_NOT_DELIVERED,
            'source_hashes': gate.manifest, 'score_totals': totals,
            'score_average': sum(totals.values()) / 10 if len(totals) == 10 else None,
            'thresholds': {'average': 85, 'minimum_role': 80, 'major_failures': 0, 'functional_max': 80, 'ui_must_be_observed': True},
            'scope': scope, 'blockers': gate.blockers}


def read_report(path):
    if not path:
        return None
    try:
        p = Path(path)
        if p.stat().st_size > 8 * 1024 * 1024:
            return None
        return json.loads(p.read_text(), parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    except (OSError, ValueError, TypeError):
        return None


def main():
    parser = argparse.ArgumentParser(description='Fail-closed Ledger production acceptance gate')
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    for kind in ['core', 'browser', 'live', 'scores']:
        parser.add_argument('--' + kind)
    parser.add_argument('--output')
    args = parser.parse_args()
    result = evaluate_reports(args.root, **{k: read_report(getattr(args, k)) for k in ['core', 'browser', 'live', 'scores']})
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        output = Path(args.output);output.parent.mkdir(parents=True, exist_ok=True);output.write_text(encoded)
    print(encoded)
    return 0 if result['production_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
