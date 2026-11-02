#!/usr/bin/env python3
"""
Generate catalog.md, stats.md, changes*.md, docs/catalog.json, the static per-product
pages under docs/apps/, docs/sitemap.xml, and update README.md from *_AppCatalog.json
files found in catalog/ and archive/.

Static website files (docs/index.html, docs/app.css, docs/app.js) are committed once
and never regenerated — docs/catalog.json, docs/changes.json, docs/feed.xml,
docs/apps/ and docs/sitemap.xml are rewritten on each run.

Generated pages are written only when their content actually changes. What
changed, when each URL last changed, and the per-product change history the
pages render all live in docs/.pagestate.json, which is why the sitemap's
<lastmod> dates and the IndexNow submission can be trusted by a crawler.

Run from the repository root:
    python .github/scripts/generate_docs.py
"""

import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit


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


# ---------------------------------------------------------------------------
# docs/.pagestate.json — what every generated page looked like last run
#
# Two published signals depend on knowing whether a page's *content* actually
# moved. Sitemap <lastmod> is worthless to a crawler the moment it fires on
# every URL every day: it stamped all 900-odd product pages with the export
# date even though the only difference between two runs was the export
# timestamp printed in the footer, so a crawler that sampled a few of them
# learned the dates carry no information and deprioritised the rest. IndexNow
# has the same failure mode, louder. Both need a content fingerprint per URL.
#
# The file also carries the per-product change history the pages render, so
# that history survives without re-reading a few hundred megabytes of archived
# exports on every run.
#
# It lives under docs/ so it travels with the pages it describes, and its name
# starts with a dot so GitHub Pages does not publish it.
# ---------------------------------------------------------------------------

STATE_PATH = "docs/.pagestate.json"

# Bumped when the history gains something old entries cannot be patched up to
# carry. Version 2 added first-seen dates and the package, architecture and
# locale detail of each entry; a file older than this has its history dropped
# and re-seeded from the archive on the next run. Page fingerprints are kept.
STATE_VERSION = 2

# Entries shown per product. Long enough to show a real release cadence, short
# enough that a page stays a page rather than a changelog dump. The stored log
# is not capped: the release-cadence figures are counted from it, and a capped
# log undercounts exactly the products that move most.
HISTORY_MAX = 24

# How many of the archived exports to walk when the history is empty and has to
# be seeded. Zero means all of them, which is the default because the whole
# archive currently costs a few seconds and a product whose page shows no
# history at all is exactly the thin page this is meant to fix. Set
# HISTORY_SEED to a positive number to bound the backfill if the archive ever
# grows past the point where that is comfortable.
HISTORY_SEED = int(os.environ.get("HISTORY_SEED", "0"))

# IndexNow accepts at most 10,000 URLs in a single submission.
INDEXNOW_MAX = 10000


def _load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    state.setdefault("version", 1)
    state.setdefault("pages", {})     # absolute URL -> {hash, exact, lastmod}
    state.setdefault("history", {})   # slug -> [entry, ...] oldest first
    state.setdefault("first_seen", {})         # slug -> first export it was in
    state.setdefault("first_seen_after", {})   # slug -> export before that, if far
    state.setdefault("history_from", None)     # oldest export the history saw
    state.setdefault("history_through", None)  # newest export already folded in
    if state["version"] < STATE_VERSION:
        state.update(version=STATE_VERSION, history={}, first_seen={},
                     first_seen_after={}, history_from=None, history_through=None)
    return state


def _save_state(state):
    """Written indented and key-sorted, not minified.

    It is committed on every export, and the one thing anyone will ever want to
    check in a diff is which dates moved — which is unreadable as a single
    400 KB line. Indenting also gives git something to delta against.
    """
    os.makedirs("docs", exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, sort_keys=True, indent=1)
        f.write("\n")


# The export timestamp is stamped into pages that are otherwise byte-identical
# run to run. Folding it into the digest would mark every page as changed every
# day — the exact false signal this machinery exists to remove — so it is
# blanked before the hash is taken. Bare YYYY-MM-DD dates are left alone: those
# are history entries, and they are real content.
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?( UTC)?")


def _fingerprint(html):
    """Digest of a page's meaningful content, export timestamp excluded."""
    return hashlib.sha1(_TS_RE.sub("", html).encode("utf-8")).hexdigest()


def _disk_digest(path):
    """Digest of the file as it stands, or None if it is not there.

    Read as text, not bytes, so it is comparable with the digest of the string
    that was written: Windows translates newlines on the way out, which makes a
    byte-for-byte comparison report every page as stale on every local run.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return hashlib.sha1(f.read().encode("utf-8")).hexdigest()
    except OSError:
        return None


def _write_page(path, html, url, state, today, changed):
    """Write a generated page, and record whether its content actually moved.

    Two digests, because the two questions are different. Whether to write the
    file at all is settled against the bytes on disk — cheap, and self-healing
    if the state file and the working tree ever drift apart. Whether the URL
    counts as *changed* is settled by `hash`, which ignores the export
    timestamp; that is what feeds <lastmod> and IndexNow, so a page whose only
    difference is a printed date is rewritten without claiming to have changed.
    """
    prev  = state["pages"].get(url) or {}
    exact = hashlib.sha1(html.encode("utf-8")).hexdigest()
    content = _fingerprint(html)
    moved = prev.get("hash") != content
    wrote = _disk_digest(path) != exact
    if wrote:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
    if moved:
        changed.append(url)
    state["pages"][url] = {
        "hash":    content,
        "exact":   exact,
        "lastmod": today if moved else (prev.get("lastmod") or today),
    }
    return wrote


def _track_static(path, url, state, today, changed):
    """Give a hand-maintained page the same honest lastmod as a generated one.

    docs/index.html is committed by hand and never regenerated, so there is
    nothing to write — but its sitemap entry should still move on the day it is
    edited rather than on every export, which means fingerprinting the file as
    it currently stands.
    """
    try:
        with open(path, encoding="utf-8") as f:
            html = f.read()
    except OSError:
        return
    prev = state["pages"].get(url) or {}
    content = _fingerprint(html)
    moved = prev.get("hash") != content
    if moved:
        changed.append(url)
    state["pages"][url] = {
        "hash":    content,
        "exact":   content,
        "lastmod": today if moved else (prev.get("lastmod") or today),
    }


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


def get_site_url(repo_url):
    """Public base URL of the website, with trailing slash.

    Prefers the custom domain in docs/CNAME; falls back to the GitHub Pages URL
    derived from the repo URL: github.com/user/repo → user.github.io/repo.
    """
    if os.path.exists("docs/CNAME"):
        with open("docs/CNAME", encoding="utf-8") as f:
            domain = f.read().strip()
        if domain:
            return f"https://{domain}/"
    m = re.search(r"github\.com/([^/]+)/([^/]+)$", repo_url or "")
    if m:
        return f"https://{m.group(1)}.github.io/{m.group(2)}/"
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

    # Branch and version break the tie between packages of one product, which
    # otherwise came out in set order — different on every run.
    def sk(a):
        return (a.get("publisherDisplayName","").lower(), a.get("productDisplayName","").lower(),
                (a.get("branchDisplayName") or "").lower(), _version_key(a.get("versionDisplayName")))

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

def generate_catalog_json(apps, stats, source_file, slug_by_key=None):
    sorted_apps = sorted(
        apps,
        key=lambda a: (
            a.get("publisherDisplayName", "").lower(),
            a.get("productDisplayName", "").lower(),
        ),
    )

    # Each row carries the slug of its product page, so the catalog table can
    # link every app to its own URL without rebuilding the slug rules in JS.
    # Copies, not mutations — the loaded export is still read further down.
    if slug_by_key:
        sorted_apps = [dict(a, slug=slug_by_key.get(_product_key(a), ""))
                       for a in sorted_apps]

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


def generate_summary_json(stats, changes, slug_by_key=None):
    """A few hundred bytes of headline numbers for other sites to embed
    (drung.dev shows them on its project page). catalog.json and changes.json
    are far too large for a one-line stat, so this carries only the counts,
    the change totals per period and the most recent additions — no package
    lists. Served with CORS by GitHub Pages like everything else in docs/."""
    def period(key):
        p = (changes or {}).get(key)
        if not p:
            return None
        return {
            "compared_to_ts": p.get("compared_to_ts"),
            "span_label":     p.get("span_label"),
            "added_count":    p.get("added_count", 0),
            "removed_count":  p.get("removed_count", 0),
            "updated_count":  p.get("updated_count", 0),
        }

    recent = []
    for a in ((changes or {}).get("monthly") or {}).get("added", [])[:3]:
        recent.append({
            "name":      a.get("productDisplayName", ""),
            "publisher": a.get("publisherDisplayName", ""),
            "slug":      (slug_by_key or {}).get(_product_key(a), ""),
        })

    payload = {
        "source_ts":       stats["source_ts"],
        "generated":       now_utc(),
        "total":           stats["total"],
        "unique_products": stats["unique_products"],
        "publishers":      stats["publishers"],
        "auto_pct":        stats["auto_pct"],
        "locales":         stats["locales"],
        "latest":          period("latest"),
        "daily":           period("daily"),
        "weekly":          period("weekly"),
        "monthly":         period("monthly"),
        "recent_added":    recent,
    }
    os.makedirs("docs", exist_ok=True)
    with open("docs/summary.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  docs/summary.json         — {os.path.getsize('docs/summary.json'):,} bytes")


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


FD_LINK = FD_UPDATED   # product names link to their pages in the accent colour

_FD_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_FD_MONTHS   = ("January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December")

# Item kinds in the order a title, a preview line and the body list them.
_FEED_KINDS = (("Added", "added"), ("Updated", "updated"), ("Removed", "removed"))

# Long enough for three app names in the common case; past it, fewer names.
_FEED_TITLE_MAX = 110


def _feed_has_changes(structured):
    return any(structured.get(key) for _, key in _FEED_KINDS)


def _feed_when(ts):
    """'2026-10-31 17:07:15' -> 'Saturday, 31 October 2026 · 17:07 UTC' (HTML).

    Spelled out by hand rather than with %A/%B, which follow the runner's
    locale.
    """
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return _xml_escape(ts)
    return (f"{_FD_WEEKDAYS[dt.weekday()]}, {dt.day} {_FD_MONTHS[dt.month - 1]} "
            f"{dt.year} &middot; {dt:%H:%M} UTC")


def _fd_names(items, limit):
    """Distinct product names in order, as 'A, B, C and 4 more' (plain text)."""
    names = []
    for p in items:
        n = p.get("productDisplayName") or ""
        if n and n not in names:
            names.append(n)
    rest = len(names) - limit
    return ", ".join(names[:limit]) + (f" and {rest:,} more" if rest > 0 else "")


def _feed_title(structured):
    """Item title that names what changed.

    Outlook files each item as a mail and cuts the subject after a few dozen
    characters, so the app names lead. The date already has its own column and
    the package counts are in the body; neither earns the space up front.
    """
    groups = [(label, structured.get(key)) for label, key in _FEED_KINDS
              if structured.get(key)]
    title = ""
    for limit in (3, 2, 1):
        title = " · ".join(f"{label}: {_fd_names(items, limit)}" for label, items in groups)
        if len(title) <= _FEED_TITLE_MAX:
            break
    return title


def _feed_preview(structured, limit=4):
    """The line a reader shows under the title: what moved, with versions."""
    bits = []
    for label, key in _FEED_KINDS:
        seen = set()
        for p in structured.get(key) or []:
            name = p.get("productDisplayName") or ""
            if not name or name in seen:
                continue
            seen.add(name)
            ver  = p.get("versionDisplayName") or ""
            prev = p.get("prevVersionDisplayName") or ""
            if key == "updated" and prev and ver and prev != ver:
                bits.append(f"{name} {prev} → {ver}")
            elif key == "updated":
                fields = [c.get("label") for c in p.get("changes", []) if c.get("label")]
                bits.append(f"{name}: {', '.join(fields) or 'entry'} changed")
            else:
                bits.append(" ".join(x for x in (name, ver, label.lower()) if x))
    rest = len(bits) - limit
    return " · ".join(bits[:limit]) + (f" · and {rest:,} more" if rest > 0 else "")


def _fd_product(p, slug_by_key, pages_url):
    """Product name, linked to its page while the product still has one."""
    name = _xml_escape(p.get("productDisplayName", ""))
    slug = (slug_by_key or {}).get(_product_key(p))
    if not (slug and pages_url):
        return name
    return (f'<a href="{_xml_escape(pages_url)}apps/{slug}.html" '
            f'style="color:{FD_LINK};text-decoration:none;">{name}</a>')


def _fd_header(when_html, sub_html, preview=""):
    """Hidden preview line, then brand, date and one line of context.

    The preview comes first because Outlook and most readers show the opening
    text of an item under its title; without it that was the header, which
    only repeated the title. No filled colour band: Outlook's dark mode
    re-tints backgrounds, and the white-on-blue header came out as dark text on
    pale blue. Text under an accent rule survives the re-tint.
    """
    pre = (f'<div style="display:none;font-size:1px;line-height:1px;max-height:0;'
           f'max-width:0;opacity:0;overflow:hidden;mso-hide:all;">'
           f"{_xml_escape(preview)}</div>" if preview else "")
    cell = f"font-family:{FD_FONT};"
    return (
        f"{pre}{_FD_TABLE}>"
        f'<tr><td style="{cell}padding:14px 0 0;border-top:3px solid {FD_UPDATED};'
        f'font-size:11px;font-weight:700;letter-spacing:.08em;color:{FD_UPDATED};">'
        "INTUNE EAM APP CATALOG</td></tr>"
        f'<tr><td style="{cell}padding:6px 0 0;font-size:19px;font-weight:600;'
        f'line-height:26px;color:{FD_INK};">{when_html}</td></tr>'
        f'<tr><td style="{cell}padding:2px 0 0;font-size:13px;color:{FD_DIM};">'
        f"{sub_html}</td></tr>"
        "</table>"
    )


def _fd_footer(site_url, pages_url):
    link = f"color:{FD_LINK};font-weight:600;text-decoration:none;"
    changes = (f'<a href="{_xml_escape(pages_url)}#changes" style="{link}">'
               "All recent changes &#8594;</a>"
               f'<span style="color:{FD_DIM};">&nbsp;&nbsp;&middot;&nbsp;&nbsp;</span>'
               if pages_url else "")
    return (
        f"{_FD_TABLE}>"
        f'<tr><td style="padding:22px 0 0;font-family:{FD_FONT};font-size:13px;">'
        f'{changes}<a href="{_xml_escape(site_url)}" style="{link}">Browse the catalog</a>'
        "</td></tr>"
        f'<tr><td style="padding:10px 0 0;font-family:{FD_FONT};font-size:11px;'
        f'color:{FD_DIM};">Independent project built from the Microsoft Graph API '
        "&mdash; not affiliated with Microsoft.</td></tr></table>"
    )


def _feed_description(structured, source_ts, site_url="#", pages_url="",
                      slug_by_key=None, max_show=20):
    """Build the HTML body of a feed item — readable in feed readers and in Outlook."""
    a = structured.get("added_count",   0)
    r = structured.get("removed_count", 0)
    u = structured.get("updated_count", 0)

    ver = lambda p, key: _xml_escape(p.get(key, "")) or "&mdash;"
    product = lambda p: _fd_product(p, slug_by_key, pages_url)

    def grouped(items, keys):
        """Packages that moved identically, as one group each, in order.

        Firefox ships one package per locale; 47 identical rows would push
        every other app past the cut-off, and even two read as a duplicate.
        """
        groups = {}
        for p in items:
            groups.setdefault(tuple(p.get(k) for k in keys), []).append(p)
        return list(groups.values())

    def count_note(g, groups):
        """'2 packages', or — when the product has rows of its own on either
        side (CMake 4.3 and 4.4 both moving) — the branch that tells them apart."""
        if len(g) > 1:
            return f"{len(g):,} packages"
        name = g[0].get("productDisplayName")
        if sum(1 for h in groups if h[0].get("productDisplayName") == name) > 1:
            return _xml_escape(g[0].get("branchDisplayName", ""))
        return ""

    def simple_rows(items, color_version=FD_INK):
        groups = grouped(items, ("productDisplayName", "publisherDisplayName",
                                 "versionDisplayName"))
        rows = "".join(
            _fd_row(
                product(g[0]),
                _xml_escape(g[0].get("publisherDisplayName", "")),
                f'<span style="color:{color_version};">{ver(g[0], "versionDisplayName")}</span>',
                note=count_note(g, groups),
            )
            for g in groups[:max_show]
        )
        return rows + _fd_more(max_show, len(groups))

    def updated_rows(items):
        groups = grouped(items, ("productDisplayName", "publisherDisplayName",
                                 "prevVersionDisplayName", "versionDisplayName"))
        rows = ""
        for g in groups[:max_show]:
            p = g[0]
            labels = []
            for q in g:
                for c in q.get("changes", []):
                    if c["field"] != "versionDisplayName" and c["label"] not in labels:
                        labels.append(c["label"])
            note = " &middot; ".join(x for x in (
                count_note(g, groups), ("also " + _xml_escape(", ".join(labels))) if labels else "",
            ) if x)
            rows += _fd_row(
                product(p),
                _xml_escape(p.get("publisherDisplayName", "")),
                f'<span style="color:{FD_DIM};">{ver(p, "prevVersionDisplayName")}</span>'
                f'<span style="color:{FD_DIM};">&nbsp;&#8594;&nbsp;</span>'
                f'<span style="color:{FD_UPDATED};font-weight:700;">{ver(p, "versionDisplayName")}</span>',
                note=note,
            )
        return rows + _fd_more(max_show, len(groups))

    span = structured.get("span_label")
    sub = (f"Compared with the previous export, {_xml_escape(span)} earlier."
           if span else "Compared with the previous export.")
    # Only the counters that moved — "+0 added, −0 removed" said nothing — and
    # only when there are two kinds to compare: a lone "3 UPDATED" just repeats
    # the section heading under it.
    moved = [(value, label, color) for n, value, label, color in (
        (a, f"+{a:,}",        "ADDED",   FD_ADDED),
        (r, f"&minus;{r:,}",  "REMOVED", FD_REMOVED),
        (u, f"{u:,}",         "UPDATED", FD_UPDATED),
    ) if n]
    parts = [_fd_header(_feed_when(source_ts), sub, _feed_preview(structured))]
    if len(moved) > 1:
        parts.append(
            f'{_FD_TABLE}><tr><td style="padding:14px 0 0;">'
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
            + "".join(_fd_counter(*m) for m in moved)
            + "</tr></table></td></tr></table>"
        )

    if a:
        parts.append(_fd_section("Added",   a, FD_ADDED,   simple_rows(structured.get("added", []), FD_ADDED)))
    if r:
        parts.append(_fd_section("Removed", r, FD_REMOVED, simple_rows(structured.get("removed", []), FD_REMOVED)))
    if u:
        parts.append(_fd_section("Updated", u, FD_UPDATED, updated_rows(structured.get("updated", []))))

    parts.append(_fd_footer(site_url, pages_url))
    return "".join(parts)


FEED_PATH  = "docs/feed.xml"
FEED_MAX   = 50
# Short enough to fit the sender column Outlook shows it in.
FEED_TITLE = "Intune EAM App Catalog"

# Written into the feed so the next run knows whose items it is carrying. A
# feed without this marker — written by an older version of this script — has
# its items rebuilt from the archived exports rather than carried forward, so a
# change to how items look reaches the whole feed at once instead of one
# export at a time. Guids are the export timestamps either way, which is what
# keeps a subscribed reader from listing a rebuilt item a second time.
FEED_FORMAT = 2
_FEED_MARK  = f"<!-- feed-format {FEED_FORMAT} -->"


def _feed_guid(source_ts):
    return source_ts.replace(" ", "T") + "Z"


def _feed_item_xml(title, source_ts, link, desc_html, categories=()):
    cats = "".join(f"      <category>{c}</category>\n" for c in categories)
    return (
        "<item>\n"
        f"      <title>{_xml_escape(title)}</title>\n"
        f"      <link>{_xml_escape(link)}</link>\n"
        f"      <pubDate>{_rss_date(source_ts)}</pubDate>\n"
        f"      <guid isPermaLink='false'>{_xml_escape(_feed_guid(source_ts))}</guid>\n"
        f"{cats}"
        f"      <description><![CDATA[{desc_html}]]></description>\n"
        "    </item>"
    )


def _feed_item(structured, source_ts, site_url, pages_url, slug_by_key):
    """One <item>, for an export that changed something."""
    return _feed_item_xml(
        _feed_title(structured), source_ts,
        f"{pages_url}#changes" if pages_url else site_url,
        _feed_description(structured, source_ts, site_url, pages_url, slug_by_key),
        [label for label, key in _FEED_KINDS if structured.get(key)],
    )


def _feed_initial_item(stats, site_url, pages_url):
    title = (f"Initial import: {stats['total']:,} packages from "
             f"{stats['publishers']:,} publishers")
    desc = (_fd_header(_feed_when(stats["source_ts"]),
                       "First catalog export &mdash; nothing to compare against yet.",
                       title)
            + _fd_footer(site_url, pages_url))
    return _feed_item_xml(title, stats["source_ts"], site_url, desc)


def _rebuild_feed_items(files, site_url, pages_url, slug_by_key, floor=None):
    """The items the feed should carry, rebuilt from the archived exports.

    Walks back from the newest pair of exports until FEED_MAX of them turn out
    to have changed something, or until `floor` — the oldest export the old
    feed still carried. Each pair is the same one the item was first written
    from, so the guids — and the dates readers sort by — come out as they
    were.
    """
    items = []
    curr_file = files[-1]
    curr_apps = load_catalog(curr_file)
    for prev_file in reversed(files[:-1]):
        if len(items) >= FEED_MAX or (floor and filename_to_ts(curr_file) < floor):
            break
        prev_apps = load_catalog(prev_file)
        structured, *_ = _compute_changes(curr_apps, prev_apps, curr_file, prev_file)
        if _feed_has_changes(structured):
            items.append(_feed_item(structured, filename_to_ts(curr_file),
                                    site_url, pages_url, slug_by_key))
        curr_file, curr_apps = prev_file, prev_apps
    return items


def generate_feed(changes_latest, stats, files, repo_url, slug_by_key=None):
    """Write docs/feed.xml: one item per export that changed something, newest
    first, at most FEED_MAX of them.

    An export that changed nothing adds no item. It used to add one reading
    "+0 added, −0 removed, 0 updated" — a quarter of the feed, landing in
    every subscriber's Outlook twice a day.
    """
    pages_url = get_site_url(repo_url)
    site_url  = pages_url or repo_url or "#"
    feed_url  = site_url.rstrip("/") + "/feed.xml"
    source_ts = stats["source_ts"]

    raw = ""
    if os.path.exists(FEED_PATH):
        with open(FEED_PATH, encoding="utf-8") as f:
            raw = f.read()

    if changes_latest is None:
        items = [_feed_initial_item(stats, site_url, pages_url)]
        how = "initial import"
    elif _FEED_MARK not in raw:
        # Only as far back as the old feed reached. Going further would hand
        # every subscriber a batch of unread items dated weeks ago; the feed
        # grows back to FEED_MAX on its own.
        old = re.findall(r">(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})Z</guid>", raw)
        floor = min(f"{d} {t}" for d, t in old) if old else None
        items = _rebuild_feed_items(files, site_url, pages_url, slug_by_key, floor)
        how = f"rebuilt from the archive (format {FEED_FORMAT})"
    else:
        # Re-running an export replaces its item rather than stacking a second.
        guid = _feed_guid(source_ts)
        items = [i for i in re.findall(r"<item>.*?</item>", raw, re.DOTALL) if guid not in i]
        if _feed_has_changes(changes_latest):
            items.insert(0, _feed_item(changes_latest, source_ts, site_url,
                                       pages_url, slug_by_key))
            how = "1 new"
        else:
            how = "no changes, no new item"
    items = items[:FEED_MAX]

    # The newest item's date, not the export's: an export that adds no item
    # leaves the file byte-identical, so there is nothing for git to commit.
    m = re.search(r"<pubDate>(.*?)</pubDate>", items[0]) if items else None
    built = m.group(1) if m else _rss_date(source_ts)

    # Feed readers that show a channel icon (Feedly, Inoreader, Thunderbird)
    # take it from here. The spec caps the declared width at 144.
    image = (
        "    <image>\n"
        f"      <url>{_xml_escape(pages_url)}apple-touch-icon.png</url>\n"
        f"      <title>{FEED_TITLE}</title>\n"
        f"      <link>{_xml_escape(pages_url)}</link>\n"
        "      <width>144</width>\n"
        "      <height>144</height>\n"
        "    </image>\n"
        if pages_url else ""
    )
    channel = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"{_FEED_MARK}\n"
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        f"    <title>{FEED_TITLE}</title>\n"
        f"    <link>{_xml_escape(site_url)}</link>\n"
        "    <description>New, updated and removed apps in the Microsoft Intune "
        "Enterprise App Management (EAM) catalog, published after every catalog "
        "export that changes something.</description>\n"
        "    <language>en</language>\n"
        "    <category>Microsoft Intune</category>\n"
        # Exports land twice a day; asking readers to poll more often than
        # that only costs them requests.
        "    <ttl>360</ttl>\n"
        f"{image}"
        f'    <atom:link href="{_xml_escape(feed_url)}" rel="self" type="application/rss+xml"/>\n'
        f"    <lastBuildDate>{built}</lastBuildDate>\n"
        + "".join(f"    {i}\n" for i in items)
        + "  </channel>\n"
        "</rss>\n"
    )

    os.makedirs("docs", exist_ok=True)
    with open(FEED_PATH, "w", encoding="utf-8") as f:
        f.write(channel)
    print(f"  docs/feed.xml             — {len(items)} item(s), {how}")


# ---------------------------------------------------------------------------
# docs/apps/ — static per-product pages + index, crawlable without JavaScript.
# The SPA renders the catalog client-side, which search engines index poorly;
# these pages give every product a stable URL with real HTML content.
# ---------------------------------------------------------------------------

# Single-line copy of the theme bootstrap inlined in docs/index.html.
_THEME_SCRIPT = (
    "(function(){var t=null;try{t=localStorage.getItem('theme')}catch(e){}"
    "if(t!=='light'&&t!=='dark'){t=window.matchMedia('(prefers-color-scheme: dark)')"
    ".matches?'dark':'light'}document.documentElement.dataset.theme=t}());"
)

# App pages live in docs/apps/, so the shared icon sits one level up.
_FAVICON = "../favicon.svg"

# Same Cloudflare Web Analytics beacon as docs/index.html — covered by the
# privacy notice in the imprint on the main page.
_CF_BEACON = (
    '<script type=\'module\' src=\'https://static.cloudflareinsights.com/beacon.min.js\' '
    'data-cf-beacon=\'{"token": "2905f0a2f65147db9c879ca2702b1743"}\'></script>'
)

# SVG icons copied from docs/index.html so both look identical.
_ICON_GITHUB = (
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">'
    '<path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 '
    "0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695"
    "-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99"
    ".105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225"
    "-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405"
    "c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 "
    "0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 "
    '0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z"/></svg>'
)
_ICON_SUN = (
    '<svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="12" cy="12" r="4"/>'
    '<path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2'
    'M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'
)
_ICON_MOON = (
    '<svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>'
)

_TOPNAV = (
    '<nav class="topnav"><div class="topnav-inner">'
    '<a class="nav-btn" href="../">Catalog</a>'
    '<a class="nav-btn" href="../#stats">Statistics</a>'
    '<a class="nav-btn" href="../#changes">Changes</a>'
    '<a class="nav-btn" href="../#docs">Docs</a>'
    "</div></nav>"
)

# Wires the theme toggle; the initial theme is resolved by _THEME_SCRIPT in <head>.
_TOGGLE_SCRIPT = (
    "<script>(function(){var r=document.documentElement,"
    "b=document.getElementById('theme-toggle');if(!b)return;"
    "b.addEventListener('click',function(){"
    "var n=r.dataset.theme==='dark'?'light':'dark';r.dataset.theme=n;"
    "try{localStorage.setItem('theme',n)}catch(e){}});}());</script>"
)


def _page_header(repo_url):
    gh = (
        f'<a class="header-link" href="{_xml_escape(repo_url)}" target="_blank" '
        f'rel="noopener">{_ICON_GITHUB} GitHub</a>'
    ) if repo_url else ""
    return (
        '<header><div class="header-inner"><div class="header-brand">'
        # The mark itself comes from app.css (logo.svg / logo-dark.svg), which
        # resolves to the same file from /index.html and from /apps/*.html.
        '<div class="header-logo" aria-hidden="true"></div>'
        '<div><div class="header-title"><a href="../">Intune EAM App Catalog</a></div>'
        '<div class="header-sub">Microsoft Intune Enterprise Application Management</div>'
        "</div></div>"
        '<div class="header-meta">'
        '<button class="theme-toggle" id="theme-toggle" type="button" '
        'aria-label="Switch between light and dark theme" title="Toggle theme">'
        f"{_ICON_SUN}{_ICON_MOON}</button>"
        f"{gh}"
        "</div></div></header>"
    )


def _page_footer(repo_url):
    gh = (
        f'<a href="{_xml_escape(repo_url)}" target="_blank" rel="noopener">View on GitHub</a>'
        '<span class="footer-sep">&middot;</span>'
    ) if repo_url else ""
    return (
        '<footer><span>Made with <span class="heart">&#9829;</span> by Daniel Rung</span>'
        "<span>Data from the Microsoft Graph API &mdash; "
        "<code>win32MobileAppCatalogPackage</code></span>"
        f'<div class="footer-links"><a href="./">All Apps</a>'
        f'<span class="footer-sep">&middot;</span>{gh}'
        '<a href="../#imprint">Legal Notice</a></div></footer>'
    )


def _slugify(s):
    """URL slug from a display name; accents folded, everything else hyphenated."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "app"


def _version_key(v):
    """Sort key that orders '9.0' before '10.1' inside a version string."""
    return [(0, int(p)) if p.isdigit() else (1, p.lower())
            for p in re.split(r"(\d+)", v or "")]


def _product_key(a):
    return a.get("productId") or (
        (a.get("publisherDisplayName") or "") + "|" + (a.get("productDisplayName") or "")
    )


def _build_slug_map(apps):
    """Group packages by product and hand each group its page slug.

    Returns (slug_map, slug_by_key): slug -> its packages, and product key ->
    slug, so catalog.json can point the catalog table at the very same URLs the
    pages are written to. Slugs are assigned in a deterministic order so name
    collisions keep the same ordinal suffix run after run.
    """
    groups = {}
    for a in apps:
        groups.setdefault(_product_key(a), []).append(a)

    def gname(key):
        p = groups[key][0]
        return ((p.get("publisherDisplayName") or "").lower(),
                (p.get("productDisplayName") or "").lower())

    slug_map, slug_by_key = {}, {}
    for key in sorted(groups, key=gname):
        g = groups[key]
        pub, name = g[0].get("publisherDisplayName", ""), g[0].get("productDisplayName", "")
        # Skip the publisher prefix when the product name already starts with it
        # ("Mozilla" + "Mozilla Firefox" → mozilla-firefox, not mozilla-mozilla-firefox).
        base = _slugify(name if name.lower().startswith(pub.lower()) else f"{pub}-{name}")
        slug, i = base, 2
        while slug in slug_map:
            slug = f"{base}-{i}"
            i += 1
        slug_map[slug] = g
        slug_by_key[key] = slug
    return slug_map, slug_by_key


# Search engines cut a description off past ~160 characters, and the SEO
# checkers flag anything outside 25–160. Product names in this catalog run long
# enough that a fixed template cannot be trusted to stay inside that, so the
# descriptions are assembled to fit rather than written and hoped for.
_META_DESC_MAX = 160


def _meta_desc(head, *clauses, limit=_META_DESC_MAX):
    """Join head and clauses into a meta description that fits `limit`.

    Renders as "head — clause, clause." Trailing clauses are dropped one at a
    time until the text fits, so pass them least-important last; a head long
    enough to overrun on its own is cut back to a word boundary.
    """
    clauses = list(clauses)
    while True:
        s = head + (" — " + ", ".join(clauses) if clauses else "") + "."
        if len(s) <= limit or not clauses:
            break
        clauses.pop()
    if len(s) > limit:
        s = head[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,—-") + "…"
    return s


# Bing warns on any <title> past 70 characters, and product names in this
# catalog run long enough that a fixed "{product} by {publisher} — site" template
# cannot be trusted to stay inside that. Same approach as the descriptions:
# assembled to fit, dropping the least important part first — the publisher goes
# before the site name, and the product name itself is only cut as a last resort.
_TITLE_MAX = 70


def _page_title(product, publisher):
    """Longest variant of the app-page <title> that fits `_TITLE_MAX`."""
    for title in (
        f"{product} by {publisher} — Intune EAM App Catalog",
        f"{product} — Intune EAM App Catalog",
        f"{product} — Intune EAM",
        product,
    ):
        if len(title) <= _TITLE_MAX:
            return title
    # A product name too long even on its own is cut in the middle, not at the
    # end: the tail is where such names differ ("… Update 1607" vs "… Update
    # 1809"), and cutting it would leave several pages with the same title.
    words = product.split()
    head, tail = words[:len(words) // 2], words[len(words) // 2:]
    while head and len(" ".join(head + ["…"] + tail)) > _TITLE_MAX:
        head.pop()
    if head:
        return " ".join(head + ["…"] + tail)
    return product[:_TITLE_MAX - 1].rsplit(" ", 1)[0].rstrip(" ,—-") + "…"


def _static_page(title, desc, canonical, body_html, extra_head="", repo_url="",
                 site_url=""):
    canon = f'\n  <link rel="canonical" href="{_xml_escape(canonical)}" />' if canonical else ""
    # Link previews (Teams, Slack, LinkedIn, Mastodon) read Open Graph, not the
    # canonical tag. Absolute URLs only — a relative og:image is ignored.
    og = (
        '\n  <meta property="og:type" content="website" />'
        '\n  <meta property="og:site_name" content="Intune EAM App Catalog" />'
        f'\n  <meta property="og:title" content="{_xml_escape(title)}" />'
        f'\n  <meta property="og:description" content="{_xml_escape(desc)}" />'
        + (f'\n  <meta property="og:url" content="{_xml_escape(canonical)}" />'
           if canonical else "")
        + (f'\n  <meta property="og:image" content="{_xml_escape(site_url)}og-image.png" />'
           '\n  <meta property="og:image:width" content="1200" />'
           '\n  <meta property="og:image:height" content="630" />'
           '\n  <meta property="og:image:alt" content="Intune EAM App Catalog" />'
           '\n  <meta name="twitter:card" content="summary_large_image" />'
           if site_url else '\n  <meta name="twitter:card" content="summary" />')
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="UTF-8" />\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
        '  <meta name="color-scheme" content="light dark" />\n'
        f"  <title>{_xml_escape(title)}</title>\n"
        f'  <meta name="description" content="{_xml_escape(desc)}" />'
        f"{canon}{og}\n"
        f"  <script>{_THEME_SCRIPT}</script>\n"
        f'  <link rel="icon" href="{_FAVICON}" type="image/svg+xml" />\n'
        '  <link rel="stylesheet" href="../app.css" />\n'
        f"  {_CF_BEACON}"
        f"{extra_head}\n"
        "</head>\n"
        '<body>\n<div id="app">\n'
        f"{_page_header(repo_url)}\n{_TOPNAV}\n"
        '<main><div class="docs-container app-page">\n'
        f"{body_html}\n"
        "</div></main>\n"
        f"{_page_footer(repo_url)}\n"
        "</div>\n"
        f"{_TOGGLE_SCRIPT}\n"
        "</body>\n</html>\n"
    )


# Width order an admin expects, rather than whatever order the export used.
_ARCH_ORDER = ("x86", "x64", "arm", "arm64")


def _arch_tags(arch):
    """Architecture string ("x86,x64") as the same tags the catalog view uses."""
    parts = [a.strip() for a in (arch or "").split(",") if a.strip()]
    if not parts:
        return '<span class="app-dash">&mdash;</span>'
    return "".join(f'<span class="tag tag-arch">{_xml_escape(a)}</span>' for a in parts)


def _locale_tags(locales, limit=None):
    """Locale tags. With a limit, the overflow collapses into a "+N" tag —
    Firefox alone ships 47 locales, which would swamp the summary panel."""
    parts = [l for l in (locales or []) if l]
    if not parts:
        return '<span class="app-dash">&mdash;</span>'
    rest = ""
    if limit and len(parts) > limit:
        rest = f'<span class="tag tag-locale tag-more">+{len(parts) - limit}</span>'
        parts = parts[:limit]
    return "".join(
        f'<span class="tag tag-locale">{_xml_escape(l)}</span>' for l in parts
    ) + rest


def _auto_badge(capable, label=None):
    cls = "badge-yes" if capable else "badge-no"
    return f'<span class="badge {cls}">{label or ("Yes" if capable else "No")}</span>'


def _fact(term, value):
    return f'<div class="app-fact"><dt>{term}</dt><dd>{value}</dd></div>'


# Kept short on purpose: enough to act on, not a second manual.
# Microsoft's own page for the feature. Linked from the app pages and the app
# index so a visitor who landed here from a search can get to the authoritative
# documentation without going back through a search engine.
_LEARN_URL = ("https://learn.microsoft.com/en-us/intune/app-management/deployment/enterprise-app-management")


_HOWTO = (
    '<details class="app-howto"><summary>How to deploy this from Intune</summary>'
    "<ol>"
    "<li>In the Microsoft Intune admin center, go to <strong>Apps &rsaquo; All apps "
    "&rsaquo; Create</strong>.</li>"
    "<li>Choose the <strong>Enterprise App Catalog app</strong> type, then search the "
    "catalog for {product}.</li>"
    "<li>Pick the package whose branch, version, architecture and locale you need "
    "&mdash; the table above lists every one Microsoft publishes.</li>"
    "<li>Finish the app information and assignment steps. Where auto-update is "
    "supported, Intune keeps the app current on its own.</li>"
    "</ol>"
    '<p class="howto-more">Microsoft\'s own documentation: '
    f'<a href="{_LEARN_URL}" target="_blank" rel="noopener">'
    "Enterprise App Management in Intune</a>.</p>"
    "</details>"
)


# ---------------------------------------------------------------------------
# Per-product change history
#
# What made the product pages weak was that they had nothing of their own to
# say: nine hundred pages off one template, differing by a name and a version,
# with no reason for any of them to be re-read. The catalog diffs this script
# already computes are the missing content — "Firefox moved 154.0 → 154.0.1 on
# 27 Aug" is specific to the page, true, and grows on its own.
#
# History accumulates in the state file: each run folds its own diff in, so the
# archived exports are re-read only once, when there is nothing to carry
# forward.
# ---------------------------------------------------------------------------

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _pretty_date(iso):
    """'2026-08-27' -> '27 Aug 2026'; anything unparseable passes through."""
    try:
        y, m, d = (int(p) for p in iso.split("-"))
        return f"{d} {_MONTHS[m - 1]} {y}"
    except (ValueError, IndexError):
        return iso


# Optional per-entry lists, written only when non-empty — most entries would
# carry them empty, and the state file is committed on every export.
_DETAIL_KEYS = ("branches_added", "branches_removed", "archs_added",
                "archs_removed", "locales_added", "locales_removed")


def _facets(apps, slug_by_key):
    """Per-product union of architectures and locales in one export.

    Package-level field changes cannot answer "when did this app gain arm64":
    a new arm64 package is an addition, not a change, and one package moving
    x86 -> x64 while another still ships x86 changes nothing an admin would
    notice. The product-level union, compared export to export, answers it
    directly. Its keys double as the set of products present in that export.
    """
    out = {}
    for a in apps:
        slug = slug_by_key.get(_product_key(a))
        if not slug:
            continue
        archs, locs = out.setdefault(slug, (set(), set()))
        archs.update(x.strip() for x in (a.get("applicableArchitectures") or "").split(",")
                     if x.strip())
        locs.update(l for l in (a.get("locales") or []) if l)
    return out


# Exports normally land twice a day, but not always: the archive has a
# four-month hole after its first export. A product first seen right after a
# hole like that arrived somewhere inside it, and "added on <that date>" would
# be wrong, so arrivals after a gap this long also keep the earlier bound.
ARRIVAL_SLACK_DAYS = 3


def _note_arrivals(state, slugs, date, prev_date):
    """Record the first export each not-yet-seen product appears in."""
    far = bool(prev_date) and (
        datetime.strptime(date, "%Y-%m-%d") - datetime.strptime(prev_date, "%Y-%m-%d")
    ).days > ARRIVAL_SLACK_DAYS
    for slug in slugs:
        if slug not in state["first_seen"]:
            state["first_seen"][slug] = date
            if far:
                state["first_seen_after"][slug] = prev_date


def _arch_sorted(archs):
    return sorted(archs, key=lambda a: (
        _ARCH_ORDER.index(a.lower()) if a.lower() in _ARCH_ORDER else len(_ARCH_ORDER), a
    ))


def _summarise_changes(structured, slug_by_key, date, prev_facets=None, curr_facets=None):
    """Fold one export-to-export diff into a per-product history entry.

    Package-level detail is deliberately collapsed. Firefox ships one package
    per locale, so a version bump lands as 47 near-identical rows; what belongs
    on a product page is "the version moved from X to Y", once. Version moves
    are therefore reduced to their distinct from-to pairs and everything else
    is counted or named. Given both exports' facets, the entry also records
    which architectures and locales the product as a whole gained or lost.
    """
    per_slug = {}

    def entry(slug):
        return per_slug.setdefault(slug, {
            "date": date, "added": 0, "removed": 0, "versions": [], "fields": [],
        })

    def bucket(app):
        slug = slug_by_key.get(_product_key(app))
        return entry(slug) if slug else None

    def note(b, key, value):
        if value and value not in b.setdefault(key, []):
            b[key].append(value)

    for a in structured.get("added") or []:
        b = bucket(a)
        if b is not None:
            b["added"] += 1
            note(b, "branches_added", a.get("branchDisplayName"))

    # A removed package still belongs to a product that may well remain in the
    # catalog — only a product that left entirely has no page to record it on.
    for a in structured.get("removed") or []:
        b = bucket(a)
        if b is not None:
            b["removed"] += 1
            note(b, "branches_removed", a.get("branchDisplayName"))

    # Only products present on both sides: for one that just arrived (or came
    # back), its packages are the news, not every architecture they cover.
    for slug, (archs, locs) in (curr_facets or {}).items():
        if prev_facets is None or slug not in prev_facets:
            continue
        p_archs, p_locs = prev_facets[slug]
        for key, vals in (("archs_added",     _arch_sorted(archs - p_archs)),
                          ("archs_removed",   _arch_sorted(p_archs - archs)),
                          ("locales_added",   sorted(locs - p_locs)),
                          ("locales_removed", sorted(p_locs - locs))):
            if vals:
                entry(slug)[key] = vals

    for a in structured.get("updated") or []:
        b = bucket(a)
        if b is None:
            continue
        prev = a.get("prevVersionDisplayName") or ""
        curr = a.get("versionDisplayName") or ""
        if prev and curr and prev != curr:
            # Counted, not just noted. A product like Firefox ships one package
            # per locale and Microsoft rolls them over across two exports, so
            # the same from-to pair legitimately appears on consecutive days;
            # without the count that reads as a duplicate rather than as a
            # staged rollout.
            for v in b["versions"]:
                if v[0] == prev and v[1] == curr:
                    v[2] += 1
                    break
            else:
                b["versions"].append([prev, curr, 1])
        for c in a.get("changes") or []:
            label = c.get("label") or c.get("field")
            if label and label != "Version" and label not in b["fields"]:
                b["fields"].append(label)
    return per_slug


def _merge_entries(a, b):
    """Combine two same-day entries — exports are sometimes pushed hours apart."""
    versions = [list(v) for v in a["versions"]]
    for prev, curr, n in b["versions"]:
        for v in versions:
            if v[0] == prev and v[1] == curr:
                v[2] += n
                break
        else:
            versions.append([prev, curr, n])
    merged = {
        "date":     a["date"],
        "added":    a["added"] + b["added"],
        "removed":  a["removed"] + b["removed"],
        "versions": versions,
        "fields":   a["fields"] + [f for f in b["fields"] if f not in a["fields"]],
    }
    for key in _DETAIL_KEYS:
        vals = a.get(key, []) + [v for v in b.get(key, []) if v not in a.get(key, [])]
        if vals:
            merged[key] = vals
    return merged


def _record_history(state, per_slug):
    """Append one run's per-product entries, newest last. The log is kept whole;
    only the page caps what it shows."""
    for slug, entry in per_slug.items():
        if not (entry["added"] or entry["removed"] or entry["versions"] or entry["fields"]
                or any(entry.get(k) for k in _DETAIL_KEYS)):
            continue
        log = state["history"].setdefault(slug, [])
        if log and log[-1].get("date") == entry["date"]:
            log[-1] = _merge_entries(log[-1], entry)
        else:
            log.append(entry)


def seed_history(state, files, slug_by_key):
    """Backfill history from the exports already in the repository.

    Runs only when there is nothing to carry forward — after this pass the
    state file carries the history and each run appends only its own diff. Walks
    the whole archive by default; HISTORY_SEED bounds it to the newest N exports
    if that ever gets expensive. Also records the first export each product
    appears in; one already in the oldest export is dated to that export, which
    the pages word as "or earlier".
    """
    if state["history"] or state["first_seen"] or len(files) < 2:
        return False
    window = files[-HISTORY_SEED:] if HISTORY_SEED > 0 else files
    if len(window) < 2:
        return False
    print(f"  history                   — seeding from {len(window)} export(s), "
          "one-off; subsequent runs append only")
    prev_apps = load_catalog(window[0])
    prev_facets = _facets(prev_apps, slug_by_key)
    history_from = (parse_dt(window[0]) or datetime.min).strftime("%Y-%m-%d")
    _note_arrivals(state, prev_facets, history_from, None)
    prev_date = history_from
    for prev_file, curr_file in zip(window, window[1:]):
        curr_apps = load_catalog(curr_file)
        curr_facets = _facets(curr_apps, slug_by_key)
        dt = parse_dt(curr_file)
        if dt:
            date = dt.strftime("%Y-%m-%d")
            structured, *_ = _compute_changes(curr_apps, prev_apps, curr_file, prev_file)
            _record_history(state, _summarise_changes(
                structured, slug_by_key, date, prev_facets, curr_facets))
            _note_arrivals(state, curr_facets, date, prev_date)
            prev_date = date
        prev_apps, prev_facets = curr_apps, curr_facets
    state["history_from"] = history_from
    n = sum(len(v) for v in state["history"].values())
    print(f"  history                   — {n:,} entries across "
          f"{len(state['history']):,} product(s)")
    return True


def _names(items, limit=3):
    """'A, B, C and 4 more' — enough to recognise, not a full manifest."""
    shown = ", ".join(_xml_escape(x) for x in items[:limit])
    rest = len(items) - limit
    return shown + (f" and {rest:,} more" if rest > 0 else "")


def _render_history(entries, since, first_seen=None):
    """The change-history section for one product page."""
    if not entries:
        return "", ""

    def version_bit(prev, curr, n):
        moved = (f'<span class="hist-n">{n:,} packages</span>' if n > 1 else "")
        return (f"<code>{_xml_escape(prev)}</code> &rarr; "
                f"<code>{_xml_escape(curr)}</code>{moved}")

    def packages_bit(n, verb, branches):
        text = f"{n:,} package{'' if n == 1 else 's'} {verb}"
        return text + (f" ({_names(branches)})" if branches else "")

    def phrase(e):
        # A product already in the oldest export has no arrival on record; its
        # first entry is an ordinary change, not the day it joined the catalog.
        arrived = bool(first_seen and since and first_seen > since
                       and e["date"] == first_seen)
        bits = []
        if arrived and e.get("added"):
            n = e["added"]
            bits.append(f"First listed in the catalog with {n:,} "
                        f"package{'' if n == 1 else 's'}")
        # Tolerates the pre-count entry shape so an existing state file does not
        # have to be thrown away to pick up this change.
        bits += [version_bit(v[0], v[1], v[2] if len(v) > 2 else 1)
                 for v in e.get("versions") or []]
        if e.get("added") and not arrived:
            bits.append(packages_bit(e["added"], "added", e.get("branches_added")))
        if e.get("removed"):
            bits.append(packages_bit(e["removed"], "removed", e.get("branches_removed")))
        for key, label in (("archs_added",   "New architecture"),
                           ("archs_removed", "Dropped architecture")):
            vals = e.get(key)
            if vals:
                bits.append(f"{label}{'' if len(vals) == 1 else 's'} "
                            f"{_arch_tags(','.join(vals))}")
        for key, label in (("locales_added",   "New locale"),
                           ("locales_removed", "Dropped locale")):
            vals = e.get(key)
            if vals:
                bits.append(f"{label}{'' if len(vals) == 1 else 's'} "
                            f"{_locale_tags(vals, limit=6)}")
        # The package-level field label says less than the product-level
        # difference above, so it gives way when that difference is recorded.
        covered = set()
        if e.get("archs_added") or e.get("archs_removed"):
            covered.add("Architecture")
        if e.get("locales_added") or e.get("locales_removed"):
            covered.add("Locales")
        for label in e.get("fields") or []:
            if label not in covered:
                bits.append(f"{_xml_escape(label)} changed")
        return ", ".join(bits) or "Catalog entry updated"

    shown = entries[-HISTORY_MAX:]
    items = "".join(
        f'<li><time datetime="{_xml_escape(e["date"])}">'
        f'{_xml_escape(_pretty_date(e["date"]))}</time>'
        f'<span class="hist-what">{phrase(e)}</span></li>'
        for e in reversed(shown)
    )
    more = (f" Showing the latest {len(shown):,} of {len(entries):,}."
            if len(entries) > len(shown) else "")
    note = (f'<p class="hist-note">Recorded from the catalog exports in this '
            f"repository since {_xml_escape(_pretty_date(since))}.{more}</p>"
            if since else "")
    html = (
        '\n<section class="docs-section">\n'
        f'<h2>Change history<span class="count-pill">{len(entries):,}</span></h2>\n'
        f'<ol class="app-history">{items}</ol>\n{note}\n'
        "</section>"
    )
    return html, entries[-1]["date"]


def _cadence(entries):
    """(version-update days, typical gap in days, latest version move).

    Counted per day, not per package: Firefox moving 47 locale packages from
    one version to the next is one release, and so is a staged rollout that
    lands across two exports on the same day. The typical gap is the mean
    spacing between the first and the last update, not the median of the
    gaps: Java ships patch re-releases days apart, which put the median at
    five days for an app that moves about once a month. It needs at least two
    gaps to mean anything.

    Every figure is anchored to the app's own changes, never to today: a
    sliding "last 90 days" count would rewrite pages when nothing about the
    app moved, which is the false <lastmod> signal this pipeline exists to
    avoid.
    """
    days = [e["date"] for e in entries if e.get("versions")]
    gap = None
    if len(days) >= 3:
        span = (datetime.strptime(days[-1], "%Y-%m-%d")
                - datetime.strptime(days[0], "%Y-%m-%d")).days
        gap = max(1, round(span / (len(days) - 1)))
    last = None
    for e in reversed(entries):
        if e.get("versions"):
            v = max(e["versions"], key=lambda v: _version_key(v[1]))
            last = (e["date"], v[0], v[1])
            break
    return days, gap, last


def _interval(days):
    """'9 days', '3 weeks', '2 months' — the precision a cadence deserves."""
    if days < 14:
        return f"{days:,} day{'' if days == 1 else 's'}"
    if days < 63:
        n = round(days / 7)
        return f"{n} weeks"
    n = round(days / 30)
    return f"{n} month{'' if n == 1 else 's'}"


def _activity_prose(entries, since, first_seen, first_seen_after=None):
    """A few sentences on how this app has moved in the catalog.

    The template sentence before it is the same on every page; this is the
    part that is not.
    """
    if not since:
        return ""
    days, gap, last = _cadence(entries)
    if first_seen and first_seen > since and first_seen_after:
        s = (f"It was first listed in the catalog export of {_pretty_date(first_seen)}; "
             f"the one before, on {_pretty_date(first_seen_after)}, did not include it yet.")
    elif first_seen and first_seen > since:
        s = f"It was first listed in the catalog on {_pretty_date(first_seen)}."
    else:
        s = (f"It has been in the catalog since at least {_pretty_date(since)}, "
             "the first export on record.")
    if not days:
        return s + " No new version has been published to it since."
    move = (f"<code>{_xml_escape(last[1])}</code> &rarr; "
            f"<code>{_xml_escape(last[2])}</code>")
    if len(days) == 1:
        return (s + f" One version update has been recorded since, on "
                f"{_pretty_date(last[0])}, moving {move}.")
    every = f", roughly one every {_interval(gap)}" if gap else ""
    return (s + f" {len(days):,} version updates have been recorded since{every}; "
            f"the latest, on {_pretty_date(last[0])}, moved {move}.")


def _render_app_page(slug, packages, site_url, repo_url,
                     siblings=(), history=(), history_from=None, first_seen=None,
                     first_seen_after=None):
    product   = packages[0].get("productDisplayName", "")
    publisher = packages[0].get("publisherDisplayName", "")
    n      = len(packages)
    latest = max((p.get("versionDisplayName") or "" for p in packages), key=_version_key)
    n_auto = sum(1 for p in packages if p.get("packageAutoUpdateCapable"))
    auto   = n_auto > 0

    # Union of the per-package values, in a stable presentation order.
    arches = {a.strip() for p in packages
              for a in (p.get("applicableArchitectures") or "").split(",") if a.strip()}
    arches = sorted(arches, key=lambda a: (
        _ARCH_ORDER.index(a.lower()) if a.lower() in _ARCH_ORDER else len(_ARCH_ORDER), a
    ))
    locales = sorted({l for p in packages for l in (p.get("locales") or []) if l})

    hist_html, last_change = _render_history(history, history_from, first_seen)
    update_days, gap, _ = _cadence(history)
    prose = _activity_prose(history, history_from, first_seen, first_seen_after)
    prose = f" {prose}" if prose else ""
    # The date the record for this app starts: its arrival, or the oldest
    # export for an app that was already there.
    anchor = first_seen if first_seen and history_from and first_seen > history_from \
        else history_from

    # Least-important clause last: on a long product name the auto-update note
    # falls off the end rather than the version, and the name is never cut.
    updates_clause = ()
    if update_days and anchor:
        y, m = anchor.split("-")[:2]
        k = len(update_days)
        updates_clause = (f"{k:,} version update{'' if k == 1 else 's'} since "
                          f"{_MONTHS[int(m) - 1]} {y}",)
    desc = _meta_desc(
        f"{product} by {publisher} in the Intune EAM app catalog",
        f"{n} package{'s' if n != 1 else ''}",
        f"latest {latest}",
        *updates_clause,
        f"auto-update {'supported' if auto else 'not supported'}",
    )
    ld_data = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": product,
        "operatingSystem": "Windows",
        "softwareVersion": latest,
        "publisher": {"@type": "Organization", "name": publisher},
    }
    # The date the *app* last moved in the catalog, not the date this file was
    # regenerated — the second one is noise and was what the page used to say.
    if last_change:
        ld_data["dateModified"] = last_change
    ld = json.dumps(ld_data, ensure_ascii=False)

    # Auto-update reads three ways: every package, none of them, or a mix.
    if n_auto == n:
        auto_fact = _auto_badge(True, "Supported")
    elif n_auto == 0:
        auto_fact = _auto_badge(False, "Not supported")
    else:
        auto_fact = _auto_badge(False, f"{n_auto} of {n} packages")

    facts = (
        '<dl class="app-facts">'
        + _fact("Latest version", f"<code>{_xml_escape(latest)}</code>")
        + _fact("Packages", f"{n:,}")
        + _fact("Auto-update", auto_fact)
        + _fact("Architectures", _arch_tags(",".join(arches)))
        + _fact("Locales", _locale_tags(locales, limit=8))
        + (_fact("In catalog since",
                 f'<time datetime="{_xml_escape(anchor)}">'
                 f"{_xml_escape(_pretty_date(anchor))}</time>"
                 + ("" if anchor != history_from and not first_seen_after
                    else ' <span class="app-dash">or earlier</span>'))
           if anchor else "")
        + (_fact("Version updates",
                 f"{len(update_days):,}"
                 + (f' <span class="app-dash">&middot; every ~{_interval(gap)}</span>'
                    if gap else ""))
           if update_days else
           _fact("Version updates", '<span class="app-dash">None recorded</span>')
           if anchor else "")
        # A product that has never moved says so rather than dropping the row:
        # "unchanged since December" is a real answer for an admin deciding
        # whether a package is stable, and it keeps the panel the same shape on
        # every page.
        + (_fact("Last change",
                 f'<time datetime="{_xml_escape(last_change)}">'
                 f"{_xml_escape(_pretty_date(last_change))}</time>")
           if last_change else
           _fact("Last change",
                 '<span class="app-dash">No change since '
                 f"{_xml_escape(_pretty_date(history_from))}</span>")
           if history_from else "")
        + "</dl>"
    )

    # The latest-version marker only earns its place when the rows disagree —
    # Firefox ships 47 packages that all carry the same version, and flagging
    # every one of them says nothing.
    mixed_versions = len({p.get("versionDisplayName") or "" for p in packages}) > 1

    def row(p):
        ver  = p.get("versionDisplayName") or ""
        mark = ('<span class="tag tag-latest">Latest</span>'
                if mixed_versions and ver == latest else "")
        cls  = ' class="is-latest"' if mark else ""
        return (
            f"<tr{cls}><td>{_xml_escape(p.get('branchDisplayName', ''))}</td>"
            f'<td class="col-version"><code>{_xml_escape(ver)}</code>{mark}</td>'
            f"<td>{_arch_tags(p.get('applicableArchitectures'))}</td>"
            f"<td>{_auto_badge(p.get('packageAutoUpdateCapable'))}</td>"
            f"<td>{_locale_tags(p.get('locales'))}</td></tr>"
        )

    rows = "".join(row(p) for p in sorted(packages, key=lambda p: (
        (p.get("branchDisplayName") or "").lower(),
        _version_key(p.get("versionDisplayName")),
    )))

    # Sibling products keep a visitor who landed from a search moving sideways.
    sib_html = ""
    if siblings:
        shown = siblings[:12]
        chips = "".join(
            f'<a class="app-chip" href="{s}.html">{_xml_escape(name)}</a>'
            for s, name in shown
        )
        more = (f'<a class="app-chip app-chip-more" href="./">'
                f"+{len(siblings) - len(shown):,} more</a>"
                if len(siblings) > len(shown) else "")
        sib_html = (
            '\n<section class="docs-section">\n'
            f'<h2>More from {_xml_escape(publisher)}'
            f'<span class="count-pill">{len(siblings):,}</span></h2>\n'
            f'<div class="app-siblings">{chips}{more}</div>\n'
            "</section>"
        )

    body = (
        '<nav class="app-breadcrumb"><a href="../">Catalog</a> &rsaquo; '
        f'<a href="./">All Apps</a> &rsaquo; <span>{_xml_escape(product)}</span></nav>\n'
        '<section class="docs-section app-hero">\n'
        f'<h1 class="app-title">{_xml_escape(product)}</h1>\n'
        f'<p class="app-publisher">by {_xml_escape(publisher)}</p>\n'
        f'<p class="app-lede">{_xml_escape(product)} is published in the Microsoft Intune '
        "Enterprise App Management (EAM) catalog, so Intune can deploy and update it on "
        f"Windows devices without a repackaged installer.{prose}</p>\n"
        f"{facts}\n"
        "</section>\n"
        '<section class="docs-section">\n'
        f'<h2>Packages<span class="count-pill">{n:,}</span></h2>\n'
        '<div class="app-table-wrap"><table class="docs-table"><thead><tr>'
        "<th>Branch</th><th>Version</th><th>Architecture</th>"
        "<th>Auto-Update</th><th>Locales</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>\n"
        f"{_HOWTO.format(product=_xml_escape(product))}\n"
        "</section>"
        f"{hist_html}"
        f"{sib_html}\n"
        # No export timestamp here. It changed on every run whether or not this
        # app did, which rewrote all 900-odd pages daily and pushed a bogus
        # <lastmod> for each of them into the sitemap; the date that actually
        # belongs on this page is the app's own last change, up in the facts.
        '<p class="app-note">Built from the Microsoft Graph API resource '
        "<code>win32MobileAppCatalogPackage</code>. "
        '<a href="../">Browse and search the full catalog</a> for the current '
        "export date and the complete change history. "
        "Not affiliated with Microsoft.</p>"
    )
    canonical = f"{site_url}apps/{slug}.html" if site_url else ""
    extra_head = f'\n  <script type="application/ld+json">{ld}</script>'
    return _static_page(_page_title(product, publisher),
                        desc, canonical, body, extra_head, repo_url, site_url)


# Answers double as page copy and as FAQPage structured data, so the two can
# never drift. Plain text only — the HTML rendering escapes, the JSON does not
# want markup. These target the questions people actually type into a search
# engine ("what is the intune eam catalog", "how many apps"), which the SPA on
# the home page cannot answer in crawlable HTML.
def _apps_index_faq(n_products, n_packages, source_ts):
    return [
        ("What is the Intune EAM app catalog?",
         "Enterprise App Management (EAM) is the Microsoft Intune feature that offers "
         "ready-made Win32 applications. Microsoft packages the app, its detection rules "
         "and its update logic; an administrator picks it from the catalog in the Intune "
         "admin center and assigns it, instead of wrapping an installer by hand."),
        ("How many apps are in the Intune EAM catalog?",
         f"{n_products:,} applications and {n_packages:,} individual packages as of the "
         f"export on {source_ts}. One application can ship several packages \u2014 one per "
         "branch, architecture and version."),
        ("Is this an official Microsoft list?",
         "No. This is an independent, read-only view built from the Microsoft Graph API "
         "resource win32MobileAppCatalogPackage. It is not affiliated with or endorsed by "
         "Microsoft, and it reflects the catalog as of the last export rather than this "
         "moment."),
        ("How often is this list updated?",
         "Every time a new catalog export is processed. Each page names the export it was "
         "built from, and the RSS feed publishes an entry for every set of added, removed "
         "and updated packages."),
        ("Can Intune update EAM apps automatically?",
         "Many packages are auto-update capable, which lets Intune install a newer version "
         "as soon as it appears in the catalog. Every app page states whether its packages "
         "support it, and the main catalog can be filtered by it."),
        ("How do I check whether a specific app is available in EAM?",
         "Search the table on this page, or the package table on the main catalog page \u2014 "
         "publisher, app name, branch and version are all searchable. Every application also "
         "has its own page listing each branch, version, architecture and locale it ships."),
    ]


def _render_apps_index(products, site_url, source_ts, repo_url):
    rows = "".join(
        f"<tr><td>{_xml_escape(pub)}</td>"
        f'<td><a href="{slug}.html">{_xml_escape(name)}</a></td>'
        f"<td>{n}</td><td><code>{_xml_escape(latest)}</code></td></tr>"
        for slug, pub, name, n, latest in products
    )
    n_products = len(products)
    n_packages = sum(r[3] for r in products)

    desc = _meta_desc(
        f"All {n_products:,} apps in the Intune EAM catalog",
        "publisher", "package count", "latest version for every app",
        "updated with each export",
    )

    faq = _apps_index_faq(n_products, n_packages, source_ts)
    faq_html = "".join(
        f'<h3 class="faq-q">{_xml_escape(q)}</h3>\n<p>{_xml_escape(a)}</p>\n'
        for q, a in faq
    )
    ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in faq
        ],
    }, ensure_ascii=False)

    body = (
        '<nav class="app-breadcrumb"><a href="../">Catalog</a> &rsaquo; All Apps</nav>\n'
        '<section class="docs-section app-hero">\n'
        '<h1 class="app-title">All Apps in the Intune EAM Catalog</h1>\n'
        f'<p class="app-lede">Every one of the {n_products:,} applications currently '
        "published in the Microsoft Intune Enterprise App Management (EAM) catalog, with "
        "the publisher, how many packages each one ships and the latest version Intune "
        f"offers. Exported {_xml_escape(source_ts)} from the Microsoft Graph API.</p>\n"
        "<p>Open an application to see every branch, version, architecture and locale it "
        'ships, or <a href="../">search the full package table</a> on the main catalog '
        "page. Microsoft's own documentation for the feature is on "
        f'<a href="{_LEARN_URL}" target="_blank" rel="noopener">Microsoft Learn</a>.</p>\n'
        "</section>\n"
        '<section class="docs-section">\n'
        f'<h2>Applications<span class="count-pill">{n_products:,}</span></h2>\n'
        '<div class="app-table-wrap"><table class="docs-table">'
        '<thead><tr><th>Publisher</th><th>App</th>'
        "<th>Packages</th><th>Latest Version</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>\n"
        "</section>\n"
        '<section class="docs-section">\n'
        "<h2>Frequently asked questions</h2>\n"
        f"{faq_html}"
        "</section>"
    )
    canonical = f"{site_url}apps/" if site_url else ""
    extra_head = f'\n  <script type="application/ld+json">{ld}</script>'
    return _static_page("All Apps in the Intune EAM Catalog \u2014 Complete List",
                        desc, canonical, body, extra_head, repo_url, site_url)


def _page_url(site_url, rel):
    """Absolute URL of a page, or its root-relative path when no site URL is
    known — either way a stable key for the state file."""
    return f"{site_url}{rel}" if site_url else f"/{rel}"


def generate_app_pages(slug_map, latest_file, site_url, repo_url, state, today, changed):
    """Write one static page per product plus an index; prune pages for products
    that left the catalog. Takes the map from _build_slug_map so the pages and
    the links in catalog.json cannot drift apart. Returns the sorted slugs."""
    # Every other product from the same publisher, for the cross-links at the
    # foot of each page. Alphabetical so the pick stays stable run to run.
    by_publisher = {}
    for s, g in slug_map.items():
        key = (g[0].get("publisherDisplayName") or "").lower()
        by_publisher.setdefault(key, []).append((s, g[0].get("productDisplayName") or ""))
    for v in by_publisher.values():
        v.sort(key=lambda t: t[1].lower())

    os.makedirs("docs/apps", exist_ok=True)
    source_ts = filename_to_ts(latest_file)
    history_from = state.get("history_from")
    index_rows, written = [], 0
    for slug, g in slug_map.items():
        key = (g[0].get("publisherDisplayName") or "").lower()
        siblings = [t for t in by_publisher[key] if t[0] != slug]
        html = _render_app_page(slug, g, site_url, repo_url, siblings,
                                state["history"].get(slug, []), history_from,
                                state["first_seen"].get(slug),
                                state["first_seen_after"].get(slug))
        written += _write_page(f"docs/apps/{slug}.html", html,
                               _page_url(site_url, f"apps/{slug}.html"),
                               state, today, changed)
        index_rows.append((
            slug,
            g[0].get("publisherDisplayName", ""),
            g[0].get("productDisplayName", ""),
            len(g),
            max((p.get("versionDisplayName") or "" for p in g), key=_version_key),
        ))

    index_rows.sort(key=lambda r: (r[1].lower(), r[2].lower()))
    written += _write_page(
        "docs/apps/index.html",
        _render_apps_index(index_rows, site_url, source_ts, repo_url),
        _page_url(site_url, "apps/"), state, today, changed)

    keep = {f"{s}.html" for s in slug_map} | {"index.html"}
    stale = [p for p in glob.glob("docs/apps/*.html") if os.path.basename(p) not in keep]
    for p in stale:
        os.remove(p)
        base = os.path.basename(p)
        state["pages"].pop(_page_url(site_url, f"apps/{base}"), None)
        state["history"].pop(os.path.splitext(base)[0], None)
        state["first_seen"].pop(os.path.splitext(base)[0], None)
        state["first_seen_after"].pop(os.path.splitext(base)[0], None)

    note = f", {len(stale)} stale page(s) removed" if stale else ""
    print(f"  docs/apps/                — {len(slug_map):,} product page(s) + index"
          f"{note}; {written:,} file(s) rewritten")
    return sorted(slug_map)


# ---------------------------------------------------------------------------
# docs/sitemap.xml — homepage, apps index, and every product page
# ---------------------------------------------------------------------------

def generate_sitemap(site_url, slugs, state):
    """One <lastmod> per URL, taken from when that page's content last moved.

    This used to stamp every URL with the export date, so the sitemap claimed
    900-odd changes a day that had not happened. A crawler that samples a few of
    those, finds them identical and has no other way to prioritise a flat set of
    near-identical pages stops trusting the dates and stops crawling most of
    them — which is exactly the state Bing reported. Dates that move only on a
    real change are the whole point of the state file.
    """
    if not site_url:
        print("  docs/sitemap.xml          — skipped (no site URL available)")
        return
    urls = [site_url, f"{site_url}apps/"] + [f"{site_url}apps/{s}.html" for s in slugs]

    def entry(u):
        lm = (state["pages"].get(u) or {}).get("lastmod")
        lastmod = f"\n    <lastmod>{lm}</lastmod>" if lm else ""
        return f"  <url>\n    <loc>{_xml_escape(u)}</loc>{lastmod}\n  </url>\n"

    entries = "".join(entry(u) for u in urls)
    with open("docs/sitemap.xml", "w", encoding="utf-8") as f:
        f.write(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + entries + "</urlset>\n"
        )
    dates = {(state["pages"].get(u) or {}).get("lastmod") for u in urls} - {None}
    print(f"  docs/sitemap.xml          — {len(urls):,} URL(s), "
          f"{len(dates):,} distinct lastmod date(s)")


# ---------------------------------------------------------------------------
# IndexNow — tell Bing which URLs moved, and only those
#
# Bing discovered these pages from the sitemap and then declined to crawl them.
# A submission naming URLs that did not change is the same false signal in a
# louder channel, so the list comes straight from _write_page: a page whose
# content is unchanged never reaches this function. The payload is left on disk
# for the workflow to POST after the push, because the key file has to be live
# at keyLocation before the submission can be validated.
# ---------------------------------------------------------------------------

INDEXNOW_PAYLOAD = "docs/.indexnow.json"
_KEYFILE_RE = re.compile(r"^[0-9a-f]{8,128}\.txt$")


def generate_indexnow(site_url, changed, state):
    if os.path.exists(INDEXNOW_PAYLOAD):
        os.remove(INDEXNOW_PAYLOAD)
    if not site_url:
        print("  IndexNow                  — skipped (no site URL available)")
        return
    # A key supplied by the workflow wins, so it can be rotated without touching
    # the repository; otherwise one is minted once and carried in the state
    # file. Either way the key file has to be published at the site root.
    key = os.environ.get("INDEXNOW_KEY", "").strip() or state.get("indexnow_key")
    if not key:
        key = uuid.uuid4().hex
    state["indexnow_key"] = key

    with open(f"docs/{key}.txt", "w", encoding="utf-8") as f:
        f.write(key + "\n")
    for p in glob.glob("docs/*.txt"):
        base = os.path.basename(p)
        if _KEYFILE_RE.match(base) and base != f"{key}.txt":
            os.remove(p)

    if not changed:
        print("  IndexNow                  — nothing changed, no submission")
        return
    if len(changed) > INDEXNOW_MAX:
        print(f"  IndexNow                  — {len(changed):,} changed, submitting "
              f"the first {INDEXNOW_MAX:,} (per-request limit)")
    payload = {
        "host":        urlsplit(site_url).netloc,
        "key":         key,
        "keyLocation": f"{site_url}{key}.txt",
        "urlList":     changed[:INDEXNOW_MAX],
    }
    with open(INDEXNOW_PAYLOAD, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"  IndexNow                  — {len(payload['urlList']):,} changed URL(s) "
          f"queued in {INDEXNOW_PAYLOAD}")


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
    previous_apps = None

    if len(files) >= 2:
        previous_file = files[-2]
        previous_apps = load_catalog(previous_file)
        print(f"Previous: {previous_file}")
        changes_data["latest"] = generate_changes(
            current_apps, previous_apps, latest_file, previous_file
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

    slug_map, slug_by_key = _build_slug_map(current_apps)
    generate_catalog_json(current_apps, stats, latest_file, slug_by_key)
    generate_changes_json(changes_data)
    generate_summary_json(stats, changes_data, slug_by_key)
    repo_url = get_repo_url()
    site_url = get_site_url(repo_url)
    generate_feed(changes_data.get("latest"), stats, files, repo_url, slug_by_key)

    # Which pages to write, which sitemap dates to move and which URLs to hand
    # IndexNow are all the same question — did this page's content change — so
    # they share one state file and one pass.
    state   = _load_state()
    today   = (parse_dt(latest_file) or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    changed = []

    # Each export's diff is folded in exactly once. The seed window ends on the
    # same pair of exports changes_data describes, and re-running the script
    # against an unchanged catalog would otherwise keep merging that diff into
    # the same dated entry and inflating its counts.
    latest_name = os.path.basename(latest_file)
    seeded = seed_history(state, files, slug_by_key)
    if (not seeded and changes_data.get("latest")
            and state.get("history_through") != latest_name):
        _record_history(state, _summarise_changes(
            changes_data["latest"], slug_by_key, today,
            _facets(previous_apps, slug_by_key), _facets(current_apps, slug_by_key)))
    state["history_through"] = latest_name
    if not state.get("history_from"):
        state["history_from"] = today
    # A product that arrived with this export is first seen today; one already
    # on record keeps its date.
    prev_dt = parse_dt(files[-2]) if len(files) >= 2 else None
    _note_arrivals(state, slug_map, today,
                   prev_dt.strftime("%Y-%m-%d") if prev_dt else None)

    slugs = generate_app_pages(slug_map, latest_file, site_url, repo_url,
                               state, today, changed)
    # The home page is hand-maintained, but its sitemap entry should still date
    # from when it was last edited rather than from the newest export.
    _track_static("docs/index.html", _page_url(site_url, ""), state, today, changed)
    generate_sitemap(site_url, slugs, state)
    generate_indexnow(site_url, changed, state)
    _save_state(state)
    update_readme(stats)
    print("\nDone.")


if __name__ == "__main__":
    main()
