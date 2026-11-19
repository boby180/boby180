"""פענוח מיילים של AliExpress לאירועים: איזו הזמנה, איזו חבילה, מה בפנים ומה הסטטוס."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from .htmltree import Node, parse
from .status import classify

ORDER_RE = re.compile(r"\bOrder (\d{12,20})\b", re.I)
PACKAGE_RE = re.compile(r"\bPackage ([A-Z0-9]{8,30})\b", re.I)
PRICE_RE = re.compile(r"(?:US\s?\$|\$|₪|ILS|€)\s?[\d.,]+")
SELLER_MSG_RE = re.compile(r"messages? from sellers?", re.I)


@dataclass
class Item:
    name: str
    sku: str = ""
    qty: str = ""
    image: str = ""


@dataclass
class PackageInfo:
    tracking: str
    order_ids: list[str] = field(default_factory=list)
    status_text: str = ""
    items: list[Item] = field(default_factory=list)


@dataclass
class OrderInfo:
    order_id: str
    items: list[Item] = field(default_factory=list)
    total: str = ""


@dataclass
class MailEvent:
    """מה שלמדנו ממייל אחד."""
    date: datetime
    subject: str
    message_id: str = ""
    status: str = ""                 # קוד סטטוס (ראו status.py), ריק אם לא זוהה
    status_text: str = ""            # הטקסט המקורי מהמייל
    orders: list[OrderInfo] = field(default_factory=list)
    packages: list[PackageInfo] = field(default_factory=list)
    seller_message: bool = False


def _qs(url: str) -> dict[str, str]:
    try:
        q = parse_qs(urlparse(url.replace("&amp;", "&")).query)
    except ValueError:
        return {}
    return {k: v[0] for k, v in q.items() if v}


def _status_from_subject(subject: str) -> str:
    """'Package X: at customs' -> 'at customs'; 'Package X has been delivered' -> 'has been delivered'."""
    if ":" in subject:
        return subject.split(":", 1)[1].strip()
    m = PACKAGE_RE.search(subject) or ORDER_RE.search(subject)
    return subject[m.end():].strip() if m else subject.strip()


def _clean(s: str) -> str:
    return " ".join(s.replace("‌", "").split())


def _items_order_logistics(root: Node) -> list[Item]:
    names = root.find_all("EDM-ORDER-LOGISTICS-product-name")
    images = root.find_all("EDM-ORDER-LOGISTICS-product-image")
    items = []
    for i, nm in enumerate(names):
        row = nm.ancestor("table")
        sku = qty = ""
        if row is not None:
            for n in row.find_all("EDM-ORDER-LOGISTICS-sku"):
                t = _clean(n.text())
                if "EDM-ORDER-LOGISTICS-number" in n.classes or re.fullmatch(r"x\s?\d+", t):
                    qty = qty or t
                else:
                    sku = sku or t
        img = images[i].first_image() if i < len(images) else ""
        items.append(Item(_clean(nm.text()), sku, qty, img))
    return items


def _items_package_detail(root: Node) -> list[Item]:
    items = []
    for nm in root.find_all("EDM-SYSTEM-TEXT-productName"):
        box = nm.ancestor("tr")
        sku = qty = img = ""
        if box is not None:
            s, c, im = (box.find("EDM-SYSTEM-TEXT-sku"), box.find("EDM-SYSTEM-TEXT-sku-count"),
                        box.find("EDM-SYSTEM-TEXT-productImage"))
            sku = _clean(s.text()) if s else ""
            qty = _clean(c.text()) if c else ""
            img = im.first_image() if im else ""
        items.append(Item(_clean(nm.text()), sku, qty, img))
    return items


def _multi_packages(root: Node) -> list[PackageInfo]:
    out = []
    for card in root.find_all("EDM-MULTIPLE-PACKAGES-product-card"):
        q = next((_qs(u) for u in card.links() if "trackingNumber=" in u), {})
        if not q.get("trackingNumber"):
            continue
        header, title, sku, image = (card.find("EDM-MULTIPLE-PACKAGES-item-header"),
                                     card.find("EDM-MULTIPLE-PACKAGES-item-title"),
                                     card.find("EDM-MULTIPLE-PACKAGES-item-sku"),
                                     card.find("EDM-MULTIPLE-PACKAGES-item-image"))
        items = [Item(_clean(title.text()), _clean(sku.text()) if sku else "", "",
                      image.first_image() if image else "")] if title else []
        out.append(PackageInfo(q["trackingNumber"],
                               [q["tradeOrderId"]] if q.get("tradeOrderId") else [],
                               _clean(header.text()) if header else "", items))
    return out


def parse_mail(subject: str, html: str, date: datetime, message_id: str = "") -> MailEvent:
    ev = MailEvent(date=date, subject=subject, message_id=message_id)
    if SELLER_MSG_RE.search(subject):
        ev.seller_message = True
        return ev

    root = parse(html or "")
    links = root.links()
    pairs: dict[str, set[str]] = {}          # tracking -> order ids
    order_ids: list[str] = []
    for u in links:
        q = _qs(u)
        if q.get("trackingNumber"):
            pairs.setdefault(q["trackingNumber"], set())
            if q.get("tradeOrderId"):
                pairs[q["trackingNumber"]].add(q["tradeOrderId"])
        for key in ("tradeOrderId", "orderId", "o_ids"):
            for oid in q.get(key, "").split(","):
                if oid.isdigit() and oid not in order_ids:
                    order_ids.append(oid)

    # ---- מייל מרוכז על כמה חבילות: לכל חבילה סטטוס משלה
    multi = _multi_packages(root)
    if multi:
        ev.packages = multi
        return ev

    m_pkg, m_ord = PACKAGE_RE.search(subject), ORDER_RE.search(subject)
    ev.status_text = _status_from_subject(subject)
    ev.status = classify(ev.status_text)

    pkg_items = _items_package_detail(root)
    ord_items = _items_order_logistics(root)

    if m_pkg:
        tracking = m_pkg.group(1).upper()
        oids = sorted(pairs.get(tracking, set())) or order_ids
        ev.packages.append(PackageInfo(tracking, oids, ev.status_text, pkg_items or ord_items))
        for oid in oids:
            ev.orders.append(OrderInfo(oid))
    elif m_ord:
        oid = m_ord.group(1)
        total = ""
        price = root.find("EDM-ORDER-LOGISTICS-price")
        if price is not None:
            pm = PRICE_RE.search(price.text())
            total = pm.group(0) if pm else ""
        ev.orders.append(OrderInfo(oid, ord_items or pkg_items, total))
        for tracking, oids in pairs.items():         # מייל הזמנה שכבר כולל מספר מעקב
            if not oids or oid in oids:
                ev.packages.append(PackageInfo(tracking, [oid], ev.status_text, []))
    return ev
