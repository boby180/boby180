"""שמירת הסיסמה לאפליקציה בצורה מוצפנת למשתמש הנוכחי ב-Windows (DPAPI)."""
from __future__ import annotations

import base64
import sys

PREFIX_DPAPI, PREFIX_PLAIN = "dpapi:", "plain:"


def protect(text: str) -> str:
    if not text:
        return ""
    if sys.platform == "win32":
        return PREFIX_DPAPI + base64.b64encode(_dpapi(text.encode("utf-8"), True)).decode()
    return PREFIX_PLAIN + base64.b64encode(text.encode("utf-8")).decode()


def unprotect(blob: str) -> str:
    try:
        if blob.startswith(PREFIX_DPAPI):
            return _dpapi(base64.b64decode(blob[len(PREFIX_DPAPI):]), False).decode("utf-8")
        if blob.startswith(PREFIX_PLAIN):
            return base64.b64decode(blob[len(PREFIX_PLAIN):]).decode("utf-8")
    except Exception:
        return ""
    return ""


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src, dst = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), BLOB()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst)):
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        kernel32.LocalFree(dst.pbData)
