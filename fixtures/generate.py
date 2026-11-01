#!/usr/bin/env python3
"""Deterministic, entirely synthetic financial PDFs, using only Python stdlib.

The fixture oracle is handwritten, not derived from the application parser.
Run: python3 fixtures/generate.py [--render]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
PDF = ROOT / 'pdf'
PREVIEW = ROOT / 'preview'


def literal(value: str) -> str:
    return value.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def text(value: str, x: float, y: float, size: int = 11, font: str = 'F1', color: str = '0.06 0.06 0.07') -> str:
    return f'BT /{font} {size} Tf {color} rg 1 0 0 1 {x} {y} Tm ({literal(value)}) Tj ET\n'


def page(title: str, subtitle: str, rows: list[tuple[str, int, str]], number: int, total: int) -> bytes:
    c = '0.985 0.985 0.975 rg 0 0 612 792 re f\n'
    c += '0.949 0.753 0.078 rg 36 716 540 40 re f\n'
    c += '0.06 0.06 0.07 RG 3 w 36 716 540 40 re S\n'
    c += text('SYNTHETIC / NOT REAL', 49, 729, 22, 'F2')
    c += text(title, 36, 679, 23, 'F2')
    c += text(subtitle, 36, 656, 10, 'F1', '0.3 0.3 0.3')
    c += '0.06 0.06 0.07 RG 1 w 36 641 m 576 641 l S\n'
    y = 615
    for value, size, font in rows:
        if value == '---':
            c += f'0.06 0.06 0.07 RG 0.5 w 36 {y+5} m 576 {y+5} l S\n'
            y -= 15
        else:
            c += text(value, 36, y, size, font)
            y -= max(19, size + 9)
    c += '0.06 0.06 0.07 RG 1 w 36 56 m 576 56 l S\n'
    c += text('TEST DATA ONLY - No real person, account, employer or bank.', 36, 38, 9)
    c += text(f'{number} / {total}', 540, 38, 9, 'F3')
    return c.encode('ascii')


def write_pdf(name: str, pages: list[bytes]) -> None:
    # PDF object IDs are fixed and xref byte offsets are computed after encoding.
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        ('<< /Type /Pages /Kids [' + ' '.join(f'{6+2*i} 0 R' for i in range(len(pages))) + f'] /Count {len(pages)} >>').encode(),
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>',
    ]
    for i, content in enumerate(pages):
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> /Contents {7+2*i} 0 R >>'.encode())
        objects.append(f'<< /Length {len(content)} >>\nstream\n'.encode() + content + b'endstream')
    data = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(data)
    data += f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
    for offset in offsets[1:]:
        data += f'{offset:010d} 00000 n \n'.encode()
    data += f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    (PDF / name).write_bytes(data)


def t(value: str, size: int = 11, font: str = 'F1') -> tuple[str, int, str]:
    return value, size, font


def generate() -> None:
    PDF.mkdir(parents=True, exist_ok=True)
    bank_header = [t('Account: TEST Bank 001'), t('Statement date: 2026-10-31'), t('Currency: TWD'), t('Opening Balance: 100000'), t('Closing Balance: 134400'), t('---'), t('Date | Description | Amount | Category', 10, 'F3')]
    write_pdf('bank-statement.pdf', [
        page('BANK STATEMENT', 'Fixture BANK-001 / October 2026 / Account TEST-001', bank_header + [
            t('2026-10-28 | Salary | 65000 | Salary', 10, 'F3'),
            t('2026-10-20 | Rent | -18000 | Housing', 10, 'F3'),
            t('2026-10-10 | Card repayment | -3800 | Card Payment', 10, 'F3'),
            t('---'), t('Continued on page 2. Same statement and same account.'),
        ], 1, 2),
        page('BANK STATEMENT', 'Fixture BANK-001 / continued / Account TEST-001', [
            t('Account: TEST Bank 001'), t('Statement date: 2026-10-31'), t('---'),
            t('Date | Description | Amount | Category', 10, 'F3'),
            t('2026-10-08 | Internal transfer | -10000 | Transfer', 10, 'F3'),
            t('2026-10-22 | Dividend | 1200 | Dividend', 10, 'F3'),
            t('---'), t('Reconciliation: 100000 + 65000 - 18000 - 3800 - 10000 + 1200'),
            t('Closing Balance: 134400'),
        ], 2, 2),
    ])
    write_pdf('credit-card-statement.pdf', [page('CREDIT CARD STATEMENT', 'Fixture CARD-001 / October 2026 / Card TEST-4242', [
        t('Account: TEST Credit Card 4242'), t('Statement date: 2026-10-31'), t('Currency: TWD'),
        t('Total due: 3800'), t('Due date: 2026-11-10'), t('Minimum payment: 380'), t('---'),
        t('Date | Description | Amount | Category', 10, 'F3'),
        t('2026-10-05 | Meals | -1200 | Food', 10, 'F3'),
        t('2026-10-06 | Transport | -600 | Transport', 10, 'F3'),
        t('2026-10-07 | Insurance | -2000 | Insurance', 10, 'F3'), t('---'),
        t('Reconciliation: 1200 + 600 + 2000 = 3800'),
        t('Card repayment is a settlement, not another purchase.'),
    ], 1, 1)])
    write_pdf('payslip.pdf', [page('PAYSLIP', 'Fixture PAY-001 / Synthetic employer TEST COMPANY', [
        t('Employee: TEST PERSON'), t('Account: TEST Payroll 001'), t('Pay date: 2026-10-28'),
        t('Currency: TWD'), t('Description: Salary'), t('---'),
        t('Gross pay: 72000', 14, 'F2'), t('Tax: 5000'), t('Insurance: 2000'),
        t('---'), t('Net pay: 65000', 18, 'F2'),
        t('Reconciliation: 72000 - 5000 - 2000 = 65000'),
        t('One net salary transaction; gross and deductions are payroll metadata.'),
        t('The same Salary deposit also appears on BANK-001.'),
    ], 1, 1)])
    write_pdf('unknown-document.pdf', [page('VISITOR CHECKLIST', 'Fixture UNKNOWN-001 / unsupported document type', [
        t('This is a synthetic attendance checklist, not a financial document.'),
        t('Date: 2026-10-31'), t('Person: TEST VISITOR'), t('Task: Read the notice.'),
        t('No transaction should be inferred from this file.'),
    ], 1, 1)])
    write_pdf('blank-financial-content.pdf', [page('NO FINANCIAL CONTENT', 'Fixture EMPTY-001 / intentionally no financial fields', [], 1, 1)])
    (ROOT / 'transactions.csv').write_text('date,description,amount,category\n2026-10-28,Salary,65000,薪資\n2026-10-20,Rent,-18000,生活\n2026-10-10,Card repayment,-3800,信用卡繳款\n2026-10-08,Internal transfer,-10000,轉帳\n2026-10-22,Dividend,1200,股息\n2026-10-05,Meals,-1200,餐飲\n2026-10-06,Transport,-600,交通\n2026-10-07,Insurance,-2000,保險\n', encoding='utf-8')
    manifest = {}
    for f in sorted(PDF.glob('*.pdf')):
        manifest[f.name] = {'sha256': hashlib.sha256(f.read_bytes()).hexdigest(), 'bytes': f.stat().st_size}
    (ROOT / 'generated-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'pdf_directory': str(PDF), 'documents': manifest}, indent=2))


def render() -> None:
    tool = shutil.which('pdftoppm')
    if not tool:
        raise SystemExit('pdftoppm unavailable; PDF rendering is required before visual approval')
    PREVIEW.mkdir(exist_ok=True)
    for f in sorted(PDF.glob('*.pdf')):
        subprocess.run([tool, '-r', '90', '-png', str(f), str(PREVIEW / f.stem)], check=True)
    print(f'Rendered PNGs: {PREVIEW}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--render', action='store_true')
    args = parser.parse_args()
    generate()
    if args.render:
        render()
