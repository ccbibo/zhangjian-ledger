"""Real SQLite integration checks, not a browser/DOM mock."""
import copy
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.store import Store


def tx(name="Salary", amount="100.00", category="薪資", **kwargs):
    return {"date": "2026-10-28", "name": name, "amount": amount, "category": category, **kwargs}


class SQLiteStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.store = Store(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def preview(self, rows=None, **extra):
        doc = {"filename": "sample.csv", "kind": "CSV", "transactions": rows if rows is not None else [tx()], "assets": [], "debts": [], "metadata": {}, **extra}
        hash_ = hashlib.sha256(json.dumps(doc, ensure_ascii=False).encode()).hexdigest()
        return self.store.preview(doc, hash_)

    def commit(self, p, **kwargs):
        return self.store.commit(p["document_id"], p["transactions"], **kwargs)

    def test_real_database_and_restart_persistence(self):
        p = self.preview()
        self.assertEqual(self.store.snapshot()["transactions"], [])
        self.assertEqual(p["status"], "pending")
        self.commit(p)
        reloaded = Store(self.path)
        self.assertEqual(reloaded.stats("2026-10")["income"], "100.00")
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(con.execute("SELECT amount_cents FROM transactions").fetchone()[0], 10000)

    def test_same_file_reuses_original_even_after_commit(self):
        p = self.preview()
        self.commit(p)
        again = self.preview()
        self.assertEqual(again["document_id"], p["document_id"])
        self.assertTrue(again["reused"])
        self.assertEqual(again["status"], "committed")
        with self.assertRaises(ValueError):
            self.commit(again)
        self.assertEqual(len(self.store.snapshot()["transactions"]), 1)

    def test_decimal_statistics_and_transfer_exclusions(self):
        p = self.preview([tx("薪資", "0.10"), tx("股息", "0.20", "股息"), tx("餐飲", "-0.10", "餐飲"), tx("轉帳", "-100.00", "轉帳"), tx("投資", "-50.00", "投資"), tx("繳款", "-70.00", "信用卡繳款")])
        self.commit(p)
        self.assertEqual(self.store.stats("2026-10"), {"month":"2026-10", "income":"0.30", "expense":"0.10", "balance":"0.20", "transaction_count":6, "excluded_count":3})

    def test_cross_document_exact_duplicate_recheck(self):
        one = self.preview(kind="PAYSLIP")
        two = self.preview(kind="BANK")
        self.commit(one)
        with self.assertRaises(ValueError):
            self.commit(two)
        self.assertEqual(len(self.store.snapshot()["transactions"]), 1)
        self.assertEqual(self.store.commit(two["document_id"], two["transactions"], True)["duplicate_count"], 1)

    def test_within_document_duplicate_defaults_unselected(self):
        p = self.preview([tx(), tx()])
        self.assertEqual([r["keep"] for r in p["transactions"]], [True, False])
        self.assertEqual(self.commit(p)["committed_count"], 1)

    def test_duplicate_review_id_rejected_atomically(self):
        p = self.preview()
        with self.assertRaises(ValueError):
            self.store.commit(p["document_id"], [p["transactions"][0]] * 2)
        self.assertEqual(self.store.snapshot()["transactions"], [])

    def test_foreign_review_id_rejected_atomically(self):
        p = self.preview()
        rows = copy.deepcopy(p["transactions"])
        rows[0]["id"] = "unknown"
        with self.assertRaises(ValueError):
            self.store.commit(p["document_id"], rows)
        self.assertEqual(self.store.snapshot()["transactions"], [])

    def test_review_can_edit_values_but_not_provenance(self):
        p = self.preview([tx(page=2)])
        rows = copy.deepcopy(p["transactions"])
        rows[0].update(amount="90.10", source="spoof", page=999, line=10)
        saved = self.store.commit(p["document_id"], rows)["snapshot"]["transactions"][0]
        self.assertEqual(saved["amount"], "90.10")
        self.assertEqual(saved["source"], "sample.csv")
        self.assertEqual(saved["page"], 2)
        self.assertNotIn("line", saved)

    def test_invalid_decimal_and_metadata_rejected(self):
        for value in ["0.000001", "NaN", "Infinity", "1e3", "0x10", True, "1000000000000.01"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.preview([tx(amount=value)])
        for value in [0, -1, True, 1.5, '<img onerror="alert(1)">']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.preview([tx(page=value)])

    def test_review_validation_failure_keeps_pending_and_no_partial_writes(self):
        p = self.preview([tx("a"), tx("b")])
        rows = copy.deepcopy(p["transactions"])
        rows[1]["amount"] = "1.123"
        with self.assertRaises(ValueError):
            self.store.commit(p["document_id"], rows)
        self.assertEqual(self.store.snapshot()["transactions"], [])
        self.assertEqual(self.preview([tx("a"), tx("b")])["status"], "pending")

    def test_asset_and_debt_upsert_without_double_count(self):
        def doc(value, payment):
            return self.preview([], assets=[{"account":"bank-A", "name":"銀行A", "value":value}], debts=[{"account":"card-A", "name":"信用卡A", "balance":payment, "payment":payment, "due":"2026-11-10"}])
        self.commit(doc("1000.00", "100.00"))
        self.commit(doc("1200.00", "80.00"))
        data = self.store.snapshot()
        self.assertEqual(len(data["assets"]), 1)
        self.assertEqual(data["assets"][0]["value"], "1200.00")
        self.assertEqual(len(data["debts"]), 1)
        self.assertEqual(data["debts"][0]["balance"], "80.00")

    def test_invalid_month_and_date_rejected(self):
        for month in ["2026-13", "2026-00", "1899-12", "2201-01", ["2026-10"]]:
            with self.subTest(month=month), self.assertRaises(ValueError):
                self.store.stats(month)
        for day in ["2026-02-29", "2026-13-01", ["2026-10-28"]]:
            with self.subTest(day=day), self.assertRaises(ValueError):
                self.preview([tx(date=day)])

    def test_raw_bytes_and_non_twd_rejected(self):
        with self.assertRaises(ValueError):
            self.store.preview({"metadata":{"raw": b"PDF bytes"}}, "a" * 64)
        with self.assertRaises(ValueError):
            self.preview([], assets=[{"account":"usd", "value":"1.00", "currency":"USD"}])

    def test_bad_batch_rolls_back_asset_upsert(self):
        self.commit(self.preview([], assets=[{"account":"A", "value":"999999999999.00"}]))
        p = self.preview([], assets=[{"account":"B", "value":"2.00"}])
        with self.assertRaises(ValueError):
            self.commit(p)
        self.assertEqual([a["account"] for a in self.store.snapshot()["assets"]], ["A"])

    def test_create_edit_delete_recompute_real_database_statistics(self):
        created = self.store.create_transaction(tx())
        saved = created["snapshot"]["transactions"][0]
        self.assertEqual(saved["source"], "手動新增")
        edited = self.store.replace_transaction(saved["id"], {"amount":"-20.00", "category":"餐飲", "source":"spoof"})
        self.assertEqual(edited["transaction"]["source"], "手動新增")
        self.assertEqual(self.store.stats("2026-10")["expense"], "20.00")
        self.assertTrue(self.store.delete_transaction(saved["id"])["deleted"])
        self.assertEqual(self.store.stats("2026-10")["transaction_count"], 0)
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(con.execute("SELECT action FROM transaction_audit ORDER BY created_at").fetchall(), [("edit",), ("delete",)])

    def test_edit_validation_rollback_and_missing_delete(self):
        saved = self.store.create_transaction(tx())["snapshot"]["transactions"][0]
        for data in [{"id":"different"},{"amount":"0.001"},{"category":[]}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.store.replace_transaction(saved["id"], data)
        self.assertEqual(self.store.stats("2026-10")["income"], "100.00")
        with self.assertRaises(ValueError):
            self.store.delete_transaction("not-found")

    def test_edit_duplicate_is_rejected(self):
        saved = self.store.create_transaction(tx())["snapshot"]["transactions"][0]
        self.store.create_transaction(tx("Other"))
        with self.assertRaises(ValueError):
            self.store.replace_transaction(saved["id"], {"name":"Other"})
        self.assertEqual(self.store.stats("2026-10")["transaction_count"], 2)

    def test_old_asset_snapshot_does_not_rewind_but_old_transactions_commit(self):
        self.commit(self.preview([], assets=[{"account":"A", "value":"200.00", "as_of":"2026-10-31"}]))
        old = self.preview([tx("九月交易", "-10.00", "餐飲", date="2026-09-15")], assets=[{"account":"A", "value":"100.00", "as_of":"2026-09-30"}])
        self.assertIn("較舊", " ".join(old["warnings"]))
        result = self.commit(old)
        self.assertEqual(result["committed_count"], 1)
        self.assertIn("2026-10-31", " ".join(result["warnings"]))
        self.assertEqual(result["snapshot"]["assets"][0]["value"], "200.00")
        self.assertEqual(self.store.stats("2026-09")["expense"], "10.00")
        self.assertTrue(result["snapshot"]["imports"][0]["warnings"])

    def test_undated_asset_cannot_overwrite_dated_snapshot(self):
        self.commit(self.preview([], assets=[{"account":"A", "value":"200.00", "as_of":"2026-10-31"}]))
        result = self.commit(self.preview([], assets=[{"account":"A", "value":"300.00"}]))
        self.assertEqual(result["snapshot"]["assets"][0]["value"], "200.00")
        self.assertIn("缺少快照日期", " ".join(result["warnings"]))

    def test_new_asset_snapshot_updates_and_dated_can_replace_undated(self):
        self.commit(self.preview([], assets=[{"account":"A", "value":"100.00"}]))
        self.commit(self.preview([], assets=[{"account":"A", "value":"200.00", "as_of":"2026-10-31"}]))
        result = self.commit(self.preview([], assets=[{"account":"A", "value":"300.00", "as_of":"2026-11-30"}]))
        self.assertEqual(result["snapshot"]["assets"][0]["value"], "300.00")
        self.assertEqual(result["warnings"], [])

    def test_credit_debt_dates_protect_from_older_and_undated_statements(self):
        first = {"account":"C", "balance":"200.00", "due":"2026-11-10", "as_of":"2026-10-31"}
        self.commit(self.preview([], debts=[first]))
        old = {**first, "balance":"100.00", "as_of":"2026-09-30"}
        result = self.commit(self.preview([], debts=[old]))
        self.assertEqual(result["snapshot"]["debts"][0]["balance"], "200.00")
        self.assertIn("負債帳戶", " ".join(result["warnings"]))
        undated = {"account":"C", "balance":"500.00", "due":"2027-01-10"}
        result = self.commit(self.preview([], debts=[undated]))
        self.assertEqual(result["snapshot"]["debts"][0]["balance"], "200.00")
        self.assertIn("缺少快照日期", " ".join(result["warnings"]))

    def test_snapshot_chronology_is_rechecked_at_commit(self):
        old_pending = self.preview([], assets=[{"account":"A", "value":"100.00", "as_of":"2026-09-30"}])
        self.assertEqual(old_pending["warnings"], [])
        self.commit(self.preview([], assets=[{"account":"A", "value":"200.00", "as_of":"2026-10-31"}]))
        result = self.commit(old_pending)
        self.assertEqual(result["snapshot"]["assets"][0]["value"], "200.00")
        self.assertIn("較舊", " ".join(result["warnings"]))

    def test_snapshot_sources_and_minimum_payment_preserved(self):
        p = self.preview([], assets=[{"account":"A", "value":"100.00", "source":"bank.pdf", "as_of":"2026-10-31"}], debts=[{"account":"C", "balance":"100.00", "payment":"100.00", "minimum_payment":"20.00", "source":"card.pdf", "as_of":"2026-10-31"}])
        data = self.commit(p)["snapshot"]
        self.assertEqual(data["assets"][0]["source"], "bank.pdf")
        self.assertEqual(data["debts"][0]["source"], "card.pdf")
        self.assertEqual(data["debts"][0]["minimum_payment"], "20.00")
        self.assertEqual(data["debts"][0]["as_of"], "2026-10-31")
        absent = self.preview([], debts=[{"account":"other", "balance":"100.00"}])
        self.assertNotIn("minimum_payment", absent["debts"][0])

    def test_invalid_minimum_payment_and_currency_rejected(self):
        for value in ["100.01", "-1.00", "0.00001", True]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.preview([], debts=[{"account":"C", "payment":"100.00", "minimum_payment":value}])
        with self.assertRaises(ValueError):
            self.preview([], debts=[{"account":"C", "payment":"100.00", "currency":"USD"}])

    def test_same_month_refund_offsets_expense_not_income(self):
        self.commit(self.preview([tx("薪資", "100.00"), tx("股息", "20.00", "股息"), tx("餐飲消費", "-50.00", "餐飲"), tx("餐飲退款", "10.00", "餐飲")]))
        stats = self.store.stats("2026-10")
        self.assertEqual(stats["income"], "120.00")
        self.assertEqual(stats["expense"], "40.00")
        self.assertEqual(stats["balance"], "80.00")

    def test_cross_month_refund_remains_in_actual_posting_month(self):
        self.commit(self.preview([tx("九月消費", "-50.00", "生活", date="2026-09-20"), tx("十月退款", "20.00", "生活", date="2026-10-01")]))
        self.assertEqual(self.store.stats("2026-09")["expense"], "50.00")
        october = self.store.stats("2026-10")
        self.assertEqual(october["income"], "0.00")
        self.assertEqual(october["expense"], "-20.00")
        self.assertEqual(october["balance"], "20.00")

    def test_negative_salary_and_dividend_corrections_reduce_income(self):
        self.commit(self.preview([tx("薪資更正", "-15.00", "薪資"), tx("股息更正", "-3.00", "股息"), tx("轉帳回存", "500.00", "轉帳"), tx("繳卡費沖銷", "100.00", "信用卡繳款")]))
        stats = self.store.stats("2026-10")
        self.assertEqual(stats["income"], "-18.00")
        self.assertEqual(stats["expense"], "0.00")
        self.assertEqual(stats["balance"], "-18.00")
        self.assertEqual(stats["excluded_count"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
