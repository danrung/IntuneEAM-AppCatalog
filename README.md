# IntuneEAM AppCatalog

A reference for the **Microsoft Intune Enterprise Application Management (EAM)** app catalog. If you work with Intune EAM and have ever wanted a quick, browsable overview of what's actually in the catalog, what apps are available, which publishers are represented, what changed recently, here it is.

The raw data comes from the Microsoft Graph API. Exports are dropped into this repo and a GitHub Actions workflow takes care of building the tables, stats, and change history automatically.

## What's in Here

🌐 **[Live Catalog](https://danrung.github.io/IntuneEAM-AppCatalog/)** Searchable, filterable website with the full package list.

📦 **[App Catalog](catalog.md)** Every available package, sorted by publisher and app name.

📊 **[Statistics](stats.md)** Total counts, top publishers, architecture breakdown, and available locales.

🔄 **[Changes](changes.md)** What was added, removed, or version-updated between the two most recent exports.

📅 **[Daily Changes](changes_daily.md)** Changes compared to the nearest export from at least 24 hours ago.

📆 **[Weekly Changes](changes_weekly.md)** Changes compared to the nearest export from at least 7 days ago.

🗓️ **[Monthly Changes](changes_monthly.md)** Changes compared to the nearest export from at least 30 days ago.

📡 **[RSS Feed](https://danrung.github.io/IntuneEAM-AppCatalog/feed.xml)** Subscribe to catalog changes in any RSS reader.

Raw exports: [catalog/](catalog/) for the current file, [archive/](archive/) for the full history.

## RSS Feed

Stay up to date without checking the site manually. Every time a new catalog export is processed, a new entry is published to the feed.

**Feed URL:**
```
https://danrung.github.io/IntuneEAM-AppCatalog/feed.xml
```

Each feed item contains:
- How many packages were added, removed, and updated
- A full list of added and removed packages with publisher, name, and version
- A list of updated packages showing the previous and new version
- The timestamp of the catalog export it is based on

The feed keeps the last 50 entries. Compatible with any RSS reader — Feedly, Outlook, Thunderbird, Apple Mail, or any other client that supports RSS 2.0.

## Latest Statistics

<!-- CATALOG_STATS_START -->
| Metric | Value |
|--------|-------|
| Total Packages | **1,516** |
| Unique Products | 915 |
| Publishers | 493 |
| Auto-Update Capable | 361 (23.8%) |
| Available Locales | 64 |
| Last Export | 2026-04-20 07:35:17 |
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
| `id` | Unique package instance ID |

## Understanding the Stats and Changes

One product can ship as multiple packages, for example separate x64 and x86 branches each get their own entry. The statistics page accounts for this by tracking both raw package count and unique product count.

The changes pages compare package IDs between exports and surface three things: packages that are new, packages that have been removed, and packages where the version string changed. The period-based changelogs (daily, weekly, monthly) use the timestamp embedded in each filename to find the right comparison point automatically.

## Disclaimer

This project is not affiliated with, endorsed by, or in any way officially connected to Microsoft Corporation. All product names, trademarks, and registered trademarks are property of their respective owners.

The data displayed here is sourced from the Microsoft Graph API and is provided for informational purposes only. Accuracy and completeness depend on the timing of catalog exports and may not reflect the current state of the Intune EAM catalog at any given moment. No warranty, express or implied, is given regarding the accuracy, reliability, or fitness of this data for any particular purpose. Use at your own discretion.
