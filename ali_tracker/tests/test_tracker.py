"""בדיקות: פענוח מיילים, עדכון המסד והמלצות."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ali_tracker import money, samples, status
from ali_tracker.parser import parse_mail
from ali_tracker.secret import protect, unprotect
from ali_tracker.store import Store

NOW = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)


def _load(tmp_path) -> Store:
    st = Store(tmp_path / "t.db")
    for subject, html, date, mid in samples.demo_mails(NOW):
        st.apply(parse_mail(subject, html, date, mid))
    return st


def test_parse_order_mail():
    ev = parse_mail("Order 1000000000000002: order confirmed",
                    samples.order_mail("1000000000000002",
                                       [("Mat...", "Grey", "x2"), ("Scale...", "White", "x1")], "US $12.90"),
                    NOW)
    assert ev.status == status.CONFIRMED and not ev.packages
    (o,) = ev.orders
    assert o.total == "US $12.90"
    assert [(i.name, i.sku, i.qty) for i in o.items] == [("Mat...", "Grey", "x2"), ("Scale...", "White", "x1")]
    assert all(i.image.endswith(".jpg") for i in o.items)


def test_parse_package_mail():
    ev = parse_mail("Package RS0000000001Y: cleared export customs",
                    samples.package_mail("RS0000000001Y", "1000000000000001", "Earbuds...", "Black"), NOW)
    assert ev.status == status.CUSTOMS
    (p,) = ev.packages
    assert p.tracking == "RS0000000001Y" and p.order_ids == ["1000000000000001"]
    assert p.items[0].name == "Earbuds..." and p.items[0].qty == "x1"
    assert "54x54" not in p.items[0].image                    # לא אייקון


def test_parse_multi_and_seller():
    ev = parse_mail("Your 2 packages have delivery updates",
                    samples.multi_mail([("T1AAAAAAAA", "1", "Ready to ship", "A...", "a"),
                                        ("T2BBBBBBBB", "2", "Left sorting center", "B...", "b")]), NOW)
    assert [(p.tracking, p.order_ids, status.classify(p.status_text)) for p in ev.packages] == \
        [("T1AAAAAAAA", ["1"], status.CONFIRMED), ("T2BBBBBBBB", ["2"], status.SHIPPED)]
    assert parse_mail("You have messages from sellers", "", NOW).seller_message


def test_classify():
    cases = {"left the departure region": status.TRANSIT, "export clearance started": status.CUSTOMS,
             "has been delivered": status.DELIVERED, "is ready for pickup": status.PICKUP,
             "with local carrier": status.LOCAL, "how did it go?": status.DELIVERED,
             "received by sorting center": status.SHIPPED, "order confirmed": status.CONFIRMED,
             "delivery failed": status.PROBLEM, "something new": ""}
    for text, code in cases.items():
        assert status.classify(text) == code, text


def test_store_rows(tmp_path):
    st = _load(tmp_path)
    rows = {r.key: r for r in st.rows()}
    assert set(rows) == {"RS0000000001Y", "LP00000000000002", "AP00000000000003", "1000000000000004"}
    earbuds = rows["RS0000000001Y"]
    assert earbuds.status == status.CUSTOMS and earbuds.order_ids == ["1000000000000001"]
    assert earbuds.total == "US $30.40" and earbuds.first_seen == NOW - timedelta(days=22)
    assert rows["AP00000000000003"].status == status.PICKUP
    mat = rows["LP00000000000002"]
    assert mat.items[0].name == "Silicone Baking Mat..."
    order_only = rows["1000000000000004"]
    assert order_only.kind == "order" and order_only.tracking == "" and order_only.status == status.CONFIRMED
    assert st.get_meta("seller_msg_at")
    st.close()


def test_store_idempotent_and_no_regression(tmp_path):
    st = _load(tmp_path)
    before = [(r.key, r.status) for r in st.rows()]
    for subject, html, date, mid in samples.demo_mails(NOW):          # אותם מיילים שוב
        st.apply(parse_mail(subject, html, date, mid))
    assert [(r.key, r.status) for r in st.rows()] == before
    # מייל ישן עם שלב מוקדם לא מחזיר את הסטטוס אחורה
    st.apply(parse_mail("Package RS0000000001Y: left the departure region",
                        samples.package_mail("RS0000000001Y", "1000000000000001", "E", "B"),
                        NOW - timedelta(days=15), "<old@x>"))
    assert {r.key: r.status for r in st.rows()}["RS0000000001Y"] == status.CUSTOMS
    # "how did it go" על ההזמנה = החבילה שבדרך נמסרה, אבל חבילה שמחכה באיסוף נשארת באיסוף
    st.apply(parse_mail("Order 1000000000000001: how did it go?", "", NOW, "<rev@x>"))
    st.apply(parse_mail("Order 1000000000000003: how did it go?", "", NOW, "<rev3@x>"))
    after = {r.key: r.status for r in st.rows()}
    assert after["RS0000000001Y"] == status.DELIVERED and after["AP00000000000003"] == status.PICKUP
    st.close()


def test_done_and_advice(tmp_path):
    st = _load(tmp_path)
    rows = {r.key: r for r in st.rows()}
    assert status.advice(status.PICKUP, NOW, False, NOW).urgent
    assert not status.advice(status.TRANSIT, NOW - timedelta(days=3), False, NOW).urgent
    assert status.advice(status.TRANSIT, NOW - timedelta(days=25), False, NOW).urgent
    assert status.advice(status.CONFIRMED, NOW - timedelta(days=9), False, NOW).urgent
    assert not status.advice(status.DELIVERED, NOW, True, NOW).urgent
    assert status.advice(status.DELIVERED, NOW - timedelta(days=2), False, NOW).urgent
    assert not status.advice(status.DELIVERED, NOW - timedelta(days=20), False, NOW).urgent
    st.set_done(rows["AP00000000000003"], True)
    assert {r.key: r.done for r in st.rows()}["AP00000000000003"]
    st.close()


def test_ordered_at_and_late_shipment(tmp_path):
    st = _load(tmp_path)
    rows = {r.key: r for r in st.rows()}
    earbuds = rows["RS0000000001Y"]
    # החבילה הופיעה לפני 22 ימים, אבל ההזמנה עצמה לפני 30
    assert earbuds.ordered_at == NOW - timedelta(days=30)
    assert rows["1000000000000004"].ordered_at == NOW - timedelta(days=10)

    def adv(r, **kw):
        return status.advice(r.status, r.last_update, r.done, NOW, ordered_at=r.ordered_at, **kw)

    assert "הודעה למוכר" in adv(earbuds, late_days=30).text and adv(earbuds, late_days=30).urgent
    assert "הודעה למוכר" not in adv(earbuds, late_days=31).text
    assert "הודעה למוכר" not in adv(earbuds, late_days=0).text           # כבוי
    assert adv(rows["AP00000000000003"], late_days=5).text.startswith("ללכת לאסוף")   # כבר בישראל

    st.set_messaged(earbuds, NOW - timedelta(days=2))
    earbuds = {r.key: r for r in st.rows()}["RS0000000001Y"]
    a = adv(earbuds, late_days=30, messaged_at=earbuds.messaged_at)
    assert not a.urgent and "לחכות" in a.text
    a = adv(earbuds, late_days=30, messaged_at=NOW - timedelta(days=status.FOLLOWUP_DAYS))
    assert a.urgent and "מחלוקת" in a.text
    st.set_messaged(earbuds, None)
    assert {r.key: r for r in st.rows()}["RS0000000001Y"].messaged_at is None
    st.close()


def test_order_total_without_price_class():
    html = ("<table><tr><td>20mm Parachute Loop Strap...</td></tr></table>"
            "<table><tr><td><span>Order total</span></td><td><span>₪12.65</span></td></tr></table>")
    ev = parse_mail("Order 1124000000000001: order confirmed", html, NOW)
    assert ev.orders[0].total == "₪12.65"


def test_money():
    assert money.amount("₪12.65") == 12.65 and money.amount("US $1,234.50") == 1234.5
    assert money.amount("€7,50") == 7.5 and money.amount("") is None
    assert money.to_ils("₪12.65", 3.7, 4.0) == (12.65, False)
    assert money.to_ils("US $10", 3.7, 4.0) == (37.0, True)
    assert money.to_ils("€10", 3.7, 4.0) == (40.0, True)
    assert money.to_ils("12 RUB", 3.7, 4.0) is None
    assert money.fmt(1234.5) == "₪1,234.50" and money.fmt(37, True) == "≈ ₪37.00"


def test_item_prices(tmp_path):
    st = _load(tmp_path)
    rows = {r.key: r for r in st.rows()}
    # שם המוצר במייל החבילה מקוצר יותר מבמייל ההזמנה, ועדיין מקבל את המחיר
    assert rows["RS0000000001Y"].items[0].price == "US $30.40"
    assert rows["AP00000000000003"].items[0].price == "₪18.90"
    assert rows["1000000000000004"].items[0].price == "₪28.65"
    # בהזמנה עם שני מוצרים אין מחיר לכל מוצר, רק להזמנה כולה
    assert all(not i.price for i in rows["LP00000000000002"].items)
    assert rows["LP00000000000002"].totals == {"1000000000000002": "₪47.80"}
    assert [o for o, *_ in st.orders_since(NOW - timedelta(days=29))] == \
        ["1000000000000003", "1000000000000002", "1000000000000004"]
    st.close()


def test_secret_roundtrip():
    assert unprotect(protect("abcd efgh")) == "abcd efgh"
    assert unprotect("") == "" and protect("") == ""
