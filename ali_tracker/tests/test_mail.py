"""בדיקת משיכת המיילים מול שרת IMAP מדומה שמתנהג כמו Gmail (בלי רשת)."""
from __future__ import annotations

import imaplib
import socket
import sys
import threading
from email.message import EmailMessage
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ali_tracker import mail, samples


def _message(subject: str, html: str, n: int) -> bytes:
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = "AliExpress <transaction@notice.aliexpress.com>"
    m["Date"] = f"Tue, 17 Nov 2026 1{n}:00:00 +0000"
    m["Message-ID"] = f"<id{n}@demo>"
    m.set_content("plain")
    m.add_alternative(html, subtype="html")
    return bytes(m)


class FakeGmail(threading.Thread):
    """שרת IMAP מינימלי: LOGIN, LIST, SELECT, UID SEARCH X-GM-RAW, UID FETCH, LOGOUT."""

    def __init__(self, messages: dict[int, bytes], password: str = "abcdefghijklmnop"):
        super().__init__(daemon=True)
        self.messages, self.password = messages, password
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.commands: list[str] = []

    def run(self) -> None:
        conn, _ = self.sock.accept()
        f = conn.makefile("rwb")
        send = lambda s: (f.write(s if isinstance(s, bytes) else s.encode()), f.flush())  # noqa: E731
        send("* OK Gimap ready\r\n")
        while True:
            line = f.readline().decode().strip()
            if not line:
                return
            tag, cmd, *rest = line.split(" ", 2)
            arg = rest[0] if rest else ""
            self.commands.append(f"{cmd} {arg}")
            c = cmd.upper()
            if c == "CAPABILITY":
                send(f"* CAPABILITY IMAP4rev1 X-GM-EXT-1\r\n{tag} OK done\r\n")
            elif c == "LOGIN":
                ok = arg.split(" ", 1)[1].strip('"') == self.password
                send(f"{tag} OK done\r\n" if ok else f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials\r\n")
            elif c == "LIST":
                send('* LIST (\\HasNoChildren) "/" "INBOX"\r\n'
                     '* LIST (\\All \\HasNoChildren) "/" "[Gmail]/&BdsF3A- &BdQF0wXVBdAF6A-"\r\n'
                     f"{tag} OK done\r\n")
            elif c == "EXAMINE" or c == "SELECT":
                send(f"* {len(self.messages)} EXISTS\r\n* OK [UIDVALIDITY 7] ok\r\n{tag} OK [READ-ONLY]\r\n")
            elif c == "UID" and arg.upper().startswith("SEARCH"):
                send("* SEARCH " + " ".join(map(str, sorted(self.messages))) + f"\r\n{tag} OK done\r\n")
            elif c == "UID" and arg.upper().startswith("FETCH"):
                for uid in map(int, arg.split()[1].split(",")):
                    body = self.messages[uid]
                    send(f"* {uid} FETCH (UID {uid} BODY[] {{{len(body)}}}\r\n".encode() + body + b")\r\n")
                send(f"{tag} OK done\r\n")
            elif c == "LOGOUT":
                send(f"* BYE\r\n{tag} OK done\r\n")
                return
            else:
                send(f"{tag} BAD unknown\r\n")


@pytest.fixture
def fake(monkeypatch):
    mails = samples.demo_mails()
    server = FakeGmail({10 + n: _message(s, h, n) for n, (s, h, _d, _m) in enumerate(mails)})
    server.start()
    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL",
                        lambda host, port, timeout=None: imaplib.IMAP4("127.0.0.1", server.port))
    return server


def test_fetch_all(fake):
    got, max_uid, validity = mail.fetch("me@gmail.com", "abcd efgh ijkl mnop", days=90)
    assert len(got) == len(samples.demo_mails()) and max_uid == 18 and validity == "7"
    subject, html, date, mid = got[0]
    assert subject.startswith("Order 1000000000000001") and "EDM-ORDER-LOGISTICS" in html
    assert mid == "<id0@demo>" and date.tzinfo is not None
    joined = " | ".join(fake.commands)
    assert 'X-GM-RAW "from:aliexpress.com newer_than:90d"' in joined
    assert '[Gmail]/&BdsF3A- &BdQF0wXVBdAF6A-' in joined          # "כל הדואר" בחשבון בעברית
    assert "BODY.PEEK[]" in joined                                 # לא מסמן כנקרא


def test_fetch_only_new(fake):
    got, max_uid, _ = mail.fetch("me@gmail.com", "abcdefghijklmnop", after_uid=16, uidvalidity="7")
    assert len(got) == 2 and max_uid == 18


def test_wrong_password(fake):
    with pytest.raises(mail.MailError, match="סיסמה לאפליקציה"):
        mail.fetch("me@gmail.com", "wrong")
