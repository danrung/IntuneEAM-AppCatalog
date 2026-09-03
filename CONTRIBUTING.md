# Contributing

Thanks for your interest in the IntuneEAM AppCatalog. This repository is mostly generated data: the catalog exports come straight from the Microsoft Graph API, and everything else (tables, statistics, change history, the website) is built from them by a GitHub Actions workflow. That shapes what kind of contributions make sense.

## Reporting a problem with the catalog data

If an app is missing, shows a wrong version, or looks otherwise off, please open an issue and include:

- The app and publisher name as shown on [eam.drung.dev](https://eam.drung.dev/)
- What you expected to see, and where you saw it (website, `catalog.md`, RSS feed, app page)
- The export timestamp from the page footer or the `Last Export` value in the README

Note that the data reflects what Microsoft Graph returned at export time. If the catalog itself is wrong, the fix has to happen on Microsoft's side, but an issue still helps others who run into the same thing.

## Please do not edit generated files

The following files are rewritten by the workflow on every export and any manual edits will be overwritten:

- `catalog.md`, `stats.md`, `changes*.md`
- The statistics block in `README.md`
- `docs/catalog.json`, `docs/changes.json`, `docs/summary.json`, `docs/feed.xml`, `docs/sitemap.xml`
- Everything under `docs/apps/`

If something in those files is wrong, the fix belongs in `.github/scripts/generate_docs.py`.

## Code and website changes

Pull requests for the generator script, the workflow, or the website in `docs/` are welcome. A few things that make review easier:

- Keep the change focused on one thing.
- Describe what changed and why in the pull request. A screenshot helps for anything visual.
- For website changes, check both the light and dark theme.
- For generator changes, run the script locally against the current export in `catalog/` and make sure the output still builds. The workflow file shows the exact steps.

## Feature ideas and questions

Use [Discussions](https://github.com/danrung/IntuneEAM-AppCatalog/discussions) for questions, ideas, or anything that is not a concrete bug. Issues are for things that are broken.

## License

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).
