// web/script.js

// --- Global State & Element Refs ---
let expandedState = {}, daFareOverrides = {}, allTubeData = [], unmatchedIgsFiles = [], zzxValidationIssues = [];
let lastIgsWarningSignature = '';
let lastZzxWarningSignature = '';
let nestingRenderSequence = 0;
let uiPreferences = {
    sorting: {},
    columnWidths: {},
    databaseCollapsedRipiani: {},
    databaseCollapsedLocations: {},
    showUnlockedRodsDefault: true,
    showUnlockedRodsByTube: {}
};
let mainContainer, historyContainer, mainViewBtn, historyViewBtn;
let tubeDatabaseContainer, databaseViewBtn, tubeDatabaseList, tubeDatabaseStatus;
let dbFilterMisura, dbFilterSpessore, dbFilterForma, dbFilterMateriale, dbFilterFinitura, dbFilterCodice;
let dbListViewBtn, dbPanelViewBtn;
let dbTubeModal, dbTubeModalTitle, dbTubeModalSource, dbModalMisura, dbModalSpessore, dbModalMateriale, dbModalFinitura;
let dbModalQuantita, dbModalTargetSection, dbModalTargetLocation, dbModalTargetRipiano, dbModalNewRipiano;
let dbTubeModalConfirm, dbTubeModalCancel, dbTubeModalClose;
let inventoryUseModal, inventoryUsePosition, inventoryUseNote, inventoryUseConfirm, inventoryUseCancel, inventoryUseClose;
let summaryModal, thresholdSlider, thresholdValue, summaryPreview, generateFileBtn, generatePrintBtn;
let debugModal;
let searchDimInput, searchSpInput, searchMatSelect, searchFinSelect, searchBtn;
let searchModal, searchResult, closeSearchBtn;
let historyList, startDateInput, endDateInput, lastCheckedCheckbox = null;
let isCtrlPressed = false;
let isShiftPressed = false;
let tubeDatabaseRows = [];
let tubeDatabaseDebug = {};
let tubeDatabaseLocations = [];
let tubeDatabaseTargets = [];
let tubeDatabaseMode = 'list';
let tubeDatabaseSort = { key: 'misura', dir: 'asc' };
let dbCollapsedRipiani = {};
let dbCollapsedLocations = {};
let dbCurrentOperation = null;
let lockedRods = {};
let availableSegmentMap = new Map();
let currentPieceMap = new Map();
let currentPieceByLogicalKey = new Map();
let draggedSegment = null;
let pendingInventoryRod = null;
let settingsModal, settingsDaFarePath, settingsInventoryXlsxPath, settingsDocxOutputDir;
let settingsIgnoreInput, settingsIgnoreList;
let settingsIgnoredFolders = [];
let lockColors = { unlocked: '#dc3545', locked: '#198754' };
let inventoryExportModal, inventoryExportIncludeMissing;
let orderPriorityModal, orderPrioritySearch, orderPrioritySelectAll, orderPriorityRowsContainer;
let orderPriorityStatus, orderPriorityRows = [];
let orderPriorityDraft = new Map();
let orderPrioritySelected = new Set();

const settingsPathFieldIds = {
    da_fare_path: 'settings-da-fare-path',
    inventory_xlsx_path: 'settings-inventory-xlsx-path',
    database_tubi_dir: 'settings-database-tubi-dir',
    codici_tubi_path: 'settings-codici-tubi-path',
    docx_output_dir: 'settings-docx-output-dir',
    summary_txt_output_dir: 'settings-summary-txt-output-dir',
    summary_docx_output_dir: 'settings-summary-docx-output-dir',
    tt_docx_output_dir: 'settings-tt-docx-output-dir',
    adhoc_docx_output_dir: 'settings-adhoc-docx-output-dir',
    codes_docx_output_dir: 'settings-codes-docx-output-dir',
    inventory_xlsx_output_dir: 'settings-inventory-xlsx-output-dir',
    order_txt_output_dir: 'settings-order-txt-output-dir',
    inventory_request_folder: 'settings-inventory-request-folder',
};


// --- Main Setup ---
window.addEventListener('pywebviewready', function() {
    console.log("PyWebView is ready!");
    
    // Assign all element references from the DOM
    mainContainer = document.getElementById('tube-list-container');
    historyContainer = document.getElementById('history-container');
    tubeDatabaseContainer = document.getElementById('tube-database-container');
    tubeDatabaseList = document.getElementById('tube-database-list');
    tubeDatabaseStatus = document.getElementById('tube-database-status');
    historyList = document.getElementById('history-list');
    mainViewBtn = document.getElementById('main-view-btn');
    historyViewBtn = document.getElementById('history-view-btn');
    databaseViewBtn = document.getElementById('database-view-btn');
    summaryModal = document.getElementById('summary-modal');
    thresholdSlider = document.getElementById('threshold-slider');
    thresholdValue = document.getElementById('threshold-value');
    summaryPreview = document.getElementById('summary-preview');
    generateFileBtn = document.getElementById('generate-file-btn');
    generatePrintBtn = document.getElementById('generate-print-btn');
    startDateInput = document.getElementById('start-date');
    endDateInput = document.getElementById('end-date');


debugModal = document.getElementById('debug-modal');

// Inventory search refs
searchDimInput = document.getElementById('search-dimensione');
searchSpInput = document.getElementById('search-spessore');
searchMatSelect = document.getElementById('search-materiale');
searchFinSelect = document.getElementById('search-finitura');
searchBtn = document.getElementById('search-btn');

// Tube database refs
dbFilterMisura = document.getElementById('db-filter-misura');
dbFilterSpessore = document.getElementById('db-filter-spessore');
dbFilterForma = document.getElementById('db-filter-forma');
dbFilterMateriale = document.getElementById('db-filter-materiale');
dbFilterFinitura = document.getElementById('db-filter-finitura');
dbFilterCodice = document.getElementById('db-filter-codice');
dbListViewBtn = document.getElementById('db-list-view-btn');
dbPanelViewBtn = document.getElementById('db-panel-view-btn');

dbTubeModal = document.getElementById('db-tube-modal');
dbTubeModalTitle = document.getElementById('db-tube-modal-title');
dbTubeModalSource = document.getElementById('db-tube-modal-source');
dbModalMisura = document.getElementById('db-modal-misura');
dbModalSpessore = document.getElementById('db-modal-spessore');
dbModalMateriale = document.getElementById('db-modal-materiale');
dbModalFinitura = document.getElementById('db-modal-finitura');
dbModalQuantita = document.getElementById('db-modal-quantita');
dbModalTargetSection = document.getElementById('db-modal-target-section');
dbModalTargetLocation = document.getElementById('db-modal-target-location');
dbModalTargetRipiano = document.getElementById('db-modal-target-ripiano');
dbModalNewRipiano = document.getElementById('db-modal-new-ripiano');
dbTubeModalConfirm = document.getElementById('db-tube-modal-confirm');
dbTubeModalCancel = document.getElementById('db-tube-modal-cancel');
dbTubeModalClose = document.getElementById('db-tube-modal-close');

inventoryUseModal = document.getElementById('inventory-use-modal');
inventoryUsePosition = document.getElementById('inventory-use-position');
inventoryUseNote = document.getElementById('inventory-use-note');
inventoryUseConfirm = document.getElementById('inventory-use-confirm');
inventoryUseCancel = document.getElementById('inventory-use-cancel');
inventoryUseClose = document.getElementById('inventory-use-close');

settingsModal = document.getElementById('settings-modal');
settingsDaFarePath = document.getElementById('settings-da-fare-path');
settingsInventoryXlsxPath = document.getElementById('settings-inventory-xlsx-path');
settingsDocxOutputDir = document.getElementById('settings-docx-output-dir');
settingsIgnoreInput = document.getElementById('settings-ignore-input');
settingsIgnoreList = document.getElementById('settings-ignore-list');
inventoryExportModal = document.getElementById('inventory-export-modal');
inventoryExportIncludeMissing = document.getElementById('inventory-export-include-missing');
orderPriorityModal = document.getElementById('order-priority-modal');
orderPrioritySearch = document.getElementById('order-priority-search');
orderPrioritySelectAll = document.getElementById('order-priority-select-all');
orderPriorityRowsContainer = document.getElementById('order-priority-rows');
orderPriorityStatus = document.getElementById('order-priority-status');

// Search modal refs
searchModal = document.getElementById('search-modal');
searchResult = document.getElementById('search-result');
closeSearchBtn = document.getElementById('close-search-btn');

    // Attach all event listeners
    document.getElementById('refresh-button').addEventListener('click', loadTubeData);
    document.getElementById('summary-btn').addEventListener('click', openSummaryModal);
    document.getElementById('close-summary-btn').addEventListener('click', closeSummaryModal);
    document.getElementById('settings-btn')?.addEventListener('click', openSettingsModal);
    document.getElementById('debug-btn').addEventListener('click', showDebugModal);
    const ttDocxBtn = document.getElementById('tt-docx-btn');
    if (ttDocxBtn) ttDocxBtn.addEventListener('click', generateTTDocxFromSelection);
    document.getElementById('close-debug-btn').addEventListener('click', closeDebugModal);

// Search listeners
if (searchBtn) searchBtn.addEventListener('click', runInventorySearch);
const searchEnterHandler = (e) => {
    if (e.key === 'Enter') {
        e.preventDefault();
        runInventorySearch();
    }
};
if (searchDimInput) searchDimInput.addEventListener('keydown', searchEnterHandler);
if (searchSpInput) searchSpInput.addEventListener('keydown', searchEnterHandler);
if (searchMatSelect) searchMatSelect.addEventListener('keydown', searchEnterHandler);
if (searchFinSelect) searchFinSelect.addEventListener('keydown', searchEnterHandler);
if (closeSearchBtn) closeSearchBtn.addEventListener('click', closeSearchModal);

// Close modals by clicking outside
if (summaryModal) summaryModal.addEventListener('mousedown', (e) => { if (e.target === summaryModal) closeSummaryModal(); });
if (debugModal) debugModal.addEventListener('mousedown', (e) => { if (e.target === debugModal) closeDebugModal(); });
if (searchModal) searchModal.addEventListener('mousedown', (e) => { if (e.target === searchModal) closeSearchModal(); });

    thresholdSlider.addEventListener('input', handleSliderChange);
    generateFileBtn.addEventListener('click', generateSummaryFile);
    if (generatePrintBtn) generatePrintBtn.addEventListener('click', generateSummaryDocx);
    mainViewBtn.addEventListener('click', showMainView);
    historyViewBtn.addEventListener('click', showHistoryView);
    if (databaseViewBtn) databaseViewBtn.addEventListener('click', showTubeDatabaseView);
    document.getElementById('db-refresh-btn')?.addEventListener('click', loadTubeDatabase);
    document.getElementById('db-import-btn')?.addEventListener('click', importTubeDatabase);
    document.getElementById('db-validate-btn')?.addEventListener('click', validateTubeDatabase);
    document.getElementById('db-apply-filters-btn')?.addEventListener('click', loadTubeDatabase);
    document.getElementById('db-clear-filters-btn')?.addEventListener('click', clearTubeDatabaseFilters);
    document.getElementById('db-add-scaffolding-btn')?.addEventListener('click', addTubeDatabaseScaffolding);
    document.getElementById('db-expand-all-scaff-btn')?.addEventListener('click', () => setAllDatabaseLocationsCollapsed(false));
    document.getElementById('db-collapse-all-scaff-btn')?.addEventListener('click', () => setAllDatabaseLocationsCollapsed(true));
    document.getElementById('db-expand-all-ripiani-btn')?.addEventListener('click', () => setAllDatabaseRipianiCollapsed(false));
    document.getElementById('db-collapse-all-ripiani-btn')?.addEventListener('click', () => setAllDatabaseRipianiCollapsed(true));
    document.getElementById('db-import-codes-btn')?.addEventListener('click', importTubeCodes);
    document.getElementById('db-add-code-btn')?.addEventListener('click', addManualTubeCode);
    document.getElementById('db-codes-docx-btn')?.addEventListener('click', generateTubeCodesDocx);
    document.getElementById('db-export-xlsx-btn')?.addEventListener('click', openInventoryExportModal);
    document.getElementById('db-order-priorities-btn')?.addEventListener('click', openOrderPriorityModal);
    document.getElementById('db-order-txt-btn')?.addEventListener('click', generateTubeOrderTxt);
    document.getElementById('adhoc-docx-btn')?.addEventListener('click', generateAdhocDocx);
    document.getElementById('adhoc-reset-btn')?.addEventListener('click', resetAdhocBaseline);
    if (dbListViewBtn) dbListViewBtn.addEventListener('click', () => setTubeDatabaseMode('list'));
    if (dbPanelViewBtn) dbPanelViewBtn.addEventListener('click', () => setTubeDatabaseMode('panel'));
    if (dbTubeModalConfirm) dbTubeModalConfirm.addEventListener('click', submitTubeDatabaseOperation);
    if (dbTubeModalCancel) dbTubeModalCancel.addEventListener('click', closeTubeDatabaseModal);
    if (dbTubeModalClose) dbTubeModalClose.addEventListener('click', closeTubeDatabaseModal);
    if (dbTubeModal) dbTubeModal.addEventListener('mousedown', (e) => { if (e.target === dbTubeModal) closeTubeDatabaseModal(); });
    if (inventoryUseConfirm) inventoryUseConfirm.addEventListener('click', confirmInventoryUse);
    if (inventoryUseCancel) inventoryUseCancel.addEventListener('click', closeInventoryUseModal);
    if (inventoryUseClose) inventoryUseClose.addEventListener('click', closeInventoryUseModal);
    if (inventoryUseModal) inventoryUseModal.addEventListener('mousedown', (e) => { if (e.target === inventoryUseModal) closeInventoryUseModal(); });
    document.getElementById('settings-close')?.addEventListener('click', closeSettingsModal);
    document.getElementById('settings-cancel')?.addEventListener('click', closeSettingsModal);
    document.getElementById('settings-save')?.addEventListener('click', saveSettings);
    document.getElementById('settings-ignore-add')?.addEventListener('click', addIgnoredFolderFromInput);
    if (settingsIgnoreInput) settingsIgnoreInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            addIgnoredFolderFromInput();
        }
    });
    if (settingsIgnoreList) settingsIgnoreList.addEventListener('click', handleIgnoredFolderClick);
    if (settingsModal) {
        settingsModal.addEventListener('mousedown', (e) => { if (e.target === settingsModal) closeSettingsModal(); });
        settingsModal.querySelectorAll('[data-settings-picker]').forEach(button => {
            button.addEventListener('click', () => pickSettingsPath(button));
        });
    }
    document.getElementById('inventory-export-close')?.addEventListener('click', closeInventoryExportModal);
    document.getElementById('inventory-export-cancel')?.addEventListener('click', closeInventoryExportModal);
    document.getElementById('inventory-export-confirm')?.addEventListener('click', generateTubeInventoryXlsx);
    if (inventoryExportModal) inventoryExportModal.addEventListener('mousedown', (e) => { if (e.target === inventoryExportModal) closeInventoryExportModal(); });
    document.getElementById('order-priority-close')?.addEventListener('click', closeOrderPriorityModal);
    document.getElementById('order-priority-cancel')?.addEventListener('click', closeOrderPriorityModal);
    document.getElementById('order-priority-save')?.addEventListener('click', saveOrderPriorities);
    document.getElementById('order-priority-apply-bulk')?.addEventListener('click', applyBulkOrderPriority);
    if (orderPrioritySearch) orderPrioritySearch.addEventListener('input', renderOrderPriorityRows);
    if (orderPrioritySelectAll) orderPrioritySelectAll.addEventListener('change', toggleAllVisibleOrderPriorities);
    if (orderPriorityRowsContainer) orderPriorityRowsContainer.addEventListener('change', handleOrderPriorityRowChange);
    if (orderPriorityModal) orderPriorityModal.addEventListener('mousedown', (e) => { if (e.target === orderPriorityModal) closeOrderPriorityModal(); });
    if (dbModalTargetLocation) dbModalTargetLocation.addEventListener('change', populateTargetRipiani);
    [dbFilterMisura, dbFilterSpessore, dbFilterForma, dbFilterMateriale, dbFilterFinitura, dbFilterCodice].forEach(el => {
        if (!el) return;
        el.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                loadTubeDatabase();
            }
        });
        if (el.tagName === 'SELECT') el.addEventListener('change', loadTubeDatabase);
    });
    
    // History specific listeners
    startDateInput.addEventListener('change', showHistoryView);
    endDateInput.addEventListener('change', showHistoryView);
    document.getElementById('clear-filter-btn').addEventListener('click', () => {
        startDateInput.value = '';
        endDateInput.value = '';
        showHistoryView();
    });
    document.getElementById('remove-selected-btn').addEventListener('click', removeSelectedHistory);
    document.getElementById('remove-all-btn').addEventListener('click', removeAllHistory);

    // Delegated listeners for dynamic content
    mainContainer.addEventListener('click', handleContainerClick);
    mainContainer.addEventListener('dblclick', handleContainerDblClick);
    mainContainer.addEventListener('dragstart', handleSegmentDragStart);
    mainContainer.addEventListener('dragend', handleSegmentDragEnd);
    mainContainer.addEventListener('dragover', handleRodDragOver);
    mainContainer.addEventListener('drop', handleRodDrop);
    historyContainer.addEventListener('click', handleHistoryContainerClick);
    historyContainer.addEventListener('dblclick', handleContainerDblClick);
    if (tubeDatabaseContainer) tubeDatabaseContainer.addEventListener('click', handleTubeDatabaseClick);
    mainContainer.addEventListener('mousedown', handleResizeMouseDown);
    
    // Listen for guarded action keys globally
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeAllModals();
            return;
        }
        if (e.key === 'Control' && !isCtrlPressed) {
            isCtrlPressed = true;
            updateGuardedButtonStates();
        }
        if (e.key === 'Shift' && !isShiftPressed) {
            isShiftPressed = true;
            updateGuardedButtonStates();
        }
    });
    document.addEventListener('keyup', (e) => {
        if (e.key === 'Control') {
            isCtrlPressed = false;
            updateGuardedButtonStates();
        }
        if (e.key === 'Shift') {
            isShiftPressed = false;
            updateGuardedButtonStates();
        }
    });
    window.addEventListener('blur', () => {
        isCtrlPressed = false;
        isShiftPressed = false;
        updateGuardedButtonStates();
    });

    // Start the application's data loading process
    initialize();
});


// --- View Toggling & Modals ---
function showMainView() {
    mainContainer.style.display = 'block';
    historyContainer.style.display = 'none';
    if (tubeDatabaseContainer) tubeDatabaseContainer.style.display = 'none';
    mainViewBtn.classList.add('active');
    historyViewBtn.classList.remove('active');
    if (databaseViewBtn) databaseViewBtn.classList.remove('active');
    renderUI();
}
async function showHistoryView() {
    mainContainer.style.display = 'none';
    historyContainer.style.display = 'block';
    if (tubeDatabaseContainer) tubeDatabaseContainer.style.display = 'none';
    mainViewBtn.classList.remove('active');
    historyViewBtn.classList.add('active');
    if (databaseViewBtn) databaseViewBtn.classList.remove('active');
    
    historyList.innerHTML = '<p>Loading history...</p>';
    try {
        const historyData = await window.pywebview.api.get_history();
        renderHistory(historyData);
    } catch (e) {
        console.error("Failed to load history:", e);
        historyList.innerHTML = '<p style="color: red;">Error loading history.</p>';
    }
}
async function showTubeDatabaseView() {
    mainContainer.style.display = 'none';
    historyContainer.style.display = 'none';
    if (tubeDatabaseContainer) tubeDatabaseContainer.style.display = 'block';
    mainViewBtn.classList.remove('active');
    historyViewBtn.classList.remove('active');
    if (databaseViewBtn) databaseViewBtn.classList.add('active');
    await loadTubeDatabase();
}
async function openSummaryModal() {
    summaryModal.style.display = 'flex';
    await updateSummaryPreview();
}
function closeSummaryModal() {
    summaryModal.style.display = 'none';
}

function closeDebugModal() {
    if (debugModal) debugModal.style.display = 'none';
}
function openSearchModal(text) {
    if (!searchModal) return;
    if (searchResult) searchResult.textContent = text || '';
    searchModal.style.display = 'flex';
}
function closeSearchModal() {
    if (searchModal) searchModal.style.display = 'none';
}
function closeAllModals() {
    closeSearchModal();
    closeDebugModal();
    closeSummaryModal();
    closeTubeDatabaseModal();
    closeInventoryUseModal();
    closeSettingsModal();
    closeInventoryExportModal();
    closeOrderPriorityModal();
}

async function openSettingsModal() {
    try {
        const response = await window.pywebview.api.get_config_settings();
        if (!response || response.status !== 'success') {
            alert(response?.message || 'Impossibile caricare le impostazioni.');
            return;
        }
        const config = response.config || {};
        applyLockColors(config.lock_unlocked_color, config.lock_locked_color);
        Object.entries(settingsPathFieldIds).forEach(([configKey, elementId]) => {
            const field = document.getElementById(elementId);
            if (field) field.value = config[configKey] || '';
        });
        document.getElementById('settings-lock-unlocked-color').value = lockColors.unlocked;
        document.getElementById('settings-lock-locked-color').value = lockColors.locked;
        document.getElementById('settings-inventory-request-enabled').checked = !!config.inventory_request_enabled;
        document.getElementById('settings-order-low-quantity').value = parseNonNegativeInteger(config.order_priority_low_quantity, 1);
        document.getElementById('settings-order-medium-quantity').value = parseNonNegativeInteger(config.order_priority_medium_quantity, 3);
        document.getElementById('settings-order-high-quantity').value = parseNonNegativeInteger(config.order_priority_high_quantity, 5);
        document.getElementById('settings-nesting-gap-mm').value = Number.isFinite(Number(config.nesting_gap_mm)) ? Number(config.nesting_gap_mm) : 2;
        document.getElementById('settings-order-include-low-priority').checked = config.order_include_low_priority !== false;
        settingsIgnoredFolders = Array.isArray(config.ignore_folders) ? [...config.ignore_folders] : [];
        settingsIgnoreInput.value = '';
        renderIgnoredFolders();
        document.body.classList.add('modal-scroll-locked');
        settingsModal.style.display = 'flex';
    } catch (error) {
        console.error(error);
        alert('Impossibile caricare le impostazioni.');
    }
}

function applyLockColors(unlocked, locked) {
    const validColor = value => /^#[0-9a-fA-F]{6}$/.test(String(value || ''));
    lockColors = {
        unlocked: validColor(unlocked) ? String(unlocked) : '#dc3545',
        locked: validColor(locked) ? String(locked) : '#198754',
    };
    document.documentElement.style.setProperty('--lock-unlocked-color', lockColors.unlocked);
    document.documentElement.style.setProperty('--lock-locked-color', lockColors.locked);
}

function closeSettingsModal() {
    if (settingsModal) settingsModal.style.display = 'none';
    document.body.classList.remove('modal-scroll-locked');
}

function renderIgnoredFolders() {
    if (!settingsIgnoreList) return;
    settingsIgnoreList.innerHTML = '';
    if (settingsIgnoredFolders.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'settings-ignore-empty';
        empty.textContent = 'Nessuna cartella ignorata';
        settingsIgnoreList.appendChild(empty);
        return;
    }
    settingsIgnoredFolders.forEach((folder, index) => {
        const item = document.createElement('div');
        item.className = 'settings-ignore-item';
        const label = document.createElement('span');
        label.textContent = folder;
        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'settings-ignore-remove';
        removeButton.dataset.ignoreIndex = String(index);
        removeButton.title = 'Rimuovi cartella ignorata';
        removeButton.textContent = '\u00d7';
        item.append(label, removeButton);
        settingsIgnoreList.appendChild(item);
    });
}

function addIgnoredFolderFromInput() {
    const folder = (settingsIgnoreInput?.value || '').trim();
    if (!folder) return;
    if (!settingsIgnoredFolders.some(item => item.toLowerCase() === folder.toLowerCase())) {
        settingsIgnoredFolders.push(folder);
        renderIgnoredFolders();
    }
    settingsIgnoreInput.value = '';
    settingsIgnoreInput.focus();
}

function handleIgnoredFolderClick(event) {
    const removeButton = event.target.closest('[data-ignore-index]');
    if (!removeButton) return;
    const index = parseInt(removeButton.dataset.ignoreIndex, 10);
    if (Number.isInteger(index) && index >= 0 && index < settingsIgnoredFolders.length) {
        settingsIgnoredFolders.splice(index, 1);
        renderIgnoredFolders();
    }
}

async function pickSettingsPath(button) {
    try {
        let response;
        if (button.dataset.pickerType === 'excel') {
            response = await window.pywebview.api.pick_inventory_excel_file();
        } else if (button.dataset.pickerType === 'json') {
            response = await window.pywebview.api.pick_code_catalog_file();
        } else {
            response = await window.pywebview.api.pick_settings_folder();
        }
        if (response?.status === 'success' && response.path) {
            const target = document.getElementById(button.dataset.settingsPicker);
            if (target) target.value = response.path;
            if (button.dataset.settingsPicker === 'settings-inventory-request-folder') {
                const setupResponse = await window.pywebview.api.initialize_inventory_request_folder(response.path);
                if (!setupResponse || setupResponse.status !== 'success') {
                    alert(setupResponse?.message || 'Impossibile preparare la cartella condivisa.');
                }
            }
        }
    } catch (error) {
        console.error(error);
        alert('Impossibile aprire la selezione del percorso.');
    }
}

async function saveSettings() {
    try {
        const payload = {
            ignore_folders: settingsIgnoredFolders,
            inventory_request_enabled: document.getElementById('settings-inventory-request-enabled').checked,
            lock_unlocked_color: document.getElementById('settings-lock-unlocked-color').value,
            lock_locked_color: document.getElementById('settings-lock-locked-color').value,
            order_priority_low_quantity: parseNonNegativeInteger(document.getElementById('settings-order-low-quantity').value, 1),
            order_priority_medium_quantity: parseNonNegativeInteger(document.getElementById('settings-order-medium-quantity').value, 3),
            order_priority_high_quantity: parseNonNegativeInteger(document.getElementById('settings-order-high-quantity').value, 5),
            order_include_low_priority: document.getElementById('settings-order-include-low-priority').checked,
            nesting_gap_mm: Math.max(0, Number(document.getElementById('settings-nesting-gap-mm').value) || 0),
        };
        Object.entries(settingsPathFieldIds).forEach(([configKey, elementId]) => {
            payload[configKey] = document.getElementById(elementId)?.value || '';
        });
        const response = await window.pywebview.api.save_config_settings(payload);
        if (!response || response.status !== 'success') {
            alert(response?.message || 'Impossibile salvare le impostazioni.');
            return;
        }
        closeSettingsModal();
        applyLockColors(response.config?.lock_unlocked_color, response.config?.lock_locked_color);
        document.getElementById('current-folder-path').textContent = response.config?.da_fare_path || 'Non impostato';
        await loadTubeData();
        if (tubeDatabaseContainer?.style.display !== 'none') await loadTubeDatabase();
    } catch (error) {
        console.error(error);
        alert('Impossibile salvare le impostazioni.');
    }
}

function openInventoryExportModal() {
    if (!inventoryExportModal) return;
    inventoryExportIncludeMissing.checked = true;
    inventoryExportModal.style.display = 'flex';
}

function closeInventoryExportModal() {
    if (inventoryExportModal) inventoryExportModal.style.display = 'none';
}

async function generateTubeInventoryXlsx() {
    try {
        const response = await window.pywebview.api.generate_tube_inventory_xlsx(inventoryExportIncludeMissing.checked);
        if (!response || response.status !== 'success') {
            alert(response?.message || 'Errore durante la generazione dell\'inventario Excel.');
            return;
        }
        closeInventoryExportModal();
        alert(`Inventario Excel generato:\n${response.path}\n\nTubi tondi: ${response.round_count}\nTubi quadri/rettangolari: ${response.shaped_count}`);
    } catch (error) {
        console.error(error);
        alert('Errore durante la generazione dell\'inventario Excel.');
    }
}

async function runInventorySearch() {
    const dim = (searchDimInput?.value || '').trim();
    const sp = (searchSpInput?.value || '').trim();
    const mat = (searchMatSelect?.value || '304').trim();
    const fin = (searchFinSelect?.value || '2B').trim();

    if (!dim || !sp) {
        alert("Inserisci Dimensione e Spessore.");
        return;
    }

    try {
        const resp = await window.pywebview.api.search_tube_inventory(dim, sp, mat, fin);
        if (resp && resp.text) {
            openSearchModal(resp.text);
        } else if (resp && resp.message) {
            alert(resp.message);
        } else {
            alert("Errore durante la ricerca in inventario.");
        }
    } catch (err) {
        console.error(err);
        alert("Errore durante la ricerca in inventario.");
    }
}

async function showDebugModal() {
    const debugModal = document.getElementById('debug-modal');
    const debugArea = document.getElementById('debug-info-area');
    debugArea.textContent = 'Loading debug info...';
    debugModal.style.display = 'flex';
    
    const info = await window.pywebview.api.get_debug_info();
    
    let infoText = `Percorso Dati Applicazione:\n${info.app_data_path}\n\n`;
    infoText += `Percorso File di Configurazione:\n${info.config_file_path}\n\n`;
    infoText += `Contenuto Configurazione Caricato:\n${JSON.stringify(info.loaded_config_content, null, 2)}\n\n`;
    infoText += `Percorso 'DA FARE' in uso:\n${info.da_fare_path_in_use || 'Non impostato'}\n\n`;
    infoText += `Database Tubi:\n${JSON.stringify(info.inventory_debug?.database || {}, null, 2)}`;
    
    debugArea.textContent = infoText;
}


// --- Summary Logic ---
async function updateSummaryPreview() {
    const threshold = parseInt(thresholdSlider.value, 10);
    thresholdValue.textContent = `${threshold}%`;
    summaryPreview.textContent = 'Generazione riepilogo in corso...';
    try {
        const plan = await buildSummaryPlanFromCurrentView();
        const summaryData = await window.pywebview.api.get_summary_data_for_plan(threshold, plan);
        summaryPreview.textContent = summaryData.text;
    } catch (e) {
        console.error("Failed to generate summary:", e);
        summaryPreview.textContent = "Errore durante la generazione del riepilogo.";
    }
}
function handleSliderChange() {
    updateSummaryPreview();
}
async function generateSummaryFile() {
    const summaryText = summaryPreview.textContent;
    if (!summaryText || summaryText.includes('in corso') || summaryText.includes('Errore')) {
        alert("Nessun riepilogo valido da generare.");
        return;
    }
    try {
        const response = await window.pywebview.api.save_and_open_summary(summaryText);
        if (response.status === 'success') {
            alert(`File di riepilogo salvato con successo:\n${response.path}`);
            closeSummaryModal();
        } else {
            alert(`Errore durante la generazione del file:\n${response.message}`);
        }
    } catch(e) {
        console.error("Failed to save summary file:", e);
        alert("Un errore critico ha impedito il salvataggio del file.");
    }
}


// --- Initialization & Data Loading ---
async function initialize() {
    try {
        const savedState = await window.pywebview.api.load_ui_state() || {};
        daFareOverrides = savedState.daFareOverrides || {};
        uiPreferences = savedState.uiPreferences || { sorting: {}, columnWidths: {} };
        uiPreferences.sorting = uiPreferences.sorting || {};
        uiPreferences.columnWidths = uiPreferences.columnWidths || {};
        uiPreferences.databaseCollapsedRipiani = uiPreferences.databaseCollapsedRipiani || {};
        uiPreferences.databaseCollapsedLocations = uiPreferences.databaseCollapsedLocations || {};
        uiPreferences.showUnlockedRodsDefault = uiPreferences.showUnlockedRodsDefault !== false;
        uiPreferences.showUnlockedRodsByTube = uiPreferences.showUnlockedRodsByTube || {};
        dbCollapsedRipiani = uiPreferences.databaseCollapsedRipiani;
        dbCollapsedLocations = uiPreferences.databaseCollapsedLocations;
        lockedRods = sanitizeLockedRods(savedState.lockedRods || {});

        const configResponse = await window.pywebview.api.get_config_settings();
        if (configResponse?.status === 'success') {
            applyLockColors(configResponse.config?.lock_unlocked_color, configResponse.config?.lock_locked_color);
        }
        
        const initialData = await window.pywebview.api.get_initial_data();
        
        if (initialData && initialData.da_fare_path) {
            document.getElementById('current-folder-path').textContent = initialData.da_fare_path;
            await loadTubeData();
        } else {
            document.getElementById('current-folder-path').textContent = 'Non impostato';
            mainContainer.innerHTML = `
                <div class="error-message" style="text-align: center; padding: 40px;">
                    <h2>Benvenuto!</h2>
                    <p>La cartella di lavoro non è stata ancora impostata.</p>
                    <p>Per favore, seleziona la tua cartella 'Tubi' principale.</p>
                    <button id="select-folder-initial-btn" class="action-button" style="padding: 10px 20px; font-size: 1.1em; margin-top: 20px;">Seleziona Cartella</button>
                </div>`;
            document.getElementById('select-folder-initial-btn').addEventListener('click', selectFolderAndReload);
        }
    } catch (e) {
        console.error("Initialization failed:", e);
        alert("A critical error occurred on startup. Check the browser console for details.");
    }
}
async function loadTubeData() {
    console.log("Requesting fresh tube data from Python...");
    mainContainer.innerHTML = '<p>Loading...</p>';
    try {
        const data = await window.pywebview.api.get_tubes_state();
        if (data === null || typeof data === 'undefined') {
            throw new Error("Received null or undefined data from backend. Check the Python console for a traceback.");
        }
        allTubeData = data;
        unmatchedIgsFiles = await window.pywebview.api.get_unmatched_igs_files();
        zzxValidationIssues = await window.pywebview.api.get_zzx_validation_issues();
        console.log("Received fresh data from backend:", allTubeData);
        await renderUI();
        showIgsWarningAlertIfNeeded();
        showZzxWarningAlertIfNeeded();
    } catch (e) {
        console.error("Error fetching tube data:", e);
        mainContainer.innerHTML = `<p style="color: red;">Error loading data. Check the terminal for a detailed error message.</p>`;
    }
}
function getTubeDatabaseFilters() {
    return {
        misura: (dbFilterMisura?.value || '').trim(),
        spessore: (dbFilterSpessore?.value || '').trim(),
        forma: dbFilterForma?.value || 'tutti',
        materiale: dbFilterMateriale?.value || 'tutti',
        finitura: dbFilterFinitura?.value || 'tutti',
        codice: dbFilterCodice?.value || 'tutti'
    };
}
async function loadTubeDatabase() {
    if (!tubeDatabaseList) return;
    tubeDatabaseStatus.textContent = 'Caricamento Database Tubi...';
    tubeDatabaseList.innerHTML = '';
    try {
        const resp = await window.pywebview.api.get_tube_database(getTubeDatabaseFilters());
        if (!resp || resp.status !== 'success') {
            tubeDatabaseStatus.textContent = resp?.message || 'Database Tubi non disponibile.';
            return;
        }
        tubeDatabaseRows = resp.rows || [];
        tubeDatabaseLocations = resp.locations || [];
        tubeDatabaseTargets = resp.targets || [];
        tubeDatabaseDebug = resp.debug || {};
        renderTubeDatabase(tubeDatabaseDebug);
    } catch (e) {
        console.error(e);
        tubeDatabaseStatus.textContent = 'Errore durante il caricamento del Database Tubi.';
    }
}
function orderPriorityBadge(priority) {
    const normalized = ['bassa', 'media', 'alta'].includes(String(priority || '').toLowerCase())
        ? String(priority).toLowerCase()
        : 'media';
    const label = normalized.charAt(0).toUpperCase() + normalized.slice(1);
    return `<span class="order-priority-badge priority-${normalized}">${label}</span>`;
}
function renderTubeDatabase(debug) {
    const totalTypes = tubeDatabaseRows.length;
    const totalQuantity = tubeDatabaseRows.reduce((sum, row) => sum + (parseInt(row.quantitaTotale, 10) || 0), 0);
    tubeDatabaseStatus.textContent = `${totalTypes} tipi tubo visualizzati, ${totalQuantity} verghe totali. Posizioni: ${debug.position_rows || '-'} in ${debug.locations_count || '-'} ubicazioni.`;

    if (tubeDatabaseMode === 'panel') {
        renderTubeDatabasePanels();
        return;
    }

    if (tubeDatabaseRows.length === 0) {
        tubeDatabaseList.innerHTML = '<p>Nessun tubo trovato con questi filtri.</p>';
        return;
    }

    const rows = [...tubeDatabaseRows].sort(compareTubeDatabaseRows);
    const headers = [
        { key: 'misura', label: 'Misura' },
        { key: 'spessore', label: 'Spessore' },
        { key: 'forma', label: 'Forma' },
        { key: 'materiale', label: 'Materiale' },
        { key: 'finitura', label: 'Finitura' },
        { key: 'codice', label: 'Codice' },
        { key: 'priorita', label: 'Priorita' },
        { key: 'quantitaTotale', label: 'Quantita' },
        { key: 'posizioni', label: 'Posizioni' },
        { key: 'actions', label: 'Azioni' }
    ];

    let html = '<div class="database-table">';
    headers.forEach(header => {
        const arrow = header.key === tubeDatabaseSort.key ? (tubeDatabaseSort.dir === 'asc' ? '▲' : '▼') : '';
        const sortAttr = header.key === 'actions' ? '' : ` data-db-sort="${header.key}"`;
        html += `<div class="database-header"${sortAttr}>${header.label} <span class="sort-arrow">${arrow}</span></div>`;
    });

    rows.forEach(row => {
        const positions = (row.posizioni || [])
            .sort((a, b) => String(a.label).localeCompare(String(b.label)))
            .map(pos => `${escapeHtml(pos.label)} ${pos.quantita}V`)
            .join('<br>');

        html += `<div class="database-cell"><strong>${escapeHtml(row.misura)}</strong></div>
                 <div class="database-cell">${escapeHtml(formatNumber(row.spessore))}</div>
                 <div class="database-cell">${escapeHtml(row.forma)}</div>
                 <div class="database-cell">${escapeHtml(row.materiale)}</div>
                 <div class="database-cell">${escapeHtml(row.finitura)}</div>
                 <div class="database-cell database-code ${row.hasCode ? '' : 'missing-code'}">${escapeHtml(row.codice || 'CODICE MANCANTE')}</div>
                 <div class="database-cell">${row.hasCode ? orderPriorityBadge(row.priorita) : '-'}</div>
                 <div class="database-cell database-qty">${escapeHtml(row.quantitaTotale)}</div>
                 <div class="database-cell database-positions">${positions}</div>
                 <div class="database-cell">
                    <button class="action-button database-mini-button" data-db-action="edit-code"
                        data-misura="${escapeAttr(row.misura)}"
                        data-spessore="${escapeAttr(formatNumber(row.spessore))}"
                        data-materiale="${escapeAttr(row.materiale)}"
                        data-finitura="${escapeAttr(row.finitura)}"
                        data-codice="${escapeAttr(row.codice || '')}">Codice</button>
                 </div>`;
    });
    html += '</div>';
    tubeDatabaseList.innerHTML = html;
    updateGuardedButtonStates();
}
function setTubeDatabaseMode(mode) {
    tubeDatabaseMode = mode;
    if (dbListViewBtn) dbListViewBtn.classList.toggle('active', mode === 'list');
    if (dbPanelViewBtn) dbPanelViewBtn.classList.toggle('active', mode === 'panel');
    renderTubeDatabase(tubeDatabaseDebug);
}
function databaseRipianoKey(locationName, ripianoName) {
    return `${locationName || ''}::${ripianoName || ''}`;
}
function renderTubeDatabasePanels() {
    if (!tubeDatabaseLocations || tubeDatabaseLocations.length === 0) {
        tubeDatabaseList.innerHTML = '<p>Nessuna scaffalatura trovata con questi filtri.</p>';
        return;
    }

    let html = '<div class="database-panels">';
    tubeDatabaseLocations.forEach(location => {
        const locationQty = (location.ripiani || []).reduce((sum, ripiano) => {
            return sum + (ripiano.tubi || []).reduce((inner, tube) => inner + (parseInt(tube.quantita, 10) || 0), 0);
        }, 0);
        const locationCollapsed = !!dbCollapsedLocations[location.name];

        html += `<section class="database-location-panel${locationCollapsed ? ' collapsed' : ''}">
                    <div class="database-location-header">
                        <button class="database-ripiano-toggle" data-db-action="toggle-location" data-location="${escapeAttr(location.name)}" title="${locationCollapsed ? 'Espandi scaffalatura' : 'Comprimi scaffalatura'}">${locationCollapsed ? '&#9654;' : '&#9660;'}</button>
                        <h3>${escapeHtml(location.name)}</h3>
                        <span>${locationQty}V</span>
                        <button class="action-button database-mini-button" data-db-action="add-ripiano" data-location="${escapeAttr(location.name)}">+ Ripiano</button>
                        ${location.type === 'terra' ? '' : `<button class="action-button database-mini-button remove-btn ctrl-shift-required" data-db-action="remove-scaffolding" data-location="${escapeAttr(location.name)}">Rimuovi</button>`}
                    </div>`;

        if (locationCollapsed) {
            html += '</section>';
            return;
        }

        html += '<div class="database-ripiani-grid">';

        (location.ripiani || []).forEach(ripiano => {
            const ripianoQty = (ripiano.tubi || []).reduce((sum, tube) => sum + (parseInt(tube.quantita, 10) || 0), 0);
            const collapseKey = databaseRipianoKey(location.name, ripiano.name);
            const isCollapsed = !!dbCollapsedRipiani[collapseKey];
            html += `<div class="database-ripiano${isCollapsed ? ' collapsed' : ''}">
                        <div class="database-ripiano-header">
                            <button class="database-ripiano-toggle" data-db-action="toggle-ripiano" data-collapse-key="${escapeAttr(collapseKey)}" title="${isCollapsed ? 'Espandi ripiano' : 'Comprimi ripiano'}">${isCollapsed ? '▶' : '▼'}</button>
                            <strong>${escapeHtml(ripiano.name)}</strong>
                            <span>${ripianoQty}V</span>
                            <button class="action-button database-mini-button" data-db-action="add-here" data-location="${escapeAttr(location.name)}" data-ripiano="${escapeAttr(ripiano.name)}">Aggiungi qui</button>
                            <button class="action-button database-mini-button remove-btn ctrl-shift-required" data-db-action="remove-ripiano" data-location="${escapeAttr(location.name)}" data-ripiano="${escapeAttr(ripiano.name)}">Rimuovi</button>
                        </div>`;

            if (isCollapsed) {
                html += '';
            } else if (!ripiano.tubi || ripiano.tubi.length === 0) {
                html += '<div class="database-empty-ripiano">Vuoto</div>';
            } else {
                html += '<div class="database-tube-cards">';
                ripiano.tubi.forEach(tube => {
                    html += `<div class="database-tube-card"
                                data-location="${escapeAttr(location.name)}"
                                data-ripiano="${escapeAttr(ripiano.name)}"
                                data-misura="${escapeAttr(tube.misura)}"
                                data-spessore="${escapeAttr(formatNumber(tube.spessore))}"
                                data-materiale="${escapeAttr(tube.materiale)}"
                                data-finitura="${escapeAttr(tube.finitura)}"
                                data-quantita="${escapeAttr(tube.quantita)}"
                                data-codice="${escapeAttr(tube.codice || '')}"
                                data-priorita="${escapeAttr(tube.priorita || '')}">
                                <div class="database-tube-main">${escapeHtml(tube.misura)} x ${escapeHtml(formatNumber(tube.spessore))}</div>
                                <div class="database-tube-meta">${escapeHtml(tube.materiale)} ${escapeHtml(tube.finitura)} · ${escapeHtml(tube.forma)}</div>
                                <div class="database-tube-code ${tube.hasCode ? '' : 'missing-code'}">${escapeHtml(tube.codice || 'CODICE MANCANTE')}</div>
                                ${tube.hasCode ? orderPriorityBadge(tube.priorita) : ''}
                                <div class="database-tube-actions">
                                    <strong>${escapeHtml(tube.quantita)}V</strong>
                                    <button class="action-button database-mini-button" data-db-action="edit-code">Codice</button>
                                    <button class="action-button database-mini-button" data-db-action="move">Sposta</button>
                                    <button class="action-button database-mini-button remove-btn" data-db-action="remove">Modifica</button>
                                </div>
                             </div>`;
                });
                html += '</div>';
            }
            html += '</div>';
        });
        html += '</div></section>';
    });
    html += '</div>';
    tubeDatabaseList.innerHTML = html;
}
function compareTubeDatabaseRows(a, b) {
    const key = tubeDatabaseSort.key;
    let valA = a[key];
    let valB = b[key];
    if (key === 'posizioni') {
        valA = (a.posizioni || []).map(p => p.label).join(' ');
        valB = (b.posizioni || []).map(p => p.label).join(' ');
    }
    const numA = parseFloat(valA);
    const numB = parseFloat(valB);
    let result;
    if (!Number.isNaN(numA) && !Number.isNaN(numB)) {
        result = numA - numB;
    } else {
        result = String(valA || '').localeCompare(String(valB || ''), 'it', { numeric: true });
    }
    return tubeDatabaseSort.dir === 'asc' ? result : -result;
}
async function handleTubeDatabaseClick(event) {
    const actionButton = event.target.closest('[data-db-action]');
    if (actionButton) {
        const action = actionButton.dataset.dbAction;
        if (action === 'toggle-location') {
            const name = actionButton.dataset.location;
            if (name) {
                dbCollapsedLocations[name] = !dbCollapsedLocations[name];
                if (!dbCollapsedLocations[name]) {
                    delete dbCollapsedLocations[name];
                }
                uiPreferences.databaseCollapsedLocations = dbCollapsedLocations;
                saveUiState();
                renderTubeDatabase(tubeDatabaseDebug);
            }
            return;
        }
        if (action === 'toggle-ripiano') {
            const key = actionButton.dataset.collapseKey;
            if (key) {
                dbCollapsedRipiani[key] = !dbCollapsedRipiani[key];
                if (!dbCollapsedRipiani[key]) {
                    delete dbCollapsedRipiani[key];
                }
                uiPreferences.databaseCollapsedRipiani = dbCollapsedRipiani;
                saveUiState();
                renderTubeDatabase(tubeDatabaseDebug);
            }
            return;
        }
        if (action === 'add-ripiano') {
            await addTubeDatabaseRipiano(actionButton.dataset.location);
            return;
        }
        if (action === 'remove-ripiano') {
            if (!isCtrlShiftActive()) return;
            await removeTubeDatabaseRipiano(actionButton.dataset.location, actionButton.dataset.ripiano);
            return;
        }
        if (action === 'remove-scaffolding') {
            if (!isCtrlShiftActive()) return;
            await removeTubeDatabaseScaffolding(actionButton.dataset.location);
            return;
        }
        if (action === 'add-here') {
            openTubeDatabaseModal('add', {
                target_location: actionButton.dataset.location,
                target_ripiano: actionButton.dataset.ripiano
            });
            return;
        }
        if (action === 'edit-code') {
            const card = actionButton.closest('.database-tube-card');
            const source = card || actionButton;
            await editTubeCode({
                misura: source.dataset.misura,
                spessore: source.dataset.spessore,
                materiale: source.dataset.materiale,
                finitura: source.dataset.finitura,
                codice: source.dataset.codice || ''
            });
            return;
        }

        const card = actionButton.closest('.database-tube-card');
        if (!card) return;
        const tube = {
            source_location: card.dataset.location,
            source_ripiano: card.dataset.ripiano,
            misura: card.dataset.misura,
            spessore: card.dataset.spessore,
            materiale: card.dataset.materiale,
            finitura: card.dataset.finitura,
            quantita: card.dataset.quantita
        };
        openTubeDatabaseModal(action, tube);
        return;
    }

    const sortHeader = event.target.closest('[data-db-sort]');
    if (!sortHeader) return;
    const key = sortHeader.dataset.dbSort;
    if (tubeDatabaseSort.key === key) {
        tubeDatabaseSort.dir = tubeDatabaseSort.dir === 'asc' ? 'desc' : 'asc';
    } else {
        tubeDatabaseSort = { key, dir: 'asc' };
    }
    renderTubeDatabase(tubeDatabaseDebug);
}
function openTubeDatabaseModal(operation, data = {}) {
    dbCurrentOperation = { operation, data };
    const isAdd = operation === 'add';
    const isMove = operation === 'move';
    const isRemove = operation === 'remove';

    dbTubeModalTitle.textContent = isAdd ? 'Aggiungi tubo' : isMove ? 'Sposta tubo' : 'Modifica tubo';
    dbTubeModalSource.textContent = isAdd ? '' : `Origine: ${data.source_location} / ${data.source_ripiano}`;

    dbModalMisura.value = data.misura || '';
    dbModalSpessore.value = data.spessore || '';
    dbModalMateriale.value = data.materiale || '304';
    dbModalFinitura.value = data.finitura || '2B';
    dbModalQuantita.value = data.quantita || 1;
    dbModalQuantita.min = isRemove ? '0' : '1';

    [dbModalMisura, dbModalSpessore, dbModalMateriale, dbModalFinitura].forEach(input => {
        input.disabled = !isAdd;
    });

    dbModalTargetSection.style.display = isRemove ? 'none' : 'block';
    populateTargetLocations(data.target_location || data.source_location);
    if (isAdd && data.target_location) {
        dbModalTargetLocation.value = data.target_location;
    }
    populateTargetRipiani(data.target_ripiano || data.source_ripiano);
    dbModalNewRipiano.value = '';

    dbTubeModal.style.display = 'flex';
}
function closeTubeDatabaseModal() {
    if (dbTubeModal) dbTubeModal.style.display = 'none';
    dbCurrentOperation = null;
}
function populateTargetLocations(selectedLocation) {
    if (!dbModalTargetLocation) return;
    const targets = tubeDatabaseTargets || [];
    dbModalTargetLocation.innerHTML = targets.map(target => {
        const selected = target.name === selectedLocation ? ' selected' : '';
        return `<option value="${escapeAttr(target.name)}"${selected}>${escapeHtml(target.name)}</option>`;
    }).join('');
}
function populateTargetRipiani(selectedRipiano) {
    if (!dbModalTargetRipiano || !dbModalTargetLocation) return;
    const locationName = dbModalTargetLocation.value;
    const target = (tubeDatabaseTargets || []).find(item => item.name === locationName);
    const ripiani = target?.ripiani || [];
    dbModalTargetRipiano.innerHTML = ripiani.map(ripiano => {
        const selected = ripiano === selectedRipiano ? ' selected' : '';
        return `<option value="${escapeAttr(ripiano)}"${selected}>${escapeHtml(ripiano)}</option>`;
    }).join('');
}
function tubeModalPayload() {
    const operation = dbCurrentOperation?.operation;
    const data = dbCurrentOperation?.data || {};
    const targetRipiano = (dbModalNewRipiano.value || '').trim() || dbModalTargetRipiano.value;
    const base = {
        misura: dbModalMisura.value.trim(),
        spessore: dbModalSpessore.value.trim(),
        materiale: dbModalMateriale.value,
        finitura: dbModalFinitura.value,
        quantita: parseInt(dbModalQuantita.value, 10) || 0
    };

    if (operation === 'add') {
        return {
            ...base,
            target_location: dbModalTargetLocation.value,
            target_ripiano: targetRipiano
        };
    }

    if (operation === 'move') {
        return {
            ...base,
            source_location: data.source_location,
            source_ripiano: data.source_ripiano,
            target_location: dbModalTargetLocation.value,
            target_ripiano: targetRipiano
        };
    }

    return {
        ...base,
        source_location: data.source_location,
        source_ripiano: data.source_ripiano
    };
}
async function submitTubeDatabaseOperation() {
    if (!dbCurrentOperation) return;
    const payload = tubeModalPayload();
    if (!payload.misura || !payload.spessore || payload.quantita < 0 || (dbCurrentOperation.operation !== 'remove' && payload.quantita <= 0)) {
        alert('Inserisci misura, spessore e una quantità maggiore di zero.');
        return;
    }

    let resp;
    try {
        if (dbCurrentOperation.operation === 'add') {
            resp = await window.pywebview.api.add_tube_database_item(payload);
        } else if (dbCurrentOperation.operation === 'move') {
            resp = await window.pywebview.api.move_tube_database_item(payload);
        } else {
            resp = await window.pywebview.api.set_tube_database_item_quantity(payload);
        }
    } catch (e) {
        console.error(e);
        alert('Errore durante la modifica del Database Tubi.');
        return;
    }

    if (!resp || resp.status !== 'success') {
        alert(resp?.message || 'Errore durante la modifica del Database Tubi.');
        return;
    }

    closeTubeDatabaseModal();
    await loadTubeDatabase();
}
function clearTubeDatabaseFilters() {
    if (dbFilterMisura) dbFilterMisura.value = '';
    if (dbFilterSpessore) dbFilterSpessore.value = '';
    if (dbFilterForma) dbFilterForma.value = 'tutti';
    if (dbFilterMateriale) dbFilterMateriale.value = 'tutti';
    if (dbFilterFinitura) dbFilterFinitura.value = 'tutti';
    if (dbFilterCodice) dbFilterCodice.value = 'tutti';
    loadTubeDatabase();
}
async function editTubeCode(data) {
    if (!data || !data.misura || !data.spessore) return;
    const label = `${data.misura}x${data.spessore} ${data.materiale || ''} ${data.finitura || ''}`.trim();
    const value = prompt(`Codice per ${label}\nUna misura puo avere un solo codice. Lascia vuoto per rimuoverlo.`, data.codice || '');
    if (value === null) return;
    try {
        const resp = await window.pywebview.api.set_tube_code_catalog_entry({
            misura: data.misura,
            spessore: data.spessore,
            materiale: data.materiale,
            finitura: data.finitura,
            code: value
        });
        if (!resp || resp.status !== 'success') {
            alert(resp?.message || 'Errore durante salvataggio codice.');
            return;
        }
        await loadTubeDatabase();
    } catch (e) {
        console.error(e);
        alert('Errore durante salvataggio codice.');
    }
}
async function addManualTubeCode() {
    const misura = prompt('Misura tubo (es. 101.6 oppure 50x50):');
    if (misura === null || !misura.trim()) return;
    const spessore = prompt('Spessore:', '2');
    if (spessore === null || !spessore.trim()) return;
    const materiale = prompt('Materiale:', '304');
    if (materiale === null || !materiale.trim()) return;
    const finitura = prompt('Finitura:', '2B');
    if (finitura === null || !finitura.trim()) return;
    const code = prompt(`Codice per ${misura.trim()} x ${spessore.trim()} ${materiale.trim()} ${finitura.trim()}:`);
    if (code === null || !code.trim()) return;

    try {
        const resp = await window.pywebview.api.set_tube_code_catalog_entry({
            misura: misura.trim(),
            spessore: spessore.trim(),
            materiale: materiale.trim(),
            finitura: finitura.trim(),
            code: code.trim()
        });
        if (!resp || resp.status !== 'success') {
            alert(resp?.message || 'Errore durante il salvataggio del codice.');
            return;
        }
        alert('Codice salvato nel catalogo condiviso.');
        await loadTubeDatabase();
    } catch (e) {
        console.error(e);
        alert('Errore durante il salvataggio del codice.');
    }
}
async function addTubeDatabaseScaffolding() {
    const name = prompt('Nome nuova scaffalatura:');
    if (!name || !name.trim()) return;
    try {
        const resp = await window.pywebview.api.add_tube_database_scaffolding({ name: name.trim() });
        if (!resp || resp.status !== 'success') {
            alert(resp?.message || 'Errore durante aggiunta scaffalatura.');
            return;
        }
        await loadTubeDatabase();
    } catch (e) {
        console.error(e);
        alert('Errore durante aggiunta scaffalatura.');
    }
}
async function removeTubeDatabaseScaffolding(locationName) {
    if (!locationName) return;
    try {
        const resp = await window.pywebview.api.remove_tube_database_scaffolding({ name: locationName });
        if (!resp || resp.status !== 'success') {
            alert(resp?.message || 'Errore durante rimozione scaffalatura.');
            return;
        }
        delete dbCollapsedLocations[locationName];
        uiPreferences.databaseCollapsedLocations = dbCollapsedLocations;
        await saveUiState();
        await loadTubeDatabase();
    } catch (e) {
        console.error(e);
        alert('Errore durante rimozione scaffalatura.');
    }
}
async function addTubeDatabaseRipiano(locationName) {
    if (!locationName) return;
    const ripiano = prompt('Nome nuovo ripiano/zona (lascia vuoto per automatico):') || '';
    try {
        const resp = await window.pywebview.api.add_tube_database_ripiano({ location: locationName, ripiano: ripiano.trim() });
        if (!resp || resp.status !== 'success') {
            alert(resp?.message || 'Errore durante aggiunta ripiano.');
            return;
        }
        await loadTubeDatabase();
    } catch (e) {
        console.error(e);
        alert('Errore durante aggiunta ripiano.');
    }
}
async function removeTubeDatabaseRipiano(locationName, ripianoName) {
    if (!locationName || !ripianoName) return;
    try {
        const resp = await window.pywebview.api.remove_tube_database_ripiano({ location: locationName, ripiano: ripianoName });
        if (!resp || resp.status !== 'success') {
            alert(resp?.message || 'Errore durante rimozione ripiano.');
            return;
        }
        delete dbCollapsedRipiani[databaseRipianoKey(locationName, ripianoName)];
        uiPreferences.databaseCollapsedRipiani = dbCollapsedRipiani;
        await saveUiState();
        await loadTubeDatabase();
    } catch (e) {
        console.error(e);
        alert('Errore durante rimozione ripiano.');
    }
}
function setAllDatabaseLocationsCollapsed(collapsed) {
    dbCollapsedLocations = {};
    if (collapsed) {
        (tubeDatabaseLocations || []).forEach(location => {
            dbCollapsedLocations[location.name] = true;
        });
    }
    uiPreferences.databaseCollapsedLocations = dbCollapsedLocations;
    saveUiState();
    renderTubeDatabase(tubeDatabaseDebug);
}
function setAllDatabaseRipianiCollapsed(collapsed) {
    dbCollapsedRipiani = {};
    if (collapsed) {
        (tubeDatabaseLocations || []).forEach(location => {
            (location.ripiani || []).forEach(ripiano => {
                dbCollapsedRipiani[databaseRipianoKey(location.name, ripiano.name)] = true;
            });
        });
    }
    uiPreferences.databaseCollapsedRipiani = dbCollapsedRipiani;
    saveUiState();
    renderTubeDatabase(tubeDatabaseDebug);
}
async function importTubeDatabase() {
    if (!confirm('Importare il Database Tubi dal file Excel configurato? Il database attuale verra copiato in _Backups e poi sostituito.')) {
        return;
    }
    tubeDatabaseStatus.textContent = 'Importazione da Excel in corso...';
    try {
        const resp = await window.pywebview.api.import_tube_database_from_excel();
        if (resp && resp.status === 'success') {
            alert(`Importazione completata.\nUbicazioni: ${resp.locations_count}\nRighe: ${resp.rows_count}\nQuantita totale: ${resp.total_quantity}`);
            await loadTubeDatabase();
        } else {
            alert(resp?.message || 'Errore durante importazione Excel.');
            tubeDatabaseStatus.textContent = resp?.message || 'Errore durante importazione Excel.';
        }
    } catch (e) {
        console.error(e);
        alert('Errore durante importazione Excel.');
    }
}
async function validateTubeDatabase() {
    tubeDatabaseStatus.textContent = 'Validazione contro Excel in corso...';
    try {
        const resp = await window.pywebview.api.validate_tube_database();
        if (!resp || resp.status !== 'success') {
            alert(resp?.message || 'Errore durante validazione.');
            await loadTubeDatabase();
            return;
        }

        const mismatches = resp.mismatches || [];
        if (mismatches.length === 0) {
            alert(`Validazione completata: nessuna differenza.\nRighe database: ${resp.database_rows}\nRighe Excel: ${resp.excel_rows}`);
        } else {
            const preview = mismatches.slice(0, 12).map(item =>
                `${item.misura} x ${item.spessore} ${item.materiale || '-'} ${item.finitura}: JSON ${item.database}, Excel ${item.excel}`
            ).join('\n');
            alert(`Validazione completata con ${mismatches.length} differenze.\n\n${preview}${mismatches.length > 12 ? '\n...' : ''}`);
        }
        await loadTubeDatabase();
    } catch (e) {
        console.error(e);
        alert('Errore durante validazione.');
    }
}
async function importTubeCodes() {
    try {
        const resp = await window.pywebview.api.import_tube_code_catalog();
        if (!resp || resp.status !== 'success') {
            alert(resp?.message || 'Errore durante importazione codici.');
            return;
        }
        alert(`Codici importati.\nFonti: ${resp.sources_count}\nRighe importate: ${resp.rows_imported}\nVoci catalogo: ${resp.entries_count}`);
        await loadTubeDatabase();
    } catch (e) {
        console.error(e);
        alert('Errore durante importazione codici.');
    }
}
async function generateAdhocDocx() {
    try {
        const resp = await window.pywebview.api.generate_adhoc_docx();
        if (!resp || resp.status !== 'success') {
            alert(resp?.message || 'Errore durante generazione Lista ADHOC.');
            return;
        }
        alert(`Lista ADHOC generata:\n${resp.path}`);
    } catch (e) {
        console.error(e);
        alert('Errore durante generazione Lista ADHOC.');
    }
}
async function generateTubeCodesDocx() {
    try {
        const resp = await window.pywebview.api.generate_tube_codes_docx();
        if (!resp || resp.status !== 'success') {
            alert(resp?.message || 'Errore durante generazione Lista Codici.');
            return;
        }
        alert(`Lista Codici generata:\n${resp.path}\n\nTubi: ${resp.rows_count}\nCodici mancanti: ${resp.missing_codes_count}`);
    } catch (e) {
        console.error(e);
        alert('Errore durante generazione Lista Codici.');
    }
}
function orderPriorityTubeLabel(row) {
    const misura = String(row.misura || '').split('x').map(part => part.replace('.', ',')).join('x');
    const spessore = String(row.spessore || '').replace('.', ',');
    const prefix = row.forma === 'tondo' ? '\u00d8' : '';
    return `${prefix}${misura}x${spessore} ${row.materiale || ''} ${row.finitura || ''}`.trim();
}
function filteredOrderPriorityRows() {
    const query = String(orderPrioritySearch?.value || '').trim().toLowerCase();
    if (!query) return orderPriorityRows;
    return orderPriorityRows.filter(row => [
        row.codice,
        row.misura,
        row.spessore,
        row.materiale,
        row.finitura,
        row.forma,
        row.priorita,
    ].some(value => String(value || '').toLowerCase().includes(query)));
}
function updateOrderPriorityStatus(visibleCount = filteredOrderPriorityRows().length) {
    if (!orderPriorityStatus) return;
    orderPriorityStatus.textContent = `${orderPriorityRows.length} tubi codificati, ${visibleCount} visibili, ${orderPrioritySelected.size} selezionati.`;
}
function renderOrderPriorityRows() {
    if (!orderPriorityRowsContainer) return;
    const visibleRows = filteredOrderPriorityRows();
    orderPriorityRowsContainer.innerHTML = visibleRows.map(row => {
        const priority = orderPriorityDraft.get(row.key) || row.priorita || 'media';
        const checked = orderPrioritySelected.has(row.key) ? ' checked' : '';
        return `<label class="order-priority-cell order-priority-check-cell">
                    <input type="checkbox" class="order-priority-check" data-order-key="${escapeAttr(row.key)}"${checked}>
                </label>
                <div class="order-priority-cell database-code">${escapeHtml(row.codice)}</div>
                <div class="order-priority-cell">${escapeHtml(orderPriorityTubeLabel(row))}</div>
                <div class="order-priority-cell order-priority-quantity">${escapeHtml(row.quantitaTotale)}</div>
                <div class="order-priority-cell">
                    <select class="search-select order-priority-select" data-order-key="${escapeAttr(row.key)}">
                        <option value="bassa"${priority === 'bassa' ? ' selected' : ''}>Bassa</option>
                        <option value="media"${priority === 'media' ? ' selected' : ''}>Media</option>
                        <option value="alta"${priority === 'alta' ? ' selected' : ''}>Alta</option>
                    </select>
                </div>`;
    }).join('');
    if (!visibleRows.length) {
        orderPriorityRowsContainer.innerHTML = '<div class="order-priority-empty">Nessun tubo codificato trovato.</div>';
    }
    if (orderPrioritySelectAll) {
        orderPrioritySelectAll.checked = visibleRows.length > 0 && visibleRows.every(row => orderPrioritySelected.has(row.key));
        orderPrioritySelectAll.indeterminate = visibleRows.some(row => orderPrioritySelected.has(row.key)) && !orderPrioritySelectAll.checked;
    }
    updateOrderPriorityStatus(visibleRows.length);
}
async function openOrderPriorityModal() {
    try {
        const response = await window.pywebview.api.get_tube_order_priorities();
        if (!response || response.status !== 'success') {
            alert(response?.message || 'Impossibile caricare le priorita ordini.');
            return;
        }
        orderPriorityRows = response.rows || [];
        orderPriorityDraft = new Map(orderPriorityRows.map(row => [row.key, row.priorita || 'media']));
        orderPrioritySelected = new Set();
        if (orderPrioritySearch) orderPrioritySearch.value = '';
        renderOrderPriorityRows();
        document.body.classList.add('modal-scroll-locked');
        orderPriorityModal.style.display = 'flex';
    } catch (error) {
        console.error(error);
        alert('Impossibile caricare le priorita ordini.');
    }
}
function closeOrderPriorityModal() {
    if (orderPriorityModal) orderPriorityModal.style.display = 'none';
    document.body.classList.remove('modal-scroll-locked');
}
function toggleAllVisibleOrderPriorities() {
    const checked = !!orderPrioritySelectAll?.checked;
    filteredOrderPriorityRows().forEach(row => {
        if (checked) orderPrioritySelected.add(row.key);
        else orderPrioritySelected.delete(row.key);
    });
    renderOrderPriorityRows();
}
function handleOrderPriorityRowChange(event) {
    const key = event.target?.dataset?.orderKey;
    if (!key) return;
    if (event.target.classList.contains('order-priority-check')) {
        if (event.target.checked) orderPrioritySelected.add(key);
        else orderPrioritySelected.delete(key);
        renderOrderPriorityRows();
        return;
    }
    if (event.target.classList.contains('order-priority-select')) {
        orderPriorityDraft.set(key, event.target.value);
    }
}
function applyBulkOrderPriority() {
    if (!orderPrioritySelected.size) {
        if (orderPriorityStatus) orderPriorityStatus.textContent = 'Seleziona almeno un tubo da modificare.';
        return;
    }
    const priority = document.getElementById('order-priority-bulk-value')?.value || 'media';
    orderPrioritySelected.forEach(key => orderPriorityDraft.set(key, priority));
    renderOrderPriorityRows();
}
async function saveOrderPriorities() {
    const items = orderPriorityRows.map(row => ({
        key: row.key,
        priorita: orderPriorityDraft.get(row.key) || row.priorita || 'media',
    }));
    if (!items.length) return;
    try {
        const response = await window.pywebview.api.set_tube_order_priorities({ items });
        if (!response || response.status !== 'success') {
            alert(response?.message || 'Errore durante il salvataggio delle priorita.');
            return;
        }
        closeOrderPriorityModal();
        await loadTubeDatabase();
    } catch (error) {
        console.error(error);
        alert('Errore durante il salvataggio delle priorita.');
    }
}
async function generateTubeOrderTxt() {
    try {
        const response = await window.pywebview.api.generate_tube_order_txt();
        if (response?.status === 'empty') {
            alert(response.message || 'Nessun tubo ha raggiunto la soglia di riordino.');
            return;
        }
        if (!response || response.status !== 'success') {
            alert(response?.message || 'Errore durante la generazione della lista ordine.');
            return;
        }
        alert(`Lista ordine generata:\n${response.path}\n\nTubi da ordinare: ${response.rows_count}`);
    } catch (error) {
        console.error(error);
        alert('Errore durante la generazione della lista ordine.');
    }
}
async function resetAdhocBaseline() {
    if (!isCtrlShiftActive()) return;
    try {
        const resp = await window.pywebview.api.reset_adhoc_baseline();
        if (!resp || resp.status !== 'success') {
            alert(resp?.message || 'Errore durante azzeramento ADHOC.');
            return;
        }
        alert(`Baseline ADHOC aggiornata.\nRighe salvate nel backlog precedente: ${resp.rows_logged}`);
        await loadTubeDatabase();
    } catch (e) {
        console.error(e);
        alert('Errore durante azzeramento ADHOC.');
    }
}
async function selectFolderAndReload() {
    try {
        const response = await window.pywebview.api.select_and_save_da_fare_folder();
        if (response.status === 'success') {
            await initialize();
        } else if (response.status !== 'cancelled') {
            alert(`Error saving folder path: ${response.message}`);
        }
    } catch(e) {
        alert("Failed to open folder dialog.");
        console.error(e);
    }
}
async function saveUiState() {
    await window.pywebview.api.save_ui_state({
        daFareOverrides: daFareOverrides,
        uiPreferences: uiPreferences,
        lockedRods: lockedRods
    });
}


// --- Core UI Rendering ---
async function renderUI() {
    if (mainContainer.style.display === 'none') return;
    const renderSequence = ++nestingRenderSequence;

    try {
        const nextAvailableSegmentMap = new Map();
        const nextCurrentPieceMap = new Map();
        const nextCurrentPieceByLogicalKey = new Map();
        const groupContexts = [];
        const nestRequests = [];

        (allTubeData || []).sort((a, b) => a.tubeType.localeCompare(b.tubeType));

        for (const group of (allTubeData || [])) {
            const pieceInstances = buildPieceInstances(group.pieces);
            const lockedForTube = reconcileLockedRodsForTube(group.tubeType, pieceInstances);
            const lockedInstanceKeys = new Set();

            lockedForTube.forEach(rod => {
                (rod.segments || []).forEach(segment => {
                    if (!segment.done && segment.instanceKey) lockedInstanceKeys.add(segment.instanceKey);
                });
            });

            const piecesToNest = pieceInstances.filter(segment => !lockedInstanceKeys.has(segment.instanceKey));
            piecesToNest.forEach(segment => nextAvailableSegmentMap.set(segment.instanceKey, segment));
            group.pieces.forEach(piece => {
                nextCurrentPieceMap.set(piece.id, piece);
                nextCurrentPieceByLogicalKey.set(piece.logicalKey || stablePieceKey(piece.filePath), piece);
            });

            groupContexts.push({ group, lockedForTube });
            nestRequests.push({ tubeType: group.tubeType, pieces: piecesToNest });
        }

        const nestedGroups = await nestGroupsBackend(nestRequests);
        if (renderSequence !== nestingRenderSequence) return;

        const nestedByTube = new Map(nestedGroups.map(group => [group.tubeType, group.rods || []]));
        availableSegmentMap = nextAvailableSegmentMap;
        currentPieceMap = nextCurrentPieceMap;
        currentPieceByLogicalKey = nextCurrentPieceByLogicalKey;

        mainContainer.innerHTML = '';
        renderIgsWarningBanner();
        renderZzxWarningBanner();

        if (!allTubeData || allTubeData.length === 0) {
            mainContainer.insertAdjacentHTML('beforeend', '<p>No tubes found.</p>');
            return;
        }

        mainContainer.insertAdjacentHTML('beforeend', renderNestingGlobalControls());

        groupContexts.forEach(({ group, lockedForTube }) => {
            const nestedRods = nestedByTube.get(group.tubeType) || [];
            const groupDiv = document.createElement('div');
            groupDiv.className = 'tube-group';

            const header = document.createElement('div');
            header.className = 'tube-header';
            header.dataset.tubeType = group.tubeType;
            const isExpanded = expandedState[group.tubeType] || false;
            const headerText = isExpanded ? `▼ ${group.tubeType}` : `► ${group.tubeType}`;
            const styledHeaderText = headerText.replace(/316/g, '<span class="material-316">316</span>');
            header.innerHTML = `<span class="tube-header-label">${styledHeaderText}</span>
                <button class="action-button icon-button tube-location-button" data-action="search-tube-location" data-tube-type="${escapeAttr(group.tubeType)}" title="Mostra ubicazioni tubo" aria-label="Mostra ubicazioni tubo">&#128269;</button>`;

            const detailsDiv = document.createElement('div');
            detailsDiv.className = 'tube-details';
            detailsDiv.style.display = isExpanded ? 'block' : 'none';
            detailsDiv.innerHTML = renderRodsHTML(group.tubeType, lockedForTube, nestedRods);
            detailsDiv.innerHTML += renderPieceListHTML(group.pieces, group.tubeType);

            groupDiv.appendChild(header);
            groupDiv.appendChild(detailsDiv);
            mainContainer.appendChild(groupDiv);
        });

        updateGuardedButtonStates();
    } catch (error) {
        if (renderSequence !== nestingRenderSequence) return;
        console.error('Backend nesting render failed:', error);
        mainContainer.innerHTML = '<p style="color: red;">Errore durante il nesting backend. Controlla il terminale.</p>';
    }
}
function renderIgsWarningBanner() {
    if (!unmatchedIgsFiles || unmatchedIgsFiles.length === 0) return;

    const banner = document.createElement('div');
    banner.className = 'igs-warning-banner';

    const title = document.createElement('div');
    title.className = 'igs-warning-title';
    title.textContent = `Attenzione: ${unmatchedIgsFiles.length} file IGS senza ZZX corrispondente`;
    banner.appendChild(title);

    const list = document.createElement('ul');
    unmatchedIgsFiles.slice(0, 25).forEach(item => {
        const li = document.createElement('li');
        const text = document.createElement('span');
        text.textContent = `${item.relativePath}  ->  manca ${item.expectedZzxName}`;

        const button = document.createElement('button');
        button.className = 'action-button igs-open-button';
        button.dataset.action = 'open-igs-file';
        button.dataset.filePath = item.filePath;
        button.textContent = 'Apri';

        li.appendChild(text);
        li.appendChild(button);
        list.appendChild(li);
    });
    banner.appendChild(list);

    if (unmatchedIgsFiles.length > 25) {
        const more = document.createElement('div');
        more.className = 'igs-warning-more';
        more.textContent = `Altri ${unmatchedIgsFiles.length - 25} file non mostrati.`;
        banner.appendChild(more);
    }

    mainContainer.appendChild(banner);
}
function showIgsWarningAlertIfNeeded() {
    const signature = (unmatchedIgsFiles || []).map(item => item.filePath).sort().join('|');
    if (!signature) {
        lastIgsWarningSignature = '';
        return;
    }
    if (signature === lastIgsWarningSignature) return;

    lastIgsWarningSignature = signature;
    const preview = unmatchedIgsFiles.slice(0, 10).map(item => `- ${item.relativePath}`).join('\n');
    const extra = unmatchedIgsFiles.length > 10 ? `\n...e altri ${unmatchedIgsFiles.length - 10}` : '';
    alert(`Attenzione: ci sono ${unmatchedIgsFiles.length} file IGS senza ZZX corrispondente.\n\n${preview}${extra}`);
}

function renderZzxWarningBanner() {
    if (!zzxValidationIssues || zzxValidationIssues.length === 0) return;

    const banner = document.createElement('div');
    banner.className = 'igs-warning-banner';

    const uniqueFiles = new Set(zzxValidationIssues.map(item => item.filePath)).size;
    const title = document.createElement('div');
    title.className = 'igs-warning-title';
    title.textContent = `Attenzione: ${uniqueFiles} file ZZX richiedono controllo disegno`;
    banner.appendChild(title);

    const list = document.createElement('ul');
    zzxValidationIssues.slice(0, 25).forEach(item => {
        const li = document.createElement('li');

        const text = document.createElement('span');
        text.textContent = `${item.relativePath}  ->  ${item.message}`;

        const button = document.createElement('button');
        button.className = 'action-button igs-open-button';
        button.dataset.action = 'open-zzx-validation-file';
        button.dataset.filePath = item.filePath;
        button.textContent = 'Apri';

        li.appendChild(text);
        li.appendChild(button);
        list.appendChild(li);
    });
    banner.appendChild(list);

    if (zzxValidationIssues.length > 25) {
        const more = document.createElement('div');
        more.className = 'igs-warning-more';
        more.textContent = `Altri ${zzxValidationIssues.length - 25} problemi non mostrati.`;
        banner.appendChild(more);
    }

    mainContainer.appendChild(banner);
}

function showZzxWarningAlertIfNeeded() {
    const signature = (zzxValidationIssues || [])
        .map(item => `${item.filePath}|${item.type}|${item.message}`)
        .sort()
        .join('|');

    if (!signature) {
        lastZzxWarningSignature = '';
        return;
    }
    if (signature === lastZzxWarningSignature) return;

    lastZzxWarningSignature = signature;

    const uniqueFiles = new Set(zzxValidationIssues.map(item => item.filePath)).size;
    const preview = zzxValidationIssues.slice(0, 10)
        .map(item => `- ${item.relativePath}\n  ${item.message}`)
        .join('\n');
    const extra = zzxValidationIssues.length > 10
        ? `\n...e altri ${zzxValidationIssues.length - 10} problemi`
        : '';

    alert(
        `Attenzione: ${uniqueFiles} file ZZX richiedono un controllo del disegno.\n\n` +
        `${preview}${extra}`
    );
}

function renderRodsHTML(tubeType, lockedForTube, nestedRods) {
    const unlockedVisible = isUnlockedRodsVisible(tubeType);
    const unlockedCount = unlockedVisible ? nestedRods.length + 1 : 0;
    const totalShown = (lockedForTube?.length || 0) + unlockedCount;
    let html = `<div class="rod-section-heading">
        <h3 class="details-section-title">Visualizzazione Verghe (${totalShown} visibili)</h3>
        <button class="action-button database-mini-button" data-action="toggle-unlocked-rods" data-tube-type="${escapeAttr(tubeType)}">${unlockedVisible ? 'Nascondi libere' : 'Mostra libere'}</button>
    </div>`;

    (lockedForTube || []).forEach((rod, index) => {
        html += renderSingleRodHTML({
            tubeType,
            rodId: rod.rodId,
            label: `Verga bloccata ${index + 1}`,
            kind: 'locked',
            segments: rod.segments || [],
            isLocked: true,
            isDone: !!rod.done
        });
    });

    if (!unlockedVisible) {
        return html;
    }

    nestedRods.forEach((rod, index) => {
        html += renderSingleRodHTML({
            tubeType,
            rodId: `unlocked-${slugify(tubeType)}-${index}`,
            label: `Verga libera ${index + 1}`,
            kind: 'unlocked',
            segments: rod.segments || [],
            isLocked: false,
            nestRod: rod
        });
    });

    html += renderSingleRodHTML({
        tubeType,
        rodId: `unlocked-${slugify(tubeType)}-empty`,
        label: 'Verga libera vuota',
        kind: 'unlocked',
        segments: [],
        isLocked: false,
        isEmptyPlaceholder: true
    });
    return html;
}

function getSegmentPreviewGeometry(segment) {
    const sourcePiece = findPieceById(segment?.sourceId);
    const part = sourcePiece?.tubePart;
    if (!part || !Array.isArray(part.ends) || part.ends.length < 2) return null;

    const profile = part.profile || {};
    const outsideHeight = Number(
        profile.outside_height ?? profile.outside_diameter ?? profile.outside_width
    );
    const partLength = Number(part.overall_length || segment.length || 0);
    if (!Number.isFinite(outsideHeight) || outsideHeight <= 0 || !Number.isFinite(partLength) || partLength <= 0) {
        return null;
    }

    const placement = segment.nestPlacement || {};
    const ends = [
        placement.end_a || part.ends[0],
        placement.end_b || part.ends[1],
    ];

    const readPlane = end => {
        if (!end) return null;
        if (Array.isArray(end.plane_z_equals_c_plus_ax_plus_by)) {
            const [c, sx, sv] = end.plane_z_equals_c_plus_ax_plus_by.map(Number);
            if ([c, sx, sv].every(Number.isFinite)) return { c, sx, sv };
        }
        const c = Number(end.c);
        const sx = Number(end.slope_x);
        const sv = Number(end.slope_vertical);
        if ([c, sx, sv].every(Number.isFinite)) return { c, sx, sv };
        return null;
    };

    const a = readPlane(ends[0]);
    const b = readPlane(ends[1]);
    if (!a || !b) return null;

    const halfHeight = outsideHeight / 2;
    const aTop = a.c + a.sv * halfHeight;
    const aBottom = a.c - a.sv * halfHeight;
    const bTop = b.c + b.sv * halfHeight;
    const bBottom = b.c - b.sv * halfHeight;

    const pct = value => Math.max(0, Math.min(100, value / partLength * 100));
    const polygon = [
        `${pct(aTop).toFixed(3)}% 0%`,
        `${pct(bTop).toFixed(3)}% 0%`,
        `${pct(bBottom).toFixed(3)}% 100%`,
        `${pct(aBottom).toFixed(3)}% 100%`,
    ].join(', ');

    const angleFor = end => {
        const value = Number(end?.angle_from_perpendicular_degrees);
        if (Number.isFinite(value)) return Math.abs(value);
        const plane = readPlane(end);
        return plane ? Math.atan(Math.hypot(plane.sx, plane.sv)) * 180 / Math.PI : 0;
    };

    return {
        polygon,
        leftAngle: angleFor(ends[0]),
        rightAngle: angleFor(ends[1]),
    };
}

function getRodUsedMm(segments, nestRod = null) {
    const backendUsed = Number(nestRod?.used);
    if (Number.isFinite(backendUsed)) return backendUsed;

    const placedEnds = (segments || [])
        .map(segment => Number(segment?.nestPlacement?.z_end))
        .filter(Number.isFinite);
    if (placedEnds.length === (segments || []).length && placedEnds.length > 0) {
        return Math.max(...placedEnds);
    }

    return (segments || []).reduce((sum, segment) => sum + (Number(segment.length) || 0), 0);
}

function segmentLayoutMm(segments) {
    let cursor = 0;
    return (segments || []).map(segment => {
        const placement = segment?.nestPlacement || {};
        let start = Number(placement.z_start);
        let end = Number(placement.z_end);

        if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
            start = cursor;
            end = start + (Number(segment.length) || 0);
        }

        cursor = Math.max(cursor, end);
        return { segment, start, end };
    });
}

function renderSingleRodHTML({ tubeType, rodId, label, kind, segments, isLocked, isDone, isEmptyPlaceholder, nestRod = null }) {
    const totalUsed = getRodUsedMm(segments, nestRod);
    const remainingToCut = (segments || []).reduce((sum, segment) => {
        return segment.done ? sum : sum + (Number(segment.length) || 0);
    }, 0);
    const nominalLockedTotal = (segments || []).reduce((sum, segment) => sum + (Number(segment.length) || 0), 0);
    const usedPercent = (totalUsed / 6000 * 100).toFixed(1);
    const overfull = totalUsed > 6000 + 1e-6;
    const remainingLabel = isLocked ? ` - Rimasto da tagliare ${remainingToCut}/${nominalLockedTotal}mm` : '';
    const rodPieceIds = JSON.stringify((segments || []).map(segment => segment.sourceId || segment.id).filter(Boolean));
    const segmentKeys = JSON.stringify((segments || []).map(segment => segment.instanceKey).filter(Boolean));

    let html = `<div class="rod-entry ${kind}-rod${isDone ? ' rod-complete' : ''}${overfull ? ' rod-overfull' : ''}" data-rod-entry="${escapeAttr(rodId)}">
        <div class="rod-body">
            <p class="rod-label"><strong>${escapeHtml(label)}:</strong> Usati ${totalUsed.toFixed(totalUsed % 1 ? 1 : 0)}mm (${usedPercent}%)${remainingLabel}${overfull ? ' - oltre 6000mm' : ''}</p>
            <div class="rod-bar" data-drop-zone="rod" data-rod-kind="${escapeAttr(kind)}" data-rod-id="${escapeAttr(rodId)}" data-tube-type="${escapeAttr(tubeType)}">
                <div class="rod-chuck-dead-zone" title="Ultimi 400mm: zona non tagliabile senza strategia di ribaltamento"></div>`;

    if (!segments || segments.length === 0) {
        html += `<div class="rod-empty-label">${isEmptyPlaceholder ? 'Trascina qui per sbloccare pezzi' : 'Vuota'}</div>`;
    } else {
        segmentLayoutMm(segments).forEach(({ segment, start, end }) => {
            html += renderRodSegmentHTML(segment, kind, rodId, tubeType, start, end);
        });
    }

    html += `</div></div><div class="rod-actions">`;
    if (kind === 'locked') {
        html += `<button class="action-button icon-button rod-lock-button rod-lock-locked" data-action="unlock-locked-rod" data-rod-id="${escapeAttr(rodId)}" data-tube-type="${escapeAttr(tubeType)}" title="Sblocca verga">&#128274;</button>`;
        html += `<button class="action-button fatto-button ctrl-required" data-action="mark-locked-rod-fatto" data-rod-id="${escapeAttr(rodId)}" data-tube-type="${escapeAttr(tubeType)}"${isDone ? ' disabled' : ''}>Fatto</button>`;
    } else {
        html += `<button class="action-button icon-button rod-lock-button rod-lock-unlocked" data-action="lock-unlocked-rod" data-tube-type="${escapeAttr(tubeType)}" data-segment-keys='${escapeAttr(segmentKeys)}' title="Blocca verga"${segments.length === 0 ? ' disabled' : ''}>&#128275;</button>`;
        html += `<button class="action-button fatto-button ctrl-required" data-action="mark-rod-fatto" data-rod-pieces='${escapeAttr(rodPieceIds)}'${segments.length === 0 ? ' disabled' : ''}>Fatto</button>`;
    }
    html += '</div></div>';
    return html;
}

function renderRodSegmentHTML(segment, kind, rodId, tubeType, startMm, endMm) {
    const leftPercent = Math.max(0, startMm / 6000 * 100);
    const widthPercent = Math.max(0, (endMm - startMm) / 6000 * 100);
    const filePath = segment.currentFilePath || segment.filePath || '';
    const doneClass = segment.done ? ' done' : '';
    const draggable = kind === 'locked' && segment.done ? 'false' : 'true';
    const preview = getSegmentPreviewGeometry(segment);
    const clipStyle = preview ? ` clip-path: polygon(${preview.polygon});` : '';
    const angleText = preview
        ? ` | tagli ${preview.leftAngle.toFixed(1)}° / ${preview.rightAngle.toFixed(1)}°`
        : '';
    const placement = segment.nestPlacement;
    const placementText = placement
        ? ` | Y ${Number(placement.z_start).toFixed(1)}→${Number(placement.z_end).toFixed(1)}`
        : '';
    const title = `${segment.length}mm - ${segment.fileName || ''}${angleText}${placementText}\nClick per aprire il file`;

    let html = `<div class="rod-segment${doneClass}" draggable="${draggable}" data-action="open-file-path" data-file-path="${escapeAttr(filePath)}" data-segment-key="${escapeAttr(segment.instanceKey)}" data-rod-kind="${escapeAttr(kind)}" data-rod-id="${escapeAttr(rodId)}" data-tube-type="${escapeAttr(tubeType)}" style="left: ${leftPercent}%; width: ${widthPercent}%; background-color: ${getColorForPiece(segment)};${clipStyle}" title="${escapeAttr(title)}"><span>${escapeHtml(segment.length)}</span>`;

    if (preview && widthPercent > 4) {
        const leftLabel = preview.leftAngle > 0.05 ? `${preview.leftAngle.toFixed(0)}°` : '';
        const rightLabel = preview.rightAngle > 0.05 ? `${preview.rightAngle.toFixed(0)}°` : '';
        if (leftLabel) html += `<span class="rod-cut-angle rod-cut-angle-left">${escapeHtml(leftLabel)}</span>`;
        if (rightLabel) html += `<span class="rod-cut-angle rod-cut-angle-right">${escapeHtml(rightLabel)}</span>`;
    }

    if (kind === 'locked' && !segment.done) {
        html += `<button class="segment-done-button fatto-button ctrl-required" data-action="mark-locked-segment-fatto" data-segment-key="${escapeAttr(segment.instanceKey)}" data-rod-id="${escapeAttr(rodId)}" data-tube-type="${escapeAttr(tubeType)}" title="Segna questo pezzo come fatto">&#10003;</button>`;
    }
    html += '</div>';
    return html;
}
function renderPieceListHTML(pieces, tubeType) {
    const gridId = `grid-${tubeType.replace(/[^a-zA-Z0-9]/g, '-')}`;
    const sortPref = uiPreferences.sorting[tubeType] || { key: 'mainFolder', dir: 'asc' };
    const columnWidths = uiPreferences.columnWidths[gridId] || '2fr 1fr 1fr 1fr 1fr 2fr';
    pieces.sort((a, b) => {
        let valA, valB;
        if (sortPref.key === 'daFare') {
            valA = daFareOverrides[a.filePath] !== undefined ? daFareOverrides[a.filePath] : a.quantityNeeded;
            valB = daFareOverrides[b.filePath] !== undefined ? daFareOverrides[b.filePath] : b.quantityNeeded;
        } else { valA = a[sortPref.key]; valB = b[sortPref.key]; }
        if (typeof valA === 'string') { return sortPref.dir === 'asc' ? valA.localeCompare(valB) : valB.localeCompare(valA); } 
        else { return sortPref.dir === 'asc' ? valA - valB : valB - valA; }
    });
    const headers = [
        { key: 'mainFolder', label: 'Cartella Prodotto' }, { key: 'length', label: 'Lunghezza' },
        { key: 'totalQuantity', label: 'Quantità Totale' }, { key: 'completedQuantity', label: 'Quantità Fatta' },
        { key: 'daFare', label: 'Da Fare (Editabile)' }, { key: 'actions', label: 'Azioni' }
    ];
    let html = `<h3 class="details-section-title">Elenco Pezzi</h3><div id="${gridId}" class="piece-list" style="grid-template-columns: ${columnWidths};">`;
    headers.forEach((header, index) => {
        const sortArrow = header.key === sortPref.key ? (sortPref.dir === 'asc' ? '▲' : '▼') : '';
        const resizeHandle = index < headers.length - 1 ? `<div class="resize-handle" data-grid-id="${gridId}" data-column-index="${index}"></div>` : '';
        html += `<div class="piece-list-header" data-sort-key="${header.key}" data-tube-type="${tubeType}">${header.label} <span class="sort-arrow">${sortArrow}</span>${resizeHandle}</div>`;
    });
    pieces.forEach(piece => {
        const daFareValue = daFareOverrides[piece.filePath] !== undefined ? daFareOverrides[piece.filePath] : piece.quantityNeeded;
        html += `<div class="piece-item" data-piece-id="${piece.id}">
                    <div>${piece.mainFolder}</div>
                    <div>L${piece.length}</div>
                    <div>${piece.totalQuantity} pz</div>
                    <div><input type="number" class="quantity-input" value="${piece.completedQuantity}" min="0" data-piece-id="${piece.id}" onchange="handleQuantityChange(this)"></div>
                    <div><input type="number" class="dafare-input" value="${daFareValue}" min="0" data-filepath="${piece.filePath}" onchange="handleDaFareChange(this)"></div>
                    <div><button class="action-button" data-action="open-folder" data-piece-id="${piece.id}">Apri</button><button class="action-button fatto-button ctrl-required" data-action="mark-fatto" data-piece-id="${piece.id}">Fatto</button></div>
                 </div>`;
    });
    html += `</div>`;
    return html;
}
function renderHistory(historyData) {
    const startDate = startDateInput.value ? new Date(startDateInput.value) : null;
    const endDate = endDateInput.value ? new Date(endDateInput.value) : null;
    if(startDate) startDate.setHours(0, 0, 0, 0);
    if(endDate) endDate.setHours(23, 59, 59, 999);
    const filteredData = historyData.filter(rod => {
        const completionDate = new Date(rod.completionDate);
        if (startDate && completionDate < startDate) return false;
        if (endDate && completionDate > endDate) return false;
        return true;
    });
    if (filteredData.length === 0) {
        historyList.innerHTML = '<p>No completed items found for the selected period.</p>';
        return;
    }
    const groupedByTube = filteredData.reduce((acc, rod) => {
        (acc[rod.tubeType] = acc[rod.tubeType] || []).push(rod);
        return acc;
    }, {});
    let html = '';
    Object.keys(groupedByTube).sort().forEach(tubeType => {
        const isExpanded = expandedState[tubeType] || false;
        const headerText = isExpanded ? `▼ ${tubeType}` : `► ${tubeType}`;
        const styledHeaderText = headerText.replace(/316/g, '<span class="material-316">316</span>');
        html += `<div class="tube-group"><div class="tube-header" data-tube-type="${tubeType}">${styledHeaderText}</div><div class="tube-details" style="display: ${isExpanded ? 'block' : 'none'};">`;
        groupedByTube[tubeType].sort((a, b) => new Date(b.completionDate) - new Date(a.completionDate));
        groupedByTube[tubeType].forEach(rod => {
            const completionDate = new Date(rod.completionDate);
            const dateStr = completionDate.toLocaleDateString('it-IT', { day: '2-digit', month: 'long', year: 'numeric' });
            const timeStr = completionDate.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' });
            html += `<div class="history-rod-entry" data-rod-id="${rod.rodId}"><input type="checkbox" class="history-checkbox" data-rod-id="${rod.rodId}"><div style="flex-grow: 1;"><p style="margin: 0 0 5px 0;"><strong>Completato il:</strong> ${dateStr} alle ${timeStr}</p><div class="history-rod-bar">`;
            rod.pieces.forEach(piece => {
                const segmentWidth = (piece.length / 6000) * 100;
                html += `<div class="rod-segment" data-action="open-folder" data-piece-id="${piece.id}" data-file-path="${escapeAttr(piece.filePath || '')}" style="width: ${segmentWidth}%; background-color: ${getColorForPiece(piece)};" title="${piece.length}mm - ${piece.fileName}\n(Double-click to open folder)">${piece.length}</div>`;
            });
            html += `</div></div><button class="action-button annulla-btn" data-action="undo-history-item" data-rod-id="${rod.rodId}">Annulla</button><button class="action-button remove-btn" data-action="remove-history-item" data-rod-id="${rod.rodId}">Rimuovi</button></div>`;
        });
        html += `</div></div>`;
    });
    historyList.innerHTML = html;
    lastCheckedCheckbox = null;
}


// --- Event Handlers & Actions ---
async function handleContainerClick(event) {
    const target = event.target;
    const actionTarget = target.closest('[data-action]');
    const headerTarget = target.closest('.tube-header');
    if (headerTarget && !actionTarget) {
        const tubeType = headerTarget.dataset.tubeType;
        expandedState[tubeType] = !expandedState[tubeType];
        renderUI();
        return;
    }
    if (target.classList.contains('piece-list-header')) {
        const sortKey = target.dataset.sortKey;
        const tubeType = target.dataset.tubeType;
        if (sortKey && sortKey !== 'actions') {
            const currentSort = uiPreferences.sorting[tubeType] || {};
            if (currentSort.key === sortKey) {
                currentSort.dir = currentSort.dir === 'asc' ? 'desc' : 'asc';
            } else {
                currentSort.key = sortKey;
                currentSort.dir = 'asc';
            }
            uiPreferences.sorting[tubeType] = currentSort;
            saveUiState();
            renderUI();
        }
        return;
    }
    const action = actionTarget?.dataset.action;
    if (action) {
        if ((action === 'mark-fatto' || action === 'mark-rod-fatto' || action === 'mark-locked-rod-fatto' || action === 'mark-locked-segment-fatto') && !isCtrlPressed) {
            console.log("Ctrl+Click required for 'fatto' action.");
            return;
        }
        const pieceId = actionTarget.dataset.pieceId;
        switch (action) {
            case 'open-folder': if (pieceId) openFolder(pieceId); break;
            case 'open-file-path': if (actionTarget.dataset.filePath) openFilePath(actionTarget.dataset.filePath); break;
            case 'open-igs-file': if (actionTarget.dataset.filePath) openFilePath(actionTarget.dataset.filePath); break;
            case 'open-zzx-validation-file': if (actionTarget.dataset.filePath) openFilePath(actionTarget.dataset.filePath); break;
            case 'search-tube-location':
                await showTubeLocation(actionTarget.dataset.tubeType);
                break;
            case 'mark-fatto': if (pieceId) markFatto(pieceId); break;
            case 'mark-rod-fatto':
                const rodPieces = JSON.parse(actionTarget.dataset.rodPieces || '[]');
                markRodFatto(rodPieces);
                break;
            case 'lock-unlocked-rod':
                lockUnlockedRod(actionTarget.dataset.tubeType, JSON.parse(actionTarget.dataset.segmentKeys || '[]'));
                break;
            case 'unlock-locked-rod':
                unlockLockedRod(actionTarget.dataset.tubeType, actionTarget.dataset.rodId);
                break;
            case 'mark-locked-rod-fatto':
                await markLockedRodFatto(actionTarget.dataset.tubeType, actionTarget.dataset.rodId);
                break;
            case 'mark-locked-segment-fatto':
                await markLockedSegmentFatto(actionTarget.dataset.tubeType, actionTarget.dataset.rodId, actionTarget.dataset.segmentKey);
                break;
            case 'toggle-unlocked-rods':
                toggleUnlockedRodsForTube(actionTarget.dataset.tubeType);
                break;
            case 'toggle-all-unlocked-rods':
                toggleAllUnlockedRods();
                break;
            case 'lock-all-rods':
                await lockAllUnlockedRods();
                break;
            case 'unlock-all-rods':
                unlockAllLockedRods();
                break;
        }
    }
}
function handleContainerDblClick(event) {
    const target = event.target.closest('.rod-segment');
    if (!target) return;
    if (target.classList.contains('rod-segment') && target.dataset.action === 'open-folder') {
        if (target.dataset.pieceId) openFolder(target.dataset.pieceId);
    }
    if (target.classList.contains('rod-segment') && target.dataset.action === 'open-file-path') {
        if (target.dataset.filePath) openFilePath(target.dataset.filePath);
    }
}
function handleHistoryContainerClick(event) {
    const target = event.target;
    if (target.classList.contains('tube-header')) {
        const tubeType = target.dataset.tubeType;
        expandedState[tubeType] = !expandedState[tubeType];
        showHistoryView();
        return;
    }
    const action = target.dataset.action;
    if (action) {
        const rodId = target.dataset.rodId;
        switch (action) {
            case 'remove-history-item': removeHistoryItems([rodId]); break;
            case 'undo-history-item': undoHistoryItem(rodId); break;
        }
    }
    if (target.classList.contains('history-checkbox')) {
        if (event.shiftKey && lastCheckedCheckbox) {
            const checkboxes = Array.from(historyList.querySelectorAll('.history-checkbox'));
            const start = checkboxes.indexOf(lastCheckedCheckbox);
            const end = checkboxes.indexOf(target);
            checkboxes.slice(Math.min(start, end), Math.max(start, end) + 1).forEach(cb => cb.checked = target.checked);
        }
        lastCheckedCheckbox = target;
    }
}
async function openFolder(pieceId) {
    try { await window.pywebview.api.open_folder_by_id(pieceId); } 
    catch (e) { console.error("Error calling open_folder_by_id:", e); }
}
async function openFilePath(filePath) {
    try {
        const response = await window.pywebview.api.open_file_path(filePath);
        if (response && response.status === 'error') {
            alert(`Errore: ${response.message}`);
        }
    } catch (e) {
        console.error("Error calling open_file_path:", e);
        alert("Errore durante l'apertura del file IGS.");
    }
}
async function showTubeLocation(tubeType) {
    if (!tubeType) return;
    try {
        const response = await window.pywebview.api.search_tube_inventory_by_type(tubeType);
        if (response && response.text) {
            openSearchModal(response.text);
        } else {
            alert(response?.message || 'Errore durante la ricerca in inventario.');
        }
    } catch (error) {
        console.error('Error searching tube locations:', error);
        alert('Errore durante la ricerca in inventario.');
    }
}
async function markFatto(pieceId) {
    const piece = findPieceById(pieceId);
    if (!piece) return;
    try {
        const response = await window.pywebview.api.process_quantity_update_by_id(piece.id, piece.totalQuantity);
        if (response.status === 'success') {
            delete daFareOverrides[piece.filePath];
            await saveUiState();
            if (response.popup_message) alert(response.popup_message);
            loadTubeData();
        } else { alert(`Errore: ${response.message}`); }
    } catch (e) { console.error("Error in markFatto:", e); }
}
async function markRodFatto(pieceIds) {
    try {
        const response = await window.pywebview.api.mark_rod_fatto_by_ids(pieceIds);
        if (response.status === 'success') {
            for (const pieceId of pieceIds) {
                const piece = findPieceById(pieceId);
                if (piece) { delete daFareOverrides[piece.filePath]; }
            }
            await saveUiState();
            if (response.popup_message) alert(response.popup_message);
            loadTubeData();
        } else { alert(`Errore durante l'operazione: ${response.message}`); }
    } catch (e) {
        console.error("Error calling mark_rod_fatto_by_ids:", e);
        alert("Si è verificato un errore critico. Controlla la console.");
    }
}
async function handleDaFareChange(input) {
    const filePath = input.dataset.filepath;
    const newDaFare = Math.max(0, parseInt(input.value, 10));
    daFareOverrides[filePath] = newDaFare;
    await saveUiState();
    await renderUI();
}
async function handleQuantityChange(input) {
    const pieceId = input.dataset.pieceId;
    const newCompletedCount = parseInt(input.value, 10);
    try {
        const response = await window.pywebview.api.process_quantity_update_by_id(pieceId, newCompletedCount);
        if (response.status === 'success') {
            if (response.popup_message) alert(response.popup_message);
            loadTubeData();
        } else {
            alert(`Errore: ${response.message}`);
            loadTubeData(); // Revert visual changes on error
        }
    } catch(e) { console.error("Error calling process_quantity_update_by_id:", e); }
}
async function removeSelectedHistory() {
    const selectedIds = Array.from(historyList.querySelectorAll('.history-checkbox:checked')).map(cb => cb.dataset.rodId);
    if (selectedIds.length === 0) { alert("Nessun elemento selezionato da rimuovere."); return; }
    removeHistoryItems(selectedIds);
}
async function removeAllHistory() {
    const allIds = Array.from(historyList.querySelectorAll('.history-rod-entry')).map(entry => entry.dataset.rodId);
    if (allIds.length === 0) { alert("Nessun elemento nella vista corrente da rimuovere."); return; }
    removeHistoryItems(allIds);
}
async function removeHistoryItems(rodIds) {
    if (!confirm(`Sei sicuro di voler rimuovere ${rodIds.length} elementi dalla cronologia? Questa azione è permanente.`)) return;
    try {
        await window.pywebview.api.remove_history_items(rodIds);
        showHistoryView();
    } catch(e) { alert("Errore durante la rimozione."); console.error(e); }
}
async function undoHistoryItem(rodId) {
    if (!confirm(`Sei sicuro di voler annullare questa verga? I file verranno ripristinati e la voce verrà rimossa dalla cronologia.`)) return;
    try {
        const response = await window.pywebview.api.undo_rod_by_id(rodId);
        if (response.status === 'success') {
            await showHistoryView();
            await loadTubeData();
        } else {
            alert(`Errore durante l'annullamento: ${response.message}`);
        }
    } catch(e) {
        alert("Errore critico durante l'operazione di annullamento.");
        console.error(e);
    }
}


// --- Guarded Buttons & Column Resizing ---
function updateFattoButtonStates() {
    updateGuardedButtonStates();
}
function isCtrlShiftActive() {
    return isCtrlPressed && isShiftPressed;
}
function updateGuardedButtonStates() {
    const buttons = document.querySelectorAll('.fatto-button.ctrl-required');
    buttons.forEach(btn => {
        if (isCtrlPressed && !btn.disabled) {
            btn.classList.add('ctrl-active');
            btn.title = '';
        } else {
            btn.classList.remove('ctrl-active');
            btn.title = 'Cliccare tenendo premuto il tasto Ctrl.';
        }
    });

    const dangerButtons = document.querySelectorAll('.ctrl-shift-required');
    dangerButtons.forEach(btn => {
        if (isCtrlShiftActive() && !btn.disabled) {
            btn.classList.add('ctrl-shift-active');
            btn.title = '';
        } else {
            btn.classList.remove('ctrl-shift-active');
            btn.title = 'Tenere premuti Ctrl + Shift per abilitare.';
        }
    });
}
let startX, startWidths, gridElement, columnIndex;
function handleResizeMouseDown(e) {
    if (e.target.classList.contains('resize-handle')) {
        gridElement = document.getElementById(e.target.dataset.gridId);
        startX = e.pageX;
        columnIndex = parseInt(e.target.dataset.columnIndex, 10);
        const gridComputedStyle = window.getComputedStyle(gridElement);
        const gridColumnValues = gridComputedStyle.getPropertyValue('grid-template-columns').split(' ');
        startWidths = gridColumnValues.map(val => parseFloat(val));
        document.addEventListener('mousemove', handleResizeMouseMove);
        document.addEventListener('mouseup', handleResizeMouseUp);
        e.preventDefault();
    }
}
function handleResizeMouseMove(e) {
    if (!gridElement) return;
    const dx = e.pageX - startX;
    const newWidths = [...startWidths];
    newWidths[columnIndex] += dx;
    gridElement.style.gridTemplateColumns = newWidths.map(w => `${w}px`).join(' ');
}
async function handleResizeMouseUp() {
    if (gridElement) {
        const gridId = gridElement.id;
        uiPreferences.columnWidths[gridId] = gridElement.style.gridTemplateColumns;
        await saveUiState();
    }
    document.removeEventListener('mousemove', handleResizeMouseMove);
    document.removeEventListener('mouseup', handleResizeMouseUp);
    gridElement = null;
}


// --- Locked Rod State & Dragging ---
function renderNestingGlobalControls() {
    const allVisible = uiPreferences.showUnlockedRodsDefault !== false;
    return `<div class="nesting-global-controls">
        <button class="action-button" data-action="toggle-all-unlocked-rods">${allVisible ? 'Nascondi tutte le verghe libere' : 'Mostra tutte le verghe libere'}</button>
        <button class="action-button" data-action="lock-all-rods">Blocca tutte le verghe</button>
        <button class="action-button" data-action="unlock-all-rods">Sblocca tutte le verghe</button>
    </div>`;
}
function sanitizeLockedRods(raw) {
    const cleaned = {};
    Object.entries(raw || {}).forEach(([tubeType, rods]) => {
        if (!Array.isArray(rods)) return;
        cleaned[tubeType] = rods
            .filter(rod => rod && Array.isArray(rod.segments))
            .map(rod => ({
                rodId: rod.rodId || makeRodId(),
                tubeType: rod.tubeType || tubeType,
                done: !!rod.done,
                historyLogged: !!rod.historyLogged,
                inventoryDecrementDone: !!rod.inventoryDecrementDone,
                inventoryDecision: rod.inventoryDecision || null,
                segments: rod.segments.map(segment => ({ ...segment }))
            }));
    });
    return cleaned;
}
function makeRodId() {
    return `locked-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
function stablePieceKey(filePath) {
    return String(filePath || '').replace(/\s*\(\s*fatti\s+\d+\s*\)(?=\.zzx$)/i, '').replace(/\\/g, '/').toLowerCase();
}
function buildPieceInstances(pieces) {
    const instances = [];
    (pieces || []).forEach(piece => {
        const logicalKey = piece.logicalKey || stablePieceKey(piece.filePath);
        const quantityToCut = Math.max(0, parseInt(daFareOverrides[piece.filePath] !== undefined ? daFareOverrides[piece.filePath] : piece.quantityNeeded, 10) || 0);
        for (let i = 0; i < quantityToCut; i++) {
            const pieceNumber = (parseInt(piece.completedQuantity, 10) || 0) + i + 1;
            instances.push({
                instanceKey: `${logicalKey}::${pieceNumber}`,
                logicalKey,
                sourceId: piece.id,
                filePath: piece.filePath,
                currentFilePath: piece.filePath,
                fileName: piece.fileName,
                mainFolder: piece.mainFolder,
                tubeType: piece.tubeType,
                length: piece.length,
                totalQuantity: piece.totalQuantity,
                completedQuantity: piece.completedQuantity,
                pieceNumber,
                done: false
            });
        }
    });
    return instances;
}
async function buildSummaryPlanFromCurrentView() {
    const planByTube = new Map();
    const nestRequests = [];

    for (const group of (allTubeData || [])) {
        const instances = buildPieceInstances(group.pieces || []);
        const lockedForTube = reconcileLockedRodsForTube(group.tubeType, instances);
        const lockedKeys = new Set();
        const rods = [];

        lockedForTube.forEach(rod => {
            (rod.segments || []).forEach(segment => {
                if (!segment.done && segment.instanceKey) lockedKeys.add(segment.instanceKey);
            });
            if (!rod.done && (rod.segments || []).length > 0) {
                const used = rod.segments.reduce((sum, segment) => sum + (parseInt(segment.length, 10) || 0), 0);
                rods.push({ remaining: Math.max(0, 6000 - used), pieces: [] });
            }
        });

        const freeSegments = instances.filter(segment => !lockedKeys.has(segment.instanceKey));
        planByTube.set(group.tubeType, rods);
        nestRequests.push({ tubeType: group.tubeType, pieces: freeSegments });
    }

    const nestedGroups = await nestGroupsBackend(nestRequests);
    nestedGroups.forEach(group => {
        const rods = planByTube.get(group.tubeType) || [];
        (group.rods || []).forEach(rod => {
            if ((rod.segments || []).length > 0) {
                rods.push({ remaining: rod.remaining, pieces: [] });
            }
        });
    });

    const plan = [];
    for (const [tubeType, rods] of planByTube.entries()) {
        if (rods.length > 0) plan.push({ tubeType, rods });
    }
    return plan;
}
function reconcileLockedRodsForTube(tubeType, currentInstances) {
    const currentByInstance = new Map(currentInstances.map(segment => [segment.instanceKey, segment]));
    const rods = (lockedRods[tubeType] || []).map(rod => {
        const updatedSegments = (rod.segments || []).map(segment => {
            const current = currentByInstance.get(segment.instanceKey);
            if (!current) return { ...segment };
            return {
                ...segment,
                ...current,
                done: !!segment.done,
                currentFilePath: segment.done ? (segment.currentFilePath || segment.filePath || current.filePath) : current.filePath
            };
        });
        const done = updatedSegments.length > 0 && updatedSegments.every(segment => segment.done);
        return { ...rod, tubeType, done, segments: updatedSegments };
    }).filter(rod => (rod.segments || []).length > 0);
    lockedRods[tubeType] = rods;
    return rods;
}
function isUnlockedRodsVisible(tubeType) {
    if (Object.prototype.hasOwnProperty.call(uiPreferences.showUnlockedRodsByTube || {}, tubeType)) {
        return uiPreferences.showUnlockedRodsByTube[tubeType] !== false;
    }
    return uiPreferences.showUnlockedRodsDefault !== false;
}
function toggleUnlockedRodsForTube(tubeType) {
    uiPreferences.showUnlockedRodsByTube = uiPreferences.showUnlockedRodsByTube || {};
    uiPreferences.showUnlockedRodsByTube[tubeType] = !isUnlockedRodsVisible(tubeType);
    saveUiState();
    renderUI();
}
function toggleAllUnlockedRods() {
    uiPreferences.showUnlockedRodsDefault = uiPreferences.showUnlockedRodsDefault === false;
    uiPreferences.showUnlockedRodsByTube = {};
    saveUiState();
    renderUI();
}
async function lockAllUnlockedRods() {
    let rodsAdded = 0;
    const nestRequests = [];

    for (const group of (allTubeData || [])) {
        const instances = buildPieceInstances(group.pieces || []);
        const lockedForTube = reconcileLockedRodsForTube(group.tubeType, instances);
        const lockedInstanceKeys = new Set();

        lockedForTube.forEach(rod => {
            (rod.segments || []).forEach(segment => {
                if (!segment.done && segment.instanceKey) lockedInstanceKeys.add(segment.instanceKey);
            });
        });

        const freeSegments = instances.filter(segment => !lockedInstanceKeys.has(segment.instanceKey));
        if (freeSegments.length > 0) {
            nestRequests.push({ tubeType: group.tubeType, pieces: freeSegments });
        }
    }

    const nestedGroups = await nestGroupsBackend(nestRequests);

    nestedGroups.forEach(group => {
        lockedRods[group.tubeType] = lockedRods[group.tubeType] || [];
        (group.rods || []).forEach(nestedRod => {
            if (!(nestedRod.segments || []).length) return;
            lockedRods[group.tubeType].push({
                rodId: makeRodId(),
                tubeType: group.tubeType,
                done: false,
                historyLogged: false,
                inventoryDecrementDone: false,
                inventoryDecision: null,
                segments: nestedRod.segments.map(segment => ({ ...segment, done: false }))
            });
            rodsAdded += 1;
        });
    });

    if (rodsAdded === 0) return;
    await saveUiState();
    await renderUI();
}
function unlockAllLockedRods() {
    const hasLockedRods = Object.values(lockedRods || {}).some(rods => Array.isArray(rods) && rods.length > 0);
    if (!hasLockedRods) return;
    lockedRods = {};
    saveUiState();
    renderUI();
}
function getLockedRod(tubeType, rodId) {
    return (lockedRods[tubeType] || []).find(rod => rod.rodId === rodId);
}
function clearLockedRodPlacements(rod) {
    if (!rod) return;
    (rod.segments || []).forEach(segment => {
        delete segment.nestPlacement;
    });
}

function lockUnlockedRod(tubeType, segmentKeys) {
    const segments = (segmentKeys || []).map(key => availableSegmentMap.get(key)).filter(Boolean);
    if (segments.length === 0) return;
    const rod = {
        rodId: makeRodId(),
        tubeType,
        done: false,
        historyLogged: false,
        inventoryDecrementDone: false,
        inventoryDecision: null,
        segments: segments.map(segment => ({ ...segment, done: false }))
    };
    lockedRods[tubeType] = lockedRods[tubeType] || [];
    lockedRods[tubeType].push(rod);
    saveUiState();
    renderUI();
}
function unlockLockedRod(tubeType, rodId) {
    lockedRods[tubeType] = (lockedRods[tubeType] || []).filter(rod => rod.rodId !== rodId);
    saveUiState();
    renderUI();
}
function removeSegmentFromLockedRod(tubeType, rodId, instanceKey) {
    const rod = getLockedRod(tubeType, rodId);
    if (!rod) return null;
    const index = (rod.segments || []).findIndex(segment => segment.instanceKey === instanceKey);
    if (index < 0) return null;
    const [segment] = rod.segments.splice(index, 1);
    clearLockedRodPlacements(rod);
    delete segment.nestPlacement;
    rod.done = rod.segments.length > 0 && rod.segments.every(item => item.done);
    lockedRods[tubeType] = (lockedRods[tubeType] || []).filter(item => item.segments && item.segments.length > 0);
    return segment;
}
function insertSegmentIntoLockedRod(tubeType, rodId, segment, insertIndex = null) {
    const rod = getLockedRod(tubeType, rodId);
    if (!rod || !segment || segment.done) return false;
    if ((rod.segments || []).some(item => item.instanceKey === segment.instanceKey)) return true;
    const used = rod.segments.reduce((sum, item) => sum + (parseInt(item.length, 10) || 0), 0);
    if (used + (parseInt(segment.length, 10) || 0) > 6000) {
        alert('Questa verga supererebbe 6000mm.');
        return false;
    }
    clearLockedRodPlacements(rod);
    const cleanSegment = { ...segment, done: false };
    delete cleanSegment.nestPlacement;
    if (insertIndex === null || insertIndex < 0 || insertIndex > rod.segments.length) {
        rod.segments.push(cleanSegment);
    } else {
        rod.segments.splice(insertIndex, 0, cleanSegment);
    }
    rod.done = false;
    rod.historyLogged = false;
    rod.inventoryDecrementDone = false;
    return true;
}
function clearSegmentDragState() {
    if (mainContainer) {
        mainContainer.querySelectorAll('.rod-segment.dragging').forEach(segment => segment.classList.remove('dragging'));
    }
    draggedSegment = null;
}
function handleSegmentDragStart(event) {
    const segment = event.target.closest('.rod-segment');
    if (!segment || segment.classList.contains('done')) {
        event.preventDefault();
        return;
    }
    draggedSegment = {
        instanceKey: segment.dataset.segmentKey,
        sourceKind: segment.dataset.rodKind,
        sourceRodId: segment.dataset.rodId,
        tubeType: segment.dataset.tubeType
    };
    if (!draggedSegment.instanceKey || !draggedSegment.sourceKind || !draggedSegment.tubeType) {
        draggedSegment = null;
        event.preventDefault();
        return;
    }
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', draggedSegment.instanceKey || '');
    segment.classList.add('dragging');
}
function handleSegmentDragEnd() {
    clearSegmentDragState();
}
function handleRodDragOver(event) {
    if (!draggedSegment) return;
    const rodBar = event.target.closest('[data-drop-zone="rod"]');
    if (!rodBar) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
}
function handleRodDrop(event) {
    if (!draggedSegment) return;
    const rodBar = event.target.closest('[data-drop-zone="rod"]');
    if (!rodBar) return;
    event.preventDefault();

    const targetKind = rodBar.dataset.rodKind;
    const targetRodId = rodBar.dataset.rodId;
    const targetTubeType = rodBar.dataset.tubeType;
    if (targetTubeType !== draggedSegment.tubeType) {
        alert('Puoi spostare pezzi solo dentro lo stesso tipo tubo.');
        clearSegmentDragState();
        return;
    }

    let moved = false;
    if (targetKind === 'unlocked') {
        if (draggedSegment.sourceKind === 'locked') {
            moved = !!removeSegmentFromLockedRod(draggedSegment.tubeType, draggedSegment.sourceRodId, draggedSegment.instanceKey);
        }
        clearSegmentDragState();
        if (moved) {
            saveUiState();
            renderUI();
        }
        return;
    }

    if (targetKind !== 'locked') {
        clearSegmentDragState();
        return;
    }
    const targetSegment = event.target.closest('.rod-segment');
    const targetRod = getLockedRod(targetTubeType, targetRodId);
    let insertIndex = null;
    if (targetRod && targetSegment) {
        const baseIndex = Math.max(0, targetRod.segments.findIndex(segment => segment.instanceKey === targetSegment.dataset.segmentKey));
        const rect = targetSegment.getBoundingClientRect();
        insertIndex = event.clientX > rect.left + rect.width / 2 ? baseIndex + 1 : baseIndex;
    }

    let segmentToMove = null;
    if (draggedSegment.sourceKind === 'locked') {
        const sourceRod = getLockedRod(draggedSegment.tubeType, draggedSegment.sourceRodId);
        segmentToMove = sourceRod?.segments?.find(segment => segment.instanceKey === draggedSegment.instanceKey);
        if (!segmentToMove) {
            clearSegmentDragState();
            return;
        }
        if (draggedSegment.sourceRodId === targetRodId) {
            if ((sourceRod.segments || []).length <= 1) {
                clearSegmentDragState();
                return;
            }
            const oldIndex = sourceRod.segments.findIndex(segment => segment.instanceKey === draggedSegment.instanceKey);
            const adjustedIndex = oldIndex >= 0 && insertIndex !== null && oldIndex < insertIndex ? insertIndex - 1 : insertIndex;
            removeSegmentFromLockedRod(draggedSegment.tubeType, draggedSegment.sourceRodId, draggedSegment.instanceKey);
            moved = insertSegmentIntoLockedRod(targetTubeType, targetRodId, segmentToMove, adjustedIndex);
        } else if (insertSegmentIntoLockedRod(targetTubeType, targetRodId, segmentToMove, insertIndex)) {
            removeSegmentFromLockedRod(draggedSegment.tubeType, draggedSegment.sourceRodId, draggedSegment.instanceKey);
            moved = true;
        }
    } else {
        segmentToMove = availableSegmentMap.get(draggedSegment.instanceKey);
        moved = insertSegmentIntoLockedRod(targetTubeType, targetRodId, segmentToMove, insertIndex);
    }
    clearSegmentDragState();
    if (moved) {
        saveUiState();
        renderUI();
    }
}
async function markLockedSegmentFatto(tubeType, rodId, instanceKey) {
    const rod = getLockedRod(tubeType, rodId);
    const segment = rod?.segments?.find(item => item.instanceKey === instanceKey);
    if (!rod || !segment || segment.done) return;
    const response = await window.pywebview.api.mark_locked_segments_fatto([segment]);
    if (!response || response.status !== 'success') {
        alert(response?.message || 'Errore durante aggiornamento pezzo bloccato.');
        return;
    }
    applyLockedCompletionResponse([segment], response);
    await finalizeLockedRodIfDone(rod);
    await saveUiState();
    if (response.popup_message) alert(response.popup_message);
    await loadTubeData();
}
async function markLockedRodFatto(tubeType, rodId) {
    const rod = getLockedRod(tubeType, rodId);
    if (!rod) return;
    const pendingSegments = (rod.segments || []).filter(segment => !segment.done);
    if (pendingSegments.length > 0) {
        const response = await window.pywebview.api.mark_locked_segments_fatto(pendingSegments);
        if (!response || response.status !== 'success') {
            alert(response?.message || 'Errore durante aggiornamento verga bloccata.');
            return;
        }
        applyLockedCompletionResponse(pendingSegments, response);
        if (response.popup_message) alert(response.popup_message);
    }
    await finalizeLockedRodIfDone(rod);
    await saveUiState();
    await loadTubeData();
}
function applyLockedCompletionResponse(segments, response) {
    const updates = response?.segments || {};
    const logicalPathUpdates = new Map();
    segments.forEach(segment => {
        const update = updates[segment.instanceKey] || {};
        segment.done = true;
        if (update.finalPath) segment.currentFilePath = update.finalPath;
        if (update.fileName) segment.fileName = update.fileName;
        if (segment.logicalKey && update.finalPath) {
            logicalPathUpdates.set(segment.logicalKey, update);
        }
    });
    if (logicalPathUpdates.size === 0) return;
    Object.values(lockedRods || {}).forEach(rods => {
        (rods || []).forEach(rod => {
            (rod.segments || []).forEach(segment => {
                const update = logicalPathUpdates.get(segment.logicalKey);
                if (!update) return;
                if (update.finalPath) segment.currentFilePath = update.finalPath;
                if (update.fileName) segment.fileName = update.fileName;
            });
        });
    });
}
async function finalizeLockedRodIfDone(rod) {
    rod.done = (rod.segments || []).length > 0 && rod.segments.every(segment => segment.done);
    if (!rod.done) return;
    if (!rod.historyLogged) {
        const response = await window.pywebview.api.log_locked_rod_completion(rod);
        if (response && response.status === 'success') {
            rod.historyLogged = true;
        }
    }
    if (!rod.inventoryDecrementDone) {
        await promptInventoryUseForRod(rod);
    }
}
async function promptInventoryUseForRod(rod) {
    pendingInventoryRod = { tubeType: rod.tubeType, rodId: rod.rodId };
    inventoryUseNote.textContent = `Verga completata: ${rod.tubeType}`;
    inventoryUsePosition.innerHTML = '<option value="">Non rimuovere</option>';
    try {
        const response = await window.pywebview.api.get_tube_database_positions_for_tube_type(rod.tubeType);
        if (response && response.status === 'success') {
            (response.positions || []).forEach((position, index) => {
                const option = document.createElement('option');
                option.value = String(index);
                option.textContent = `${position.label} - ${position.quantita}V`;
                option.dataset.payload = JSON.stringify({
                    tube_type: rod.tubeType,
                    source_location: position.source_location,
                    source_ripiano: position.source_ripiano
                });
                inventoryUsePosition.appendChild(option);
            });
        }
    } catch (e) {
        console.error(e);
    }
    inventoryUseModal.style.display = 'flex';
}
function closeInventoryUseModal() {
    if (inventoryUseModal) inventoryUseModal.style.display = 'none';
    pendingInventoryRod = null;
}
async function confirmInventoryUse() {
    if (!pendingInventoryRod) return;
    const rod = getLockedRod(pendingInventoryRod.tubeType, pendingInventoryRod.rodId);
    if (!rod) {
        closeInventoryUseModal();
        return;
    }
    const selected = inventoryUsePosition.options[inventoryUsePosition.selectedIndex];
    if (selected && selected.value !== '') {
        const payload = JSON.parse(selected.dataset.payload || '{}');
        const response = await window.pywebview.api.decrement_tube_database_position(payload);
        if (!response || response.status !== 'success') {
            alert(response?.message || 'Errore durante decremento Database Tubi.');
            return;
        }
        rod.inventoryDecision = payload;
    } else {
        rod.inventoryDecision = 'skip';
    }
    rod.inventoryDecrementDone = true;
    lockedRods[rod.tubeType] = (lockedRods[rod.tubeType] || []).filter(item => item.rodId !== rod.rodId);
    await saveUiState();
    closeInventoryUseModal();
    await loadTubeDatabase();
    renderUI();
}

// --- Utilities ---
async function nestGroupsBackend(groupRequests, rodLength = 6000) {
    const sourcePieces = new Map();
    (allTubeData || []).forEach(group => {
        (group.pieces || []).forEach(piece => sourcePieces.set(piece.id, piece));
    });

    const requestByTube = new Map();
    const payload = (groupRequests || []).map(group => {
        const pieces = group.pieces || [];
        requestByTube.set(group.tubeType, pieces);
        return {
            tubeType: group.tubeType,
            pieces: pieces.map(segment => {
                const sourcePiece = sourcePieces.get(segment.sourceId);
                return {
                    instanceKey: segment.instanceKey,
                    sourceId: segment.sourceId,
                    length: segment.length,
                    tubePart: sourcePiece?.tubePart || null
                };
            })
        };
    });

    if (payload.length === 0) return [];

    const response = await window.pywebview.api.nest_piece_groups(payload, rodLength);
    if (!response || response.status !== 'success') {
        throw new Error(response?.message || 'Nesting backend non disponibile.');
    }

    return (response.groups || []).map(group => {
        const originals = new Map(
            (requestByTube.get(group.tubeType) || []).map(segment => [segment.instanceKey, segment])
        );

        const rods = (group.rods || []).map(rod => {
            const placements = rod.placements || [];
            const segments = placements.map(placement => {
                const original = originals.get(placement.instance_key);
                if (!original) {
                    throw new Error(`Placement backend sconosciuto: ${placement.instance_key}`);
                }
                return {
                    ...original,
                    nestPlacement: placement
                };
            });

            return {
                rodLength: rod.rodLength,
                used: rod.used,
                remaining: rod.remaining,
                placements,
                segments
            };
        });

        return { tubeType: group.tubeType, rods };
    });
}

function getColorForPiece(piece) {
    const seed = String(piece?.logicalKey || piece?.filePath || piece?.fileName || piece?.length || '');
    let hash = 0;
    for (let i = 0; i < seed.length; i++) {
        hash = ((hash << 5) - hash) + seed.charCodeAt(i);
        hash |= 0;
    }
    const hue = Math.abs(hash) % 360;
    return `hsl(${hue}, 70%, 82%)`;
}
function findPieceById(pieceId) {
    if (currentPieceMap.has(pieceId)) return currentPieceMap.get(pieceId);
    for (const group of allTubeData) {
        const foundPiece = group.pieces.find(p => p.id === pieceId);
        if (foundPiece) return foundPiece;
    }
    return null;
}
function slugify(value) {
    return String(value || '').replace(/[^a-zA-Z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'tube';
}
function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
function escapeAttr(value) {
    return escapeHtml(value).replace(/`/g, '&#096;');
}
function formatNumber(value) {
    if (value === null || typeof value === 'undefined') return '';
    return String(value);
}
function parseNonNegativeInteger(value, fallback = 0) {
    const parsed = parseInt(value, 10);
    return Number.isInteger(parsed) && parsed >= 0 ? parsed : fallback;
}
// --- DOCX Printout ---


async function generateTTDocxFromSelection() {
    try {
        const resp = await pywebview.api.generate_tt_docx_from_explorer_selection();
        if (!resp) {
            alert('Errore: nessuna risposta dal backend.');
            return;
        }
        if (resp.status === 'success') {
            const lines = [];
            if (resp.generated && resp.generated.length) {
                lines.push('DOCX generati in: ' + (resp.save_dir || ''));
                lines.push('');
                lines.push(...resp.generated);
            }
            if (resp.failed && resp.failed.length) {
                lines.push('');
                lines.push('Errori:');
                resp.failed.forEach(f => lines.push('- ' + (f.folder || '') + ': ' + (f.error || '')));
            }
            alert(lines.join('\n') || 'Operazione completata.');
        } else {
            alert(resp.message || 'Errore durante la generazione DOCX TT.');
        }
    } catch (e) {
        console.error(e);
        alert('Errore durante la generazione DOCX TT: ' + e);
    }
}
async function generateSummaryDocx() {
    const summaryText = summaryPreview.textContent;
    if (!summaryText || summaryText.includes('in corso') || summaryText.includes('Errore')) {
        alert("Nessun riepilogo valido da generare.");
        return;
    }
    try {
        const response = await window.pywebview.api.save_and_open_summary_docx(summaryText);
        if (response.status === 'success') {
            alert(`File di stampa salvato con successo:\n${response.path}`);
            closeSummaryModal();
        } else {
            alert(`Errore durante la generazione della stampa:\n${response.message || 'Errore sconosciuto'}`);
        }
    } catch (e) {
        console.error('Failed to generate DOCX:', e);
        alert('Un errore critico ha impedito la generazione del file DOCX.');
    }
}
