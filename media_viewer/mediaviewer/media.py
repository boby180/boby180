"""File discovery and playlist order. No Qt here, so it is easy to test."""
import os
import random
import re

IMAGE_EXT = {
    "jpg", "jpeg", "jfif", "jpe", "png", "apng", "gif", "webp", "bmp", "dib",
    "tif", "tiff", "ico", "svg", "heic", "heif", "avif", "tga", "ppm", "pgm",
}
VIDEO_EXT = {
    "mp4", "m4v", "mov", "qt", "mkv", "webm", "avi", "wmv", "asf", "flv",
    "f4v", "mpg", "mpeg", "m2v", "ts", "mts", "m2ts", "3gp", "3g2", "ogv",
    "vob", "divx", "rm", "rmvb",
}
ANIMATED_EXT = {"gif", "webp", "apng"}


def ext(path):
    return os.path.splitext(path)[1][1:].lower()


def kind_of(path):
    e = ext(path)
    if e in IMAGE_EXT:
        return "image"
    if e in VIDEO_EXT:
        return "video"
    return None


def natural_key(text):
    """'clip2' sorts before 'clip10'."""
    return [int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", text)]


def collect_files(paths):
    """Expand files and folders (recursively) into media file paths, without duplicates."""
    out, seen = [], set()

    def add(p):
        p = os.path.abspath(p)
        key = os.path.normcase(p)
        if key not in seen and kind_of(p):
            seen.add(key)
            out.append(p)

    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs.sort(key=natural_key)
                for f in sorted(files, key=natural_key):
                    add(os.path.join(root, f))
        elif os.path.isfile(p):
            add(p)
    return out


class Item:
    __slots__ = ("path", "name", "kind", "size", "mtime", "bad")

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        self.kind = kind_of(path)
        try:
            st = os.stat(path)
            self.size, self.mtime = st.st_size, st.st_mtime
        except OSError:
            self.size, self.mtime = 0, 0
        self.bad = False


class Playlist:
    """The visible order (after filter and sort) and moving through it."""

    def __init__(self):
        self.items = []
        self.view = []      # indices into items
        self.pos = -1       # index into view
        self.filter = "all"
        self.sort = "name"
        self.shuffle = False
        self.loop = False
        self._history = []
        self._root = ""

    def set_paths(self, paths):
        self.items = [Item(p) for p in paths]
        roots = {os.path.dirname(p) for p in paths}
        self._root = os.path.commonpath(list(roots)) if roots else ""
        self._history = []
        self.pos = -1
        self.rebuild()

    def label(self, item):
        """Path relative to the opened folder, so files in sub-folders are told apart."""
        if self._root:
            try:
                return os.path.relpath(item.path, self._root)
            except ValueError:
                pass
        return item.name

    def rebuild(self):
        current = self.current()
        idx = [i for i, it in enumerate(self.items) if self.filter in ("all", it.kind)]
        if self.sort == "date":
            idx.sort(key=lambda i: (self.items[i].mtime, natural_key(self.label(self.items[i]))))
        elif self.sort == "size":
            idx.sort(key=lambda i: -self.items[i].size)
        else:
            idx.sort(key=lambda i: natural_key(self.label(self.items[i])))
        self.view = idx
        if current is not None and self.items.index(current) in idx:
            self.pos = idx.index(self.items.index(current))
        else:
            self.pos = 0 if idx else -1

    def __len__(self):
        return len(self.view)

    def current(self):
        if 0 <= self.pos < len(self.view):
            return self.items[self.view[self.pos]]
        return None

    def go(self, pos, record=True):
        """Move to pos. record=False when stepping back, so 'previous' can be pressed again."""
        if not self.view:
            return None
        pos = max(0, min(len(self.view) - 1, pos))
        if record and pos != self.pos and self.pos >= 0:
            self._history.append(self.pos)
            del self._history[:-500]
        self.pos = pos
        return self.current()

    def next_pos(self, auto=False):
        """Where 'next' goes, or None at the end of the list when it shouldn't wrap."""
        n = len(self.view)
        if not n:
            return None
        if self.shuffle and n > 1:
            p = self.pos
            while p == self.pos:
                p = random.randrange(n)
            return p
        if self.pos < n - 1:
            return self.pos + 1
        if self.loop or not auto:
            return 0
        return None

    def prev_pos(self):
        n = len(self.view)
        if not n:
            return None
        if self.shuffle and self._history:
            return self._history.pop()
        return self.pos - 1 if self.pos > 0 else n - 1

    def counts(self):
        images = sum(1 for i in self.view if self.items[i].kind == "image")
        return images, len(self.view) - images


def fmt_time(ms):
    s = max(0, int(ms // 1000))
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_size(b):
    if b < 1024 * 1024:
        return f"{b / 1024:.0f} KB"
    if b < 1024 ** 3:
        return f"{b / 1024 ** 2:.1f} MB"
    return f"{b / 1024 ** 3:.2f} GB"
