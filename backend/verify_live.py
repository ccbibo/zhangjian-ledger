"""Real-network release evidence using production routes and synthetic records.

--diagnostics-only never reads a private configuration or calls a provider.
A live run explicitly uses configured credentials and real provider/feed HTTP.
"""
import argparse
import json
import os
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
from network_diagnostics import classify_failure, diagnose_network


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def as_utc(stamp):
    if not isinstance(stamp, str):
        return None
    try:
        value = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') if value.tzinfo else None
    except ValueError:
        return None


def summary_case(app):
    try:
        status, result = app.dispatch('POST', '/api/ai/summary', {'period': 'month', 'consent': True})
        valid = status == 200 and isinstance(result.get('answer'), str) and bool(result['answer'].strip())
        return {'id': 'ai_financial_summary', 'status': 'passed' if valid else 'failed',
                'method': 'real_network', 'endpoint': '/api/ai/summary', 'provider': result.get('provider'),
                'generated': valid, 'answer': result.get('answer', ''), 'statistics': result.get('statistics'),
                'model': result.get('model'), 'scope': result.get('scope'), 'http_status': status}
    except Exception as error:
        return {'id': 'ai_financial_summary', 'status': 'blocked', 'method': 'real_network',
                'endpoint': '/api/ai/summary', 'generated': False, 'message': str(error),
                **classify_failure(error)}


def news_cases(app):
    try:
        # This invokes the production source/model chain synchronously; GET uses
        # the exact application route. No fixture/news response is injected.
        app.newsroom.refresh(asynchronous=False)
        status, overview = app.dispatch('GET', '/api/news')
    except Exception as error:
        return [{ 'id': 'news_' + category, 'channel': category, 'status': 'blocked',
                  'method': 'real_network', 'message': str(error), **classify_failure(error, 'feed')}
                for category in ['ai', 'finance']], {}
    today = datetime.now().astimezone(__import__('zoneinfo').ZoneInfo('Asia/Taipei')).date().isoformat()
    cases = []
    for category in ['ai', 'finance']:
        channel = overview.get('channels', {}).get(category, {})
        items = channel.get('items', [])
        valid = (status == 200 and channel.get('ai_generated') is True and channel.get('stale') is False
                 and channel.get('date') == today and 1 <= len(items) <= 5
                 and all(isinstance(a.get('title'), str) and a['title'].strip()
                         and isinstance(a.get('summary'), str) and a['summary'].strip()
                         and isinstance(a.get('why'), str) and a['why'].strip()
                         and isinstance(a.get('url'), str) and a['url'].startswith('https://')
                         and a.get('published') for a in items))
        errors = channel.get('errors', [])
        cases.append({'id': 'news_' + category, 'channel': category,
                      'status': 'passed' if valid else 'blocked', 'method': 'real_network',
                      'provider': 'DeepSeek', 'http_status': status, 'ai_generated': channel.get('ai_generated', False),
                      'stale': channel.get('stale', False), 'date': channel.get('date'),
                      'generated_at': as_utc(channel.get('updated_at')), 'fetched_at': utc_now(),
                      'items': items, 'errors': errors,
                      'message': 'Real current-day AI digest generated' if valid else 'No verified current-day non-stale AI digest; inspect source/model errors',
                      'failure_category': None if valid else ('no_key' if not app.services.key else 'source_or_model_unavailable'),
                      'key_configured': bool(app.services.key)})
    return cases, overview


MARKET_SYMBOLS = ['TW:TAIEX', 'TW:2330', 'US:SPY', 'US:META', 'US:DJI']


def market_case(app):
    base = {'id': 'markets_live', 'method': 'real_network',
            'provider': 'TWSE / Twelve Data', 'requested_symbols': MARKET_SYMBOLS,
            'endpoint': '/api/markets/refresh'}
    if not app.market_key:
        return dict(base, status='blocked', stale=True, failure_category='no_key',
                    message='尚未設定獨立行情Key；沒有執行市場真API驗收，DeepSeek Key不能代替')
    try:
        code, result = app.dispatch('POST', '/api/markets/refresh', {'symbols': MARKET_SYMBOLS, 'history': True})
        now = datetime.now(timezone.utc)
        today = now.astimezone(ZoneInfo('Asia/Taipei')).date()
        selected = [q for q in result.get('quotes', []) if q.get('id') in MARKET_SYMBOLS]
        failures, quotes, charts = [], [], []
        if result.get('refresh', {}).get('requested') != MARKET_SYMBOLS or result.get('refresh_error'):
            failures.append({'symbol': 'request', 'reason': 'requested_refresh_not_completed'})
        found = set()
        for q in selected:
            sid = q['id']
            found.add(sid)
            try:
                price = Decimal(str(q.get('price')))
                fetched = datetime.fromisoformat(str(q.get('fetched_at')).replace('Z', '+00:00'))
                stamp = str(q.get('as_of'))
                if q.get('as_of_precision') == 'date':
                    source_day = date.fromisoformat(stamp)
                    source_current = 0 <= (today - source_day).days <= 7
                else:
                    source_time = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
                    source_current = source_time.tzinfo is not None and -300 <= (now - source_time).total_seconds() <= 7 * 86400
                valid = (q.get('status') == 'ok' and q.get('stale') is False and price.is_finite()
                         and 0 < price <= Decimal('1000000000000') and fetched.tzinfo is not None
                         and -300 <= (now - fetched).total_seconds() <= 6 * 3600 and source_current)
            except (ValueError, InvalidOperation, TypeError):
                valid = False
            if not valid:
                failures.append({'symbol': sid, 'reason': q.get('status', 'invalid_quote'), 'error': q.get('error')})
            record = dict(q)
            if q.get('as_of_precision') == 'date':
                record['data_delay'] = 'daily_eod'
            quotes.append(record)
            points = []
            for point in q.get('history', []):
                try:
                    day = date.fromisoformat(point['date'])
                    value = Decimal(str(point['close']))
                    if day <= today and value.is_finite() and value > 0:
                        points.append({'at': point['date'], 'price': point['close'], 'precision': 'date'})
                except (KeyError, ValueError, InvalidOperation, TypeError):
                    continue
            # Initial loading intentionally fetches source history on demand;
            # do not fabricate points for symbols without returned history.
            if len(points) >= 2:
                charts.append({'symbol': q['symbol'], 'instrument_id': sid, 'points': points})
        for sid in MARKET_SYMBOLS:
            if sid not in found:
                failures.append({'symbol': sid, 'reason': 'missing_quote'})
        if not charts:
            failures.append({'symbol': 'chart', 'reason': 'no_selected_source_history_with_two_points'})
        passed = code == 200 and not failures
        return dict(base, status='passed' if passed else 'blocked', http_status=code,
                    stale=any(q.get('stale') is not False for q in selected) or len(found) != len(MARKET_SYMBOLS),
                    fetched_at=utc_now(), quotes=quotes, charts=charts, errors=failures,
                    message='Requested live quotes and at least one source-backed history chart verified' if passed
                            else '市場真報價或來源歷史尚未全部滿足；未用示範價格或補點代替')
    except Exception as error:
        return dict(base, status='blocked', stale=True, message=str(error), **classify_failure(error, 'market'))


def run():
    from release_gate import source_manifest
    from app import Application
    started = utc_now()
    manifest = source_manifest(ROOT)
    private_path = ROOT / '.ledger-secrets.json'
    if private_path.is_file():
        private = json.loads(private_path.read_text())
        if not isinstance(private, dict):
            raise ValueError('本機服務設定格式錯誤')
        for name in ['LEDGER_DEEPSEEK_API_KEY', 'LEDGER_DEEPSEEK_MODEL', 'LEDGER_TWELVE_DATA_API_KEY']:
            if isinstance(private.get(name), str) and not os.environ.get(name):
                os.environ[name] = private[name]
    report = {'schema': 'ledger-release-evidence-v1', 'kind': 'live', 'run_id': str(uuid.uuid4()),
              'started_at': started, 'source_hashes': manifest,
              'scope': {'synthetic_financial_records': True, 'personal_ledger_opened': False},
              'evidence': 'Production Application dispatcher / real provider and feed network / temporary synthetic SQLite; no browser QA',
              'diagnostics': diagnose_network(), 'cases': []}
    with tempfile.TemporaryDirectory(prefix='ledger-live-verification-') as temporary:
        app = Application(Path(temporary) / 'verify.sqlite3')
        seed = json.loads((ROOT / 'dist/demo-seed.json').read_text())
        # Keep original fixture dates; the production rolling period decides
        # coverage at the actual Taipei date. No client-supplied as_of bypass.
        for transaction in seed['ledger']['transactions']:
            app.store.create_transaction({key: transaction[key] for key in ['date', 'name', 'amount', 'category']})
        report['cases'].append(summary_case(app))
        news, overview = news_cases(app)
        report['cases'].extend(news)
        report['cases'].append(market_case(app))
        report['summary'] = report['cases'][0]
        report['news'] = overview
        report['source_unchanged'] = source_manifest(ROOT) == manifest
        report['finished_at'] = report['at'] = utc_now()
        report['passed'] = report['source_unchanged'] and all(c['status'] == 'passed' for c in report['cases'])
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if any(secret and secret in encoded for secret in [app.services.key, app.market_key]):
            raise ValueError('驗收輸出包含私人設定，已停止保存')
    output = ROOT / 'outputs/live-ai-verification.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(encoded)
    print('AI與新聞實連驗收：' + ('通過' if report['passed'] else '未通過'))
    for case in report['cases']:
        print(case['id'] + '：' + case['status'] + (' / ' + case.get('category', case.get('failure_category', '')) if case['status'] != 'passed' else ''))
    print('完整報告：' + str(output))
    return 0 if report['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description='Ledger real-network acceptance or secret-free DNS diagnosis')
    parser.add_argument('--diagnostics-only', action='store_true', help='Do not load any private configuration or call a provider')
    parser.add_argument('--diagnostics-output', type=Path, default=ROOT / 'outputs/network-diagnostics.json')
    args = parser.parse_args()
    if args.diagnostics_only:
        report = diagnose_network()
        args.diagnostics_output.parent.mkdir(parents=True, exist_ok=True)
        args.diagnostics_output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print('僅DNS診斷，未讀取金鑰、未呼叫AI或新聞來源。')
        print('報告：' + str(args.diagnostics_output))
        return 0 if all(r['resolved'] for r in report['dns'].values()) else 2
    return run()


if __name__ == '__main__':
    raise SystemExit(main())
