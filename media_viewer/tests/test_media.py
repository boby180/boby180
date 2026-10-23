import os

from mediaviewer.media import Playlist, collect_files, fmt_size, fmt_time, kind_of, natural_key

DATA = os.path.join(os.path.dirname(__file__), "data")


def test_kind_of():
    assert kind_of("a/B.JPG") == "image"
    assert kind_of("x.heic") == "image"
    assert kind_of("x.MKV") == "video"
    assert kind_of("x.avi") == "video"
    assert kind_of("notes.txt") is None


def test_natural_sort():
    names = ["clip10.mp4", "clip2.mp4", "Clip1.mp4"]
    assert sorted(names, key=natural_key) == ["Clip1.mp4", "clip2.mp4", "clip10.mp4"]


def test_collect_files_recurses_and_skips_other_files(tmp_path):
    (tmp_path / "sub").mkdir()
    for name in ("b.jpg", "a.mp4", "readme.txt", "sub/c.png"):
        (tmp_path / name).write_bytes(b"x")
    found = collect_files([str(tmp_path), str(tmp_path / "b.jpg")])
    assert [os.path.relpath(p, tmp_path) for p in found] == ["a.mp4", "b.jpg", os.path.join("sub", "c.png")]


def test_playlist_filter_next_prev_and_end():
    pl = Playlist()
    pl.set_paths(collect_files([DATA]))
    assert [pl.items[i].name for i in pl.view] == ["anim.gif", "clip.avi", "clip.mp4", "photo.jpg"]
    pl.go(3)
    assert pl.next_pos(auto=True) is None      # end of the list stops a slideshow
    assert pl.next_pos(auto=False) == 0        # pressing "next" wraps around
    pl.loop = True
    assert pl.next_pos(auto=True) == 0
    pl.filter = "video"
    pl.rebuild()
    assert len(pl) == 2 and pl.pos == 0
    assert pl.prev_pos() == 1


def test_shuffle_back_returns_to_previous():
    pl = Playlist()
    pl.set_paths(collect_files([DATA]))
    pl.shuffle = True
    pl.go(0)
    p = pl.next_pos()
    assert p != 0
    pl.go(p)
    back = pl.prev_pos()
    pl.go(back, record=False)
    assert pl.pos == 0


def test_formatting():
    assert fmt_time(65_000) == "1:05"
    assert fmt_time(3_725_000) == "1:02:05"
    assert fmt_size(2 * 1024 * 1024) == "2.0 MB"
