/* Intune EAM App Catalog — client logic
 * Fetches catalog.json, renders table/stats/changes/docs, handles search/filter/navigation.
 * Only this file and catalog.json change when data is updated.
 */

(function () {
  'use strict';

  // ── Bootstrap ─────────────────────────────────────────────────────────────
  fetch('catalog.json')
    .then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(function (data) {
      init(data.meta, data.apps, data.changes || {});
    })
    .catch(function (err) {
      document.getElementById('loading').innerHTML =
        '<p style="color:#ef4444;font-size:.875rem">Failed to load catalog.json: ' +
        err.message + '</p>';
    });

  // ── View switching ────────────────────────────────────────────────────────
  var VIEWS = ['catalog', 'stats', 'changes', 'docs'];

  function switchView(name) {
    VIEWS.forEach(function (v) {
      var el = document.getElementById('view-' + v);
      if (el) el.style.display = v === name ? '' : 'none';
    });
    document.querySelectorAll('.nav-btn[data-view]').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.view === name);
    });
  }

  // ── Init ──────────────────────────────────────────────────────────────────
  function init(meta, apps, changes) {
    document.getElementById('loading').style.display = 'none';
    document.getElementById('app').style.display     = '';

    // Header metadata
    document.getElementById('source-ts').textContent = meta.source_ts;
    document.getElementById('gen-date').textContent  = meta.generated;

    // GitHub links
    var repo = meta.repo_url || '';
    if (repo) {
      document.getElementById('repo-link').href   = repo;
      document.getElementById('footer-link').href = repo;

      // Derive GitHub Pages URL: github.com/user/repo → user.github.io/repo
      var m = repo.match(/github\.com\/([^/]+)\/([^/]+)/);
      if (m) {
        var feedUrl = 'https://' + m[1] + '.github.io/' + m[2] + '/feed.xml';
        var feedEl  = document.getElementById('rss-feed-url');
        if (feedEl) feedEl.textContent = feedUrl;
      }
    }

    // Nav buttons
    document.querySelectorAll('.nav-btn[data-view]').forEach(function (btn) {
      btn.addEventListener('click', function () { switchView(btn.dataset.view); });
    });

    // Imprint modal
    var overlay = document.getElementById('imprint-overlay');
    document.getElementById('imprint-open').addEventListener('click', function () {
      overlay.classList.add('open');
    });
    document.getElementById('imprint-close').addEventListener('click', function () {
      overlay.classList.remove('open');
    });
    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) overlay.classList.remove('open');
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') overlay.classList.remove('open');
    });

    // Stat cards strip (catalog view)
    var cardColors = ['#0078d4', '#7c3aed', '#0891b2', '#16a34a', '#f59e0b'];
    var statDefs = [
      { label: 'Total Packages',  value: meta.total.toLocaleString() },
      { label: 'Unique Products', value: meta.unique_products.toLocaleString() },
      { label: 'Publishers',      value: meta.publishers.toLocaleString() },
      { label: 'Auto-Update',     value: meta.auto_pct + '%' },
      { label: 'Locales',         value: String(meta.locales) },
    ];
    var statsEl    = document.getElementById('stats');
    var statValues = [];  // keep references to update on filter
    statDefs.forEach(function (s, i) {
      var el = document.createElement('div');
      el.className = 'stat-card';
      el.style.setProperty('--card-color', cardColors[i % cardColors.length]);
      el.innerHTML =
        '<div class="stat-value">' + s.value + '</div>' +
        '<div class="stat-label">' + s.label + '</div>';
      statsEl.appendChild(el);
      statValues.push(el.querySelector('.stat-value'));
    });

    // Populate filter dropdowns
    var archSet   = new Set();
    var localeSet = new Set();
    apps.forEach(function (a) {
      if (a.applicableArchitectures) archSet.add(a.applicableArchitectures);
      (a.locales || []).forEach(function (l) { localeSet.add(l); });
    });
    populateSelect('filter-arch',   archSet);
    populateSelect('filter-locale', localeSet);

    // Pre-render all catalog rows
    var tbody = document.getElementById('tbody');
    var rowMeta = apps.map(function (a, i) {
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td class="col-num">' + (i + 1) + '</td>' +
        '<td class="col-pub">'  + esc(a.publisherDisplayName)  + '</td>' +
        '<td class="col-name">' + esc(a.productDisplayName)    + '</td>' +
        '<td>'                  + esc(a.branchDisplayName)      + '</td>' +
        '<td class="col-ver">'  + esc(a.versionDisplayName)    + '</td>' +
        '<td>'                  + archTag(a.applicableArchitectures) + '</td>' +
        '<td>'                  + autoBadge(a.packageAutoUpdateCapable) + '</td>' +
        '<td>'                  + localeTags(a.locales)         + '</td>';
      tbody.appendChild(tr);
      return {
        tr:      tr,
        app:     a,
        search:  [a.publisherDisplayName, a.productDisplayName,
                  a.branchDisplayName, a.versionDisplayName].join('\0').toLowerCase(),
        arch:    a.applicableArchitectures || '',
        locales: a.locales || [],
        auto:    a.packageAutoUpdateCapable ? '1' : '0',
      };
    });

    // Column sort + resize (scoped to the catalog table only)
    initSort(rowMeta, tbody);
    initResize(tbody.parentElement);

    // Filter logic
    var searchEl  = document.getElementById('search');
    var archSel   = document.getElementById('filter-arch');
    var localeSel = document.getElementById('filter-locale');
    var autoSel   = document.getElementById('filter-autoupdate');
    var clearBtn  = document.getElementById('clear-btn');
    var countEl   = document.getElementById('result-count');
    var emptyEl   = document.getElementById('empty-state');
    var total     = apps.length;

    function hasFilters() {
      return searchEl.value !== '' || archSel.value !== ''
          || localeSel.value !== '' || autoSel.value !== '';
    }

    function applyFilters() {
      var q      = searchEl.value.toLowerCase().trim();
      var arch   = archSel.value;
      var locale = localeSel.value;
      var au     = autoSel.value;
      clearBtn.disabled = !hasFilters();
      var visible = 0;
      rowMeta.forEach(function (row) {
        var show =
          (!q      || row.search.includes(q))       &&
          (!arch   || row.arch === arch)             &&
          (!locale || row.locales.includes(locale)) &&
          (!au     || row.auto === au);
        row.tr.style.display = show ? '' : 'none';
        if (show) visible++;
      });
      countEl.innerHTML =
        '<span class="section-title">Packages</span>' +
        '<span class="section-count">' +
        (visible === total
          ? total.toLocaleString()
          : visible.toLocaleString() + ' of ' + total.toLocaleString()) +
        '</span>';
      emptyEl.style.display = visible === 0 ? '' : 'none';

      // Update stat cards to reflect the filtered set
      var visibleApps = rowMeta.filter(function (r) { return r.tr.style.display !== 'none'; })
                                .map(function (r) { return r.app; });
      var ft     = visibleApps.length;
      var fprod  = new Set(visibleApps.map(function (a) { return a.productId; })).size;
      var fpub   = new Set(visibleApps.map(function (a) { return a.publisherDisplayName; })).size;
      var fauto  = visibleApps.filter(function (a) { return a.packageAutoUpdateCapable; }).length;
      var fapct  = ft ? (fauto / ft * 100).toFixed(1) : '0';
      var floc   = new Set(visibleApps.reduce(function (acc, a) {
        return acc.concat(a.locales || []);
      }, [])).size;
      statValues[0].textContent = ft.toLocaleString();
      statValues[1].textContent = fprod.toLocaleString();
      statValues[2].textContent = fpub.toLocaleString();
      statValues[3].textContent = fapct + '%';
      statValues[4].textContent = String(floc);
    }

    [searchEl, archSel, localeSel, autoSel].forEach(function (el) {
      el.addEventListener('input', applyFilters);
    });
    clearBtn.addEventListener('click', function () {
      searchEl.value = ''; archSel.value = ''; localeSel.value = ''; autoSel.value = '';
      applyFilters();
    });
    applyFilters();

    // Render other views
    renderStatsView(apps, meta);
    renderChangesView(changes);
  }

  // ── Column Sort ───────────────────────────────────────────────────────────
  // col index → sort key extractor
  var SORT_KEYS = {
    1: function (r) { return (r.app.publisherDisplayName || '').toLowerCase(); },
    2: function (r) { return (r.app.productDisplayName   || '').toLowerCase(); },
    3: function (r) { return (r.app.branchDisplayName    || '').toLowerCase(); },
    4: function (r) { return (r.app.versionDisplayName   || '').toLowerCase(); },
    5: function (r) { return (r.app.applicableArchitectures || '').toLowerCase(); },
    6: function (r) { return r.app.packageAutoUpdateCapable ? 0 : 1; },
    7: function (r) { return (r.app.locales || []).length; },
  };

  function initSort(rowMeta, tbody) {
    var table   = tbody.parentElement;
    var ths     = table.querySelectorAll('thead th');
    var sortCol = -1, sortDir = 1;

    ths.forEach(function (th, i) {
      if (!SORT_KEYS[i]) return;
      th.classList.add('sortable');
      th.addEventListener('click', function () {
        if (sortCol === i) sortDir *= -1; else { sortCol = i; sortDir = 1; }
        ths.forEach(function (t) { t.classList.remove('sort-asc', 'sort-desc'); });
        th.classList.add(sortDir === 1 ? 'sort-asc' : 'sort-desc');

        var keyFn = SORT_KEYS[i];
        rowMeta.sort(function (a, b) {
          var av = keyFn(a), bv = keyFn(b);
          return av < bv ? -sortDir : av > bv ? sortDir : 0;
        });
        rowMeta.forEach(function (row) { tbody.appendChild(row.tr); });

        var n = 1;
        rowMeta.forEach(function (row) {
          var numCell = row.tr.querySelector('.col-num');
          if (numCell) numCell.textContent = row.tr.style.display === 'none' ? numCell.textContent : n++;
        });
      });
    });
  }

  // ── Column Resize ─────────────────────────────────────────────────────────
  function initResize(table) {
    var ths = table.querySelectorAll('thead th');
    ths.forEach(function (th, i) {
      if (i === ths.length - 1) return;
      var handle = document.createElement('div');
      handle.className = 'col-resize';
      th.appendChild(handle);
      handle.addEventListener('mousedown', function (e) {
        e.preventDefault();
        handle.classList.add('active');
        var startX = e.pageX, startW = th.offsetWidth;
        function onMove(e) { th.style.width = Math.max(40, startW + e.pageX - startX) + 'px'; }
        function onUp() {
          handle.classList.remove('active');
          document.removeEventListener('mousemove', onMove);
          document.removeEventListener('mouseup',   onUp);
        }
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup',   onUp);
      });
    });
  }

  // Generic DOM-based sort + resize for dynamically rendered tables (changes view)
  function initSimpleSort(table) {
    var ths     = table.querySelectorAll('thead th');
    var tbody   = table.querySelector('tbody');
    var sortCol = -1, sortDir = 1;
    ths.forEach(function (th, i) {
      th.classList.add('sortable');
      th.addEventListener('click', function () {
        if (sortCol === i) sortDir *= -1; else { sortCol = i; sortDir = 1; }
        ths.forEach(function (t) { t.classList.remove('sort-asc', 'sort-desc'); });
        th.classList.add(sortDir === 1 ? 'sort-asc' : 'sort-desc');
        var rows = Array.from(tbody.querySelectorAll('tr'));
        rows.sort(function (a, b) {
          var av = a.cells[i] ? a.cells[i].textContent.toLowerCase() : '';
          var bv = b.cells[i] ? b.cells[i].textContent.toLowerCase() : '';
          return av < bv ? -sortDir : av > bv ? sortDir : 0;
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
      });
    });
  }

  // ── Statistics view ───────────────────────────────────────────────────────
  function renderStatsView(apps, meta) {
    var pubCounts = {};
    apps.forEach(function (a) {
      var pub = a.publisherDisplayName || '(unknown)';
      pubCounts[pub] = (pubCounts[pub] || 0) + 1;
    });
    var pubSorted = Object.keys(pubCounts).sort(function (a, b) { return pubCounts[b] - pubCounts[a]; });
    var top10  = pubSorted.slice(0, 10);
    var maxPub = pubCounts[top10[0]] || 1;

    var archCounts = {};
    apps.forEach(function (a) {
      var arch = a.applicableArchitectures || '(unknown)';
      archCounts[arch] = (archCounts[arch] || 0) + 1;
    });
    var archSorted = Object.keys(archCounts).sort(function (a, b) { return archCounts[b] - archCounts[a]; });

    var localeCounts = {};
    apps.forEach(function (a) {
      (a.locales || []).forEach(function (l) { localeCounts[l] = (localeCounts[l] || 0) + 1; });
    });
    var localeSorted = Object.keys(localeCounts).sort(function (a, b) { return localeCounts[b] - localeCounts[a]; });

    var cardColors = ['#0078d4', '#7c3aed', '#0891b2', '#16a34a', '#f59e0b'];
    var statDefs = [
      { label: 'Total Packages',  value: meta.total.toLocaleString() },
      { label: 'Unique Products', value: meta.unique_products.toLocaleString() },
      { label: 'Publishers',      value: meta.publishers.toLocaleString() },
      { label: 'Auto-Update',     value: meta.auto_pct + '%' },
      { label: 'Locales',         value: String(meta.locales) },
    ];
    var html = '<div class="stats-grid">';
    statDefs.forEach(function (s, i) {
      html += '<div class="stats-block" style="--card-color:' + cardColors[i] + '">' +
        '<div class="stat-value">' + s.value + '</div>' +
        '<div class="stat-label">' + s.label + '</div></div>';
    });
    html += '</div>';

    html += '<div class="stats-block"><div class="stats-block-title">Top Publishers</div>';
    top10.forEach(function (pub) {
      var count = pubCounts[pub];
      var pct   = Math.round((count / maxPub) * 100);
      html += '<div class="pub-bar-row">' +
        '<span class="pub-name" title="' + esc(pub) + '">' + esc(pub) + '</span>' +
        '<div class="bar-wrap"><div class="bar-fill" style="width:' + pct + '%"></div></div>' +
        '<span class="pub-count">' + count.toLocaleString() + '</span></div>';
    });
    if (pubSorted.length > 10) html += '<p class="stats-note">Top 10 of ' + pubSorted.length.toLocaleString() + ' publishers.</p>';
    html += '</div>';

    html += '<div class="stats-block"><div class="stats-block-title">Architecture Breakdown</div>' +
      '<table class="stats-table"><thead><tr><th>Architecture</th><th>Packages</th><th>Share</th></tr></thead><tbody>';
    archSorted.forEach(function (arch) {
      var count = archCounts[arch];
      html += '<tr><td>' + archTag(arch) + '</td><td>' + count.toLocaleString() + '</td>' +
        '<td>' + ((count / apps.length) * 100).toFixed(1) + '%</td></tr>';
    });
    html += '</tbody></table></div>';

    html += '<div class="stats-block"><div class="stats-block-title">Supported Locales</div>' +
      '<table class="stats-table"><thead><tr><th>Locale</th><th>Packages</th></tr></thead><tbody>';
    localeSorted.slice(0, 20).forEach(function (locale) {
      html += '<tr><td><span class="tag tag-locale">' + esc(locale) + '</span></td>' +
        '<td>' + localeCounts[locale].toLocaleString() + '</td></tr>';
    });
    if (localeSorted.length > 20) {
      html += '<tr><td colspan="2" class="stats-note">… and ' + (localeSorted.length - 20) + ' more</td></tr>';
    }
    html += '</tbody></table></div>';

    document.getElementById('stats-content').innerHTML = html;
  }

  // ── Changes view ──────────────────────────────────────────────────────────
  function renderChangesView(changes) {
    var activePeriod = 'latest';

    var tabs = document.querySelectorAll('.changes-tab');
    tabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        tabs.forEach(function (t) { t.classList.remove('active'); });
        tab.classList.add('active');
        activePeriod = tab.dataset.period;
        renderChangePeriod(changes, activePeriod);
      });
    });

    renderChangePeriod(changes, activePeriod);
  }

  function renderChangePeriod(changes, period) {
    var el   = document.getElementById('changes-content');
    var data = changes[period];

    if (!data) {
      el.innerHTML = '<div class="changes-no-data">No data available for this period yet. ' +
        'Push more exports over time and this will populate automatically.</div>';
      return;
    }

    var added   = data.added   || [];
    var removed = data.removed || [];
    var updated = data.updated || [];

    // Stat cards — same markup as catalog strip
    var html = '<div class="stats">' +
      statCard(added.length,   'Added',   '#16a34a') +
      statCard(removed.length, 'Removed', '#dc2626') +
      statCard(updated.length, 'Updated', '#7c3aed') +
      '</div>';

    // Meta line
    html += '<div class="changes-meta">Compared to <strong>' + esc(data.compared_to) +
      '</strong> &mdash; exported <strong>' + esc(data.compared_to_ts) + '</strong></div>';

    if (added.length) {
      html += changeSection('Added', added, [
        { label: 'Publisher', key: 'publisherDisplayName',    cls: 'col-pub' },
        { label: 'App Name',  key: 'productDisplayName',      cls: '' },
        { label: 'Branch',    key: 'branchDisplayName',       cls: '' },
        { label: 'Version',   key: 'versionDisplayName',      cls: 'col-ver' },
        { label: 'Arch',      key: 'applicableArchitectures', cls: '', render: archTag },
        { label: 'Auto-Update', key: 'packageAutoUpdateCapable', cls: '', render: autoBadge },
      ]);
    }
    if (removed.length) {
      html += changeSection('Removed', removed, [
        { label: 'Publisher',    key: 'publisherDisplayName',    cls: 'col-pub' },
        { label: 'App Name',     key: 'productDisplayName',      cls: '' },
        { label: 'Branch',       key: 'branchDisplayName',       cls: '' },
        { label: 'Last Version', key: 'versionDisplayName',      cls: 'col-ver' },
        { label: 'Arch',         key: 'applicableArchitectures', cls: '', render: archTag },
        { label: 'Auto-Update',  key: 'packageAutoUpdateCapable', cls: '', render: autoBadge },
      ]);
    }
    if (updated.length) {
      html += changeSection('Updated', updated, [
        { label: 'Publisher',    key: 'publisherDisplayName',    cls: 'col-pub' },
        { label: 'App Name',     key: 'productDisplayName',      cls: '' },
        { label: 'Branch',       key: 'branchDisplayName',       cls: '' },
        { label: 'Prev Version', key: 'prevVersionDisplayName',  cls: 'col-ver' },
        { label: 'New Version',  key: 'versionDisplayName',      cls: 'col-ver' },
        { label: 'Arch',         key: 'applicableArchitectures', cls: '', render: archTag },
      ]);
    }
    if (!added.length && !removed.length && !updated.length) {
      html += '<div class="changes-empty">No changes detected for this period.</div>';
    }

    el.innerHTML = html;

    // Init sort + resize on every table injected into the changes view
    el.querySelectorAll('table').forEach(function (table) {
      initSimpleSort(table);
      initResize(table);
    });
  }

  function statCard(value, label, color) {
    return '<div class="stat-card" style="--card-color:' + color + '">' +
      '<div class="stat-value">' + value.toLocaleString() + '</div>' +
      '<div class="stat-label">' + label + '</div></div>';
  }

  function changeSection(title, rows, cols) {
    var html =
      '<div class="changes-section">' +
      '<div class="toolbar">' +
      '<span class="section-title">' + title + '</span>' +
      '<span class="section-count">' + rows.length.toLocaleString() + '</span>' +
      '</div>' +
      '<div class="table-card"><div class="table-scroll" style="max-height:400px"><table><thead><tr>';
    cols.forEach(function (c) { html += '<th>' + c.label + '</th>'; });
    html += '</tr></thead><tbody>';
    rows.forEach(function (row) {
      html += '<tr>';
      cols.forEach(function (c) {
        var val = row[c.key];
        var cell = c.render ? c.render(val) : esc(val || '');
        html += '<td class="' + (c.cls || '') + '">' + cell + '</td>';
      });
      html += '</tr>';
    });
    html += '</tbody></table></div></div></div>';
    return html;
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function populateSelect(id, valueSet) {
    var sel = document.getElementById(id);
    Array.from(valueSet).sort().forEach(function (v) {
      var opt = document.createElement('option');
      opt.value = opt.textContent = v;
      sel.appendChild(opt);
    });
  }

  function esc(s) {
    return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function autoBadge(capable) {
    return capable
      ? '<span class="badge badge-yes">Yes</span>'
      : '<span class="badge badge-no">No</span>';
  }

  function archTag(arch) {
    return arch
      ? '<span class="tag tag-arch">' + esc(arch) + '</span>'
      : '<span style="color:var(--text-3)">—</span>';
  }

  function localeTags(locales) {
    return (locales || [])
      .map(function (l) { return '<span class="tag tag-locale">' + esc(l) + '</span>'; })
      .join('');
  }

}());
