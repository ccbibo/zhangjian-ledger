"""Privacy-minimized, deterministic summaries of supplied ledger records only.

No network, database, LLM or external price data is used. A day with no supplied
transactions is not evidence of no real spending. Tests may inject ``as_of``;
HTTP handlers must use the server's Asia/Taipei date instead of a client date.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo('Asia/Taipei')
PERIODS = {'day': 1, 'week': 7, 'month': 30}
INCOME = frozenset({'薪資', '股息'})
SPENDING = ('交通', '保險', '娛樂', '生活', '餐飲')
EXCLUDED = frozenset({'轉帳', '投資', '信用卡繳款'})
MAX_AMOUNT = Decimal('1000000000000')


def _day(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError('日期時間必須包含時區')
        return value.astimezone(TAIPEI).date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('日期須為有效的 YYYY-MM-DD')
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError('日期須為有效的 YYYY-MM-DD') from error


def _amount(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError('金額需為十進位數字')
    try:
        n = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError('金額需為十進位數字') from error
    if not n.is_finite() or n.as_tuple().exponent < -2 or abs(n) > MAX_AMOUNT:
        raise ValueError('金額超出有效範圍或兩位小數限制')
    return n


def _transaction_amount(row):
    if 'amount' in row:
        n = _amount(row['amount'])
        if 'amount_cents' in row:
            cents = row['amount_cents']
            if type(cents) is not int or Decimal(cents) != n * 100:
                raise ValueError('金額與整數分不一致')
        return n
    cents = row.get('amount_cents')
    if type(cents) is not int or abs(cents) > int(MAX_AMOUNT * 100):
        raise ValueError('交易缺少有效金額')
    return Decimal(cents) / 100


def _money(n):
    # Avoid displaying -0.00 after zero-net corrections.
    rounded = n.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    return format(abs(rounded) if rounded == 0 else rounded, '.2f')


def _window(rows, start, end):
    relevant = [r for r in rows if start <= r[0] <= end]
    counted = [r for r in relevant if r[1] in INCOME or r[1] in SPENDING]
    income = sum((n for _, c, n in counted if c in INCOME), Decimal(0))
    expense = -sum((n for _, c, n in counted if c in SPENDING), Decimal(0))
    categories = {c: _money(-sum((n for _, cat, n in counted if cat == c), Decimal(0))) for c in SPENDING}
    return {
        'start': start.isoformat(), 'end': end.isoformat(),
        'days': (end - start).days + 1,
        'income': _money(income), 'expense': _money(expense), 'balance': _money(income - expense),
        'categories': categories, 'valid_count': len(counted),
        'excluded_count': len(relevant) - len(counted), 'record_count': len(relevant),
    }


def summarize(transactions, items, period='week', as_of=None):
    """Summarize 1/7/30 inclusive calendar days and the adjacent equal period.

    ``valid_count`` counts categorized income/spending records, including signed
    corrections. Transfers, investment flows and card repayments are excluded.
    Unsupported/malformed records are counted and disclosed, never echoed.
    Monetary output is a two-decimal string; percentages have null zero bases.
    """
    if not isinstance(transactions, list) or not isinstance(items, list):
        raise ValueError('交易與規劃資料必須是陣列')
    if period == 'today':
        period = 'day'
    if not isinstance(period, str) or period not in PERIODS:
        raise ValueError('摘要期間須為 day、week 或 month')
    today = datetime.now(TAIPEI).date() if as_of is None else _day(as_of)
    days = PERIODS[period]
    try:
        start = today - timedelta(days=days - 1)
        prior_end = start - timedelta(days=1)
        prior_start = prior_end - timedelta(days=days - 1)
        upcoming_end = today + timedelta(days=7)
    except OverflowError as error:
        raise ValueError('摘要日期超出可計算範圍') from error
    rows, invalid = [], 0
    for row in transactions:
        try:
            if not isinstance(row, dict) or not isinstance(row.get('category'), str):
                raise ValueError('無效交易')
            category = row['category']
            if category not in INCOME and category not in SPENDING and category not in EXCLUDED:
                raise ValueError('未支援分類')
            rows.append((_day(row.get('date')), category, _transaction_amount(row)))
        except (ValueError, TypeError, InvalidOperation, OverflowError):
            invalid += 1
    current = _window(rows, start, today)
    prior = _window(rows, prior_start, prior_end)
    change = {field: _money(Decimal(current[field]) - Decimal(prior[field])) for field in ('income', 'expense', 'balance')}
    change['categories'] = {c: _money(Decimal(current['categories'][c]) - Decimal(prior['categories'][c])) for c in SPENDING}
    change['percent'] = {
        field: None if Decimal(prior[field]) == 0 else _money((Decimal(current[field]) - Decimal(prior[field])) / abs(Decimal(prior[field])) * 100)
        for field in ('income', 'expense', 'balance')
    }
    attention = []
    if Decimal(current['balance']) < 0:
        attention.append({'type': 'deficit', 'severity': 'notice', 'amount': _money(-Decimal(current['balance'])),
                          'message': '在此期間已匯入的紀錄中，支出高於收入；這不代表完整財務狀況。'})
    for c in SPENDING:
        delta = Decimal(change['categories'][c])
        if delta > 0:
            attention.append({'type': 'category_increase', 'severity': 'info', 'category': c, 'amount': _money(delta),
                              'message': '此分類的已匯入淨支出高於等長前期；前期零值不代表實際沒有消費。'})
    bill_groups = {'overdue_bills': [], 'upcoming_bills': []}
    invalid_bills = 0
    for item in items:
        if not isinstance(item, dict) or item.get('kind') != 'bill' or item.get('status', 'unpaid') == 'paid':
            continue
        try:
            if item.get('status', 'unpaid') != 'unpaid':
                raise ValueError('提醒狀態無效')
            due, n = _day(item.get('due')), _amount(item.get('amount'))
            if n <= 0:
                raise ValueError('提醒金額無效')
            if due < today:
                bill_groups['overdue_bills'].append((due, n))
            elif due <= upcoming_end:
                bill_groups['upcoming_bills'].append((due, n))
        except (ValueError, TypeError, InvalidOperation, OverflowError):
            invalid_bills += 1
    for kind, bills in bill_groups.items():
        if bills:
            attention.append({'type': kind, 'severity': 'notice', 'count': len(bills),
                              'amount': _money(sum((n for _, n in bills), Decimal(0))),
                              'due': min(d for d, _ in bills).isoformat(),
                              'message': '已記錄為未付且日期已過的帳單；繳款後請更新狀態。' if kind == 'overdue_bills' else '已記錄為未付且今天起七天內到期的帳單。'})
    observed = len({d for d, _, _ in rows if start <= d <= today})
    return {
        'period': period, 'as_of': today.isoformat(), 'timezone': 'Asia/Taipei',
        'income': current['income'], 'expense': current['expense'], 'balance': current['balance'],
        'categories': current['categories'], 'valid_count': current['valid_count'],
        'current': current, 'prior': prior, 'change': change,
        'coverage': {'observed_days': observed, 'expected_days': days, 'has_current_data': bool(current['record_count']),
                     'invalid_transactions': invalid, 'invalid_bills': invalid_bills, 'complete': False,
                     'note': '僅依已提供且有效的紀錄彙總，無法確認資料完整性；沒有紀錄或金額為零不代表實際沒有收入或消費。'},
        'attention': attention,
    }
