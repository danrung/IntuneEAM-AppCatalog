/* Intune EAM App Catalog — client logic
 * Fetches catalog.json, renders table/stats/changes/docs, handles search/filter/navigation.
 * Only this file and catalog.json change when data is updated.
 */

(function () {
  'use strict';

  // ── Theme ─────────────────────────────────────────────────────────────────
  // The initial theme is resolved by the inline script in <head>. Here we wire the
  // toggle and keep following the OS until the user makes an explicit choice.
  function initTheme() {
    var root = document.documentElement;
    var btn  = document.getElementById('theme-toggle');
    var mq   = window.matchMedia('(prefers-color-scheme: dark)');

    function stored() {
      try { return localStorage.getItem('theme'); } catch (e) { return null; }
    }
    function syncLabel() {
      if (btn) btn.title = 'Switch to ' + (root.dataset.theme === 'dark' ? 'light' : 'dark') + ' theme';
    }

    mq.addEventListener('change', function (e) {
      if (stored() === 'light' || stored() === 'dark') return;   // user chose explicitly
      root.dataset.theme = e.matches ? 'dark' : 'light';
      syncLabel();
    });

    if (btn) {
      btn.addEventListener('click', function () {
        var next = root.dataset.theme === 'dark' ? 'light' : 'dark';
        root.dataset.theme = next;
        try { localStorage.setItem('theme', next); } catch (e) { /* storage blocked */ }
        syncLabel();
      });
    }
    syncLabel();
  }
  initTheme();

  // ── Bootstrap ─────────────────────────────────────────────────────────────
  fetch('catalog.json')
    .then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(function (data) {
      init(data.meta, data.apps);
    })
    .catch(function (err) {
      document.getElementById('loading').innerHTML =
        '<p style="color:#ef4444;font-size:.875rem">Failed to load catalog.json: ' +
        esc(err.message) + '</p>';
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
    if (name === 'changes') showChanges();
  }

  // ── Init ──────────────────────────────────────────────────────────────────
  function init(meta, apps) {
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

    }

    // Feed URL from the page's own location — works on the custom domain and on github.io
    var feedEl = document.getElementById('rss-feed-url');
    if (feedEl && location.protocol.indexOf('http') === 0) {
      feedEl.textContent = new URL('feed.xml', location.href).href;
    }

    // Nav buttons — the hash mirrors the view so the static pages under apps/
    // can deep-link into a view (../#stats) and back/forward moves between views.
    document.querySelectorAll('.nav-btn[data-view]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var v = btn.dataset.view;
        if (v === 'catalog') {
          history.replaceState(null, '', location.pathname + location.search);
        } else if (location.hash !== '#' + v) {
          location.hash = v;
        }
        switchView(v);
      });
    });

    // Imprint modal — focus moves into the dialog on open, Tab stays inside it,
    // and closing hands focus back to wherever it came from.
    var overlay   = document.getElementById('imprint-overlay');
    var modal     = overlay.querySelector('.imprint-modal');
    var lastFocus = null;

    function openImprint() {
      lastFocus = document.activeElement;
      overlay.classList.add('open');
      document.getElementById('imprint-close').focus();
    }
    function closeImprint() {
      overlay.classList.remove('open');
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    }

    document.getElementById('imprint-open').addEventListener('click', openImprint);
    document.getElementById('imprint-close').addEventListener('click', closeImprint);
    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) closeImprint();
    });
    document.addEventListener('keydown', function (e) {
      if (!overlay.classList.contains('open')) return;
      if (e.key === 'Escape') { closeImprint(); return; }
      if (e.key !== 'Tab') return;
      var focusables = modal.querySelectorAll('a[href], button');
      if (!focusables.length) return;
      var first = focusables[0], last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first)      { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });

    // Apply the view (or the imprint modal) named in the URL hash, now and on
    // every hash change. Unknown hashes fall back to the catalog.
    function applyHash() {
      var h = location.hash.replace('#', '');
      if (h === 'imprint') { openImprint(); return; }
      switchView(VIEWS.indexOf(h) !== -1 ? h : 'catalog');
    }
    window.addEventListener('hashchange', applyHash);
    applyHash();

    // Stat cards strip (catalog view)
    var cardColors = ['var(--card-1)', 'var(--card-2)', 'var(--card-3)',
                      'var(--card-4)', 'var(--card-5)'];
    // `action` marks the cards that double as filters. Publishers and Locales
    // stay read-only — they count values spread across rows, so there is no
    // single row predicate a click could stand for.
    var statDefs = [
      { label: 'Total Packages',  value: meta.total.toLocaleString(),           action: 'reset',
        hint: 'Clear all filters' },
      { label: 'Unique Products', value: meta.unique_products.toLocaleString(), action: 'unique',
        hint: 'Show one row per product — the first version listed' },
      { label: 'Publishers',      value: meta.publishers.toLocaleString() },
      { label: 'Auto-Update',     value: meta.auto_pct + '%',                   action: 'auto',
        hint: 'Filter by auto-update capability — Yes, then No, then off' },
      { label: 'Locales',         value: String(meta.locales) },
    ];
    var statsEl    = document.getElementById('stats');
    var statValues = [];  // keep references to update on filter
    var statLabels = [];
    var statCards  = [];
    statDefs.forEach(function (s, i) {
      var el = document.createElement('div');
      el.className = 'stat-card' + (s.action ? ' stat-card-filter' : '');
      el.style.setProperty('--card-color', cardColors[i % cardColors.length]);
      el.innerHTML =
        '<div class="stat-value">' + s.value + '</div>' +
        '<div class="stat-label">' + s.label + '</div>';
      if (s.action) {
        el.dataset.action = s.action;
        el.setAttribute('role', 'button');
        el.setAttribute('tabindex', '0');
        el.setAttribute('aria-pressed', 'false');
        el.title = s.hint;
        el.addEventListener('click', function () { runCardAction(s.action); });
        el.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); runCardAction(s.action); }
        });
      }
      statsEl.appendChild(el);
      statValues.push(el.querySelector('.stat-value'));
      statLabels.push(el.querySelector('.stat-label'));
      statCards.push(el);
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
        '<td class="col-name">' + nameCell(a)                  + '</td>' +
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
        product: a.productId || (a.publisherDisplayName + '\0' + a.productDisplayName),
      };
    });

    // First row per product, decided once on the source order so re-sorting the
    // table cannot change which version represents a product.
    var seenProducts = Object.create(null);
    rowMeta.forEach(function (row) {
      row.firstOfProduct = !seenProducts[row.product];
      seenProducts[row.product] = true;
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

    var uniqueOnly = false;   // set by the Unique Products card; has no dropdown

    function hasFilters() {
      return searchEl.value !== '' || archSel.value !== ''
          || localeSel.value !== '' || autoSel.value !== '' || uniqueOnly;
    }

    // Cards drive the same filters the controls do, so both stay in step.
    function runCardAction(action) {
      if (action === 'reset') {
        if (!hasFilters()) return;
        searchEl.value = ''; archSel.value = ''; localeSel.value = ''; autoSel.value = '';
        uniqueOnly = false;
      } else if (action === 'unique') {
        uniqueOnly = !uniqueOnly;
      } else if (action === 'auto') {
        autoSel.value = autoSel.value === '1' ? '0' : (autoSel.value === '0' ? '' : '1');
      }
      applyFilters();
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
          (!au     || row.auto === au)               &&
          (!uniqueOnly || row.firstOfProduct);
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

      // Card state — driven by the filters themselves, so using the dropdowns
      // lights up the matching card too.
      statLabels[3].textContent = 'Auto-Update' +
        (autoSel.value === '1' ? ' · Yes' : autoSel.value === '0' ? ' · No' : '');
      setCardState(statCards[1], uniqueOnly);
      setCardState(statCards[3], autoSel.value !== '');
      statCards[0].classList.toggle('is-idle', !hasFilters());
      statsEl.classList.toggle('has-filter', uniqueOnly || autoSel.value !== '');
    }

    function setCardState(card, on) {
      card.classList.toggle('active', on);
      card.setAttribute('aria-pressed', on ? 'true' : 'false');
    }

    [searchEl, archSel, localeSel, autoSel].forEach(function (el) {
      el.addEventListener('input', applyFilters);
    });
    clearBtn.addEventListener('click', function () {
      searchEl.value = ''; archSel.value = ''; localeSel.value = ''; autoSel.value = '';
      uniqueOnly = false;
      applyFilters();
    });
    applyFilters();

    // Render other views
    renderStatsView(apps, meta);
    renderChangesView();
  }

  // ── Column Sort ───────────────────────────────────────────────────────────
  // Numeric-aware compare so version strings order naturally ("9.0" before "10.1").
  function cmpValues(av, bv) {
    if (typeof av === 'string' && typeof bv === 'string') {
      return av.localeCompare(bv, undefined, { numeric: true });
    }
    return av < bv ? -1 : av > bv ? 1 : 0;
  }

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
          return cmpValues(keyFn(a), keyFn(b)) * sortDir;
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
          return cmpValues(av, bv) * sortDir;
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
    var maxPub = pubCounts[pubSorted[0]] || 1;

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

    var cardColors = ['var(--card-1)', 'var(--card-2)', 'var(--card-3)',
                      'var(--card-4)', 'var(--card-5)'];
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
    pubSorted.forEach(function (pub, i) {
      var count = pubCounts[pub];
      var pct   = Math.round((count / maxPub) * 100);
      html += '<div class="pub-bar-row' + (i >= 10 ? ' stats-extra' : '') + '">' +
        '<span class="pub-name" title="' + esc(pub) + '">' + esc(pub) + '</span>' +
        '<div class="bar-wrap"><div class="bar-fill" style="width:' + pct + '%"></div></div>' +
        '<span class="pub-count">' + count.toLocaleString() + '</span></div>';
    });
    if (pubSorted.length > 10) {
      html += statsToggle('Show all ' + pubSorted.length.toLocaleString() + ' publishers',
                          'Show top 10 only');
    }
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
    localeSorted.forEach(function (locale, i) {
      html += '<tr' + (i >= 20 ? ' class="stats-extra"' : '') + '>' +
        '<td><span class="tag tag-locale">' + esc(locale) + '</span></td>' +
        '<td>' + localeCounts[locale].toLocaleString() + '</td></tr>';
    });
    html += '</tbody></table>';
    if (localeSorted.length > 20) {
      html += statsToggle('Show all ' + localeSorted.length + ' locales', 'Show top 20 only');
    }
    html += '</div>';

    var statsRoot = document.getElementById('stats-content');
    statsRoot.innerHTML = html;

    // Each toggle expands its own block; the hidden rows are already rendered.
    statsRoot.querySelectorAll('.stats-toggle').forEach(function (btn) {
      var block = btn.closest('.stats-block');
      btn.addEventListener('click', function () {
        var open = block.classList.toggle('expanded');
        btn.textContent = open ? btn.dataset.less : btn.dataset.more;
      });
    });
  }

  function statsToggle(moreLabel, lessLabel) {
    return '<button type="button" class="stats-toggle" data-more="' + esc(moreLabel) +
      '" data-less="' + esc(lessLabel) + '">' + esc(moreLabel) + '</button>';
  }

  // ── Changes view ──────────────────────────────────────────────────────────
  // Change sets live in their own file and are fetched the first time the tab
  // is opened — they are roughly a third of the payload and most visits never
  // need them.
  var changesData    = null;
  var changesPromise = null;
  var activePeriod   = 'latest';

  function loadChanges() {
    if (!changesPromise) {
      changesPromise = fetch('changes.json')
        .then(function (r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(function (d) { changesData = d || {}; });
    }
    return changesPromise;
  }

  function showChanges() {
    if (changesData) { renderChangePeriod(activePeriod); return; }
    var el = document.getElementById('changes-content');
    el.innerHTML = '<div class="changes-no-data">Loading changes…</div>';
    loadChanges()
      .then(function () { renderChangePeriod(activePeriod); })
      .catch(function (err) {
        changesPromise = null;   // let a later visit retry
        el.innerHTML = '<div class="changes-no-data">Failed to load changes.json: ' +
          esc(err.message) + '</div>';
      });
  }

  function renderChangesView() {
    var tabs = document.querySelectorAll('.changes-tab');
    tabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        tabs.forEach(function (t) { t.classList.remove('active'); });
        tab.classList.add('active');
        activePeriod = tab.dataset.period;
        showChanges();   // still loading on a fast click — showChanges waits
      });
    });
  }

  function renderChangePeriod(period) {
    var el   = document.getElementById('changes-content');
    var data = changesData ? changesData[period] : null;

    if (!data) {
      el.innerHTML = '<div class="changes-no-data">No data available for this period yet. ' +
        'Push more exports over time and this will populate automatically.</div>';
      return;
    }

    var added   = data.added   || [];
    var removed = data.removed || [];
    var updated = data.updated || [];

    // Stat cards — same markup as catalog strip, but each one filters the
    // sections below to its own kind; clicking the active card clears it again.
    var html = '<div class="stats">' +
      statCard(added.length,   'Added',   'var(--c-added)',   'Added') +
      statCard(removed.length, 'Removed', 'var(--c-removed)', 'Removed') +
      statCard(updated.length, 'Updated', 'var(--c-updated)', 'Updated') +
      '</div>';

    // Meta line — the span is stated explicitly because each period compares
    // against the newest export *at least* N days old, which can be much older.
    html += '<div class="changes-meta">Compared to <strong>' + esc(data.compared_to) +
      '</strong> &mdash; exported <strong>' + esc(data.compared_to_ts) + '</strong>' +
      (data.span_label ? ' &mdash; spanning <strong>' + esc(data.span_label) + '</strong>' : '') +
      '</div>';

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
        { label: 'Changed',      key: 'changes',                 cls: '', render: changedTags },
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

    wireChangeFilters(el);
  }

  // ── Card filtering ────────────────────────────────────────────────────────
  // A card scopes the view to its own section. The choice survives a period
  // switch; if the new period has no rows of that kind it falls back to all.
  var changeFilter = null;

  function wireChangeFilters(el) {
    var cards = el.querySelectorAll('.stat-card[data-filter]');
    if (changeFilter && !el.querySelector('.changes-section[data-section="' + changeFilter + '"]')) {
      changeFilter = null;
    }
    cards.forEach(function (card) {
      function toggle() {
        changeFilter = changeFilter === card.dataset.filter ? null : card.dataset.filter;
        applyChangeFilter(el);
      }
      card.addEventListener('click', toggle);
      card.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
      });
    });
    applyChangeFilter(el);
  }

  function applyChangeFilter(el) {
    el.querySelectorAll('.changes-section').forEach(function (section) {
      section.hidden = !!changeFilter && section.dataset.section !== changeFilter;
    });
    el.querySelectorAll('.stat-card[data-filter]').forEach(function (card) {
      var on = changeFilter === card.dataset.filter;
      card.classList.toggle('active', on);
      card.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    el.querySelector('.stats').classList.toggle('has-filter', !!changeFilter);
  }

  function statCard(value, label, color, filterKey) {
    // Only a card with rows behind it is interactive — an empty kind renders no section.
    var interactive = filterKey && value > 0;
    return '<div class="stat-card' + (interactive ? ' stat-card-filter' : '') + '"' +
      ' style="--card-color:' + color + '"' +
      (interactive
        ? ' data-filter="' + filterKey + '" role="button" tabindex="0" aria-pressed="false"' +
          ' title="Show only ' + label + ' — click again to show all"'
        : '') +
      '><div class="stat-value">' + value.toLocaleString() + '</div>' +
      '<div class="stat-label">' + label + '</div></div>';
  }

  function changeSection(title, rows, cols) {
    var html =
      '<div class="changes-section" data-section="' + title + '">' +
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
    return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // The catalog table is the site's main internal link surface: every product
  // name points at its own static page under apps/, which is the version search
  // engines can read without running this script. Exports made before slugs were
  // written into catalog.json fall back to plain text.
  function nameCell(a) {
    var name = esc(a.productDisplayName);
    return a.slug
      ? '<a class="row-link" href="apps/' + esc(a.slug) + '.html">' + name + '</a>'
      : name;
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

  function changedTags(changes) {
    return (changes || [])
      .map(function (c) {
        return '<span class="tag tag-locale" title="' + esc(c.from) + ' → ' + esc(c.to) +
               '">' + esc(c.label) + '</span>';
      })
      .join('');
  }

  function localeTags(locales) {
    return (locales || [])
      .map(function (l) { return '<span class="tag tag-locale">' + esc(l) + '</span>'; })
      .join('');
  }

}());
