"""Validated, persistent planning modules. All balances are calculations, not advice."""
import calendar
import copy
import json
import math
import re
import sqlite3
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_FLOOR
import unicodedata

from store import Store, _cents, _money, _date, _text, SPENDING_CATEGORIES

CLASSES = ['現金與存款', '台股與ETF', '美股與ETF', '基金', '黃金', '房地產', '加密資產', '其他']
KINDS = {'budget', 'goal', 'holding', 'debt', 'bill', 'account', 'investment_flow'}


def month_valid(value):
    return isinstance(value, str) and bool(re.fullmatch(r'(?:19\d{2}|20\d{2}|21\d{2}|2200)-(?:0[1-9]|1[0-2])', value))


def shift_month(value, offset):
    year, month = map(int, value.split('-'))
    n = year * 12 + month - 1 + offset
    return f'{n // 12:04d}-{n % 12 + 1:02d}'


def decimal_value(value, name, places=6, positive=False):
    if isinstance(value, bool) or not re.fullmatch(r'\d+(?:\.\d{1,' + str(places) + r'})?', str(value)):
        raise ValueError(name + '需為非負十進位數字')
    n = Decimal(str(value))
    if not n.is_finite() or n > Decimal('1000000000000') or (positive and n <= 0):
        raise ValueError(name + '超出範圍')
    return n


def amount(value, name, positive=False):
    cents = _cents(value, name, nonnegative=True)
    if positive and cents <= 0:
        raise ValueError(name + '必須大於零')
    return _money(cents)


def serialize_decimal(value):
    if not value.is_finite() or abs(value) > Decimal('1000000000000'):
        raise ValueError('計算結果超出可接受範圍')
    return format(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP), '.2f')


class Planning:
    def __init__(self, ledger):
        self.ledger = ledger
        with ledger._connect() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS planner_items (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS planner_settings (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS planner_snapshots (day TEXT PRIMARY KEY, payload TEXT NOT NULL);
            ''')

    def state(self):
        with self.ledger._connect() as con:
            items = [dict(json.loads(r['payload']), id=r['id'], kind=r['kind'], revision=r['revision'])
                     for r in con.execute('SELECT * FROM planner_items ORDER BY updated_at,id')]
            prefs = {r['key']: json.loads(r['payload']) for r in con.execute('SELECT * FROM planner_settings')}
            snapshots = [dict(json.loads(r['payload']), day=r['day']) for r in con.execute('SELECT * FROM planner_snapshots ORDER BY day')]
        return {'items': items, 'settings': prefs, 'snapshots': snapshots}

    def normalize(self, kind, data):
        if not isinstance(kind, str) or kind not in KINDS or not isinstance(data, dict):
            raise ValueError('不支援的資料類別')
        out = {'name': _text(data.get('name'), '名稱', 80)}
        if kind == 'budget':
            if not month_valid(data.get('month')) or not isinstance(data.get('category'), str) or data.get('category') not in SPENDING_CATEGORIES:
                raise ValueError('請選擇有效月份與消費分類')
            out.update(month=data['month'], category=data['category'], limit=amount(data.get('limit'), '預算', True))
        elif kind == 'goal':
            out.update(target=amount(data.get('target'), '目標金額', True), saved=amount(data.get('saved', '0'), '已存金額'))
            out['deadline'] = _date(data['deadline']) if data.get('deadline') else ''
        elif kind == 'holding':
            cls, currency = data.get('asset_class'), data.get('currency', 'TWD')
            if cls not in CLASSES or currency not in ['TWD', 'USD', 'HKD']:
                raise ValueError('不支援的資產類別或幣別')
            qty = decimal_value(data.get('quantity'), '數量', positive=True)
            cost = decimal_value(data.get('cost_price'), '單位成本')
            price = decimal_value(data.get('price'), '手動現價')
            fx = Decimal(1) if currency == 'TWD' else decimal_value(data.get('fx'), '換算匯率', positive=True)
            out.update(asset_class=cls, currency=currency, symbol=str(data.get('symbol', '')).strip()[:24],
                       quantity=str(qty), cost_price=str(cost), price=str(price), fx=str(fx),
                       as_of=_date(data.get('as_of')), value=serialize_decimal(qty * price * fx),
                       cost=serialize_decimal(qty * cost * fx))
        elif kind == 'debt':
            rate = decimal_value(data.get('rate', '0'), '年利率', 4)
            if rate > 100:
                raise ValueError('年利率須介於0與100%')
            out.update(balance=amount(data.get('balance'), '剩餘本金'), rate=str(rate),
                       payment=amount(data.get('payment'), '每月還款', True), due=_date(data.get('due')))
        elif kind == 'bill':
            if data.get('status', 'unpaid') not in ['paid', 'unpaid'] or data.get('recurrence', 'none') not in ['none', 'monthly', 'yearly']:
                raise ValueError('提醒狀態不正確')
            out.update(amount=amount(data.get('amount'), '應繳金額', True), due=_date(data.get('due')),
                       status=data.get('status', 'unpaid'), recurrence=data.get('recurrence', 'none'))
        elif kind == 'account':
            if data.get('asset_class') not in CLASSES:
                raise ValueError('請選擇資產類別')
            out.update(asset_class=data['asset_class'], value=amount(data.get('value'), '資產金額'), as_of=_date(data.get('as_of')))
        elif kind == 'investment_flow':
            cents = _cents(data.get('amount'), '投資現金流')
            if cents == 0:
                raise ValueError('現金流不可為零')
            out.update(date=_date(data.get('date')), amount=_money(cents))
        return out

    def save_item(self, kind, data):
        value = self.normalize(kind, data)
        item_id = data.get('id')
        if item_id is not None and (not isinstance(item_id, str) or not re.fullmatch(r'[a-f0-9-]{36}', item_id)):
            raise ValueError('資料ID不正確')
        with self.ledger._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            old = con.execute('SELECT * FROM planner_items WHERE id=?', (item_id,)).fetchone() if item_id else None
            if item_id and old is None:
                raise ValueError('找不到要修改的資料')
            if old and (old['kind'] != kind or type(data.get('revision')) is not int or data.get('revision') != old['revision']):
                raise ValueError('資料已在其他頁面更新，請重新整理後再修改')
            if not old and con.execute('SELECT count(*) FROM planner_items').fetchone()[0] >= 5000:
                raise ValueError('規劃資料已達5000筆上限')
            if kind == 'budget':
                for row in con.execute("SELECT id,payload FROM planner_items WHERE kind='budget'"):
                    b = json.loads(row['payload'])
                    if row['id'] != item_id and b['month'] == value['month'] and b['category'] == value['category']:
                        raise ValueError('此月份與分類已有預算，請編輯現有項目')
            proposed = [dict(json.loads(r['payload']), kind=r['kind']) for r in con.execute('SELECT * FROM planner_items WHERE id<>?', (item_id or '',))]
            if kind in ['account', 'debt']:
                normalize_name = lambda n: unicodedata.normalize('NFKC', n).strip().casefold()
                table = 'assets' if kind == 'account' else 'debts'
                names = [json.loads(r['payload'])['name'] for r in con.execute('SELECT payload FROM ' + table)]
                names.extend(i['name'] for i in proposed if i['kind'] == kind)
                if normalize_name(value['name']) in [normalize_name(n) for n in names]:
                    raise ValueError('此名稱已有帳單或手動記錄，請編輯現有項目，避免重複計入')
            proposed.append(dict(value, kind=kind))
            for table, kinds, field in [('assets', ['account', 'holding'], 'value'), ('debts', ['debt'], 'balance')]:
                total = sum(_cents(json.loads(r['payload'])[field]) for r in con.execute('SELECT payload FROM ' + table))
                total += sum(_cents(i[field]) for i in proposed if i['kind'] in kinds)
                if total > 100_000_000_000_000:
                    raise ValueError('合計金額超過安全範圍，未儲存')
            item_id = item_id or str(uuid.uuid4())
            revision = old['revision'] + 1 if old else 1
            con.execute('INSERT OR REPLACE INTO planner_items VALUES(?,?,?,?,?)',
                        (item_id, kind, json.dumps(value, ensure_ascii=False), revision, datetime.now(timezone.utc).isoformat()))
        return dict(value, id=item_id, kind=kind, revision=revision)

    def delete_item(self, item_id, revision):
        if not isinstance(item_id, str) or not re.fullmatch(r'[a-f0-9-]{36}', item_id):
            raise ValueError('資料ID不正確')
        with self.ledger._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT revision FROM planner_items WHERE id=?', (item_id,)).fetchone()
            if not row or type(revision) is not int or revision != row['revision']:
                raise ValueError('資料不存在或已更新，請重新整理')
            con.execute('DELETE FROM planner_items WHERE id=?', (item_id,))
        return {'deleted': True, 'id': item_id}

    def settings(self, data):
        if not isinstance(data, dict) or set(data) - {'theme', 'widgets', 'targets', 'news_schedule'}:
            raise ValueError('設定欄位不正確')
        if 'news_schedule' in data:
            schedule = data['news_schedule']
            if not isinstance(schedule, dict) or set(schedule) != {'enabled', 'hour', 'minute'} or type(schedule['enabled']) is not bool or type(schedule['hour']) is not int or type(schedule['minute']) is not int or not 0 <= schedule['hour'] <= 23 or not 0 <= schedule['minute'] <= 59:
                raise ValueError('每日更新時間不正確')
        allowed_widgets = ['net', 'income', 'expense', 'budgets', 'goals', 'bills', 'allocation', 'recent']
        if 'theme' in data and data['theme'] not in ['light', 'dark']:
            raise ValueError('主題不正確')
        if 'widgets' in data and (not isinstance(data['widgets'], list) or not 1 <= len(data['widgets']) <= 8 or
                                 any(not isinstance(w, str) or w not in allowed_widgets for w in data['widgets']) or len(set(data['widgets'])) != len(data['widgets'])):
            raise ValueError('請保留至少一個有效面板')
        if 'targets' in data:
            targets = data['targets']
            if not isinstance(targets, dict) or not targets or set(targets) - set(CLASSES):
                raise ValueError('目標配置類別不正確')
            total = Decimal(0)
            for value in targets.values():
                n = decimal_value(value, '目標比例', 2)
                if n > 100:
                    raise ValueError('單項目標不可超過100%')
                total += n
            if total != 100:
                raise ValueError('目標配置合計必須為100%')
        with self.ledger._connect() as con:
            for k, v in data.items():
                con.execute('INSERT OR REPLACE INTO planner_settings VALUES(?,?)', (k, json.dumps(v, ensure_ascii=False)))
        return self.state()['settings']

    def overview(self, month):
        if not month_valid(month):
            raise ValueError('月份不正確')
        state, ledger = self.state(), self.ledger.snapshot()
        accounts = [i for i in state['items'] if i['kind'] == 'account']
        holdings = [i for i in state['items'] if i['kind'] == 'holding']
        loans = [i for i in state['items'] if i['kind'] == 'debt']
        asset_value = sum((_cents(a['value']) for a in ledger['assets'] + accounts + holdings), 0)
        debt_value = sum((_cents(a['balance']) for a in ledger['debts'] + loans), 0)
        if asset_value > 100_000_000_000_000 or debt_value > 100_000_000_000_000:
            raise ValueError('合計金額超過安全範圍')
        categories = {c: 0 for c in sorted(SPENDING_CATEGORIES)}
        for t in ledger['transactions']:
            if t['date'].startswith(month) and t['category'] in categories:
                categories[t['category']] -= _cents(t['amount'])
        allocation = {c: 0 for c in CLASSES}
        allocation['現金與存款'] = sum(_cents(a['value']) for a in ledger['assets'])
        for a in accounts + holdings:
            allocation[a['asset_class']] += _cents(a['value'])
        budgets = []
        for item in state['items']:
            if item['kind'] == 'budget' and item['month'] == month:
                used = categories[item['category']]
                budgets.append(dict(item, used=_money(used), remaining=_money(_cents(item['limit']) - used)))
        total_cost = sum(_cents(h['cost']) for h in holdings)
        total_market = sum(_cents(h['value']) for h in holdings)
        targets = state['settings'].get('targets', {})
        target_cents = None
        if targets:
            exact = {c: Decimal(asset_value) * Decimal(str(targets.get(c, '0'))) / 100 for c in CLASSES}
            target_cents = {c: int(v.to_integral_value(rounding=ROUND_FLOOR)) for c, v in exact.items()}
            for c in sorted(CLASSES, key=lambda c: exact[c] - target_cents[c], reverse=True)[:asset_value - sum(target_cents.values())]:
                target_cents[c] += 1
        rebalance = [{'asset_class': c, 'value': _money(v),
                      'actual': round(v / asset_value * 100, 4) if asset_value else 0,
                      'target': str(targets.get(c, '0')),
                      'difference': _money(target_cents[c] - v) if target_cents is not None else None}
                     for c, v in allocation.items() if v or c in targets]
        return {'month': month, 'assets': _money(asset_value), 'liabilities': _money(debt_value),
                'net_worth': _money(asset_value - debt_value), 'cashflow': self.ledger.stats(month),
                'spending': {k: _money(v) for k, v in categories.items()}, 'budgets': budgets,
                'allocation': rebalance, 'investment': {'cost': _money(total_cost), 'value': _money(total_market),
                    'unrealized': _money(total_market - total_cost)},
                'items': state['items'], 'settings': state['settings'], 'snapshots': state['snapshots']}

    def history(self, month):
        if not month_valid(month):
            raise ValueError('月份不正確')
        history = [self.ledger.stats(shift_month(month, i)) for i in range(-5, 1) if month_valid(shift_month(month, i))]
        usable = [h for h in history[:-1] if h['transaction_count'] > 0]
        forecast = []
        if len(usable) >= 3:
            avg_i = int((sum(Decimal(h['income']) for h in usable) / len(usable) * 100).quantize(Decimal(1)))
            avg_e = int((sum(Decimal(h['expense']) for h in usable) / len(usable) * 100).quantize(Decimal(1)))
            forecast = [{'month': shift_month(month, i), 'income': _money(avg_i), 'expense': _money(avg_e),
                         'balance': _money(avg_i - avg_e)} for i in range(1, 4)]
        return {'history': history, 'forecast': forecast, 'sample_months': len(usable),
                'assumption': '前五個已結束月份中有資料的月份取固定均值；至少需三個月份。未納入未上傳帳戶或未來事件。'}

    def snapshot(self, day):
        _date(day)
        ov = self.overview(day[:7])
        with self.ledger._connect() as con:
            con.execute('INSERT OR REPLACE INTO planner_snapshots VALUES(?,?)',
                        (day, json.dumps({k: ov[k] for k in ['assets', 'liabilities', 'net_worth']})))
        return self.state()['snapshots']

    def copy_budgets(self, month):
        if not month_valid(month):
            raise ValueError('月份不正確')
        previous = shift_month(month, -1)
        with self.ledger._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            rows = [json.loads(r['payload']) for r in con.execute("SELECT payload FROM planner_items WHERE kind='budget'")]
            existing = {r['category'] for r in rows if r['month'] == month}
            candidates = [dict(r, month=month) for r in rows if r['month'] == previous and r['category'] not in existing]
            if con.execute('SELECT count(*) FROM planner_items').fetchone()[0] + len(candidates) > 5000:
                raise ValueError('複製後超過5000筆上限，整批未儲存')
            for item in candidates:
                value = self.normalize('budget', item)
                con.execute('INSERT INTO planner_items VALUES(?,?,?,?,?)', (str(uuid.uuid4()), 'budget', json.dumps(value, ensure_ascii=False), 1, datetime.now(timezone.utc).isoformat()))
        return {'created': len(candidates)}

    def schedule(self, item_id):
        item = next((i for i in self.state()['items'] if i['id'] == item_id and i['kind'] == 'debt'), None)
        if item is None:
            raise ValueError('找不到貸款資料')
        balance, payment = Decimal(item['balance']), Decimal(item['payment'])
        rate, rows, total_interest = Decimal(item['rate']) / 1200, [], Decimal(0)
        for n in range(1, 601):
            if balance <= 0:
                break
            interest = (balance * rate).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            if payment <= interest:
                raise ValueError('每月還款不足以支付利息，無法攤還本金')
            paid = min(payment, balance + interest)
            principal = paid - interest
            balance -= principal
            total_interest += interest
            rows.append({'period': n, 'payment': str(paid), 'interest': str(interest), 'principal': str(principal), 'remaining': str(balance)})
        return {'rows': rows, 'interest': str(total_interest), 'paid_off': balance == 0,
                'assumption': '固定年利率/12、每期末等額還款；不含費用與提前還款。最多試算600期。'}


def xirr(flows):
    if not isinstance(flows, list) or not 2 <= len(flows) <= 1000:
        raise ValueError('XIRR需要2–1000筆帶日期的投資現金流')
    values = [(_date(f.get('date')), _cents(f.get('amount'))) for f in flows if isinstance(f, dict)]
    if len(values) != len(flows) or not any(v < 0 for _, v in values) or not any(v > 0 for _, v in values):
        raise ValueError('現金流必須同時包含投入負數與收回正數')
    values.sort()
    base = date.fromisoformat(values[0][0])
    terms = [((date.fromisoformat(d) - base).days / 365, v / 100) for d, v in values]
    if max(t for t, _ in terms) == 0:
        raise ValueError('同一天現金流無法計算年化報酬')
    def npv(rate):
        exponents = [-t * math.log1p(rate) for t, _ in terms]
        scale = max(exponents)
        return sum(v * math.exp(exponent - scale) for exponent, (_, v) in zip(exponents, terms))
    points = sorted(set([-0.9999, -0.99, -0.9, -0.5, -0.1, 0, .01, .05, .1, .2, .5, 1, 2, 5, 10, 100, 1000, 10000, 1000000]))
    intervals, exact = [], []
    for a, b in zip(points, points[1:]):
        fa, fb = npv(a), npv(b)
        if abs(fa) < 1e-8:
            exact.append(a)
        if fa * fb < 0:
            intervals.append((a, b))
    roots = exact[:]
    for a, b in intervals:
        for _ in range(160):
            mid = (a + b) / 2
            if npv(a) * npv(mid) <= 0:
                b = mid
            else:
                a = mid
        roots.append((a + b) / 2)
    roots = sorted({round(r, 9) for r in roots})
    if len(roots) != 1:
        raise ValueError('此組現金流沒有可確認的唯一XIRR；請檢查現金流與估值')
    # Multiple sign changes can conceal more roots outside sampled intervals.
    signs = [1 if v > 0 else -1 for _, v in values if v]
    if sum(a != b for a, b in zip(signs, signs[1:])) > 1:
        raise ValueError('現金流多次改變方向，可能有多重報酬率；此版不輸出單一XIRR')
    return {'rate': roots[0], 'percent': round(roots[0] * 100, 6), 'assumption': '實際天數/365；必須包含期末估值正現金流，不含未記錄費用。'}
