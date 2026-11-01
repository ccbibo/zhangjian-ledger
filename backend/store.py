"""Local-only SQLite ledger. Original document bytes never enter this store.

Money crosses the API as decimal strings and is persisted as integer cents.
Every commit validates the complete batch before a single atomic transaction.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

MAX_CENTS = 100_000_000_000_000
MAX_ROWS = 10_000
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
CATEGORIES = {"薪資", "股息", "餐飲", "交通", "生活", "保險", "娛樂", "轉帳", "投資", "信用卡繳款"}
EXCLUDED_CATEGORIES = {"轉帳", "投資", "信用卡繳款"}
INCOME_CATEGORIES = {"薪資", "股息"}
SPENDING_CATEGORIES = {"生活", "餐飲", "交通", "保險", "娛樂"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, label: str, max_length: int = 255) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(f"{label} 必須是 1–{max_length} 字的文字")
    return value.strip()


def _cents(value: Any, label: str = "金額", nonnegative: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f"{label} 必須是十進位金額")
    if isinstance(value, str) and not re.fullmatch(r"[+-]?\d+(?:\.\d{1,2})?", value.strip()):
        raise ValueError(f"{label} 必須是十進位且最多兩位小數")
    try:
        n = Decimal(str(value).strip())
        if not n.is_finite() or n.as_tuple().exponent < -2 or abs(n) > Decimal("1000000000000"):
            raise ValueError(f"{label} 超出金額或兩位小數限制")
        if nonnegative and n < 0:
            raise ValueError(f"{label} 不可為負數")
        return int(n * 100)
    except (InvalidOperation, OverflowError) as error:
        raise ValueError(f"{label} 無效") from error


def _money(cents: int) -> str:
    return format(Decimal(cents) / Decimal(100), ".2f")


def _date(value: Any, label: str = "日期") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{label} 必須為 YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label} 無效") from error
    if not 1900 <= parsed.year <= 2200:
        raise ValueError(f"{label} 年份須介於 1900–2200")
    return value


def _json(value: Any) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError("文件資料必須為有效 JSON，不能包含原始 bytes") from error
    if len(encoded.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValueError("解析後文件資料上限為 2 MB")
    return encoded


def _rows(value: Any, label: str, limit: int = MAX_ROWS) -> list:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{label} 必須為陣列，最多 {limit} 筆")
    return value


def _key(row: dict) -> str:
    canonical = [row["date"], unicodedata.normalize("NFKC", row["name"]).strip(), row["amount"]]
    return hashlib.sha256(json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _transaction(row: Any, source: str, provenance: dict | None = None) -> dict:
    if not isinstance(row, dict):
        raise ValueError("交易必須為物件")
    result = {
        "date": _date(row.get("date")),
        "name": _text(row.get("name", row.get("description")), "交易摘要", 150),
        "amount": _money(_cents(row.get("amount"))),
        "category": row.get("category", "生活"),
        "source": source,
    }
    if not isinstance(result["category"], str) or result["category"] not in CATEGORIES:
        raise ValueError("不支援此交易分類")
    origin = provenance if provenance is not None else row
    for field in ("page", "line"):
        if field in origin:
            value = origin[field]
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100_000:
                raise ValueError(f"{field} 必須是 1–100000 的整數")
            result[field] = value
    return result


def _asset(row: Any) -> dict:
    if not isinstance(row, dict):
        raise ValueError("資產必須為物件")
    currency = row.get("currency", "TWD")
    if currency != "TWD":
        raise ValueError("此版本只接受 TWD 資產，其他幣別請先人工換算")
    account = _text(row.get("account", row.get("account_id", row.get("name"))), "資產帳戶", 150)
    return {
        "account": account,
        "name": _text(row.get("name", account), "資產名稱", 150),
        "value": _money(_cents(row.get("value", row.get("balance")), "資產金額", True)),
        "currency": currency,
        **({"as_of": _date(row["as_of"], "快照日期")} if "as_of" in row else {}),
        **({"source": _text(row["source"], "資產來源")} if "source" in row else {}),
    }


def _debt(row: Any) -> dict:
    if not isinstance(row, dict):
        raise ValueError("負債必須為物件")
    if row.get("currency", "TWD") != "TWD":
        raise ValueError("此版本只接受 TWD 負債，其他幣別請先人工換算")
    account = _text(row.get("account", row.get("account_id", row.get("name"))), "負債帳戶", 150)
    result = {
        "account": account,
        "name": _text(row.get("name", account), "負債名稱", 150),
        "balance": _money(_cents(row.get("balance", row.get("payment")), "負債本金", True)),
        "payment": _money(_cents(row.get("payment", row.get("balance")), "本期應繳", True)),
        "currency": "TWD",
    }
    if "due" in row and row["due"] is not None:
        result["due"] = _date(row["due"], "繳款日期")
    if "as_of" in row:
        result["as_of"] = _date(row["as_of"], "負債快照日期")
    if "source" in row:
        result["source"] = _text(row["source"], "負債來源")
    if "minimum_payment" in row:
        minimum = _cents(row["minimum_payment"], "最低應繳", True)
        if minimum > _cents(result["payment"]):
            raise ValueError("最低應繳不可大於本期應繳")
        result["minimum_payment"] = _money(minimum)
    if "rate" in row:
        try:
            rate = Decimal(str(row["rate"]))
        except InvalidOperation as error:
            raise ValueError("負債利率無效") from error
        if not rate.is_finite() or not 0 <= rate <= 100:
            raise ValueError("負債利率須介於 0–100")
        result["rate"] = str(rate)
    return result


def _snapshot_warning(table: str, incoming: dict, existing: dict | None) -> str | None:
    """Do not infer chronology from upload time or a credit-card payment due date."""
    if not existing or not existing.get("as_of"):
        return None
    label = "資產" if table == "assets" else "負債"
    if not incoming.get("as_of"):
        return f"{label}帳戶「{incoming['account']}」快照未更新：新文件缺少快照日期；保留 {existing['as_of']} 的已知日期快照。交易仍可入帳。"
    if incoming["as_of"] < existing["as_of"]:
        return f"{label}帳戶「{incoming['account']}」快照未更新：文件日期 {incoming['as_of']} 較舊；保留 {existing['as_of']} 的最新快照。交易仍可入帳。"
    return None


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        if self.db_path == ":memory:":
            raise ValueError("Store 使用檔案 SQLite；測試請使用臨時資料庫檔案")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY, file_hash TEXT UNIQUE NOT NULL,
                payload TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','committed')),
                created_at TEXT NOT NULL, committed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY, tx_key TEXT NOT NULL, document_id TEXT NOT NULL,
                date TEXT NOT NULL, name TEXT NOT NULL, amount_cents INTEGER NOT NULL,
                category TEXT NOT NULL, payload TEXT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES documents(document_id)
            );
            CREATE INDEX IF NOT EXISTS transaction_key_idx ON transactions(tx_key);
            CREATE INDEX IF NOT EXISTS transaction_date_idx ON transactions(date);
            CREATE TABLE IF NOT EXISTS assets (account TEXT PRIMARY KEY, payload TEXT NOT NULL, document_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS debts (account TEXT PRIMARY KEY, payload TEXT NOT NULL, document_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS transaction_audit (
                audit_id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL, action TEXT NOT NULL,
                previous_payload TEXT, next_payload TEXT, created_at TEXT NOT NULL
            );
            """)

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            with con:
                yield con
        finally:
            con.close()

    def _document_result(self, row: sqlite3.Row, reused: bool) -> dict:
        return {**json.loads(row["payload"]), "document_id": row["document_id"], "status": row["status"], "reused": reused}

    def preview(self, doc: dict, file_hash: str) -> dict:
        if not isinstance(file_hash, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", file_hash):
            raise ValueError("檔案 hash 必須為 SHA-256")
        file_hash = file_hash.lower()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute("SELECT * FROM documents WHERE file_hash=?", (file_hash,)).fetchone()
            if existing:
                return self._document_result(existing, True)
            if not isinstance(doc, dict):
                raise ValueError("文件資料必須為物件")
            _json(doc)
            if not isinstance(doc.get("metadata", {}), dict):
                raise ValueError("metadata 必須為物件")
            payload = copy.deepcopy(doc)
            source = _text(doc.get("filename", doc.get("source", doc.get("metadata", {}).get("filename", "上傳文件"))), "來源檔名")
            rows = _rows(doc.get("transactions", []), "交易")
            keys = {r["tx_key"] for r in con.execute("SELECT tx_key FROM transactions")}
            normalized = []
            for row in rows:
                t = _transaction(row, source)
                key = _key(t)
                duplicate = key in keys
                keys.add(key)
                normalized.append({**t, "id": str(uuid.uuid4()), "key": key, "duplicate": duplicate, "keep": not duplicate})
            if sum(abs(_cents(t["amount"])) for t in normalized) > MAX_CENTS:
                raise ValueError("文件交易總金額超過限制")
            assets = [_asset(a) for a in _rows(doc.get("assets", []), "資產", 100)]
            debts = [_debt(d) for d in _rows(doc.get("debts", []), "負債", 100)]
            if len({a["account"] for a in assets}) != len(assets) or len({d["account"] for d in debts}) != len(debts):
                raise ValueError("文件內資產或負債帳戶不可重複")
            if sum(_cents(a["value"]) for a in assets) > MAX_CENTS or sum(_cents(d["balance"]) for d in debts) > MAX_CENTS:
                raise ValueError("資產或負債總金額超過限制")
            warnings = doc.get("warnings", [])
            if not isinstance(warnings, list) or not all(isinstance(w, str) for w in warnings):
                raise ValueError("warnings 必須為文字陣列")
            warnings = list(warnings)
            for table, items in (("assets", assets), ("debts", debts)):
                for item in items:
                    current = con.execute(f"SELECT payload FROM {table} WHERE account=?", (item["account"],)).fetchone()
                    warning = _snapshot_warning(table, item, json.loads(current[0]) if current else None)
                    if warning:
                        warnings.append(warning)
            payload["warnings"] = warnings
            payload.update(transactions=normalized, assets=assets, debts=debts)
            document_id = str(uuid.uuid4())
            con.execute("INSERT INTO documents VALUES (?, ?, ?, 'pending', ?, NULL)", (document_id, file_hash, _json(payload), _now()))
            return {**payload, "document_id": document_id, "status": "pending", "reused": False}

    def commit(self, document_id: str, reviewed_transactions: list, accept_duplicates: bool = False) -> dict:
        document_id = _text(document_id, "文件 ID", 100)
        _rows(reviewed_transactions, "覆核交易")
        if not isinstance(accept_duplicates, bool):
            raise ValueError("accept_duplicates 必須為布林值")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            doc = con.execute("SELECT * FROM documents WHERE document_id=?", (document_id,)).fetchone()
            if not doc:
                raise ValueError("找不到文件")
            if doc["status"] != "pending":
                raise ValueError("此文件已入帳，不可重複提交")
            payload = json.loads(doc["payload"])
            original = {t["id"]: t for t in payload["transactions"]}
            ids = set()
            selected = []
            keys = {r["tx_key"] for r in con.execute("SELECT tx_key FROM transactions")}
            duplicates = 0
            for row in reviewed_transactions:
                if not isinstance(row, dict):
                    raise ValueError("覆核交易必須為物件")
                row_id = _text(row.get("id"), "交易 ID", 100)
                if row_id in ids:
                    raise ValueError("覆核交易 ID 不可重複")
                ids.add(row_id)
                if row_id not in original:
                    raise ValueError("覆核包含不屬於此文件的交易 ID")
                if not isinstance(row.get("keep", True), bool):
                    raise ValueError("keep 必須為布林值")
                if not row.get("keep", True):
                    continue
                origin = original[row_id]
                merged = {**origin, **row}
                t = _transaction(merged, origin["source"], origin)
                key = _key(t)
                duplicate = key in keys
                if duplicate and not accept_duplicates:
                    raise ValueError("發現重複交易；請取消勾選，或明確允許保留重複交易")
                duplicates += int(duplicate)
                keys.add(key)
                selected.append({**t, "id": row_id, "key": key, "document_id": document_id})
            existing_total = sum(abs(r[0]) for r in con.execute("SELECT amount_cents FROM transactions"))
            if existing_total + sum(abs(_cents(t["amount"])) for t in selected) > MAX_CENTS:
                raise ValueError("帳本交易總金額超過限制")
            if con.execute("SELECT count(*) FROM transactions").fetchone()[0] + len(selected) > MAX_ROWS:
                raise ValueError("帳本最多 10000 筆交易")
            for t in selected:
                con.execute("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (t["id"], t["key"], document_id, t["date"], t["name"], _cents(t["amount"]), t["category"], _json(t)))
            commit_warnings = []
            planned = []
            if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='planner_items'").fetchone():
                planned = [dict(json.loads(r['payload']), kind=r['kind']) for r in con.execute('SELECT * FROM planner_items')]
            for table, items in (("assets", payload["assets"]), ("debts", payload["debts"])):
                for item in items:
                    kind = 'account' if table == 'assets' else 'debt'
                    normalized_name = lambda name: unicodedata.normalize('NFKC', name).strip().casefold()
                    if any(p['kind'] == kind and normalized_name(p['name']) == normalized_name(item['name']) for p in planned):
                        raise ValueError('已有同名手動帳戶或負債；請先移除手動快照再匯入，避免重複計入')
                    current = con.execute(f"SELECT payload FROM {table} WHERE account=?", (item["account"],)).fetchone()
                    warning = _snapshot_warning(table, item, json.loads(current[0]) if current else None)
                    if warning:
                        commit_warnings.append(warning)
                        continue
                    con.execute(f"INSERT INTO {table} VALUES (?, ?, ?) ON CONFLICT(account) DO UPDATE SET payload=excluded.payload, document_id=excluded.document_id", (item["account"], _json(item), document_id))
            for table, field in (("assets", "value"), ("debts", "balance")):
                total = sum(_cents(json.loads(r[0])[field]) for r in con.execute(f"SELECT payload FROM {table}"))
                kinds = ['account', 'holding'] if table == 'assets' else ['debt']
                total += sum(_cents(p[field]) for p in planned if p['kind'] in kinds)
                if total > MAX_CENTS:
                    raise ValueError("帳本資產或負債總金額超過限制")
            payload["reviewed_transactions"] = selected
            payload["commit_warnings"] = commit_warnings
            con.execute("UPDATE documents SET status='committed', committed_at=?, payload=? WHERE document_id=?", (_now(), _json(payload), document_id))
        warnings = list(dict.fromkeys(payload.get("warnings", []) + commit_warnings))
        return {"document_id": document_id, "status": "committed", "committed_count": len(selected), "duplicate_count": duplicates, "warnings": warnings, "snapshot": self.snapshot()}

    def snapshot(self) -> dict:
        with self._connect() as con:
            tx = [json.loads(r[0]) for r in con.execute("SELECT payload FROM transactions ORDER BY date DESC, rowid DESC")]
            assets = [json.loads(r[0]) for r in con.execute("SELECT payload FROM assets ORDER BY account")]
            debts = [json.loads(r[0]) for r in con.execute("SELECT payload FROM debts ORDER BY account")]
            imports = []
            for row in con.execute("SELECT * FROM documents WHERE status='committed' ORDER BY committed_at DESC"):
                payload = json.loads(row["payload"])
                imports.append({"document_id": row["document_id"], "file_hash": row["file_hash"], "name": payload.get("filename", payload.get("source", payload.get("metadata", {}).get("filename", "上傳文件"))), "kind": payload.get("kind"), "count": len(payload.get("reviewed_transactions", [])), "date": row["committed_at"], "status": row["status"], "warnings": list(dict.fromkeys(payload.get("warnings", []) + payload.get("commit_warnings", [])))})
            return {"transactions": tx, "assets": assets, "debts": debts, "imports": imports}

    def create_transaction(self, data: dict) -> dict:
        """Create an explicit manual record through the same validated pipeline."""
        if not isinstance(data, dict):
            raise ValueError("交易必須為物件")
        doc = {"kind": "MANUAL", "filename": "手動新增", "transactions": [data], "metadata": {}}
        pending = self.preview(doc, hashlib.sha256(uuid.uuid4().bytes).hexdigest())
        return self.commit(pending["document_id"], pending["transactions"])

    def replace_transaction(self, transaction_id: str, data: dict) -> dict:
        """Edit financial fields, keeping immutable source, line/page and identity."""
        transaction_id = _text(transaction_id, "交易 ID", 100)
        if not isinstance(data, dict):
            raise ValueError("交易必須為物件")
        if "id" in data and data["id"] != transaction_id:
            raise ValueError("不可更換交易 ID")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM transactions WHERE id=?", (transaction_id,)).fetchone()
            if row is None:
                raise ValueError("找不到交易")
            previous = json.loads(row["payload"])
            t = _transaction({**previous, **data}, previous["source"], previous)
            key = _key(t)
            if con.execute("SELECT 1 FROM transactions WHERE tx_key=? AND id<>?", (key, transaction_id)).fetchone():
                raise ValueError("編輯後與既有交易重複，請重新確認")
            existing_total = sum(abs(r[0]) for r in con.execute("SELECT amount_cents FROM transactions WHERE id<>?", (transaction_id,)))
            if existing_total + abs(_cents(t["amount"])) > MAX_CENTS:
                raise ValueError("帳本交易總金額超過限制")
            t.update(id=transaction_id, key=key, document_id=previous["document_id"])
            con.execute("UPDATE transactions SET tx_key=?,date=?,name=?,amount_cents=?,category=?,payload=? WHERE id=?", (key, t["date"], t["name"], _cents(t["amount"]), t["category"], _json(t), transaction_id))
            con.execute("INSERT INTO transaction_audit VALUES (?, ?, 'edit', ?, ?, ?)", (str(uuid.uuid4()), transaction_id, row["payload"], _json(t), _now()))
        return {"transaction": t, "snapshot": self.snapshot()}

    def delete_transaction(self, transaction_id: str) -> dict:
        """Remove a record from balances while retaining an audit tombstone."""
        transaction_id = _text(transaction_id, "交易 ID", 100)
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT payload FROM transactions WHERE id=?", (transaction_id,)).fetchone()
            if row is None:
                raise ValueError("找不到交易")
            con.execute("INSERT INTO transaction_audit VALUES (?, ?, 'delete', ?, NULL, ?)", (str(uuid.uuid4()), transaction_id, row["payload"], _now()))
            con.execute("DELETE FROM transactions WHERE id=?", (transaction_id,))
        return {"id": transaction_id, "deleted": True, "snapshot": self.snapshot()}

    def stats(self, month: str) -> dict:
        if not isinstance(month, str) or not re.fullmatch(r"(?:19\d{2}|20\d{2}|21\d{2}|2200)-(?:0[1-9]|1[0-2])", month):
            raise ValueError("月份必須為 1900–2200 年的 YYYY-MM")
        with self._connect() as con:
            rows = con.execute("SELECT amount_cents, category FROM transactions WHERE substr(date,1,7)=?", (month,)).fetchall()
        included = [r for r in rows if r["category"] not in EXCLUDED_CATEGORIES]
        # A spending-category credit is a refund, not additional earned income.
        # Corrections keep their sign in the category and in their actual month.
        income = sum(r["amount_cents"] for r in included if r["category"] in INCOME_CATEGORIES)
        expense = -sum(r["amount_cents"] for r in included if r["category"] in SPENDING_CATEGORIES)
        return {"month": month, "income": _money(income), "expense": _money(expense), "balance": _money(income - expense), "transaction_count": len(rows), "excluded_count": len(rows) - len(included)}
