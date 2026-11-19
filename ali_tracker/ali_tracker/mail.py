"""משיכת המיילים מ-Gmail דרך IMAP עם "סיסמה לאפליקציה" (App Password)."""
from __future__ import annotations

import email
import imaplib
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Callable, Iterator

HOST = "imap.gmail.com"
QUERY = "from:aliexpress.com newer_than:{days}d"


class MailError(Exception):
    """שגיאה עם הסבר בעברית שאפשר להציג למשתמש."""


def _decode(value: str | None) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def _html_of(msg: Message) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


def _all_mail_folder(conn: imaplib.IMAP4_SSL) -> str:
    """שם התיקייה "כל הדואר" (תלוי בשפת החשבון), כדי לא לפספס מיילים שהועברו לארכיון."""
    typ, lines = conn.list()
    for raw in lines or []:
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        if "\\All" in line:
            m = re.search(r'"([^"]+)"\s*$', line) or re.search(r"(\S+)\s*$", line)
            if m:
                return m.group(1)
    return "INBOX"


def fetch(address: str, app_password: str, days: int = 120, after_uid: int = 0,
          uidvalidity: str = "", progress: Callable[[int, int], None] | None = None
          ) -> tuple[list[tuple[str, str, datetime, str]], int, str]:
    """מחזיר (רשימת (נושא, html, תאריך, message-id), ה-UID הגבוה ביותר, uidvalidity).

    אם uidvalidity לא השתנה, מושכים רק מיילים חדשים מ-after_uid.
    """
    password = app_password.replace(" ", "")
    try:
        conn = imaplib.IMAP4_SSL(HOST, 993, timeout=60)
    except OSError as e:
        raise MailError(f"אין חיבור לשרת של Gmail. בדוק את החיבור לאינטרנט.\n({e})") from e
    try:
        try:
            conn.login(address.strip(), password)
        except imaplib.IMAP4.error as e:
            raise MailError(
                "Gmail סירב להתחבר. צריך להשתמש ב«סיסמה לאפליקציה» (16 תווים) ולא בסיסמה הרגילה, "
                f"ושה-IMAP יהיה מופעל בהגדרות Gmail.\n({e})") from e
        folder = _all_mail_folder(conn)
        typ, data = conn.select(f'"{folder}"', readonly=True)
        if typ != "OK":
            conn.select("INBOX", readonly=True)
        typ, v = conn.response("UIDVALIDITY")
        validity = (v[0].decode() if v and v[0] else "")
        if validity != uidvalidity:
            after_uid = 0
        typ, data = conn.uid("SEARCH", "X-GM-RAW", f'"{QUERY.format(days=days)}"')
        if typ != "OK":
            raise MailError("החיפוש ב-Gmail נכשל.")
        uids = [int(u) for u in (data[0] or b"").split() if int(u) > after_uid]
        out = []
        for n, chunk in enumerate(_chunks(uids, 25)):
            if progress:
                progress(min(len(uids), n * 25), len(uids))
            typ, parts = conn.uid("FETCH", ",".join(map(str, chunk)), "(BODY.PEEK[])")
            for item in parts or []:
                if not isinstance(item, tuple):
                    continue
                msg = email.message_from_bytes(item[1])
                try:
                    date = parsedate_to_datetime(msg.get("Date"))
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)
                except Exception:
                    date = datetime.now(timezone.utc)
                out.append((_decode(msg.get("Subject")), _html_of(msg), date,
                            (msg.get("Message-ID") or "").strip()))
        if progress:
            progress(len(uids), len(uids))
        return out, max(uids, default=after_uid), validity
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _chunks(seq: list[int], n: int) -> Iterator[list[int]]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
