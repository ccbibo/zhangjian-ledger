"""Ledger local backend. No cloud upload or third-party financial API calls."""
from __future__ import annotations
import argparse
import base64
import binascii
import hashlib
import json
import mimetypes
import os
import re
import sqlite3
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from parsers import parse_document
from store import Store
from planning import Planning, xirr, month_valid, shift_month
from services import Services
from newsroom import NewsRoom
from financial_summary import summarize
from markets import Markets

ROOT = Path(__file__).resolve().parents[1]
MAX_REQUEST = 7 * 1024 * 1024


class Application:
    def __init__(self, db_path):
        self.store = Store(db_path)
        self.planning = Planning(self.store)
        self.services = Services()
        self.newsroom = NewsRoom(self.store, self.planning, self.services)
        self.markets = Markets(self.store)
        self.market_key = os.environ.get('LEDGER_TWELVE_DATA_API_KEY', '')

    def dispatch(self, method, target, payload=None):
        path = urlsplit(target).path
        query = parse_qs(urlsplit(target).query)
        data = {} if payload is None else payload
        if not isinstance(data, dict):
            raise ValueError('請求內容必須為物件')
        if method == 'GET' and path == '/api/markets':
            return 200, self.markets.overview()
        if method == 'GET' and path == '/api/markets/status':
            return 200, {'provider': 'Twelve Data / TWSE', 'configured': bool(self.market_key), 'scope': '台股公開收盤資料；美股與指數需獨立行情Key及資料權限，Key僅保留在後端執行期間'}
        if method == 'POST' and path == '/api/markets/configure':
            key = data.get('key')
            if not isinstance(key, str) or len(key) > 512 or any(unicodedata.category(c) in {'Cc', 'Cf', 'Cs'} for c in key):
                raise ValueError('行情Key格式不正確')
            self.market_key = key.strip()
            return 200, {'configured': bool(self.market_key), 'provider': 'Twelve Data', 'scope': '已儲存在後端執行期間；尚未代表行情連線成功'}
        if method == 'POST' and path == '/api/markets/refresh':
            return 200, self.markets.refresh(symbols=data.get('symbols'), key=self.market_key, history=data.get('history', False))
        if method == 'GET' and path == '/api/financial-summary':
            return 200, summarize(self.store.snapshot()['transactions'], self.planning.state()['items'], query.get('period', ['week'])[0])
        if method == 'POST' and path == '/api/ai/summary':
            context = summarize(self.store.snapshot()['transactions'], self.planning.state()['items'], data.get('period', 'week'))
            result = self.services.chat({'consent': data.get('consent'), 'question': '請依本期與前一期等長期間的帳本彙總，整理四段：收支概況、主要變化、需要注意的事、財務改善建議（最多三項，不固定下週或任何單一時間範圍）。每項建議需寫明發現、摘要中的數據依據、可執行的調整、適用時機與追蹤方式。適用時機應依紀錄選擇，例如帳單到期前、下次盤點或觀察一段期間，不推定薪資週期或使用者意願。先判斷資料量是否足以支持建議；只有一期變化時不得宣稱持續趨勢；可建議先觀察或補資料，不硬湊三項。可考慮支出、帳單預留、收支缺口與記帳涵蓋，避免一律要求少花錢。金額目標只能作為明確標示的情境提案，不得捏造實際預算或消費。沒有付款方式資料時，不得推定刷卡過多；繳卡費不是新增消費。不把收支結餘當成可動用資產。資料不足時說明需補哪些帳單。區分紀錄與推測；不要把資料缺漏視為零消費，不要推薦個股。'}, context)
            return 200, dict(result, statistics=context)
        if method == 'GET' and path == '/api/workspace':
            return 200, self.planning.overview(query.get('month', ['2026-10'])[0])
        if method == 'GET' and path == '/api/history':
            return 200, self.planning.history(query.get('month', ['2026-10'])[0])
        if method == 'POST' and path == '/api/items/save':
            return 200, self.planning.save_item(data.get('kind'), data.get('item'))
        if method == 'POST' and path == '/api/items/delete':
            return 200, self.planning.delete_item(data.get('id'), data.get('revision'))
        if method == 'POST' and path == '/api/settings':
            return 200, self.planning.settings(data)
        if method == 'POST' and path == '/api/snapshot':
            return 200, self.planning.snapshot(data.get('day'))
        if method == 'GET' and path == '/api/debt/schedule':
            return 200, self.planning.schedule(query.get('id', [''])[0])
        if method == 'POST' and path == '/api/xirr':
            return 200, xirr(data.get('flows'))
        if method == 'GET' and path == '/api/ai/status':
            return 200, self.services.status()
        if method == 'POST' and path == '/api/ai/configure':
            return 200, self.services.configure(data)
        if method == 'POST' and path == '/api/ai/chat':
            ov = self.planning.overview(data.get('month'))
            context = {k: ov[k] for k in ['month', 'assets', 'liabilities', 'net_worth', 'cashflow', 'spending', 'investment']}
            return 200, self.services.chat(data, context)
        if method == 'GET' and path == '/api/news':
            if query.get('refresh', ['0'])[0] == '1':
                raise ValueError('請使用POST更新新聞')
            return 200, self.newsroom.overview()
        if method == 'POST' and path == '/api/news/refresh':
            return 200, self.newsroom.refresh()
        if method == 'POST' and path == '/api/budgets/copy':
            return 200, self.planning.copy_budgets(data.get('month'))
        if method == 'POST' and path == '/api/transaction/create':
            return 200, self.store.create_transaction(data.get('transaction'))
        if method == 'GET' and path == '/api/health':
            import shutil
            return 200, {'ok': True, 'version': '0.5.0', 'storage': 'SQLite',
                         'pdf_text': bool(shutil.which('pdftotext')), 'image_ocr': False}
        if method == 'GET' and path == '/api/state':
            return 200, self.store.snapshot()
        if method == 'GET' and path == '/api/document':
            document_id = query.get('id', [''])[0]
            with self.store._connect() as con:
                row = con.execute('SELECT payload,status FROM documents WHERE document_id=?', (document_id,)).fetchone()
                if row is None:
                    return 404, {'error': '找不到來源文件'}
                data = json.loads(row['payload'])
                return 200, {'filename': data.get('filename'), 'kind': data.get('kind'),
                             'status': row['status'], 'metadata': data.get('metadata', {}),
                             'warnings': data.get('warnings', [])}
        if method == 'GET' and path == '/api/stats':
            month = query.get('month', [''])[0]
            if not re.fullmatch(r'(?:19\d{2}|20\d{2}|21\d{2}|2200)-(0[1-9]|1[0-2])', month):
                raise ValueError('請使用 YYYY-MM 格式的有效月份')
            return 200, self.store.stats(month)
        if method == 'POST' and path == '/api/upload':
            name, encoded = data.get('filename'), data.get('content_base64')
            if not isinstance(name, str) or not name.strip() or len(name) > 255:
                raise ValueError('檔名不正確')
            if not isinstance(encoded, str) or len(encoded) > MAX_REQUEST:
                raise ValueError('檔案大小超過 5 MiB 限制')
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError('無效的檔案編碼') from exc
            if not content or len(content) > 5 * 1024 * 1024:
                raise ValueError('檔案空白或超過 5 MiB 限制')
            doc = parse_document(content, name)
            doc['filename'] = name
            return 200, self.store.preview(doc, hashlib.sha256(content).hexdigest())
        if method == 'POST' and path == '/api/commit':
            if not isinstance(data.get('document_id'), str) or not isinstance(data.get('transactions'), list):
                raise ValueError('缺少待覆核文件或交易清單')
            if data.get('accept_duplicates', False) is not False:
                raise ValueError('此版不允許略過跨文件去重；如為不同交易，請修改摘要後再覆核')
            return 200, self.store.commit(data['document_id'], data['transactions'])
        if method == 'POST' and path == '/api/transaction':
            if not isinstance(data.get('id'), str) or not isinstance(data.get('transaction'), dict):
                raise ValueError('交易欄位不完整')
            return 200, self.store.replace_transaction(data['id'], data['transaction'])
        if method == 'POST' and path == '/api/transaction/delete':
            if not isinstance(data.get('id'), str):
                raise ValueError('缺少交易 ID')
            return 200, self.store.delete_transaction(data['id'])
        if method == 'GET' and path == '/api/backup':
            return 200, {'format': 'ledger-sqlite-export-v2', 'data': self.store.snapshot(), 'planning': self.planning.state()}
        return 404, {'error': '找不到這個 API'}


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'LedgerLocal/0.3'

        def _response(self, status, data, content_type='application/json; charset=utf-8'):
            body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode() if isinstance(data, (dict, list)) else data
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(body)

        def _trusted_request(self):
            # Only the same loopback host may issue requests; no permissive CORS.
            port = self.server.server_address[1]
            hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
            if self.headers.get('Host') not in hosts:
                return False
            origin = self.headers.get('Origin')
            return origin is None or origin in {f'http://{h}' for h in hosts}

        def do_GET(self):
            if not self._trusted_request():
                return self._response(403, {'error': '只允許本機同來源存取'})
            path = urlsplit(self.path).path
            try:
                if path.startswith('/api/'):
                    code, data = app.dispatch('GET', self.path)
                    return self._response(code, data)
                path = '/home.html' if path == '/' else '/connected.html' if path in ['/app', '/app/'] else path
                resolved = (ROOT / 'dist' / path.lstrip('/')).resolve()
                if not resolved.is_relative_to((ROOT / 'dist').resolve()) or not resolved.is_file():
                    return self._response(404, {'error': '找不到檔案'})
                return self._response(200, resolved.read_bytes(), mimetypes.guess_type(resolved)[0] or 'application/octet-stream')
            except ValueError as exc:
                return self._response(400, {'error': str(exc)})
            except Exception:
                return self._response(500, {'error': '讀取失敗，請查看本機服務狀態'})

        def do_POST(self):
            if not self._trusted_request():
                return self._response(403, {'error': '只允許本機同來源存取'})
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self._response(415, {'error': '僅接受 application/json'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_REQUEST:
                    return self._response(413, {'error': '請求空白或超過大小限制'})
                self.connection.settimeout(15)
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError('請求內容必須為物件')
                code, data = app.dispatch('POST', self.path, payload)
                return self._response(code, data)
            except (ValueError, UnicodeError) as exc:
                return self._response(400, {'error': str(exc)})
            except sqlite3.Error:
                return self._response(503, {'error': '資料庫寫入失敗，本次操作未完成'})
            except Exception:
                return self._response(500, {'error': '處理失敗，本次操作未完成'})

        def log_message(self, fmt, *args):
            # Never print uploaded content or query values.
            print(f'Ledger request: {self.command} {urlsplit(self.path).path}')
    return Handler


def main():
    parser = argparse.ArgumentParser(description='Ledger local PDF/CSV and SQLite backend')
    parser.add_argument('--port', type=int, default=8791)
    parser.add_argument('--database', default=str(ROOT / 'data' / 'ledger.sqlite3'))
    parser.add_argument('--open', action='store_true', help='啟動後開啟瀏覽器')
    args = parser.parse_args()
    secret_path = ROOT / '.ledger-secrets.json'
    if secret_path.is_file():
        try:
            private = json.loads(secret_path.read_text())
            if not isinstance(private, dict):
                raise ValueError('Private configuration must be an object')
            for name in ['LEDGER_DEEPSEEK_API_KEY', 'LEDGER_DEEPSEEK_MODEL', 'LEDGER_TWELVE_DATA_API_KEY']:
                if isinstance(private.get(name), str) and not os.environ.get(name):
                    os.environ[name] = private[name]
        except (ValueError, OSError):
            print('本機私密設定無法讀取；不輸出內容，請重新設定。')
    Path(args.database).parent.mkdir(parents=True, exist_ok=True)
    app = Application(args.database)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(app))
    print(f'帳見 LEDGER: http://127.0.0.1:{args.port}/ (本機限定，Ctrl+C 停止)')
    app.newsroom.start_scheduler()
    if args.open:
        import webbrowser
        webbrowser.open(f'http://127.0.0.1:{args.port}/')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.newsroom.stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
