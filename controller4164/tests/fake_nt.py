"""A small fake SC-NT-4M on localhost, speaking the Ethernet ASCII protocol."""

from __future__ import annotations

import re
import socket
import threading

CLAUSE = re.compile(r"^([A-Z])([A-Z]{2})(?:=(-?\d+))?$")


class FakeNT:
    def __init__(self):
        self.p = {L: {"PS": 0, "MO": 0, "MS": 0, "MM": 0, "SM": 0, "SP": 0, "AP": 0, "SR": 0,
                      "EM": 0, "LL": -200000, "HL": 200000} for L in "XYZW"}
        self.ip, self.op = 0b101, 0b10
        self.received: list[str] = []
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _clause(self, c: str) -> str:
        self.received.append(c)
        m = CLAUSE.match(c)
        if not m:
            return "?>"
        L, kw, val = m.groups()
        if kw in ("IP", "OP"):
            return f"{self.ip if kw == 'IP' else self.op}>"
        ax = self.p.get(L)
        if ax is None:
            return "?>"
        if kw == "BG":
            if not ax["MO"]:
                return "?>"
            if ax["MM"] == 0:
                ax["PS"] = ax["AP"]  # instant point-to-point move
            else:
                ax["MS"] = 1
            return ">"
        if kw in ("ST", "AB"):
            ax["MS"] = 0
            return ">"
        if kw not in ax:
            return "?>"
        if val is None:
            return f"{ax[kw]}>"
        ax[kw] = int(val)
        return ">"

    def _serve(self):
        conn, _ = self.srv.accept()
        buf = b""
        while True:
            data = conn.recv(4096)
            if not data:
                return
            buf += data
            while b"\r" in buf and len(buf) > buf.index(b"\r") + 1:
                i = buf.index(b"\r")
                line, num, buf = buf[:i].decode(), buf[i + 1:i + 2], buf[i + 2:]
                reply = ";".join(self._clause(c) for c in line.split(";"))
                conn.sendall(reply.encode() + num)
