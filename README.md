# IntuneEAM AppCatalog

[![Process App Catalog](https://github.com/danrung/IntuneEAM-AppCatalog/actions/workflows/process-catalog.yml/badge.svg)](https://github.com/danrung/IntuneEAM-AppCatalog/actions/workflows/process-catalog.yml) [![Packages](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Feam.drung.dev%2Fsummary.json&query=%24.total&label=packages&color=0078d4)](https://eam.drung.dev/) [![Last Commit](https://img.shields.io/github/last-commit/danrung/IntuneEAM-AppCatalog)](https://github.com/danrung/IntuneEAM-AppCatalog/commits/main) [![License: MIT](https://img.shields.io/github/license/danrung/IntuneEAM-AppCatalog)](LICENSE) [![RSS](https://img.shields.io/badge/RSS-feed-orange?logo=rss)](https://eam.drung.dev/feed.xml)

A reference for the **Microsoft Intune Enterprise Application Management (EAM)** app catalog. If you work with Intune EAM and have ever wanted a quick, browsable overview of what's actually in the catalog, what apps are available, which publishers are represented, what changed recently, here it is.

The raw data comes from the Microsoft Graph API. Exports are dropped into this repo and a GitHub Actions workflow takes care of building the tables, stats, and change history automatically.

## What's in Here

🌐 **[Live Catalog](https://eam.drung.dev/)** Searchable, filterable website with the full package list — the stat cards double as filters. Follows your system light/dark preference, with a toggle in the header.

🔗 **[App Pages](https://eam.drung.dev/apps/)** A static, linkable page for every product in the catalog: an at-a-glance summary panel, when it first appeared in the catalog and how often it gets a new version, a table of every branch, version, architecture and locale, the deployment details Intune sets up for it — install and uninstall command, MSI product and upgrade code, detection and requirement rules, install context, return codes — that product's own dated change history — including which packages, architectures and locales came and went, and rebuilds that changed only the deployment details — the steps to deploy it from Intune, and links to the publisher's other apps — rewritten only when something about the product actually changes.

🧭 **[Quirks](https://eam.drung.dev/apps/quirks.html)** What the deployment details reveal across the whole catalog: detection rules that expect a newer version than the catalog lists, version checks pinned to another number, user-context installs, MSIs behind an EXE, unusual success exit codes and publishers listed under several names.

📦 **[App Catalog](catalog.md)** Every available package, sorted by publisher and app name.

📊 **[Statistics](stats.md)** Total counts, top publishers, architecture breakdown, and available locales.

🔄 **[Changes](changes.md)** What was added, removed, or updated between the two most recent exports.

📅 **[Daily Changes](changes_daily.md)** Changes compared to the nearest export from at least 24 hours ago.

📆 **[Weekly Changes](changes_weekly.md)** Changes compared to the nearest export from at least 7 days ago.

🗓️ **[Monthly Changes](changes_monthly.md)** Changes compared to the nearest export from at least 30 days ago.

📡 **[RSS Feed](https://eam.drung.dev/feed.xml)** Subscribe to catalog changes in any RSS reader.

Raw exports: [catalog/](catalog/) for the current file, [archive/](archive/) for the full history. Since November 2026 each export also has a `*_AppDetails.json` with the deployment details; archived ones are gzipped.

## RSS Feed

Stay up to date without checking the site manually. Every catalog export that adds, removes or updates something publishes a new entry to the feed; an export that changes nothing publishes none.

**Feed URL:**
```
https://eam.drung.dev/feed.xml
```

Each feed item contains:
- A title naming the apps that changed, and a preview line with their version moves
- How many packages were added, removed, and updated
- A list of added and removed packages with publisher, name, and version
- A list of updated packages showing the previous and new version — packages that moved identically (Firefox ships one per locale) share a row
- A link from every app to its own page on the site
- The date and time of the catalog export it is based on

Items are table-based HTML with inline styles only, so they render in Outlook's RSS folders, including in dark mode. The feed keeps the last 50 entries. When the item layout changes, the script bumps `FEED_FORMAT` and the next run rebuilds the items already in the feed from `archive/`, keeping their guids so subscribed readers do not list them twice. Compatible with any RSS reader — Feedly, Outlook, Thunderbird, Apple Mail, or any other client that supports RSS 2.0.

## Latest Statistics

<!-- CATALOG_STATS_START -->
| Metric | Value |
|--------|-------|
| Total Packages | **1,595** |
| Unique Products | 933 |
| Publishers | 498 |
| Auto-Update Capable | 365 (22.9%) |
| Available Locales | 65 |
| Last Export | 2026-11-05 13:45:10 |
<!-- CATALOG_STATS_END -->

## Data Fields

Each entry reflects what the Graph API returns:

| Field | Description |
|-------|-------------|
| `productDisplayName` | Application name |
| `publisherDisplayName` | Publisher / vendor |
| `versionDisplayName` | Version string |
| `branchDisplayName` | Package branch (e.g. `WireGuard (x64)`) |
| `applicableArchitectures` | Supported CPU architectures (`x64`, `x86`, ...) |
| `packageAutoUpdateCapable` | Whether the package supports Intune auto-update |
| `locales` | Supported locale codes |
| `productId` | Stable product GUID |
| `branchId` | Stable branch GUID |
| `id` | Package instance ID — new whenever Microsoft publishes a new package for the branch |

### Deployment Details

`*_AppDetails.json` holds, per package `id`, what the Graph beta function
`deviceAppManagement/mobileApps/convertFromMobileAppCatalogPackage` returns — the Win32 app Intune would create
from that package. The exporter keeps only these fields and drops
empty ones, placeholders and the icon:

| Field | Description |
|-------|-------------|
| `installCommandLine` / `uninstallCommandLine` | Commands Intune runs |
| `fileName`, `setupFilePath`, `size` | Installer file and its size in bytes |
| `msiInformation` | Product code, upgrade code, product version, per-machine or per-user — present only for MSI packages |
| `rules` | Detection and requirement rules, told apart by `ruleType` |
| `installExperience` | Install context (`system`/`user`), restart behaviour, maximum run time |
| `returnCodes` | Exit codes and what Intune treats them as |
| `minimumSupportedWindowsRelease`, `allowedArchitectures` | Requirements |
| `description`, `informationUrl`, `privacyInformationUrl` | Vendor text and links — the catalog record's own description is always empty |

A package whose conversion failed is stored as `{"branchId": …, "error": "<status>"}`; its page then shows the
previous export's details while the package `id` is unchanged.

## Understanding the Stats and Changes

One product can ship as multiple packages, for example separate x64 and x86 branches each get their own entry. The statistics page accounts for this by tracking both raw package count and unique product count.

The changes pages match packages between exports by `branchId` and surface three things: packages that are new, packages that have been removed, and packages where a tracked field changed. Tracked fields are the version, app name, branch name, publisher, architecture, auto-update capability and locales — so renames and capability changes show up alongside ordinary version bumps, with the changed fields named in each row. The package `id` is not used for matching: it identifies the package, not the branch.

When both exports have deployment details, there is a fourth kind: **repackaged** — same branch, unchanged catalog entry, but the deployment details differ, which means Microsoft rebuilt the package without a new version. Updated packages also name the deployment details that changed with them. The comparison works two ways. With a version change only how the app is deployed counts (switches, detection method, upgrade code, install context, return codes); the installer name, its size, the MSI product code and the version in the detection rule move with every release and are not reported. Without a version change every value counts. A package that only got a new `id`, with nothing else different, is counted under the summary rather than listed. The first export with details has nothing to compare against and reports none.

The period-based changelogs (daily, weekly, monthly) use the timestamp embedded in each filename to find the right comparison point automatically. The period is a minimum, not a fixed window: each one compares against the newest export that is at least that old, which after a gap in exports can be considerably older than the name suggests. Every changelog states the actual span between the two exports it compared.

## How the Pages Are Published

The pages are static and there are around nine hundred of them, so the pipeline is careful
about claiming that any of them changed.

`docs/.pagestate.json` holds a content fingerprint and a last-changed date for every URL.
A page is rewritten only when its content differs from the fingerprint, which keeps a
no-op export from churning the whole tree through git, and `docs/sitemap.xml` takes each
`<lastmod>` from that date rather than from the export timestamp. The distinction matters:
stamping every URL with the export date told crawlers that all nine hundred pages changed
daily when the only difference was a printed timestamp, and a crawler that samples a few of
those and finds them identical stops trusting the dates — and stops crawling. The export
timestamp is therefore no longer printed on product pages at all; what each page shows
instead is the date that product itself last moved in the catalog.

Change history is accumulated in the same file. Each run folds its own export-to-export
diff into a per-product log, so a product page has something of its own to say beyond a
name and a version: version moves, which packages were added or removed, which
architectures and locales the product gained or dropped, and the first export it appeared
in. The log is kept whole — the page shows the most recent entries, but its release-cadence
figures ("30 version updates, roughly one every 6 days") are counted from all of them.
Those figures are anchored to the product's own changes, never to the current date, so a
page does not move just because time passed. On first run, with nothing to carry forward,
the log is seeded from the exports already in `archive/`; set `HISTORY_SEED` to bound that
backfill to the newest *N* exports. The same backfill runs once whenever the state file's
`version` is older than the script's `STATE_VERSION`, which is how a change to what the
history records reaches products whose history was already written.

After the push, the workflow submits the URLs that actually changed — and only those —
to [IndexNow](https://www.indexnow.org/), which Bing and several other engines accept as a
change notification. The key file is published at the site root; set an `INDEXNOW_KEY`
repository secret to pin or rotate the key, otherwise one is minted on the first run and
kept in the state file.

## Disclaimer

This project is not affiliated with, endorsed by, or in any way officially connected to Microsoft Corporation. All product names, trademarks, and registered trademarks are property of their respective owners.

The data displayed here is sourced from the Microsoft Graph API and is provided for informational purposes only. Accuracy and completeness depend on the timing of catalog exports and may not reflect the current state of the Intune EAM catalog at any given moment. No warranty, express or implied, is given regarding the accuracy, reliability, or fitness of this data for any particular purpose. Use at your own discretion.
