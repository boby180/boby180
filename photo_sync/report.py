"""Writing comparison and upload reports (CSV for Excel, HTML for a quick look)."""

from __future__ import annotations

import csv
import html
from datetime import datetime
from pathlib import Path

from .compare import EXACT, MISSING, SIMILAR, MatchResult, summarize

STATUS_LABELS = {
    EXACT: "קיים בשרת (זהה)",
    SIMILAR: "קיים בשרת (דומה)",
    MISSING: "חסר בשרת",
}

COLUMNS = [
    "status", "status_he", "local_path", "server_path", "distance", "name_on_server",
    "differences", "size", "width", "height", "taken", "camera", "title", "description",
    "keywords", "sha256", "phash", "error",
]


def _row(r: MatchResult) -> dict:
    img = r.local
    return {
        "status": r.status,
        "status_he": STATUS_LABELS[r.status],
        "local_path": img.path,
        "server_path": r.server.path if r.server else "",
        "distance": "" if r.distance is None else r.distance,
        "name_on_server": "yes" if r.name_on_server else "",
        "differences": " | ".join(r.differences),
        "size": img.size,
        "width": img.width or "",
        "height": img.height or "",
        "taken": img.taken or "",
        "camera": img.camera or "",
        "title": img.title or "",
        "description": img.description or "",
        "keywords": img.keywords or "",
        "sha256": img.sha256,
        "phash": img.phash or "",
        "error": img.error or "",
    }


def write_csv(rows: list[dict], path: Path, columns: list[str]) -> None:
    # utf-8-sig so that Excel shows Hebrew correctly.
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_html(results: list[MatchResult], path: Path) -> None:
    summary = summarize(results)
    order = {MISSING: 0, SIMILAR: 1, EXACT: 2}
    rows = []
    for r in sorted(results, key=lambda r: (order[r.status], r.local.path)):
        d = _row(r)
        rows.append(
            "<tr class='{status}'><td>{label}</td><td>{local}</td><td>{server}</td>"
            "<td>{taken}</td><td>{desc}</td><td>{diffs}</td></tr>".format(
                status=r.status,
                label=html.escape(d["status_he"]),
                local=html.escape(d["local_path"]),
                server=html.escape(d["server_path"]),
                taken=html.escape(d["taken"]),
                desc=html.escape(d["description"] or d["title"]),
                diffs=html.escape(d["differences"]).replace(" | ", "<br>"),
            )
        )
    path.write_text(
        f"""<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>השוואת תמונות</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 16px; background: #fff; color: #222; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ border: 1px solid #ddd; padding: 4px 6px; text-align: right; vertical-align: top; word-break: break-all; }}
th {{ background: #f3f3f3; position: sticky; top: 0; }}
tr.missing td:first-child {{ background: #fde2e2; }}
tr.similar td:first-child {{ background: #fff4cc; }}
tr.exact td:first-child {{ background: #e2f5e2; }}
.cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }}
.card {{ border: 1px solid #ddd; border-radius: 8px; padding: 8px 14px; }}
.card b {{ font-size: 22px; display: block; }}
</style></head><body>
<h1>השוואת תמונות: מחשב מול שרת</h1>
<p>נוצר ב-{datetime.now():%Y-%m-%d %H:%M}</p>
<div class="cards">
<div class="card"><b>{len(results)}</b>תמונות במחשב</div>
<div class="card"><b>{summary[EXACT]}</b>{STATUS_LABELS[EXACT]}</div>
<div class="card"><b>{summary[SIMILAR]}</b>{STATUS_LABELS[SIMILAR]}</div>
<div class="card"><b>{summary[MISSING]}</b>{STATUS_LABELS[MISSING]}</div>
<div class="card"><b>{summary['errors']}</b>שגיאות קריאה</div>
</div>
<table><thead><tr><th>סטטוס</th><th>קובץ במחשב</th><th>התאמה בשרת</th><th>תאריך צילום</th>
<th>תיאור</th><th>הבדלים</th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody></table></body></html>
""",
        encoding="utf-8",
    )


def write_compare_report(results: list[MatchResult], report_dir: str | Path) -> dict[str, Path]:
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    csv_path = report_dir / f"compare-{stamp}.csv"
    html_path = report_dir / f"compare-{stamp}.html"
    write_csv([_row(r) for r in results], csv_path, COLUMNS)
    write_html(results, html_path)
    return {"csv": csv_path, "html": html_path}


def write_upload_report(records: list[dict], report_dir: str | Path, dry_run: bool) -> Path:
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = report_dir / f"upload-{'dryrun-' if dry_run else ''}{stamp}.csv"
    write_csv(records, path, ["local", "target", "status", "detail"])
    return path


DUPLICATE_LABELS = {"exact": "עותק זהה", "similar": "אותה תמונה בגרסה אחרת"}

DUPLICATE_COLUMNS = [
    "group", "kind", "kind_he", "suggestion", "path", "size", "width", "height",
    "taken", "camera", "title", "description", "sha256", "phash",
]


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _duplicate_rows(groups) -> list[dict]:
    rows = []
    for number, group in enumerate(groups, 1):
        for img in group.images:
            rows.append({
                "group": number,
                "kind": group.kind,
                "kind_he": DUPLICATE_LABELS[group.kind],
                "suggestion": "keep" if img is group.keep else "extra copy",
                "path": img.path,
                "size": img.size,
                "width": img.width or "",
                "height": img.height or "",
                "taken": img.taken or "",
                "camera": img.camera or "",
                "title": img.title or "",
                "description": img.description or "",
                "sha256": img.sha256,
                "phash": img.phash or "",
            })
    return rows


def write_duplicates_report(groups, total_images: int, report_dir: str | Path) -> dict[str, Path]:
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    csv_path = report_dir / f"duplicates-{stamp}.csv"
    html_path = report_dir / f"duplicates-{stamp}.html"
    rows = _duplicate_rows(groups)
    write_csv(rows, csv_path, DUPLICATE_COLUMNS)

    exact = [g for g in groups if g.kind == "exact"]
    similar = [g for g in groups if g.kind == "similar"]
    wasted = sum(g.wasted_bytes for g in exact)
    body = []
    for number, group in enumerate(groups, 1):
        body.append(
            f"<tr class='head {group.kind}'><td colspan='5'>קבוצה {number}: "
            f"{html.escape(DUPLICATE_LABELS[group.kind])} - {len(group.images)} קבצים, "
            f"{_human_size(group.wasted_bytes)} מיותרים</td></tr>"
        )
        for img in group.images:
            keep = img is group.keep
            body.append(
                "<tr><td>{s}</td><td>{p}</td><td>{d}</td><td>{t}</td><td>{x}</td></tr>".format(
                    s="להשאיר" if keep else "עותק נוסף",
                    p=html.escape(img.path),
                    d=f"{img.width or '?'}x{img.height or '?'}, {_human_size(img.size)}",
                    t=html.escape(img.taken or ""),
                    x=html.escape(img.description or img.title or ""),
                )
            )
    html_path.write_text(
        f"""<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>תמונות כפולות</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 16px; background: #fff; color: #222; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ border: 1px solid #ddd; padding: 4px 6px; text-align: right; vertical-align: top; word-break: break-all; }}
th {{ background: #f3f3f3; position: sticky; top: 0; }}
tr.head td {{ font-weight: bold; padding-top: 10px; }}
tr.head.exact td {{ background: #fde2e2; }}
tr.head.similar td {{ background: #fff4cc; }}
.cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }}
.card {{ border: 1px solid #ddd; border-radius: 8px; padding: 8px 14px; }}
.card b {{ font-size: 22px; display: block; }}
</style></head><body>
<h1>תמונות כפולות</h1>
<p>נוצר ב-{datetime.now():%Y-%m-%d %H:%M}. שום קובץ לא נמחק - זה דוח בלבד.
"להשאיר" היא ההצעה: הגרסה ברזולוציה הגבוהה ביותר.</p>
<div class="cards">
<div class="card"><b>{total_images}</b>תמונות שנבדקו</div>
<div class="card"><b>{len(exact)}</b>קבוצות של עותקים זהים</div>
<div class="card"><b>{sum(len(g.extra) for g in exact)}</b>עותקים מיותרים</div>
<div class="card"><b>{_human_size(wasted)}</b>מקום שאפשר לפנות</div>
<div class="card"><b>{len(similar)}</b>קבוצות של אותה תמונה בגרסאות שונות</div>
</div>
<table><thead><tr><th>הצעה</th><th>קובץ</th><th>מידות וגודל</th><th>תאריך צילום</th><th>תיאור</th></tr></thead>
<tbody>
{chr(10).join(body)}
</tbody></table></body></html>
""",
        encoding="utf-8",
    )
    return {"csv": csv_path, "html": html_path}
