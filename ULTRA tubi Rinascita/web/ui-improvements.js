
/*
 * UI quality-of-life layer for ULTRA Tube Manager.
 * This file intentionally makes no changes to nesting, ZZX export, inventory
 * writes, or guarded Ctrl / Ctrl+Shift operations in script.js.
 */
(function () {
    'use strict';

    // Global functions are defined by the classic script.js loaded before this file.
    const renderDatabaseOriginal = window.renderTubeDatabase;
    const showDatabaseOriginal = window.showTubeDatabaseView;
    const changeDatabaseModeOriginal = window.setTubeDatabaseMode;

    if (typeof renderDatabaseOriginal !== 'function'
        || typeof showDatabaseOriginal !== 'function'
        || typeof changeDatabaseModeOriginal !== 'function') {
        console.error('ULTRA UI improvements: original database handlers not found');
        return;
    }

    const normalize = value => String(value == null ? '' : value)
        .toLocaleLowerCase('it').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
        .replace(/\s+/g, ' ').trim();
    const byId = id => document.getElementById(id);
    const visibleCount = amount => Number(amount || 0).toLocaleString('it-IT');

    function databaseQuickOptions() {
        return {
            text: normalize(byId('db-quick-search')?.value),
            missing: !!byId('db-quick-missing')?.checked,
            multi: !!byId('db-quick-multi')?.checked,
            location: String(byId('db-location-filter')?.value || '')
        };
    }

    function matchDatabaseRow(row, filters) {
        const positions = row.posizioni || [];
        if (filters.missing && row.hasCode) return false;
        if (filters.multi && positions.length < 2) return false;
        if (filters.location && !positions.some(pos => normalize(pos.label).includes(normalize(filters.location)))) {
            return false;
        }
        if (!filters.text) return true;
        const searchFields = [
            row.misura, row.spessore, row.forma, row.materiale, row.finitura,
            row.codice, row.priorita, ...positions.map(pos => pos.label)
        ].map(normalize).join(' ');
        // Match words in any order, instead of requiring an exact phrase.
        return filters.text.split(' ').every(part => searchFields.includes(part));
    }

    function tubeIdentity(tube) {
        return [tube.misura, tube.spessore, tube.materiale, tube.finitura]
            .map(normalize).join('|');
    }

    function filteredLocations(locations, rows, filters) {
        if (!filters.text && !filters.missing && !filters.multi && !filters.location) {
            return locations;
        }
        const allowed = new Set(rows.map(tubeIdentity));
        return locations.map(location => {
            if (filters.location && normalize(location.name) !== normalize(filters.location)) {
                return null;
            }
            const ripiani = (location.ripiani || []).map(ripiano => ({
                ...ripiano,
                tubi: (ripiano.tubi || []).filter(tube => allowed.has(tubeIdentity(tube)))
            })).filter(ripiano => ripiano.tubi.length > 0);
            if (!ripiani.length) return null;
            return { ...location, ripiani };
        }).filter(Boolean);
    }

    function updateLocationChooser(locations) {
        const select = byId('db-location-filter');
        if (!select) return;
        const selected = select.value;
        const names = (locations || []).map(location => location.name);
        const current = Array.from(select.options).slice(1).map(option => option.value);
        if (JSON.stringify(names) === JSON.stringify(current)) return;
        select.replaceChildren(new Option('Tutte le ubicazioni', ''));
        names.forEach(name => select.add(new Option(name, name)));
        select.value = names.includes(selected) ? selected : '';
    }

    function createMetric(label, value, warning) {
        const card = document.createElement('div');
        card.className = 'db-metric' + (warning ? ' is-warning' : '');
        const title = document.createElement('span');
        title.className = 'db-metric-label';
        title.textContent = label;
        const count = document.createElement('strong');
        count.className = 'db-metric-value';
        count.textContent = visibleCount(value);
        card.append(title, count);
        return card;
    }

    function renderOverview(rows, sourceRows) {
        const container = byId('db-overview');
        if (!container) return;
        const rods = rows.reduce((sum, row) => sum + (Number(row.quantitaTotale) || 0), 0);
        const noCodes = rows.filter(row => !row.hasCode).length;
        const multi = rows.filter(row => (row.posizioni || []).length > 1).length;
        container.replaceChildren(
            createMetric('Tipi di tubo', rows.length),
            createMetric('Verghe disponibili', rods),
            createMetric('Tipi senza codice', noCodes, noCodes > 0),
            createMetric('Tipi in più posizioni', multi)
        );
        const status = byId('tube-database-status');
        if (status) {
            status.textContent = rows.length === sourceRows.length
                ? 'Visualizzati tutti i ' + visibleCount(rows.length) + ' tipi di tubo.'
                : 'Visualizzati ' + visibleCount(rows.length) + ' di ' + visibleCount(sourceRows.length)
                    + ' tipi di tubo. Filtri attivi.';
        }
    }

    // Use original rendering and original action handlers, temporarily passing only
    // visible data; restore full data synchronously before returning.
    window.renderTubeDatabase = function (debug) {
        const originals = { rows: tubeDatabaseRows, locations: tubeDatabaseLocations };
        updateLocationChooser(originals.locations);
        const quick = databaseQuickOptions();
        const rows = originals.rows.filter(row => matchDatabaseRow(row, quick));
        try {
            tubeDatabaseRows = rows;
            tubeDatabaseLocations = filteredLocations(originals.locations, rows, quick);
            renderDatabaseOriginal(debug);
        } finally {
            tubeDatabaseRows = originals.rows;
            tubeDatabaseLocations = originals.locations;
        }
        renderOverview(rows, originals.rows);
        const list = byId('tube-database-list');
        if (list && rows.length === 0) {
            list.innerHTML = '<div class="database-empty-state"><strong>Nessun tubo trovato</strong>'
                + 'Prova a modificare la ricerca o a rimuovere uno dei filtri.</div>';
        } else if (list) {
            // The original panel labels its set-quantity action "Modifica".
            // Clarify its purpose without changing the data-db-action value.
            list.querySelectorAll('[data-db-action="remove"]').forEach(button => {
                button.textContent = 'Quantità';
                button.title = 'Imposta la quantità presente in questa posizione';
            });
        }
        // This is necessary when the user changes filters while Ctrl+Shift is down.
        updateGuardedButtonStates();
    };

    window.setTubeDatabaseMode = function (mode) {
        changeDatabaseModeOriginal(mode);
        if (uiPreferences.databaseViewMode !== mode) {
            uiPreferences.databaseViewMode = mode;
            Promise.resolve(saveUiState()).catch(error => console.warn('Impossibile salvare vista database', error));
        }
    };

    window.showTubeDatabaseView = async function () {
        const preferred = uiPreferences.databaseViewMode;
        if (preferred === 'panel' || preferred === 'list') {
            tubeDatabaseMode = preferred;
            if (dbListViewBtn) dbListViewBtn.classList.toggle('active', preferred === 'list');
            if (dbPanelViewBtn) dbPanelViewBtn.classList.toggle('active', preferred === 'panel');
        }
        await showDatabaseOriginal();
    };

    function refreshVisibleDatabase() {
        window.renderTubeDatabase(tubeDatabaseDebug);
    }

    // A single real input is shared between all three views; moving its parent
    // keeps the value, input events, and current database filter state intact.
    function isDatabaseVisible() {
        const database = byId('tube-database-container');
        return !!database && database.style.display !== 'none';
    }

    function placeSharedDatabaseSearch() {
        const searchGroup = byId('db-shared-search');
        const destination = byId(isDatabaseVisible()
            ? 'db-search-database-slot'
            : 'db-search-header-slot');
        if (!searchGroup || !destination) return;
        if (searchGroup.parentElement !== destination) {
            destination.appendChild(searchGroup);
        }
        const header = document.querySelector('.app-header');
        header?.classList.toggle('database-active', isDatabaseVisible());
    }

    async function submitSharedDatabaseSearch() {
        if (isDatabaseVisible()) {
            refreshVisibleDatabase();
            return;
        }
        // Navigation uses the existing database route and its Python-backed load.
        // The query is already in #db-quick-search, so no translation or separate
        // search implementation is necessary.
        placeSharedDatabaseSearch();
        try {
            await window.showTubeDatabaseView();
        } finally {
            placeSharedDatabaseSearch();
            // The DOM move may blur the input. Keep the cursor ready for
            // refining the results after switching tabs.
            byId('db-quick-search')?.focus();
        }
    }


    // Filtering here is strictly visual: it never reorders pieces, modifies rods,
    // invokes the nesting engine or changes a saved lock.
    const renderActiveOriginal = window.renderUI;
    let activeProfileSearch = '';

    function filterActiveProfiles() {
        const container = document.getElementById('tube-list-container');
        if (!container) return;
        const needle = normalize(activeProfileSearch);
        const cards = [...container.querySelectorAll('.tube-group')];
        let visible = 0;
        cards.forEach(card => {
            const label = card.querySelector('.tube-header')?.dataset.tubeType || '';
            const show = !needle || needle.split(' ').every(part => normalize(label).includes(part));
            card.style.display = show ? '' : 'none';
            if (show) visible++;
        });
        const count = document.getElementById('active-profile-count');
        if (count) count.textContent = visible + ' di ' + cards.length + ' profili';
        const empty = document.getElementById('active-filter-empty');
        if (empty) empty.hidden = visible !== 0;
    }

    function buildActiveFilterBar() {
        const container = document.getElementById('tube-list-container');
        if (!container || container.querySelector('.active-filter-bar')) return;
        const firstGroup = container.querySelector('.tube-group');
        if (!firstGroup) return;

        const bar = document.createElement('div');
        bar.className = 'active-filter-bar';
        const label = document.createElement('label');
        label.htmlFor = 'active-profile-search';
        label.textContent = 'Cerca un profilo';
        const input = document.createElement('input');
        input.id = 'active-profile-search';
        input.type = 'search';
        input.className = 'search-input';
        input.placeholder = 'Es. 100x100, Ø60.3, 316...';
        input.autocomplete = 'off';
        input.value = activeProfileSearch;
        input.addEventListener('input', () => {
            activeProfileSearch = input.value;
            filterActiveProfiles();
        });
        const count = document.createElement('span');
        count.id = 'active-profile-count';
        count.className = 'active-profile-count';
        const clear = document.createElement('button');
        clear.type = 'button';
        clear.className = 'action-button button-subtle';
        clear.textContent = 'Pulisci';
        clear.addEventListener('click', () => {
            activeProfileSearch = '';
            input.value = '';
            filterActiveProfiles();
            input.focus();
        });
        const closeAll = document.createElement('button');
        closeAll.type = 'button';
        closeAll.className = 'action-button';
        closeAll.textContent = 'Chiudi tutti i profili';
        closeAll.title = 'Chiude i profili aperti senza modificare le verghe';
        closeAll.addEventListener('click', () => {
            Object.keys(expandedState).forEach(name => { expandedState[name] = false; });
            window.renderUI();
        });
        bar.append(label, input, count, clear, closeAll);
        firstGroup.before(bar);
        const empty = document.createElement('div');
        empty.id = 'active-filter-empty';
        empty.className = 'database-empty-state';
        empty.hidden = true;
        empty.textContent = 'Nessun profilo corrisponde alla ricerca.';
        bar.after(empty);
    }

    if (typeof renderActiveOriginal === 'function') {
        window.renderUI = async function (...args) {
            const result = await renderActiveOriginal(...args);
            buildActiveFilterBar();
            filterActiveProfiles();
            return result;
        };
    }

    window.addEventListener('pywebviewready', () => {
        const search = byId('db-quick-search');
        const submit = byId('db-search-submit');
        const databaseContainer = byId('tube-database-container');
        placeSharedDatabaseSearch();
        // The original script registers navigation before this improvement layer.
        // Clicking its tabs therefore updates the visible view first.
        ['main-view-btn', 'history-view-btn', 'database-view-btn'].forEach(id => {
            byId(id)?.addEventListener('click', placeSharedDatabaseSearch);
        });
        // Also cover programmatic navigation (e.g. undoing history items).
        if (databaseContainer && typeof MutationObserver !== 'undefined') {
            const navObserver = new MutationObserver(placeSharedDatabaseSearch);
            navObserver.observe(databaseContainer, { attributes: true, attributeFilter: ['style'] });
        }
        submit?.addEventListener('click', () => {
            submitSharedDatabaseSearch().catch(error =>
                console.error('Errore durante la ricerca nel database', error)
            );
        });
        search?.addEventListener('keydown', event => {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            submitSharedDatabaseSearch().catch(error =>
                console.error('Errore durante la ricerca nel database', error)
            );
        });
        const clear = byId('db-clear-quick');
        const missing = byId('db-quick-missing');
        const multi = byId('db-quick-multi');
        const location = byId('db-location-filter');
        let searchTimer = 0;

        search?.addEventListener('input', () => {
            // Outside the database, typing prepares the query. Enter or the
            // Search button opens the database. Inside, results update live.
            clearTimeout(searchTimer);
            if (isDatabaseVisible()) {
                searchTimer = setTimeout(refreshVisibleDatabase, 110);
            }
        });
        [missing, multi, location].forEach(input =>
            input?.addEventListener('change', refreshVisibleDatabase)
        );
        clear?.addEventListener('click', () => {
            if (search) search.value = '';
            if (missing) missing.checked = false;
            if (multi) multi.checked = false;
            if (location) location.value = '';
            refreshVisibleDatabase();
            search?.focus();
        });

        // "/" always focuses the same database search in any main tab.
        document.addEventListener('keydown', event => {
            if (event.key !== '/' || event.ctrlKey || event.altKey || event.metaKey) return;
            if (document.querySelector('.modal-overlay[style*="display: flex"]')) return;
            const tag = document.activeElement?.tagName;
            if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) || document.activeElement?.isContentEditable) return;
            if (!search) return;
            event.preventDefault();
            search.focus();
        });

        // Close only the optional header tools popover when clicking elsewhere.
        document.addEventListener('click', event => {
            const more = document.querySelector('.header-more[open]');
            if (more && !more.contains(event.target)) more.open = false;
        });
    });
})();
