"""עץ HTML מינימלי (בלי ספריות חיצוניות), מספיק לחיפוש לפי class במיילים."""
from __future__ import annotations

from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}


class Node:
    def __init__(self, tag: str, attrs: dict[str, str], parent: "Node | None"):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list[Node | str] = []

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def iter(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.iter()

    def find_all(self, cls: str) -> list["Node"]:
        return [n for n in self.iter() if cls in n.classes]

    def find(self, cls: str) -> "Node | None":
        return next((n for n in self.iter() if cls in n.classes), None)

    def text(self) -> str:
        parts: list[str] = []

        def walk(n: Node) -> None:
            for c in n.children:
                if isinstance(c, str):
                    parts.append(c)
                elif c.tag not in ("style", "script"):
                    walk(c)
        walk(self)
        return " ".join("".join(parts).split())

    def first_image(self) -> str:
        for n in self.iter():
            src = n.attrs.get("background") or (n.attrs.get("src") if n.tag == "img" else "")
            if src and "aliexpress-media" in src and "/kf/" in src and not _is_icon(src):
                return src
        return ""

    def links(self) -> list[str]:
        return [n.attrs["href"] for n in self.iter() if n.tag == "a" and n.attrs.get("href")]

    def ancestor(self, tag: str) -> "Node | None":
        p = self.parent
        while p is not None and p.tag != tag:
            p = p.parent
        return p


def _is_icon(src: str) -> bool:
    """סמלים קטנים (חץ, אייקון סטטוס) ולא תמונת מוצר."""
    tail = src.rsplit("/", 1)[-1]
    return tail.endswith(".png") and "x" in tail.split(".")[0] and tail[0].isdigit()


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: v or "" for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, {k: v or "" for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:      # תגית סגירה בלי פתיחה - מתעלמים
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse(html: str) -> Node:
    b = _Builder()
    b.feed(html)
    b.close()
    return b.root
