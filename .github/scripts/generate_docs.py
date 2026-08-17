#!/usr/bin/env python3
"""
Generate catalog.md, stats.md, changes*.md, docs/catalog.json and update README.md
from *_AppCatalog.json files found in catalog/ and archive/.

Static website files (docs/index.html, docs/app.css, docs/app.js) are committed once
and never regenerated — only docs/catalog.json changes on each run.

Run from the repository root:
    python .github/scripts/generate_docs.py
"""

import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_catalog(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def all_catalog_files():
    """Return every *_AppCatalog.json in catalog/ and archive/, sorted by filename."""
    files = glob.glob("catalog/*_AppCatalog.json") + glob.glob("archive/*_AppCatalog.json")
    return sorted(files, key=os.path.basename)


def parse_dt(path):
    """Extract a naive datetime from YYYYMMDD_HHMMSS_AppCatalog.json filename."""
    m = re.search(r"(\d{8})_(\d{6})_AppCatalog", os.path.basename(path))
    if m:
        try:
            return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            pass
    return None


def filename_to_ts(path):
    dt = parse_dt(path)
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else os.path.basename(path)


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def find_comparison_file(all_files, latest_file, days):
    latest_dt = parse_dt(latest_file)
    if latest_dt is None:
        return None
    cutoff = latest_dt - timedelta(days=days)
    candidates = [
        (parse_dt(f), f)
        for f in all_files
        if f != latest_file and parse_dt(f) is not None and parse_dt(f) <= cutoff
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[0])[1]


def get_repo_url():
    """Try to derive the GitHub HTTPS URL from git remote origin."""
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", out)
        if m:
            return f"https://github.com/{m.group(1)}"
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# catalog.md
# ---------------------------------------------------------------------------

def generate_catalog(apps, source_file):
    sorted_apps = sorted(
        apps,
        key=lambda a: (
            a.get("publisherDisplayName", "").lower(),
            a.get("productDisplayName", "").lower(),
        ),
    )
    unique_products = len({a.get("productId") for a in apps})

    lines = [
        "# App Catalog — Full Package List",
        "",
        f"> **Source:** `{os.path.basename(source_file)}` (exported {filename_to_ts(source_file)})  ",
        f"> **Generated:** {now_utc()}  ",
        f"> **Total:** {len(apps):,} packages · {unique_products:,} unique products",
        "",
        "| # | Publisher | App Name | Branch | Version | Architecture | Auto-Update | Locales |",
        "|--:|-----------|----------|--------|---------|:------------:|:-----------:|---------|",
    ]
    for i, app in enumerate(sorted_apps, 1):
        lines.append(
            f"| {i} | {app.get('publisherDisplayName','')} "
            f"| {app.get('productDisplayName','')} "
            f"| {app.get('branchDisplayName','')} "
            f"| `{app.get('versionDisplayName','')}` "
            f"| {app.get('applicableArchitectures','')} "
            f"| {'✅' if app.get('packageAutoUpdateCapable') else '❌'} "
            f"| {', '.join(app.get('locales', []))} |"
        )

    with open("catalog.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  catalog.md            — {len(apps):,} packages")


# ---------------------------------------------------------------------------
# stats.md
# ---------------------------------------------------------------------------

def generate_stats(apps, source_file):
    total           = len(apps)
    unique_apps     = len({(a.get("publisherDisplayName",""), a.get("productDisplayName","")) for a in apps})
    unique_products = len({a.get("productId") for a in apps})

    pub_counts: dict[str, int] = {}
    for a in apps:
        pub = a.get("publisherDisplayName") or "(Unknown)"
        pub_counts[pub] = pub_counts.get(pub, 0) + 1
    unique_publishers = len(pub_counts)
    top10 = sorted(pub_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    auto_yes = sum(1 for a in apps if a.get("packageAutoUpdateCapable"))
    auto_no  = total - auto_yes
    auto_pct = round(auto_yes / total * 100, 1) if total else 0.0

    arch_counts: dict[str, int] = {}
    for a in apps:
        arch = a.get("applicableArchitectures") or "(Not specified)"
        arch_counts[arch] = arch_counts.get(arch, 0) + 1
    arch_stats = sorted(arch_counts.items(), key=lambda x: x[1], reverse=True)

    all_locales: set[str] = set()
    for a in apps:
        all_locales.update(a.get("locales", []))
    multi_locale = sum(1 for a in apps if len(a.get("locales", [])) > 1)
    no_arch      = sum(1 for a in apps if not a.get("applicableArchitectures"))

    lines = [
        "# App Catalog Statistics",
        "",
        f"> **Source:** `{os.path.basename(source_file)}` (exported {filename_to_ts(source_file)})  ",
        f"> **Generated:** {now_utc()}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|------:|",
        f"| Total Packages | **{total:,}** |",
        f"| Unique Apps (Publisher + Name) | {unique_apps:,} |",
        f"| Unique Products (by Product ID) | {unique_products:,} |",
        f"| Publishers | {unique_publishers:,} |",
        f"| Auto-Update Capable | {auto_yes:,} ({auto_pct}%) |",
        f"| Not Auto-Update Capable | {auto_no:,} ({round(100 - auto_pct, 1)}%) |",
        f"| Available Locales | {len(all_locales)} |",
        f"| Multi-Locale Packages | {multi_locale:,} |",
        f"| Packages Without Architecture | {no_arch:,} |",
        "",
        "## Top 10 Publishers",
        "",
        "| Rank | Publisher | Packages | Share |",
        "|-----:|-----------|--------:|------:|",
    ]
    for i, (pub, count) in enumerate(top10, 1):
        lines.append(f"| {i} | {pub} | {count:,} | {round(count/total*100,1)}% |")

    lines += ["", "## Architecture Breakdown", "", "| Architecture | Packages | Share |", "|--------------|--------:|------:|"]
    for arch, count in arch_stats:
        lines.append(f"| {arch} | {count:,} | {round(count/total*100,1)}% |")

    lines += ["", "## Available Locales", "", "| Locale |", "|--------|"]
    for loc in sorted(all_locales):
        lines.append(f"| `{loc}` |")

    with open("stats.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("  stats.md")

    return {
        "total": total, "unique_products": unique_products,
        "publishers": unique_publishers, "auto_yes": auto_yes,
        "auto_pct": auto_pct, "locales": len(all_locales),
        "source_ts": filename_to_ts(source_file),
    }


# ---------------------------------------------------------------------------
# Changes — core computation (returns structured data + raw lists)
# ---------------------------------------------------------------------------

# Fields compared when deciding whether a package counts as "updated".
# `id` is deliberately absent: the Graph API reassigns it on every export.
TRACKED_FIELDS = [
    ("versionDisplayName",       "Version"),
    ("productDisplayName",       "App Name"),
    ("branchDisplayName",        "Branch"),
    ("publisherDisplayName",     "Publisher"),
    ("applicableArchitectures",  "Architecture"),
    ("packageAutoUpdateCapable", "Auto-Update"),
    ("locales",                  "Locales"),
]


def _composite_key(a):
    """Stable composite key for exports that pre-date the branchId field.

    Deliberately minimal. Architecture and locales would make it more unique,
    but they are also things that legitimately change — folding them in turns
    an ordinary update ("dropped x86 support") into a removal plus an addition.
    The duplicates this leaves behind are handled by _index_by_key instead.
    """
    return (a.get("productId", "") + "|" + a.get("branchDisplayName", "")).lower()


def _tiebreak(a):
    """Deterministic ordering for records that share a key.

    Ordered by the fields least likely to change between exports, so the same
    two records pair up run after run. Deliberately excludes `id` (reassigned
    every export) and `branchId` — collisions only happen on the composite
    path, where one side of the comparison has no branchId at all and sorting
    on it would order the two sides by different criteria.
    """
    return (
        a.get("applicableArchitectures") or "",
        ",".join(a.get("locales") or []),
        a.get("productDisplayName") or "",
        a.get("versionDisplayName") or "",
    )


def _index_by_key(apps, key_fn):
    """Index apps by key, keeping every record when two share a key.

    A plain dict comprehension silently discards duplicates, which understates
    the package count on both sides of a comparison. Colliding records get a
    stable ordinal suffix instead. Returns (index, collision_count).
    """
    groups = {}
    for a in apps:
        groups.setdefault(key_fn(a), []).append(a)

    indexed, collisions = {}, 0
    for key, group in groups.items():
        if len(group) == 1:
            indexed[key] = group[0]
            continue
        collisions += len(group) - 1
        for i, app in enumerate(sorted(group, key=_tiebreak)):
            indexed[f"{key}#{i}"] = app
    return indexed, collisions


def _format_span(prev_dt, curr_dt):
    """Human-readable gap between two exports.

    Exports are sometimes pushed minutes apart, where a plain day count would
    read '0 days' and look broken.
    """
    if not prev_dt or not curr_dt:
        return None
    secs = max(0, int((curr_dt - prev_dt).total_seconds()))
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if secs >= size:
            n = secs // size
            return f"{n:,} {unit}{'' if n == 1 else 's'}"
    return f"{secs} second{'' if secs == 1 else 's'}"


def _fmt_value(v):
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return "" if v is None else str(v)


def _field_changes(prev, curr):
    """List every tracked field that differs between two versions of a package."""
    return [
        {"field": field, "label": label,
         "from": _fmt_value(prev.get(field)), "to": _fmt_value(curr.get(field))}
        for field, label in TRACKED_FIELDS
        if prev.get(field) != curr.get(field)
    ]


def _compute_changes(current_apps, previous_apps, current_file, previous_file):
    """Compute change sets. Returns (structured_dict, added, removed, updated_pairs)."""
    # Require branchId on *every* record before trusting it as the key — a
    # partially populated export would otherwise mix GUIDs and composite keys
    # in one index and report the difference as churn.
    curr_has_branch = all(a.get("branchId") for a in current_apps)
    prev_has_branch = all(a.get("branchId") for a in previous_apps)

    if curr_has_branch and prev_has_branch:
        key_fn = lambda a: a.get("branchId")
    else:
        key_fn = _composite_key

    curr_by_id, curr_dupes = _index_by_key(current_apps, key_fn)
    prev_by_id, prev_dupes = _index_by_key(previous_apps, key_fn)
    if curr_dupes or prev_dupes:
        print(f"      note: {curr_dupes} current / {prev_dupes} previous package(s) "
              f"share a key and were disambiguated by ordinal")
    curr_ids, prev_ids = set(curr_by_id), set(prev_by_id)

    def sk(a):
        return (a.get("publisherDisplayName","").lower(), a.get("productDisplayName","").lower())

    added   = sorted([curr_by_id[i] for i in curr_ids - prev_ids], key=sk)
    removed = sorted([prev_by_id[i] for i in prev_ids - curr_ids], key=sk)

    updated_pairs = sorted(
        [(prev_by_id[i], curr_by_id[i]) for i in curr_ids & prev_ids
         if _field_changes(prev_by_id[i], curr_by_id[i])],
        key=lambda x: sk(x[1]),
    )

    # Structured updated list: current app fields + previous version + field deltas
    updated_list = []
    for prev, curr in updated_pairs:
        entry = dict(curr)
        entry["prevVersionDisplayName"] = prev.get("versionDisplayName", "")
        entry["changes"] = _field_changes(prev, curr)
        updated_list.append(entry)

    prev_dt, curr_dt = parse_dt(previous_file), parse_dt(current_file)
    span_days = (curr_dt - prev_dt).days if prev_dt and curr_dt else None

    structured = {
        "compared_to":    os.path.basename(previous_file),
        "compared_to_ts": filename_to_ts(previous_file),
        "span_days":      span_days,
        "span_label":     _format_span(prev_dt, curr_dt),
        "added_count":    len(added),
        "removed_count":  len(removed),
        "updated_count":  len(updated_list),
        "added":          added,
        "removed":        removed,
        "updated":        updated_list,
    }
    return structured, added, removed, updated_pairs


# ---------------------------------------------------------------------------
# Changes — markdown output
# ---------------------------------------------------------------------------

def _render_changes_md(structured, added, removed, updated_pairs, current_file, title):
    span = structured.get("span_label")
    span_line = f"> **Span:** {span} between exports  " if span else ""

    lines = [
        f"# {title}", "",
        f"> **Comparing:** `{os.path.basename(current_file)}` (exported {filename_to_ts(current_file)})  ",
        f"> **vs:** `{structured['compared_to']}` (exported {structured['compared_to_ts']})  ",
    ]
    if span_line:
        lines.append(span_line)
    lines += [
        f"> **Generated:** {now_utc()}", "",
        "## Summary", "",
        "| Change | Count |", "|--------|------:|",
        f"| ✅ Added | {len(added):,} |",
        f"| ❌ Removed | {len(removed):,} |",
        f"| 🔄 Updated | {len(updated_pairs):,} |", "",
    ]

    if added:
        lines += [f"## ✅ Added ({len(added):,} packages)", "",
                  "| Publisher | App | Branch | Version | Architecture |",
                  "|-----------|-----|--------|---------|:------------:|"]
        for a in added:
            lines.append(f"| {a.get('publisherDisplayName','')} | {a.get('productDisplayName','')} "
                         f"| {a.get('branchDisplayName','')} | `{a.get('versionDisplayName','')}` "
                         f"| {a.get('applicableArchitectures','')} |")
        lines.append("")

    if removed:
        lines += [f"## ❌ Removed ({len(removed):,} packages)", "",
                  "| Publisher | App | Branch | Last Version | Architecture |",
                  "|-----------|-----|--------|:------------:|:------------:|"]
        for a in removed:
            lines.append(f"| {a.get('publisherDisplayName','')} | {a.get('productDisplayName','')} "
                         f"| {a.get('branchDisplayName','')} | `{a.get('versionDisplayName','')}` "
                         f"| {a.get('applicableArchitectures','')} |")
        lines.append("")

    if updated_pairs:
        lines += [f"## 🔄 Updated ({len(updated_pairs):,} packages)", "",
                  "| Publisher | App | Branch | Previous Version | New Version | Changed |",
                  "|-----------|-----|--------|:---------------:|:-----------:|---------|"]
        for prev, curr in updated_pairs:
            changed = ", ".join(c["label"] for c in _field_changes(prev, curr))
            lines.append(f"| {curr.get('publisherDisplayName','')} | {curr.get('productDisplayName','')} "
                         f"| {curr.get('branchDisplayName','')} | `{prev.get('versionDisplayName','')}` "
                         f"| `{curr.get('versionDisplayName','')}` | {changed} |")
        lines.append("")

    if not added and not removed and not updated_pairs:
        lines.append("> No changes detected between these two catalog exports.\n")

    return lines


def generate_changes(current_apps, previous_apps, current_file, previous_file,
                     output="changes.md", title="Catalog Changes — Latest vs Previous"):
    structured, added, removed, updated_pairs = _compute_changes(
        current_apps, previous_apps, current_file, previous_file
    )
    lines = _render_changes_md(structured, added, removed, updated_pairs, current_file, title)
    with open(output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  {output:<26} — +{len(added)} added  -{len(removed)} removed  ~{len(updated_pairs)} updated")
    return structured


def generate_changes_period(current_apps, current_file, all_files, days, output, title, period_label):
    comparison = find_comparison_file(all_files, current_file, days)
    if comparison is None:
        with open(output, "w", encoding="utf-8") as f:
            f.write(
                f"# {title}\n\n"
                f"> **Generated:** {now_utc()}\n\n"
                f"No export found that is at least {days} day(s) older than "
                f"`{os.path.basename(current_file)}`. Push more exports over time and "
                f"this file will populate automatically.\n"
            )
        print(f"  {output:<26} — no {period_label} comparison available yet")
        return None
    return generate_changes(current_apps, load_catalog(comparison), current_file, comparison, output, title)


# ---------------------------------------------------------------------------
# docs/catalog.json  (the only file the website needs regenerated each run)
# ---------------------------------------------------------------------------

def generate_catalog_json(apps, stats, source_file):
    sorted_apps = sorted(
        apps,
        key=lambda a: (
            a.get("publisherDisplayName", "").lower(),
            a.get("productDisplayName", "").lower(),
        ),
    )

    payload = {
        "meta": {
            "source_ts":       stats["source_ts"],
            "generated":       now_utc(),
            "total":           stats["total"],
            "unique_products": stats["unique_products"],
            "publishers":      stats["publishers"],
            "auto_yes":        stats["auto_yes"],
            "auto_pct":        stats["auto_pct"],
            "locales":         stats["locales"],
            "repo_url":        get_repo_url(),
        },
        "apps": sorted_apps,
    }

    os.makedirs("docs", exist_ok=True)
    with open("docs/catalog.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = os.path.getsize("docs/catalog.json") / 1024
    print(f"  docs/catalog.json         — {stats['total']:,} packages, {size_kb:,.0f} KB")


def generate_changes_json(changes):
    """Change sets live in their own file — the site fetches it only when the
    Changes tab is opened, keeping them out of the initial page load."""
    os.makedirs("docs", exist_ok=True)
    with open("docs/changes.json", "w", encoding="utf-8") as f:
        json.dump(changes or {}, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = os.path.getsize("docs/changes.json") / 1024
    print(f"  docs/changes.json         — {len(changes or {})} period(s), {size_kb:,.0f} KB")


# ---------------------------------------------------------------------------
# docs/feed.xml  — RSS feed of catalog changes (keeps last 50 items)
# ---------------------------------------------------------------------------

def _rss_date(ts_str):
    """Convert 'YYYY-MM-DD HH:MM:SS' to RFC 2822 format for RSS pubDate."""
    try:
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
    except ValueError:
        return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")


def _xml_escape(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# Feed items are read in mail clients (Outlook subscribes to the feed), so the
# description is table-based HTML with inline styles only — Outlook's Word engine
# ignores <style> blocks, flex/grid, border-radius and padding on div/span.
FD_FONT   = "'Segoe UI',Segoe,Roboto,Helvetica,Arial,sans-serif"
FD_MONO   = "Consolas,'Courier New',monospace"
FD_INK    = "#1b1f23"   # primary text
FD_DIM    = "#6b7580"   # secondary text
FD_LINE   = "#e3e8ec"   # hairline rules
FD_ADDED   = "#107c41"
FD_REMOVED = "#c4314b"
FD_UPDATED = "#0f6cbd"
FD_MAXW    = 680        # px — keeps rows readable in the Outlook reading pane

_FD_TABLE = (
    f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
    f'style="border-collapse:collapse;max-width:{FD_MAXW}px;font-family:{FD_FONT};'
    f'font-size:14px;color:{FD_INK};"'
)


def _fd_counter(value, label, color):
    """One big-number cell for the summary strip."""
    return (
        f'<td valign="top" style="padding:0 22px 0 0;font-family:{FD_FONT};">'
        f'<div style="font-size:21px;font-weight:700;line-height:24px;color:{color};">{value}</div>'
        f'<div style="font-size:11px;font-weight:600;letter-spacing:.05em;color:{FD_DIM};">{label}</div>'
        f'</td>'
    )


def _fd_section(title, count, color, rows_html):
    """A titled block: coloured rule + heading, then the package rows."""
    return (
        f'{_FD_TABLE}>'
        f'<tr><td colspan="2" style="padding:22px 0 5px;font-family:{FD_FONT};font-size:11px;'
        f'font-weight:700;letter-spacing:.08em;color:{color};border-bottom:2px solid {color};">'
        f'{title.upper()} &nbsp;&middot;&nbsp; {count:,}</td></tr>'
        f'{rows_html}</table>'
    )


def _fd_row(product, publisher, version_html, note=""):
    """Name/publisher on the left, version on the right — one hairline-separated row."""
    cell = (
        f'padding:8px 0;border-bottom:1px solid {FD_LINE};font-family:{FD_FONT};'
        f'font-size:14px;color:{FD_INK};'
    )
    note_html = (
        f'<div style="font-size:11px;color:{FD_DIM};font-style:italic;">{note}</div>' if note else ""
    )
    return (
        f'<tr>'
        f'<td valign="top" style="{cell}padding-right:12px;">'
        f'<div style="font-weight:600;">{product}</div>'
        f'<div style="font-size:12px;color:{FD_DIM};">{publisher}</div>'
        f'{note_html}</td>'
        f'<td valign="top" align="right" nowrap="nowrap" style="{cell}'
        f'font-family:{FD_MONO};font-size:12px;">{version_html}</td>'
        f'</tr>'
    )


def _fd_more(shown, total):
    if total <= shown:
        return ""
    return (
        f'<tr><td colspan="2" style="padding:8px 0;font-family:{FD_FONT};font-size:12px;'
        f'color:{FD_DIM};font-style:italic;">… and {total - shown:,} more</td></tr>'
    )


def _feed_description(structured, source_ts, site_url="#", max_show=20):
    """Build the HTML body of a feed item — readable in feed readers and in Outlook."""
    a = structured.get("added_count",   0)
    r = structured.get("removed_count", 0)
    u = structured.get("updated_count", 0)

    ver = lambda p, key: _xml_escape(p.get(key, "")) or "&mdash;"

    def simple_rows(items, color_version=FD_INK):
        rows = "".join(
            _fd_row(
                _xml_escape(p.get("productDisplayName", "")),
                _xml_escape(p.get("publisherDisplayName", "")),
                f'<span style="color:{color_version};">{ver(p, "versionDisplayName")}</span>',
            )
            for p in items[:max_show]
        )
        return rows + _fd_more(max_show, len(items))

    def updated_rows(items):
        rows = ""
        for p in items[:max_show]:
            labels = [c["label"] for c in p.get("changes", []) if c["field"] != "versionDisplayName"]
            rows += _fd_row(
                _xml_escape(p.get("productDisplayName", "")),
                _xml_escape(p.get("publisherDisplayName", "")),
                f'<span style="color:{FD_DIM};">{ver(p, "prevVersionDisplayName")}</span>'
                f'<span style="color:{FD_DIM};">&nbsp;&#8594;&nbsp;</span>'
                f'<span style="color:{FD_UPDATED};font-weight:700;">{ver(p, "versionDisplayName")}</span>',
                note=("also " + _xml_escape(", ".join(labels))) if labels else "",
            )
        return rows + _fd_more(max_show, len(items))

    parts = [
        # Header band — export timestamp and the baseline it was diffed against
        f'{_FD_TABLE}>'
        f'<tr><td style="padding:14px 18px;background:{FD_UPDATED};font-family:{FD_FONT};">'
        f'<div style="font-size:16px;font-weight:600;color:#ffffff;">'
        f'Catalog export {_xml_escape(source_ts)}</div>'
        f'<div style="font-size:12px;color:#d7e7f8;">compared to '
        f'{_xml_escape(structured.get("compared_to", ""))} '
        f'({_xml_escape(structured.get("compared_to_ts", ""))})</div>'
        f'</td></tr>'
        # Summary strip
        f'<tr><td style="padding:14px 18px;background:#f5f7f9;border:1px solid {FD_LINE};border-top:0;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        + _fd_counter(f"+{a:,}", "ADDED",   FD_ADDED)
        + _fd_counter(f"&minus;{r:,}", "REMOVED", FD_REMOVED)
        + _fd_counter(f"{u:,}", "UPDATED", FD_UPDATED)
        + f'</tr></table></td></tr></table>'
    ]

    if a:
        parts.append(_fd_section("Added",   a, FD_ADDED,   simple_rows(structured.get("added", []), FD_ADDED)))
    if r:
        parts.append(_fd_section("Removed", r, FD_REMOVED, simple_rows(structured.get("removed", []), FD_REMOVED)))
    if u:
        parts.append(_fd_section("Updated", u, FD_UPDATED, updated_rows(structured.get("updated", []))))

    parts.append(
        f'{_FD_TABLE}><tr><td style="padding:20px 0 0;font-family:{FD_FONT};font-size:12px;">'
        f'<a href="{_xml_escape(site_url)}" style="color:{FD_UPDATED};font-weight:600;'
        f'text-decoration:none;">Open the full catalog &#8594;</a></td></tr></table>'
    )

    return "".join(parts)


def generate_feed(changes_latest, stats, source_file, repo_url):
    """Append a new RSS item to docs/feed.xml, keeping the last 50 items."""
    feed_path = "docs/feed.xml"
    site_url  = repo_url or "#"
    # Prefer the custom domain in docs/CNAME; fall back to the GitHub Pages URL
    # derived from the repo URL: github.com/user/repo → user.github.io/repo
    custom_domain = ""
    if os.path.exists("docs/CNAME"):
        with open("docs/CNAME", encoding="utf-8") as f:
            custom_domain = f.read().strip()
    if custom_domain:
        site_url = f"https://{custom_domain}/"
    else:
        import re as _re
        m = _re.search(r"github\.com/([^/]+)/([^/]+)$", repo_url or "")
        if m:
            site_url = f"https://{m.group(1)}.github.io/{m.group(2)}/"
    feed_url = site_url.rstrip("/") + "/feed.xml"

    source_ts = stats["source_ts"]
    pub_date  = _rss_date(source_ts)
    guid      = source_ts.replace(" ", "T") + "Z"

    if changes_latest:
        a = changes_latest.get("added_count",   0)
        r = changes_latest.get("removed_count", 0)
        u = changes_latest.get("updated_count", 0)
        title   = f"EAM Catalog {source_ts} — +{a:,} added, \u2212{r:,} removed, \u21BA{u:,} updated"
        desc_html = _feed_description(changes_latest, source_ts, site_url)
    else:
        title     = f"EAM Catalog {source_ts} — initial import ({stats['total']:,} packages)"
        desc_html = (
            f'{_FD_TABLE}>'
            f'<tr><td style="padding:14px 18px;background:{FD_UPDATED};font-family:{FD_FONT};">'
            f'<div style="font-size:16px;font-weight:600;color:#ffffff;">'
            f'Initial catalog import &nbsp;&middot;&nbsp; {_xml_escape(source_ts)}</div>'
            f'<div style="font-size:12px;color:#d7e7f8;">{stats["total"]:,} packages from '
            f'{stats["publishers"]:,} publishers</div></td></tr></table>'
        )

    new_item = (
        f"    <item>\n"
        f"      <title>{_xml_escape(title)}</title>\n"
        f"      <link>{_xml_escape(site_url)}</link>\n"
        f"      <pubDate>{pub_date}</pubDate>\n"
        f"      <guid isPermaLink='false'>{_xml_escape(guid)}</guid>\n"
        f"      <description><![CDATA[{desc_html}]]></description>\n"
        f"    </item>"
    )

    # Read existing items to avoid duplicates and cap at 50
    existing_items = []
    if os.path.exists(feed_path):
        with open(feed_path, encoding="utf-8") as f:
            raw = f.read()
        existing_items = re.findall(r"<item>.*?</item>", raw, re.DOTALL)
        # Drop any item with the same guid
        existing_items = [i for i in existing_items if guid not in i]

    all_items = [new_item] + existing_items[:49]  # newest first, max 50

    channel = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        f'  <channel>\n'
        f'    <title>Intune EAM App Catalog \u2014 Changes</title>\n'
        f'    <link>{_xml_escape(site_url)}</link>\n'
        f'    <description>Change feed for the Microsoft Intune EAM App Catalog</description>\n'
        f'    <language>en</language>\n'
        f'    <atom:link href="{_xml_escape(feed_url)}" rel="self" type="application/rss+xml"/>\n'
        f'    <lastBuildDate>{_rss_date(source_ts)}</lastBuildDate>\n'
        + "\n".join(all_items) + "\n"
        f'  </channel>\n'
        f'</rss>\n'
    )

    os.makedirs("docs", exist_ok=True)
    with open(feed_path, "w", encoding="utf-8") as f:
        f.write(channel)
    print(f"  docs/feed.xml             — {len(all_items)} item(s)")


# ---------------------------------------------------------------------------
# README.md — inject stats between HTML comment markers
# ---------------------------------------------------------------------------

def update_readme(stats):
    readme = "README.md"
    if not os.path.exists(readme):
        print("  README.md not found — skipping")
        return

    with open(readme, encoding="utf-8") as f:
        content = f.read()

    block = (
        f"| Metric | Value |\n"
        f"|--------|-------|\n"
        f"| Total Packages | **{stats['total']:,}** |\n"
        f"| Unique Products | {stats['unique_products']:,} |\n"
        f"| Publishers | {stats['publishers']:,} |\n"
        f"| Auto-Update Capable | {stats['auto_yes']:,} ({stats['auto_pct']}%) |\n"
        f"| Available Locales | {stats['locales']} |\n"
        f"| Last Export | {stats['source_ts']} |"
    )

    new_content = re.sub(
        r"<!-- CATALOG_STATS_START -->.*?<!-- CATALOG_STATS_END -->",
        f"<!-- CATALOG_STATS_START -->\n{block}\n<!-- CATALOG_STATS_END -->",
        content, flags=re.DOTALL,
    )

    if "<!-- CATALOG_STATS_START -->" not in content:
        print("  README.md              — stats markers not found, skipping")
        return

    if new_content == content:
        print("  README.md              — stats already current")
        return

    with open(readme, "w", encoding="utf-8") as f:
        f.write(new_content)
    print("  README.md              — stats block updated")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    files = all_catalog_files()
    if not files:
        print("ERROR: No *_AppCatalog.json files found in catalog/ or archive/")
        sys.exit(1)

    latest_file  = files[-1]
    current_apps = load_catalog(latest_file)

    print(f"Latest  : {latest_file}  ({len(current_apps):,} packages)")
    print(f"Files   : {len(files)} total\n")

    stats = generate_stats(current_apps, latest_file)
    generate_catalog(current_apps, latest_file)

    # Collect structured changes for catalog.json
    changes_data = {}

    if len(files) >= 2:
        previous_file = files[-2]
        print(f"Previous: {previous_file}")
        changes_data["latest"] = generate_changes(
            current_apps, load_catalog(previous_file), latest_file, previous_file
        )
    else:
        src_name = os.path.basename(latest_file)
        with open("changes.md", "w", encoding="utf-8") as f:
            f.write(
                "# Catalog Changes — Latest vs Previous\n\n"
                f"> **Initial import:** `{src_name}` (exported {filename_to_ts(latest_file)})\n\n"
                "This is the first catalog export — no previous version available for comparison.\n\n"
                "See [catalog.md](catalog.md) for the full app list "
                "and [stats.md](stats.md) for statistics.\n"
            )
        print("  changes.md             — first run")

    # Titles name the selection rule, not a fixed window: each period compares
    # against the *newest export at least N days old*, which can be considerably
    # older than N days when exports are sparse. The Span line states the truth.
    period_defs = [
        (1,  "daily",   "changes_daily.md",   "Catalog Changes — Daily (≥1 day apart)",    "daily"),
        (7,  "weekly",  "changes_weekly.md",  "Catalog Changes — Weekly (≥7 days apart)",  "weekly"),
        (30, "monthly", "changes_monthly.md", "Catalog Changes — Monthly (≥30 days apart)", "monthly"),
    ]
    for days, key, output, title, label in period_defs:
        result = generate_changes_period(current_apps, latest_file, files, days, output, title, label)
        if result is not None:
            changes_data[key] = result

    generate_catalog_json(current_apps, stats, latest_file)
    generate_changes_json(changes_data)
    generate_feed(changes_data.get("latest"), stats, latest_file, get_repo_url())
    update_readme(stats)
    print("\nDone.")


if __name__ == "__main__":
    main()
