"""מיילים לדוגמה (פרטים בדויים) במבנה של המיילים של AliExpress. משמשים לבדיקות ולבדיקה העצמית."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

IMG = "https://ae-pic-a1.aliexpress-media.com/kf/S{0}.jpg"
ICON = "https://ae-pic-a1.aliexpress-media.com/kf/S15f57e0791ff415783f618179a1f9fecj/54x54.png"


def order_mail(order_id: str, items: list[tuple[str, str, str]], total: str) -> str:
    rows = "".join(f"""
      <tr><td width="100" class="EDM-ORDER-LOGISTICS-product-image">
        <a href="https://www.aliexpress.com/p/order/detail.html?orderId={order_id}&amp;x=1"><img src="{IMG.format(n)}"></a></td>
      <td><a href="https://www.aliexpress.com/p/order/detail.html?orderId={order_id}">
        <table><tbody>
          <tr><td colspan="2" class="EDM-ORDER-LOGISTICS-product-name"><div><span>{name}</span></div></td></tr>
          <tr><td class="EDM-ORDER-LOGISTICS-sku">{sku}</td>
              <td class="EDM-ORDER-LOGISTICS-sku EDM-ORDER-LOGISTICS-number">{qty}</td></tr>
        </tbody></table></a></td></tr>""" for n, (name, sku, qty) in enumerate(items))
    return f"""<html><body><table><tbody>{rows}</tbody></table>
      <table><tr><td class="EDM-ORDER-LOGISTICS-price">Order total <span>{total}</span></td></tr></table>
      </body></html>"""


def package_mail(tracking: str, order_id: str, name: str, sku: str, qty: str = "x1") -> str:
    link = (f"https://www.aliexpress.com/p/tracking/index.html?_addShare=no&amp;"
            f"tradeOrderId={order_id}&amp;trackingNumber={tracking}&amp;edm=1")
    return f"""<html><body>
      <a href="{link}"><div class="EDM-SYSTEM-TEXT-title">Update</div></a>
      <table><tbody><tr>
        <td class="EDM-SYSTEM-TEXT-productImage"><img src="{IMG.format(tracking)}"></td>
        <td class="EDM-SYSTEM-TEXT-order-info">
          <div class="EDM-SYSTEM-TEXT-productName"> {name} </div>
          <div class="EDM-SYSTEM-TEXT-sku">{sku}</div>
          <div class="EDM-SYSTEM-TEXT-sku-count">{qty}</div></td>
        <td class="EDM-SYSTEM-TEXT-order-action"><img src="{ICON}"></td>
      </tr></tbody></table></body></html>"""


def multi_mail(entries: list[tuple[str, str, str, str, str]]) -> str:
    cards = "".join(f"""
      <tr><td class="EDM-MULTIPLE-PACKAGES-product-card">
        <a href="https://www.aliexpress.com/p/tracking/index.html?tradeOrderId={oid}&amp;trackingNumber={t}">
        <table><tbody>
          <tr><td class="EDM-MULTIPLE-PACKAGES-item-header"><img src="{ICON}"><span>{status}</span></td></tr>
          <tr><td class="EDM-MULTIPLE-PACKAGES-item-body"><table><tbody><tr>
            <td background="{IMG.format(t)}" class="EDM-MULTIPLE-PACKAGES-item-image"></td>
            <td class="EDM-MULTIPLE-PACKAGES-item-content">
              <div class="EDM-MULTIPLE-PACKAGES-item-title">{name}</div>
              <div class="EDM-MULTIPLE-PACKAGES-item-sku">{sku}</div></td>
          </tr></tbody></table></td></tr>
        </tbody></table></a></td></tr>""" for t, oid, status, name, sku in entries)
    return f"<html><body><table><tbody>{cards}</tbody></table></body></html>"


def demo_mails(now: datetime | None = None) -> list[tuple[str, str, datetime, str]]:
    """סיפור קטן: חמש הזמנות בשלבים שונים."""
    now = now or datetime.now(timezone.utc)
    d = lambda days: now - timedelta(days=days)  # noqa: E731
    return [
        ("Order 1000000000000001: order confirmed",
         order_mail("1000000000000001", [("Wireless Earbuds ANC Bluetooth...", "Black", "x1")], "US $30.40"),
         d(30), "<m1@demo>"),
        ("Order 1000000000000002: order confirmed",
         order_mail("1000000000000002", [("Silicone Baking Mat Set...", "Grey, 2pcs", "x2"),
                                         ("Kitchen Scale Digital...", "White", "x1")], "US $12.90"),
         d(25), "<m2@demo>"),
        ("Order 1000000000000003: order confirmed",
         order_mail("1000000000000003", [("Sport Insoles Latex...", "EU41-42", "x1")], "US $5.10"),
         d(28), "<m3@demo>"),
        ("Order 1000000000000004: order confirmed",
         order_mail("1000000000000004", [("Microfiber Cleaning Cloth...", "50pcs", "x1")], "US $7.75"),
         d(10), "<m4@demo>"),
        ("Order 1000000000000001: order shipped",
         order_mail("1000000000000001", [("Wireless Earbuds ANC Bluetooth...", "Black", "x1")], "US $30.40"),
         d(26), "<m5@demo>"),
        ("Your 3 packages have delivery updates",
         multi_mail([("RS0000000001Y", "1000000000000001", "In global transit",
                      "Wireless Earbuds ANC...", "Black"),
                     ("LP00000000000002", "1000000000000002", "Left sorting center",
                      "Silicone Baking Mat...", "Grey, 2pcs"),
                     ("AP00000000000003", "1000000000000003", "In transit",
                      "Sport Insoles Latex...", "EU41-42")]),
         d(22), "<m6@demo>"),
        ("Package RS0000000001Y: at customs",
         package_mail("RS0000000001Y", "1000000000000001", "Wireless Earbuds ANC Bluetooth...", "Black"),
         d(2), "<m7@demo>"),
        ("Package AP00000000000003 is ready for pickup",
         package_mail("AP00000000000003", "1000000000000003", "Sport Insoles Latex...", "EU41-42"),
         d(1), "<m8@demo>"),
        ("You have messages from sellers", "<p>Scroll below</p>", d(1), "<m9@demo>"),
    ]
