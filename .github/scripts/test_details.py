#!/usr/bin/env python3
"""
Tests for the deployment-details part of generate_docs.py.

The fixtures are real templates from convertFromMobileAppCatalogPackage, in
the exporter's format, picked because each one exercises a case the code has
to get right: an MSI with registry detection (7-Zip), locales that differ only
in installer size (Firefox), a three-rule detection pinned to a DLL version
(BCF Manager), a detection threshold above the catalog version (Thunderbird),
an MSI behind an EXE (Notepad++), a user-context install (PowerToys) and an
unusual success code (Android Studio).

Standard library only, like the generator. Run from the repository root:
    python .github/scripts/test_details.py
"""

import copy
import gzip
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import generate_docs as g  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")


def _load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


CATALOG = _load("catalog.json")
DETAILS = _load("details.json")


def pkg(product, branch=None):
    for a in CATALOG:
        if a["productDisplayName"] == product and (branch is None or a["branchDisplayName"] == branch):
            return a
    raise KeyError(product)


def detail(product, branch=None):
    return copy.deepcopy(DETAILS[pkg(product, branch)["id"]]["detail"])


class RuleText(unittest.TestCase):
    def test_registry_rule(self):
        rules = detail("7-Zip")["rules"]
        self.assertEqual(len(rules), 1)
        kind, subject, cond = g._rule_parts(rules[0])
        self.assertEqual(kind, "Registry")
        self.assertTrue(subject.startswith("HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{"))
        self.assertEqual(cond, "DisplayVersion = 26.05.00.0")

    def test_file_rules(self):
        texts = [g._rule_text(r) for r in detail("BCF Manager for Tekla")["rules"]]
        self.assertIn("size in bytes = 942592", texts[0])
        self.assertTrue(texts[0].startswith("File: %SystemDrive%\\APPDIR"))
        self.assertIn("file version = 5.4.6.855", texts[1])

    def test_app_version_threshold(self):
        d = detail("Mozilla Firefox")
        self.assertIn("firefox.exe — app version ≥ ", g._rule_text(d["rules"][0]))

    def test_unknown_rule_type_does_not_raise(self):
        self.assertEqual(g._rule_parts({"@odata.type": "#microsoft.graph.win32LobAppNewRule"})[0], "New")


class DetailChanges(unittest.TestCase):
    def test_identical(self):
        d = detail("7-Zip")
        self.assertEqual(g._detail_changes(d, copy.deepcopy(d), True), [])

    def test_version_bump_noise_is_ignored(self):
        """A new version moves the file name, size, product code and threshold —
        none of that is news."""
        old = detail("Notepad++", "Notepad++ (x64) (msi)")
        new = copy.deepcopy(old)
        pc_old, pc_new = old["msiInformation"]["productCode"], "{11111111-2222-3333-4444-555555555555}"
        new["fileName"] = new["setupFilePath"] = old["fileName"].replace("8.9.8.1", "8.9.8.2")
        new["installCommandLine"] = old["installCommandLine"].replace(old["fileName"], new["fileName"])
        new["uninstallCommandLine"] = old["uninstallCommandLine"].replace(pc_old, pc_new)
        new["msiInformation"]["productCode"] = pc_new
        new["size"] += 4096
        for r in new["rules"]:
            r["keyPath"] = r["keyPath"].replace(pc_old, pc_new)
            r["comparisonValue"] = "8.9.8.2"
        self.assertEqual(g._detail_changes(old, new, same_version=False), [])

    def test_version_bump_with_new_switch(self):
        old = detail("7-Zip")
        new = copy.deepcopy(old)
        new["installCommandLine"] += " /norestart"
        self.assertEqual([c["label"] for c in g._detail_changes(old, new, False)], ["Install command"])

    def test_version_bump_with_other_detection_method(self):
        old = detail("Mozilla Firefox")
        new = copy.deepcopy(old)
        new["rules"][0]["operationType"] = "exists"
        self.assertEqual([c["label"] for c in g._detail_changes(old, new, False)], ["Detection rules"])

    def test_same_version_counts_every_value(self):
        old = detail("BCF Manager for Tekla")
        new = copy.deepcopy(old)
        new["size"] += 1
        new["rules"][0]["comparisonValue"] = "942600"
        labels = [c["label"] for c in g._detail_changes(old, new, True)]
        self.assertEqual(labels, ["Detection rules", "Installer size"])

    def test_upgrade_code_change_is_news_across_versions(self):
        old = detail("7-Zip")
        new = copy.deepcopy(old)
        new["msiInformation"]["upgradeCode"] = "{00000000-0000-0000-0000-000000000001}"
        self.assertEqual([c["label"] for c in g._detail_changes(old, new, False)], ["MSI upgrade code"])

    def test_labels_do_not_collide_with_catalog_fields(self):
        catalog_labels = {label for _, label in g.TRACKED_FIELDS}
        self.assertFalse(catalog_labels & {label for _, label in g.DETAIL_TRACKED})


class ExportPairs(unittest.TestCase):
    """_compute_changes against files on disk, the way the workflow lays them out."""

    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        os.makedirs("catalog")
        os.makedirs("archive")
        # Keyed by path relative to the working directory, which these tests change.
        g._read_details.cache_clear()

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()
        g._read_details.cache_clear()

    def write(self, ts, apps, details=None, gz=False, folder="catalog"):
        path = os.path.join(folder, f"{ts}_AppCatalog.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(apps, f)
        if details is not None:
            blob = json.dumps(details).encode("utf-8")
            dpath = os.path.join(folder, f"{ts}_AppDetails.json" + (".gz" if gz else ""))
            with open(dpath, "wb") as f:
                f.write(gzip.compress(blob) if gz else blob)
        return path

    def test_first_details_export_is_a_baseline(self):
        prev = self.write("20261101_000000", CATALOG, folder="archive")
        curr = self.write("20261102_000000", CATALOG, DETAILS)
        s, *_ = g._compute_changes(CATALOG, CATALOG, curr, prev)
        self.assertNotIn("details_compared", s)
        self.assertNotIn("repackaged", s)

    def test_repackaged_reissued_and_gzipped_archive(self):
        prev = self.write("20261101_000000", CATALOG, DETAILS, gz=True, folder="archive")
        apps, details = copy.deepcopy(CATALOG), copy.deepcopy(DETAILS)

        def rekey(product, branch=None):
            a = next(x for x in apps if x["productDisplayName"] == product
                     and (branch is None or x["branchDisplayName"] == branch))
            old, a["id"] = a["id"], a["id"][:-1] + ("0" if a["id"][-1] != "0" else "1")
            details[a["id"]] = details.pop(old)
            return details[a["id"]]["detail"]

        rekey("BCF Manager for Tekla")["rules"][0]["comparisonValue"] = "942600"
        rekey("Microsoft PowerToys")            # new id, nothing else
        curr = self.write("20261102_000000", apps, details)

        s, added, removed, updated = g._compute_changes(apps, CATALOG, curr, prev)
        self.assertTrue(s["details_compared"])
        self.assertEqual([a["productDisplayName"] for a in s["repackaged"]], ["BCF Manager for Tekla"])
        self.assertEqual([c["label"] for c in s["repackaged"][0]["detailChanges"]], ["Detection rules"])
        self.assertEqual(s["reissued_count"], 1)
        self.assertEqual((added, removed, updated), ([], [], []))

        md = "\n".join(g._render_changes_md(s, added, removed, updated, curr, "t"))
        self.assertIn("| 🧩 Repackaged | 1 |", md)
        self.assertIn("## 🧩 Repackaged (1 package)", md)
        self.assertIn("1 further package got a new package id", md)

    def test_failed_conversion_is_not_a_change(self):
        prev = self.write("20261101_000000", CATALOG, DETAILS)
        details = copy.deepcopy(DETAILS)
        pid = pkg("7-Zip")["id"]
        details[pid] = {"branchId": pkg("7-Zip")["branchId"], "error": "503"}
        curr = self.write("20261102_000000", CATALOG, details)
        s, *_ = g._compute_changes(CATALOG, CATALOG, curr, prev)
        self.assertEqual(s["repackaged"], [])
        self.assertEqual(g.detail_errors(curr), {pid: "503"})
        self.assertNotIn(pid, g.load_details(curr))


class History(unittest.TestCase):
    def test_repackaged_entry_and_merge(self):
        app = pkg("BCF Manager for Tekla")
        structured = {"repackaged": [dict(app, detailChanges=[{"field": "detection", "label": "Detection rules"}])]}
        slug_by_key = {g._product_key(app): "bcf"}
        per = g._summarise_changes(structured, slug_by_key, "2026-11-05")
        self.assertEqual(per["bcf"]["repackaged"], 1)
        self.assertEqual(per["bcf"]["repack_fields"], ["Detection rules"])
        merged = g._merge_entries(per["bcf"], copy.deepcopy(per["bcf"]))
        self.assertEqual(merged["repackaged"], 2)
        self.assertEqual(merged["repack_fields"], ["Detection rules"])
        html, _ = g._render_history([merged], "2025-12-07")
        self.assertIn("Repackaged without a version change &mdash; Detection rules changed", html)


class Pages(unittest.TestCase):
    def test_firefox_locales_share_one_configuration(self):
        packages = [a for a in CATALOG if a["productDisplayName"] == "Mozilla Firefox"]
        details = {k: v["detail"] for k, v in DETAILS.items()}
        html, description, installers = g._render_deploy(packages, details)
        self.assertIn("All 3 packages share one configuration.", html)
        self.assertNotIn("app-deploy", html)          # no per-group toggles
        self.assertIn("&ndash;", html)                # a size range
        self.assertEqual(installers, {"EXE"})
        self.assertTrue(description)

    def test_two_configurations_become_groups(self):
        packages = [a for a in CATALOG if a["productDisplayName"] == "Notepad++"]
        details = {k: v["detail"] for k, v in DETAILS.items()}
        html, _, installers = g._render_deploy(packages, details)
        self.assertEqual(html.count('<details class="app-deploy"'), 2)
        self.assertEqual(html.count(" open>"), 1)
        self.assertEqual(installers, {"MSI"})

    def test_no_details_renders_nothing(self):
        self.assertEqual(g._render_deploy(CATALOG, {}), ("", "", set()))

    def test_commands_are_escaped(self):
        packages = [pkg("7-Zip")]
        d = detail("7-Zip")
        d["installCommandLine"] = 'setup.exe /x "<a>&b"'
        html, _, _ = g._render_deploy(packages, {packages[0]["id"]: d})
        self.assertIn("setup.exe /x &quot;&lt;a&gt;&amp;b&quot;", html)


class Quirks(unittest.TestCase):
    def ctx(self):
        details = {k: v["detail"] for k, v in DETAILS.items()}
        return {"apps": CATALOG, "errors": {}, "slug_of": {},
                "items": [{"app": a, "slug": "x", "detail": details[a["id"]],
                           "view": g._detail_view(details[a["id"]])} for a in CATALOG]}

    def products(self, rows):
        return sorted({r[0].split(">")[1].split("<")[0] for r in rows})

    def test_versions_agree(self):
        for cat_v, rule_v in (("26.05", "26.05.00.0"), ("1.1.18", "1.1.18.2"),
                              ("1.14.29743", "1.14.9.29743"), ("8.9.8.1", "8.9.8.1")):
            self.assertTrue(g._versions_agree(cat_v, rule_v), (cat_v, rule_v))
        for cat_v, rule_v in (("115.8.0", "115.16.3.0"), ("6.2.16", "5.4.6.855")):
            self.assertFalse(g._versions_agree(cat_v, rule_v), (cat_v, rule_v))

    def test_rules(self):
        c = self.ctx()
        self.assertEqual(self.products(g._q_detection_above(c)), ["Mozilla Thunderbird"])
        self.assertIn("BCF Manager for Tekla", self.products(g._q_detection_pinned(c)))
        self.assertEqual(self.products(g._q_user_context(c)), ["Microsoft PowerToys"])
        self.assertEqual(self.products(g._q_msi_in_exe(c)), ["Notepad++"])
        self.assertEqual(self.products(g._q_unusual_success(c)), ["Android Studio 4"])

    def test_page_renders(self):
        html = g._render_quirks(self.ctx(), "https://example.test/", "2026-11-05 12:00:00", "")
        self.assertIn("<h1 class=\"app-title\">Catalog quirks</h1>", html)
        self.assertIn('href="quirks.html">Quirks</a>', html)
        for qid, *_ in g.QUIRK_RULES:
            self.assertIn(f'id="{qid}"', html)


class WritePage(unittest.TestCase):
    def test_carriage_returns_do_not_cause_rewrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.html")
            state, changed = {"pages": {}}, []
            page = "<p>line one\r\n\r\nline two\rthree</p>\n"
            self.assertTrue(g._write_page(path, page, "u", state, "2026-11-05", changed))
            self.assertFalse(g._write_page(path, page, "u", state, "2026-11-06", changed))
            self.assertEqual(changed, ["u"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
