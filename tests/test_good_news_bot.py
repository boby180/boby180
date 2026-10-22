from datetime import datetime, timedelta, timezone

import good_news_bot as bot

RSS = b"""<rss><channel>
<item><title>Turtles &amp; whales saved</title><link>https://x.org/a?b=1&amp;c=2</link>
<pubDate>Sun, 04 Oct 2026 10:00:00 +0000</pubDate></item>
<item><title>No link</title></item>
</channel></rss>"""

ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<title>Clean river</title><link href="https://y.org/b"/><updated>2026-10-05T03:00:00Z</updated>
</entry></feed>"""


def test_parse_rss_and_atom():
    items = bot.parse_feed("A", RSS) + bot.parse_feed("B", ATOM)
    assert [i["title"] for i in items] == ["Turtles & whales saved", "Clean river"]
    assert items[0]["link"] == "https://x.org/a?b=1&c=2"
    assert items[1]["date"] == datetime(2026, 10, 5, 3, tzinfo=timezone.utc)


def test_build_message_escapes_html():
    items = bot.parse_feed("A", RSS)
    msg = bot.build_message(items, datetime(2026, 10, 5))
    assert "05/10/2026" in msg
    assert 'href="https://x.org/a?b=1&amp;c=2"' in msg
    assert "Turtles &amp; whales saved" in msg


def test_collect_news_prefers_recent_and_dedupes(monkeypatch):
    now = datetime.now(timezone.utc)
    feeds = {
        "old": [{"source": "S", "title": "Old", "link": "l1", "date": now - timedelta(days=5)}],
        "new": [
            {"source": "S", "title": "New!", "link": "l2", "date": now - timedelta(hours=1)},
            {"source": "S", "title": "new", "link": "l3", "date": now - timedelta(hours=2)},
        ],
    }
    monkeypatch.setattr(bot, "FEEDS", [("S", "old"), ("S", "new"), ("S", "broken")])
    monkeypatch.setattr(bot, "fetch", lambda url: url)

    def fake_parse(source, url):
        if url == "broken":
            raise ValueError("bad feed")
        return feeds[url]

    monkeypatch.setattr(bot, "parse_feed", fake_parse)
    assert [i["title"] for i in bot.collect_news(5)] == ["New!", "Old"]


def test_only_matching_schedule_sends(monkeypatch):
    sent = []
    monkeypatch.setattr(bot, "send_telegram", lambda *a: sent.append(a))
    monkeypatch.setattr(bot, "collect_news", lambda n: [
        {"source": "S", "title": "T", "link": "L", "date": None}])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.delenv("FORCE_SEND", raising=False)

    tz = bot.ZoneInfo("Asia/Jerusalem")
    israel_offset = int(datetime.now(tz).utcoffset().total_seconds() // 3600)
    right, wrong = (5, 6) if israel_offset == 3 else (6, 5)

    monkeypatch.setenv("SCHEDULED_UTC_HOUR", str(wrong))
    bot.main()
    assert sent == []

    monkeypatch.setenv("SCHEDULED_UTC_HOUR", str(right))
    bot.main()
    assert len(sent) == 1


def test_force_send(monkeypatch):
    sent = []
    monkeypatch.setattr(bot, "send_telegram", lambda *a: sent.append(a))
    monkeypatch.setattr(bot, "collect_news", lambda n: [
        {"source": "S", "title": "T", "link": "L", "date": None}])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("FORCE_SEND", "1")
    monkeypatch.setenv("SCHEDULED_UTC_HOUR", "")
    bot.main()
    assert len(sent) == 1
