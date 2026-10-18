"""SC-AT / SC-NT-4M ASCII protocol (Control and Robotics Solutions).

Source: "SC-AT Communication Protocols Ver 3.10" and "SC-NT-4M Ethernet / LAN Connection
Guidelines and Protocols".

A clause is an axis letter + a two-letter keyword, optionally "=value" or ",argument":
    XPS        report X position (encoder counts)
    XAP=1000   set X absolute target
    XBG        begin motion on X

Ethernet (TCP, ports 4000-4003): clauses are chained with ';' (max 15), followed by CR and
one numerator byte. There is no echo. The controller answers each clause with "value>" or
"?>", separated by ';', and ends the message with the same numerator byte:
    send  "XSP=90000;YSP;XAC=200;BRP\\r" + "A"
    recv  ">;50000>;?>;2000,3000>" + "A"

RS-232 (38400 / 115200, 8N1): one clause at a time, terminated with CR. Every character is
echoed, then the value (for reports) and the prompt ">" or "?>".
"""

from __future__ import annotations

import re
import socket
import string
import time
from dataclasses import dataclass, field

AXIS_LETTERS = "XYZW"   # SC-AT-4M physical axes; J1..J4 in the GUI
LAN_PORTS = (4000, 4001, 4002, 4003)
SERIAL_BAUDS = (38400, 115200)
MAX_CHAIN = 15
_NUMERATORS = string.ascii_uppercase
_NUM = re.compile(r"-?\d+")


class ProtocolError(Exception):
    pass


@dataclass
class Reply:
    clause: str
    ok: bool
    values: list[int] = field(default_factory=list)
    text: str = ""

    @property
    def value(self) -> int | None:
        return self.values[0] if self.values else None


def parse_reply(clause: str, part: str) -> Reply:
    """One clause reply: ">", "?>", "50000>" or "2000,3000>" (blanks/CR tolerated)."""
    s = part.strip()
    if s.endswith(">"):
        s = s[:-1].strip()
    if s.endswith("?") or s.startswith("?"):
        return Reply(clause, False, [], s)
    return Reply(clause, True, [int(v) for v in _NUM.findall(s)], s)


def parse_lan_response(clauses: list[str], body: str) -> list[Reply]:
    """Body is the message without the trailing numerator byte."""
    parts = [p for p in body.strip().split(";")]
    if len(parts) != len(clauses):
        raise ProtocolError(f"expected {len(clauses)} replies, got {len(parts)}: {body!r}")
    return [parse_reply(c, p) for c, p in zip(clauses, parts)]


def check_clauses(clauses: list[str]) -> list[str]:
    out = []
    for c in clauses:
        c = c.strip()
        if not c or any(ch in c for ch in ";\r\n"):
            raise ValueError(f"bad clause {c!r}")
        out.append(c)
    if len(out) > MAX_CHAIN:
        raise ValueError(f"at most {MAX_CHAIN} clauses per message")
    return out


class LanTransport:
    """TCP connection to an SC-NT-4M controller."""

    def __init__(self, host: str, port: int = 4000, timeout: float = 1.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.sock: socket.socket | None = None
        self._n = 0

    def describe(self) -> str:
        return f"LAN {self.host}:{self.port}"

    def open(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=3.0)
        self.sock.settimeout(self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def transact(self, clauses: list[str]) -> list[Reply]:
        clauses = check_clauses(clauses)
        if not self.sock:
            raise ProtocolError("not connected")
        num = _NUMERATORS[self._n % len(_NUMERATORS)].encode()
        self._n += 1
        self.sock.sendall(";".join(clauses).encode("ascii") + b"\r" + num)
        end = b">" + num
        buf = b""
        deadline = time.monotonic() + self.timeout
        while not buf.rstrip(b"\r\n ").endswith(end):
            if time.monotonic() > deadline:
                raise ProtocolError(f"timeout waiting for reply to {';'.join(clauses)!r} (got {buf!r})")
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ProtocolError("controller closed the connection")
            buf += chunk
        body = buf.rstrip(b"\r\n ")[:-1].decode("ascii", "replace")  # drop numerator, keep last '>'
        return parse_lan_response(clauses, body)


class SerialTransport:
    """RS-232 connection (echo on, one clause per message)."""

    def __init__(self, port: str, baud: int = 115200, timeout: float = 1.0):
        self.port, self.baud, self.timeout = port, baud, timeout
        self.ser = None

    def describe(self) -> str:
        return f"{self.port} @ {self.baud}"

    def open(self) -> None:
        import serial  # pyserial
        self.ser = serial.Serial(self.port, self.baud, bytesize=8, parity="N", stopbits=1, timeout=0.05)
        self.ser.reset_input_buffer()

    def close(self) -> None:
        if self.ser:
            try:
                self.ser.close()
            finally:
                self.ser = None

    def transact(self, clauses: list[str]) -> list[Reply]:
        clauses = check_clauses(clauses)
        if not self.ser:
            raise ProtocolError("not connected")
        replies = []
        for c in clauses:
            self.ser.write(c.encode("ascii") + b"\r")
            buf = b""
            deadline = time.monotonic() + self.timeout
            while not buf.rstrip().endswith(b">"):
                if time.monotonic() > deadline:
                    raise ProtocolError(f"timeout waiting for reply to {c!r} (got {buf!r})")
                buf += self.ser.read(256)
            text = buf.decode("ascii", "replace").replace("\r", " ").replace("\n", " ").strip()
            if text.startswith(c):
                text = text[len(c):]  # drop the echo
            replies.append(parse_reply(c, text))
        return replies
