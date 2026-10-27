
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

    window.addEventListener('pywebviewready', () => {
        const search = byId('db-quick-search');
        const clear = byId('db-clear-quick');
        const missing = byId('db-quick-missing');
        const multi = byId('db-quick-multi');
        const location = byId('db-location-filter');
        let searchTimer = 0;

        search?.addEventListener('input', () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(refreshVisibleDatabase, 110);
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

        // "/" focuses the database search while no input or modal is active.
        document.addEventListener('keydown', event => {
            if (event.key !== '/' || event.ctrlKey || event.altKey || event.metaKey) return;
            if (byId('tube-database-container')?.style.display === 'none') return;
            if (document.querySelector('.modal-overlay[style*="display: flex"]')) return;
            const tag = document.activeElement?.tagName;
            if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) || document.activeElement?.isContentEditable) return;
            event.preventDefault();
            search?.focus();
        });

        // Close only the optional header tools popover when clicking elsewhere.
        document.addEventListener('click', event => {
            const more = document.querySelector('.header-more[open]');
            if (more && !more.contains(event.target)) more.open = false;
        });
    });
})();
