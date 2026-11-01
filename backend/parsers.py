"""Bounded, local-only parsers for explicitly supported statement templates.

No OCR or language model is used. Unsupported/encrypted/scanned PDFs are
rejected instead of inventing financial records. PDF support requires Poppler
(`pdfinfo` and `pdftotext`) installed by the user.
"""
from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
import tempfile
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 20
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_AMOUNT = Decimal("1000000000000")
CENT = Decimal("0.01")


class DocumentParseError(ValueError):
    """The document cannot safely be converted into reviewable records."""


CATEGORIES = {"薪資", "股息", "餐飲", "交通", "生活", "保險", "娛樂", "轉帳", "投資", "信用卡繳款"}
CATEGORY_ALIASES = {
    "salary": "薪資", "income": "薪資", "payroll": "薪資",
    "dividend": "股息", "dividends": "股息", "interest": "股息",
    "food": "餐飲", "dining": "餐飲", "meals": "餐飲",
    "transport": "交通", "transportation": "交通", "travel": "交通",
    "housing": "生活", "rent": "生活", "living": "生活", "shopping": "生活",
    "insurance": "保險", "entertainment": "娛樂",
    "transfer": "轉帳", "internal transfer": "轉帳",
    "investment": "投資", "card payment": "信用卡繳款",
    "credit card payment": "信用卡繳款", "card repayment": "信用卡繳款",
}


def parse_money(raw: str | Decimal, *, nonnegative: bool = False) -> Decimal:
    text = str(raw).strip()
    if re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", text):
        text = text.replace(",", "")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d{1,2})?|\.\d{1,2})", text):
        raise DocumentParseError("金額必須為十進位數字，最多兩位小數；千分位必須正確分組。")
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise DocumentParseError("無法辨識金額。") from error
    if not value.is_finite() or abs(value) > MAX_AMOUNT or (nonnegative and value < 0):
        raise DocumentParseError("金額超出可接受範圍（上限一兆，不允許負值的欄位須為非負數）。")
    return value.quantize(CENT)


def _money_fields(value: Decimal, key: str = "amount") -> dict[str, Any]:
    return {key: float(value), key + "_cents": int(value * 100)}


def _date(raw: str) -> str:
    text = raw.strip().replace("/", "-")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise DocumentParseError("日期須為 YYYY-MM-DD 或 YYYY/MM/DD。")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise DocumentParseError("日期不是有效的日曆日期。") from error
    if not 1900 <= parsed.year <= 2200:
        raise DocumentParseError("日期年份須介於 1900 與 2200。")
    return parsed.isoformat()


def _category(raw: str, warnings: list[str], *, line: int) -> str:
    text = raw.strip()
    if text in CATEGORIES:
        return text
    mapped = CATEGORY_ALIASES.get(text.casefold())
    if mapped:
        return mapped
    warnings.append(f"第 {line} 列分類「{text or '空白'}」未辨識，暫列生活；請人工覆核。")
    return "生活"


def _transaction(raw_date: str, name: str, raw_amount: str, category: str,
                 source: str, warnings: list[str], *, line: int, page: int | None = None) -> dict[str, Any]:
    name = name.strip()
    if not name or len(name) > 150:
        raise DocumentParseError(f"第 {line} 列摘要需介於 1–150 字。")
    result = {
        "date": _date(raw_date), "name": name,
        **_money_fields(parse_money(raw_amount)),
        "category": _category(category, warnings, line=line), "source": source,
        "line": line,
    }
    if page is not None:
        result["page"] = page
    return result


def _result(kind: str) -> dict[str, Any]:
    return {"kind": kind, "transactions": [], "assets": [], "debts": [], "warnings": [], "metadata": {}}


def _parse_csv(content: bytes, filename: str) -> dict[str, Any]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise DocumentParseError("CSV 必須使用 UTF-8 編碼；請另存 UTF-8 後重新匯入。") from error
    if "\x00" in text:
        raise DocumentParseError("CSV 含有無效的二進位字元。")
    result = _result("csv")
    aliases = {"date": "date", "日期": "date", "description": "name", "摘要": "name",
               "交易摘要": "name", "name": "name", "amount": "amount", "金額": "amount",
               "category": "category", "分類": "category"}
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
    except (StopIteration, csv.Error) as error:
        raise DocumentParseError("CSV 缺少標題列。") from error
    if not header or len(header) > 32:
        raise DocumentParseError("CSV 標題欄數不正確。")
    normalized = [h.strip().casefold() for h in header]
    if len(set(normalized)) != len(normalized):
        raise DocumentParseError("CSV 標題不可重複。")
    indexes: dict[str, int] = {}
    for i, name in enumerate(normalized):
        field = aliases.get(name)
        if field:
            if field in indexes:
                raise DocumentParseError(f"CSV 欄位 {field} 有重複或別名歧義。")
            indexes[field] = i
    if not {"date", "name", "amount"}.issubset(indexes):
        raise DocumentParseError("CSV 須含 date、description、amount 欄位。")
    if "category" not in indexes:
        result["warnings"].append("CSV 未提供分類欄，交易暫列生活；請人工覆核。")
    physical_end = reader.line_num
    try:
        for row in reader:
            start_line = physical_end + 1
            physical_end = reader.line_num
            if not row or not any(cell.strip() for cell in row):
                continue
            if len(row) != len(header):
                raise DocumentParseError(f"第 {start_line} 列欄數與標題不同；金額若含千分位逗號，請使用引號包住。")
            if len(result["transactions"]) >= MAX_ROWS:
                raise DocumentParseError("CSV 每次最多 5000 筆交易，請分批匯入。")
            result["transactions"].append(_transaction(
                row[indexes["date"]], row[indexes["name"]], row[indexes["amount"]],
                row[indexes["category"]] if "category" in indexes else "",
                filename, result["warnings"], line=start_line,
            ))
    except csv.Error as error:
        raise DocumentParseError(f"CSV 引號或分隔格式錯誤：第 {reader.line_num} 列。") from error
    if not result["transactions"]:
        raise DocumentParseError("CSV 沒有可匯入的交易。")
    result["warnings"] = list(dict.fromkeys(result["warnings"]))
    result["metadata"] = {"encoding": "UTF-8", "transaction_count": len(result["transactions"]), "local_parser": True}
    return result


def _extract_pdf(content: bytes) -> tuple[str, int]:
    if not content.startswith(b"%PDF-"):
        raise DocumentParseError("檔案內容不是有效的 PDF。")
    info_command, text_command = shutil.which("pdfinfo"), shutil.which("pdftotext")
    if not info_command or not text_command:
        raise DocumentParseError("尚未安裝 Poppler（pdfinfo、pdftotext）；無法解析 PDF，檔案不會上傳雲端。")
    try:
        with tempfile.TemporaryDirectory(prefix="ledger-pdf-") as folder:
            source = Path(folder) / "document.pdf"
            output = Path(folder) / "document.txt"
            source.write_bytes(content)
            info = subprocess.run([info_command, str(source)], capture_output=True, timeout=10, check=False)
            info_text = info.stdout.decode("utf-8", errors="replace")
            if info.returncode:
                raise DocumentParseError("PDF 無法讀取，可能已加密、損壞或需要密碼；請先解除保護再匯入。")
            if re.search(r"^Encrypted:\s+yes\b", info_text, re.I | re.M):
                raise DocumentParseError("不接受加密 PDF；請先解除保護再匯入。")
            pages_match = re.search(r"^Pages:\s+(\d+)\s*$", info_text, re.M)
            if not pages_match:
                raise DocumentParseError("無法確認 PDF 頁數，已停止解析。")
            pages = int(pages_match.group(1))
            if not 1 <= pages <= MAX_PDF_PAGES:
                raise DocumentParseError("PDF 上限 20 頁，請分拆後匯入。")
            process = subprocess.run([text_command, "-layout", "-enc", "UTF-8", str(source), str(output)],
                                     capture_output=True, timeout=10, check=False)
            if process.returncode or not output.is_file():
                raise DocumentParseError("PDF 文字擷取失敗；請確認檔案未損壞或受保護。")
            if output.stat().st_size > MAX_TEXT_BYTES:
                raise DocumentParseError("PDF 文字量過大，請分拆文件後匯入。")
            text = output.read_text(encoding="utf-8")
            if not text.strip("\f\n\r\t "):
                raise DocumentParseError("PDF 沒有可擷取文字，可能是掃描圖片；本版本未啟用 OCR，不會產生辨識結果。")
            return text, pages
    except subprocess.TimeoutExpired as error:
        raise DocumentParseError("PDF 解析超過 10 秒，已停止；請分拆或更換文件。") from error
    except OSError as error:
        raise DocumentParseError("本地 PDF 工具無法執行；請檢查 Poppler 安裝。") from error


def _pdf_lines(text: str) -> list[tuple[int, int, str]]:
    lines = []
    global_line = 0
    for page, content in enumerate(text.split("\f"), start=1):
        for raw in content.splitlines():
            global_line += 1
            if raw.strip():
                lines.append((global_line, page, raw.strip()))
    return lines


def _field(lines: list[tuple[int, int, str]], aliases: tuple[str, ...], *, required: bool = False) -> tuple[str, int, int] | None:
    matches = []
    names = "|".join(re.escape(name) for name in aliases)
    pattern = re.compile(r"^(?:" + names + r")(?:\s*[:|]\s*|\s{2,})(.+?)\s*$", re.I)
    for line, page, text in lines:
        match = pattern.fullmatch(text)
        if match:
            matches.append((match.group(1), line, page))
    # Repeated identical values in a multipage statement are safe, conflicting
    # values are not: never arbitrarily select the first financial field.
    unique = {match[0].strip() for match in matches}
    if len(unique) > 1:
        raise DocumentParseError(f"欄位 {aliases[0]} 出現互相矛盾的數值，請人工確認。")
    if not matches:
        if required:
            raise DocumentParseError(f"文件缺少必填欄位 {aliases[0]}，無法安全解析。")
        return None
    return matches[0]


def _field_money(lines: list[tuple[int, int, str]], aliases: tuple[str, ...], *, required: bool = False) -> Decimal | None:
    field = _field(lines, aliases, required=required)
    return parse_money(field[0], nonnegative=True) if field else None


def _table_transactions(lines: list[tuple[int, int, str]], filename: str, warnings: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    found_header = False
    for line, page, text in lines:
        parts = [part.strip() for part in (text.split("|") if "|" in text else re.split(r"\s{2,}", text))]
        if [part.casefold() for part in parts] == ["date", "description", "amount", "category"]:
            found_header = True
            continue
        if re.match(r"^\d{4}[-/]\d", text):
            if not found_header:
                raise DocumentParseError(f"第 {line} 列有交易資料，但缺少 Date / Description / Amount / Category 標題。")
            if len(parts) != 4:
                raise DocumentParseError(f"第 {line} 列交易欄位不完整或有多餘欄位；需四欄並以 | 或至少兩個空格分隔。")
            if len(records) >= MAX_ROWS:
                raise DocumentParseError("文件最多支援 5000 筆交易。")
            records.append(_transaction(*parts, filename, warnings, line=line, page=page))
        elif found_header and len(parts) == 4:
            # A transaction-shaped row with an unsupported date must not be
            # silently dropped, even if its amount happens to reconcile.
            raise DocumentParseError(f"第 {line} 列疑似交易但日期格式無法辨識，已停止解析。")
    if not found_header:
        raise DocumentParseError("找不到支援的交易表標題 Date / Description / Amount / Category。")
    if not records:
        raise DocumentParseError("文件中沒有可匯入的交易。")
    return records


def _sum_transactions(records: list[dict[str, Any]]) -> Decimal:
    total = sum((Decimal(record["amount_cents"]) / 100 for record in records), start=Decimal(0))
    if abs(total) > MAX_AMOUNT:
        raise DocumentParseError("交易合計超出上限一兆，請分拆文件。")
    return total


def _parse_pdf(content: bytes, filename: str) -> dict[str, Any]:
    text, pages = _extract_pdf(content)
    lines = _pdf_lines(text)
    titles = {row[2].strip().upper() for row in lines}
    kinds = [kind for title, kind in [("BANK STATEMENT", "bank_statement"),
                                      ("CREDIT CARD STATEMENT", "credit_card_statement"),
                                      ("PAYSLIP", "payslip")] if title in titles]
    if len(kinds) != 1:
        raise DocumentParseError("尚未支援此 PDF 格式；需有 BANK STATEMENT、CREDIT CARD STATEMENT 或 PAYSLIP 明確標題，請改用 CSV 匯入。")
    kind = kinds[0]
    result = _result(kind)
    result["metadata"] = {"pdf_page_count": pages, "local_parser": True, "ocr_used": False, "template": kind}
    currency = _field(lines, ("Currency",))
    if currency and currency[0].upper() not in {"TWD", "NTD", "NT$"}:
        raise DocumentParseError("目前文件模板僅支援 TWD；其他幣別請先換算並以 CSV 匯入，不會套用虛構匯率。")
    if not currency:
        result["warnings"].append("文件未標示幣別，此模板按 TWD 解析，請覆核幣別。")
    account_field = _field(lines, ("Account", "Account number", "Card account"))
    if account_field and len(account_field[0]) > 120:
        raise DocumentParseError("帳戶名稱過長，請人工確認。")
    account = account_field[0].strip() if account_field else None
    if account:
        result["metadata"]["account"] = account
    statement_date = _field(lines, ("Statement date", "Statement ending", "As of"))
    if statement_date:
        result["metadata"]["as_of"] = _date(statement_date[0])
    if kind == "payslip":
        pay_date = _field(lines, ("Pay date", "Payment date"), required=True)
        gross = _field_money(lines, ("Gross pay", "Gross salary"), required=True)
        tax = _field_money(lines, ("Tax", "Tax withholding"), required=True)
        insurance = _field_money(lines, ("Insurance",), required=True)
        net = _field_money(lines, ("Net pay", "Net salary"), required=True)
        if gross - tax - insurance != net:
            raise DocumentParseError("薪資核對不符：Gross pay − Tax − Insurance 不等於 Net pay，請人工確認。")
        description = _field(lines, ("Description",))
        result["transactions"] = [_transaction(pay_date[0], description[0] if description else "Salary",
                                               str(net), "Salary", filename, result["warnings"],
                                               line=pay_date[1], page=pay_date[2])]
        result["metadata"].update({"reconciled": True, "gross_pay": float(gross), "tax": float(tax),
                                   "insurance": float(insurance), "net_pay": float(net), "salary_basis": "net"})
    else:
        result["transactions"] = _table_transactions(lines, filename, result["warnings"])
        total = _sum_transactions(result["transactions"])
        if kind == "bank_statement":
            opening = _field_money(lines, ("Opening balance",), required=True)
            closing = _field_money(lines, ("Closing balance",), required=True)
            if opening + total != closing:
                raise DocumentParseError("銀行帳單核對不符：期初餘額加交易合計不等於期末餘額，請人工確認。")
            result["metadata"].update({"opening_balance": float(opening), "closing_balance": float(closing),
                                       "transaction_total": float(total), "reconciled": True})
            if account:
                result["assets"] = [{"account": "bank:" + account, "name": account,
                                     **_money_fields(closing, "value"), "currency": "TWD", "source": filename,
                                     **({"as_of": result["metadata"]["as_of"]} if statement_date else {})}]
            else:
                result["warnings"].append("銀行帳單缺少 Account 欄位；僅匯入交易，不建立可能覆蓋其他帳戶的資產快照。")
        else:
            due = _field(lines, ("Due date", "Payment due date"), required=True)
            due_date = _date(due[0])
            total_due = _field_money(lines, ("Total due", "Payment due", "Statement balance"), required=True)
            previous = _field_money(lines, ("Previous balance", "Opening balance")) or Decimal(0)
            minimum = _field_money(lines, ("Minimum due", "Minimum payment"))
            if minimum is not None and minimum > total_due:
                raise DocumentParseError("最低應繳不可大於本期總應繳，請人工確認。")
            if previous - total != total_due:
                raise DocumentParseError("信用卡核對不符：前期餘額減去本期帶正負號交易合計不等於總應繳，請人工確認；消費須用負數、退款／繳款須用正數。")
            result["metadata"].update({"total_due": float(total_due), "due_date": due_date,
                                       "previous_balance": float(previous), "reconciled": True})
            if minimum is not None:
                result["metadata"]["minimum_due"] = float(minimum)
            if account:
                result["debts"] = [{"account": "card:" + account, "name": account,
                                    **_money_fields(total_due, "balance"), "payment": float(total_due),
                                    "due": due_date, "currency": "TWD", "source": filename,
                                    **({"as_of": result["metadata"]["as_of"]} if statement_date else {}),
                                    **({"minimum_payment": float(minimum)} if minimum is not None else {})}]
                result["warnings"].append("未提供循環利率，本期帳款僅記錄應繳金額，未試算循環利息。")
            else:
                result["warnings"].append("信用卡帳單缺少 Account 欄位；僅匯入交易，不建立可能覆蓋其他卡片的負債快照。")
    result["metadata"]["transaction_count"] = len(result["transactions"])
    return result


def parse_document(content: bytes, filename: str) -> dict[str, Any]:
    """Parse a local CSV or supported text PDF into uncommitted review data.

    Errors are explicit and atomic: callers should stage nothing from a failed
    document. Every financial amount is validated with Decimal before JSON
    conversion. Original files are never sent to third parties.
    """
    if not isinstance(content, bytes) or not content:
        raise DocumentParseError("文件是空白或內容格式不正確。")
    if len(content) > MAX_FILE_BYTES:
        raise DocumentParseError("單一檔案上限 5 MB。")
    if not isinstance(filename, str) or not filename.strip() or len(filename) > 255 or "\x00" in filename:
        raise DocumentParseError("檔名須介於 1–255 字，不可包含無效字元。")
    # Treat a client filename only as provenance, never as a filesystem path.
    source = filename.replace("\\", "/").rsplit("/", 1)[-1]
    extension = Path(source).suffix.casefold()
    if extension == ".csv":
        if content.startswith(b"%PDF-"):
            raise DocumentParseError("副檔名與 PDF 內容不符，請使用正確檔名。")
        return _parse_csv(content, source)
    if extension == ".pdf":
        return _parse_pdf(content, source)
    raise DocumentParseError("目前支援 CSV 與指定文字型 PDF 模板；圖片 OCR 和 ZIP 尚未啟用。")
