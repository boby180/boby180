#!/usr/bin/env python3
"""Send a daily digest of good news to a Telegram chat.

Reads RSS feeds of positive-news sites, picks the freshest stories and
posts them to Telegram. Uses only the Python standard library.

Required environment variables:
  TELEGRAM_BOT_TOKEN  - token from @BotFather
  TELEGRAM_CHAT_ID    - chat id that should receive the message

Optional:
  NEWS_COUNT          - number of stories to send (default 5)
  SEND_HOUR           - local hour to send at (default 8)
  TIMEZONE            - IANA timezone for SEND_HOUR (default Asia/Jerusalem)
  FORCE_SEND=1        - skip the local-hour check (manual runs)
"""

import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

FEEDS = [
    ("Good News Network", "https://www.goodnewsnetwork.org/feed/"),
    ("Positive News", "https://www.positive.news/feed/"),
    ("Reasons to be Cheerful", "https://reasonstobecheerful.world/feed/"),
]

USER_AGENT = "Mozilla/5.0 (compatible; GoodNewsTelegramBot/1.0)"
ATOM_NS = "{http://www.w3.org/2005/Atom}"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def parse_date(text):
    if not text:
        return None
    text = text.strip()
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_feed(source, data):
    root = ET.fromstring(data)
    items = []
    # RSS 2.0
    for item in root.iter("item"):
        items.append({
            "source": source,
            "title": (item.findtext("title") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
            "date": parse_date(item.findtext("pubDate")),
        })
    # Atom
    for entry in root.iter(f"{ATOM_NS}entry"):
        link_el = entry.find(f"{ATOM_NS}link")
        items.append({
            "source": source,
            "title": (entry.findtext(f"{ATOM_NS}title") or "").strip(),
            "link": link_el.get("href", "") if link_el is not None else "",
            "date": parse_date(entry.findtext(f"{ATOM_NS}updated")
                               or entry.findtext(f"{ATOM_NS}published")),
        })
    return [i for i in items if i["title"] and i["link"]]


def collect_news(count):
    all_items = []
    for source, url in FEEDS:
        try:
            all_items.extend(parse_feed(source, fetch(url)))
        except Exception as exc:  # one broken feed shouldn't stop the digest
            print(f"Failed to read {source}: {exc}", file=sys.stderr)

    epoch = datetime.min.replace(tzinfo=timezone.utc)
    all_items.sort(key=lambda i: i["date"] or epoch, reverse=True)

    # Prefer stories from the last day, but fill up with older ones if needed.
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    recent = [i for i in all_items if i["date"] and i["date"] >= cutoff]
    older = [i for i in all_items if i not in recent]

    picked, seen = [], set()
    for item in recent + older:
        key = re.sub(r"\W+", "", item["title"].lower())
        if key in seen:
            continue
        seen.add(key)
        picked.append(item)
        if len(picked) >= count:
            break
    return picked


def build_message(items, today):
    lines = [f"☀️ <b>בוקר טוב! החדשות הטובות של {today:%d/%m/%Y}</b>", ""]
    for n, item in enumerate(items, 1):
        title = html.escape(item["title"])
        link = html.escape(item["link"], quote=True)
        source = html.escape(item["source"])
        lines.append(f"{n}. <a href=\"{link}\">{title}</a>\n<i>{source}</i>")
        lines.append("")
    lines.append("שיהיה יום נפלא! 🌈")
    return "\n".join(lines)


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    with urllib.request.urlopen(url, data=payload, timeout=30) as resp:
        result = json.load(resp)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        sys.exit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")

    tz = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Jerusalem"))
    now = datetime.now(tz)
    send_hour = int(os.environ.get("SEND_HOUR", "8"))

    # The workflow is scheduled for both possible UTC offsets (summer/winter
    # time); only the run that lands on the right local hour sends.
    if os.environ.get("FORCE_SEND") != "1":
        scheduled = os.environ.get("SCHEDULED_UTC_HOUR")
        if scheduled:
            local_hour = datetime.now(timezone.utc).replace(
                hour=int(scheduled), minute=0).astimezone(tz).hour
        else:
            local_hour = now.hour
        if local_hour != send_hour:
            print(f"Local hour is {local_hour}, not {send_hour}; skipping.")
            return

    items = collect_news(int(os.environ.get("NEWS_COUNT", "5")))
    if not items:
        sys.exit("No news items could be fetched")

    send_telegram(token, chat_id, build_message(items, now))
    print(f"Sent {len(items)} stories.")


if __name__ == "__main__":
    main()
