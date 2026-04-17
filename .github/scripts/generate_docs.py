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

def _composite_key(a):
    """Stable composite key for exports that pre-date the branchId field."""
    return (a.get("productId", "") + "|" + a.get("branchDisplayName", "")).lower()


def _compute_changes(current_apps, previous_apps, current_file, previous_file):
    """Compute change sets. Returns (structured_dict, added, removed, updated_pairs)."""
    curr_has_branch = any(a.get("branchId") for a in current_apps)
    prev_has_branch = any(a.get("branchId") for a in previous_apps)

    if curr_has_branch and prev_has_branch:
        key_fn = lambda a: a.get("branchId") or _composite_key(a)
    else:
        key_fn = _composite_key

    curr_by_id = {key_fn(a): a for a in current_apps}
    prev_by_id = {key_fn(a): a for a in previous_apps}
    curr_ids, prev_ids = set(curr_by_id), set(prev_by_id)

    def sk(a):
        return (a.get("publisherDisplayName","").lower(), a.get("productDisplayName","").lower())

    added   = sorted([curr_by_id[i] for i in curr_ids - prev_ids], key=sk)
    removed = sorted([prev_by_id[i] for i in prev_ids - curr_ids], key=sk)
    updated_pairs = sorted(
        [(prev_by_id[i], curr_by_id[i]) for i in curr_ids & prev_ids
         if curr_by_id[i].get("versionDisplayName") != prev_by_id[i].get("versionDisplayName")],
        key=lambda x: sk(x[1]),
    )

    # Structured updated list: current app fields + prevVersionDisplayName
    updated_list = []
    for prev, curr in updated_pairs:
        entry = dict(curr)
        entry["prevVersionDisplayName"] = prev.get("versionDisplayName", "")
        updated_list.append(entry)

    structured = {
        "compared_to":    os.path.basename(previous_file),
        "compared_to_ts": filename_to_ts(previous_file),
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
    lines = [
        f"# {title}", "",
        f"> **Comparing:** `{os.path.basename(current_file)}` (exported {filename_to_ts(current_file)})  ",
        f"> **vs:** `{structured['compared_to']}` (exported {structured['compared_to_ts']})  ",
        f"> **Generated:** {now_utc()}", "",
        "## Summary", "",
        "| Change | Count |", "|--------|------:|",
        f"| ✅ Added | {len(added):,} |",
        f"| ❌ Removed | {len(removed):,} |",
        f"| 🔄 Updated (version change) | {len(updated_pairs):,} |", "",
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
                  "| Publisher | App | Branch | Previous Version | New Version | Architecture |",
                  "|-----------|-----|--------|:---------------:|:-----------:|:------------:|"]
        for prev, curr in updated_pairs:
            lines.append(f"| {curr.get('publisherDisplayName','')} | {curr.get('productDisplayName','')} "
                         f"| {curr.get('branchDisplayName','')} | `{prev.get('versionDisplayName','')}` "
                         f"| `{curr.get('versionDisplayName','')}` | {curr.get('applicableArchitectures','')} |")
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

def generate_catalog_json(apps, stats, source_file, changes=None):
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
        "apps":    sorted_apps,
        "changes": changes or {},
    }

    os.makedirs("docs", exist_ok=True)
    with open("docs/catalog.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  docs/catalog.json         — {stats['total']:,} packages")


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


def _feed_description(structured, source_ts):
    """Build a plain-text HTML snippet summarising the changes for the feed item."""
    a = structured.get("added_count",   0)
    r = structured.get("removed_count", 0)
    u = structured.get("updated_count", 0)

    lines = [
        f"<p><strong>Export:</strong> {_xml_escape(source_ts)}</p>",
        f"<p><strong>Compared to:</strong> {_xml_escape(structured.get('compared_to', ''))} "
        f"({_xml_escape(structured.get('compared_to_ts', ''))})</p>",
        f"<p>+{a:,} added &nbsp; &minus;{r:,} removed &nbsp; &#x21BA;{u:,} updated</p>",
    ]

    def pkg_list(items, max_show=20):
        shown = items[:max_show]
        rows = "".join(
            f"<li>{_xml_escape(p.get('publisherDisplayName',''))} — "
            f"{_xml_escape(p.get('productDisplayName',''))} "
            f"<code>{_xml_escape(p.get('versionDisplayName',''))}</code></li>"
            for p in shown
        )
        suffix = f"<li>… and {len(items) - max_show:,} more</li>" if len(items) > max_show else ""
        return f"<ul>{rows}{suffix}</ul>"

    if a:
        lines.append(f"<h3>Added ({a:,})</h3>" + pkg_list(structured.get("added", [])))
    if r:
        lines.append(f"<h3>Removed ({r:,})</h3>" + pkg_list(structured.get("removed", [])))
    if u:
        upd = structured.get("updated", [])
        rows = "".join(
            f"<li>{_xml_escape(p.get('publisherDisplayName',''))} — "
            f"{_xml_escape(p.get('productDisplayName',''))} &nbsp;"
            f"<code>{_xml_escape(p.get('prevVersionDisplayName',''))}</code> → "
            f"<code>{_xml_escape(p.get('versionDisplayName',''))}</code></li>"
            for p in upd[:20]
        )
        suffix = f"<li>… and {len(upd) - 20:,} more</li>" if len(upd) > 20 else ""
        lines.append(f"<h3>Updated ({u:,})</h3><ul>{rows}{suffix}</ul>")

    return "".join(lines)


def generate_feed(changes_latest, stats, source_file, repo_url):
    """Append a new RSS item to docs/feed.xml, keeping the last 50 items."""
    feed_path = "docs/feed.xml"
    site_url  = f"{repo_url.rstrip('/')}/".replace("github.com/", "danrung.github.io/") \
                if "github.com" in (repo_url or "") else (repo_url or "#")
    # Derive GitHub Pages URL from repo URL: github.com/user/repo → user.github.io/repo
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
        desc_html = _feed_description(changes_latest, source_ts)
    else:
        title     = f"EAM Catalog {source_ts} — initial import ({stats['total']:,} packages)"
        desc_html = f"<p>Initial catalog import: {stats['total']:,} packages from {stats['publishers']:,} publishers.</p>"

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

    if new_content == content:
        print("  README.md              — markers not found, skipping")
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

    period_defs = [
        (1,  "daily",   "changes_daily.md",   "Catalog Changes — Last 24 Hours", "daily"),
        (7,  "weekly",  "changes_weekly.md",  "Catalog Changes — Last 7 Days",   "weekly"),
        (30, "monthly", "changes_monthly.md", "Catalog Changes — Last 30 Days",  "monthly"),
    ]
    for days, key, output, title, label in period_defs:
        result = generate_changes_period(current_apps, latest_file, files, days, output, title, label)
        if result is not None:
            changes_data[key] = result

    generate_catalog_json(current_apps, stats, latest_file, changes=changes_data)
    generate_feed(changes_data.get("latest"), stats, latest_file, get_repo_url())
    update_readme(stats)
    print("\nDone.")


if __name__ == "__main__":
    main()
