# backend_logic.py

import os
import shutil
import re
import json
import uuid
import subprocess
import sys
import traceback
import openpyxl
import importlib.util
import tube_database
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils import get_column_letter
from collections import defaultdict, Counter
from datetime import datetime

# DOCX generation (python-docx)
try:
    from docx import Document
    from docx.shared import Pt, Mm
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
except Exception:  # allow app to run even if python-docx isn't installed yet
    Document = None
    Pt = Mm = OxmlElement = qn = None

# --- Robust Path Management ---
def get_app_data_path():
    """Returns a reliable, writable directory in the user's AppData folder."""
    app_data_dir = os.path.join(os.environ.get('APPDATA', os.getcwd()), 'TubiNestingManager')
    os.makedirs(app_data_dir, exist_ok=True)
    return app_data_dir

# --- Configuration & State Management ---
IS_FROZEN = getattr(sys, "frozen", False)
PROJECT_ROOT = (
    os.path.dirname(os.path.abspath(sys.executable))
    if IS_FROZEN
    else os.path.dirname(os.path.abspath(__file__))
)
RESOURCE_ROOT = getattr(sys, "_MEIPASS", PROJECT_ROOT)
APP_DATA_PATH = get_app_data_path()
CONFIG_FILE = os.path.join(PROJECT_ROOT, 'config.json')
UI_STATE_FILE = os.path.join(PROJECT_ROOT, 'state.json')
HISTORY_FILE = os.path.join(PROJECT_ROOT, 'history.json')

def load_config(silent=False):
    if not silent:
        print(f"DEBUG: Attempting to load config from project folder: {CONFIG_FILE}")
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            configure_tube_storage(config)
            return config
        except (json.JSONDecodeError, IOError) as e:
            print(f"ERROR_CODE_01: Failed to read or parse config.json. Error: {e}")
            return {}
    if not silent:
        print("DEBUG: config.json not found in project folder. Returning empty config.")
    return {}


def configure_tube_storage(config=None):
    config = config if isinstance(config, dict) else {}
    database_path = str(config.get("database_tubi_dir") or "").strip()
    code_catalog_file = str(config.get("codici_tubi_path") or "").strip()
    result = tube_database.configure_storage(database_path, code_catalog_file)
    return {
        "database_dir": result["database_dir"] or os.path.join(PROJECT_ROOT, tube_database.DATABASE_DIR_NAME),
        "code_catalog_path": result["code_catalog_path"] or os.path.join(PROJECT_ROOT, tube_database.CODE_CATALOG_NAME),
    }

def save_config(data):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        configure_tube_storage(data)
        return {"status": "success"}
    except Exception as e:
        print(f"ERROR_CODE_02: Failed to save config.json. Error: {e}")
        return {"status": "error", "message": str(e)}

def load_ui_state():
    if os.path.exists(UI_STATE_FILE):
        try:
            with open(UI_STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}

def save_ui_state(data):
    try:
        with open(UI_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []

def save_history(history_data):
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history_data, f, indent=4)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- Helper & Info Extraction Functions ---
def get_product_folder_name(full_path, base_path):
    try:
        relative_path = os.path.relpath(os.path.dirname(full_path), base_path)
        return relative_path.split(os.path.sep)[0]
    except ValueError:
        return os.path.basename(os.path.dirname(full_path))

def extract_tube_info(filename):
    """
    Returns (size_token, material, finish) from filename.
    finish: BA if contains "BA" OR "LUCIDO", else 2B.
    """
    try:
        square_pattern = re.search(r"(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)", filename)
        circular_pattern = re.search(r"Ø(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)", filename)

        material_pattern = re.search(r"(304|316)", filename, re.IGNORECASE)
        finish_pattern = re.search(r"\b(BA|LUCIDO)\b", filename, re.IGNORECASE)

        material = material_pattern.group(0).upper() if material_pattern else "UNKNOWN"
        finish = "BA" if finish_pattern else "2B"

        if square_pattern:
            h, w, t = square_pattern.groups()
            return f"{h}x{w}x{t}", material, finish

        if circular_pattern:
            d, t = circular_pattern.groups()
            return f"Ø{d}x{t}", material, finish

        return "UNKNOWN_SIZE", material, finish
    except Exception:
        return "ERROR", "ERROR", "ERROR"

def extract_length_and_quantity_enhanced(file_path):
    filename = os.path.basename(file_path).lower()
    length_match = re.search(r'l(\d+)', filename)
    total_qty_match = re.search(r'(\d+)pz', filename)
    completed_qty_match = re.search(r'\(fatti (\d+)\)', filename)
    if not length_match or not total_qty_match:
        return None, None, 0
    length = int(length_match.group(1))
    total_quantity = int(total_qty_match.group(1))
    completed_quantity = int(completed_qty_match.group(1)) if completed_qty_match else 0
    return length, total_quantity, completed_quantity

def _stable_cut_stem(filename):
    name_part, _ = os.path.splitext(os.path.basename(filename))
    name_part = re.sub(r'\s*\(\s*fatti\s+\d+\s*\)', '', name_part, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', name_part).strip().lower()

def make_logical_piece_key(file_path, da_fare_path=None):
    try:
        base_path = da_fare_path if da_fare_path and os.path.isdir(da_fare_path) else PROJECT_ROOT
        relative_dir = os.path.relpath(os.path.dirname(file_path), base_path)
    except Exception:
        relative_dir = os.path.dirname(file_path)
    relative_dir = str(relative_dir or ".").replace("\\", "/").lower()
    return f"{relative_dir}::{_stable_cut_stem(file_path)}"

# --- Inventory (Excel) Lookup ---
_INVENTORY_CACHE = {"path": None, "mtime": None, "map": None, "debug": None}

_HDR_ALIASES = {
    "misura": {"misura", "dimensione", "dim"},
    "spessore": {"spessore", "spess", "sp.", "spess(mm)", "spessore(mm)"},
    "materiale": {"materiale", "mat", "mat.", "acciaio"},
    "finitura": {"finitura", "fin", "fin.", "superficie"},
    "quantita": {"quantita", "quantità", "qta", "q.tà", "qtà", "pezzi", "verghe", "n", "num"},
}

def _norm_cell_to_str(val):
    if val is None:
        return ""
    if isinstance(val, (int, float)):
        if float(val).is_integer():
            return str(int(val))
        return f"{val:.6f}".rstrip('0').rstrip('.')
    return str(val).strip()

def _norm_hdr(val) -> str:
    return _norm_cell_to_str(val).strip().lower().replace(" ", "")

def _is_hdr(val, keyname: str) -> bool:
    v = _norm_hdr(val)
    if not v:
        return False
    return any(v == a or v.startswith(a) for a in _HDR_ALIASES[keyname])

def _sheet_to_scaff_code(sheet_name: str) -> str:
    name = str(sheet_name).strip()
    m = re.match(r"(?i)scaffalatura\s+(.+)$", name)
    if m:
        return "S" + m.group(1).strip().replace(" ", "")
    return name.replace(" ", "")

def _ripiano_name_to_code(ripiano_name) -> str:
    s = _norm_cell_to_str(ripiano_name)
    if re.search(r"(?i)\bper\s*terra\b", s):
        return "Per Terra"
    m = re.search(r"(?i)ripiano\s*(\d+)", s)
    if m:
        return f"R{m.group(1)}"
    return s.replace(" ", "")

def _parse_size_tokens(tube_size: str):
    s = str(tube_size or "").strip()
    if s.startswith("Ø") or s.startswith("ø"):
        s2 = s[1:]
        parts = s2.split("x")
        if len(parts) >= 2:
            return (_norm_cell_to_str(parts[0]), _norm_cell_to_str(parts[1]))
        return (_norm_cell_to_str(s2), "")
    parts = s.split("x")
    if len(parts) >= 3:
        misura = f"{_norm_cell_to_str(parts[0])}x{_norm_cell_to_str(parts[1])}"
        sp = _norm_cell_to_str(parts[2])
        return (misura, sp)
    return (_norm_cell_to_str(s), "")

def _find_default_inventory_xlsx(da_fare_path):
    candidates = [
        os.path.join(PROJECT_ROOT, "Conteggio tubi.xlsx"),
        os.path.join(PROJECT_ROOT, "Conteggio Tubi.xlsx"),
        os.path.join(APP_DATA_PATH, "Conteggio tubi.xlsx"),
        os.path.join(APP_DATA_PATH, "Conteggio Tubi.xlsx"),
    ]
    if da_fare_path:
        candidates.insert(1, os.path.join(da_fare_path, "Conteggio tubi.xlsx"))
        candidates.insert(2, os.path.join(da_fare_path, "Conteggio Tubi.xlsx"))
    for p in candidates:
        if p and os.path.exists(p) and p.lower().endswith((".xlsx", ".xlsm")):
            return p
    return None

def _configured_inventory_xlsx(da_fare_path=None):
    cfg = load_config() or {}
    return cfg.get("inventory_xlsx_path") or _find_default_inventory_xlsx(da_fare_path)

def ensure_tube_database(da_fare_path=None):
    excel_path = _configured_inventory_xlsx(da_fare_path)
    return tube_database.ensure_database(PROJECT_ROOT, excel_path)

def import_tube_database_from_excel(excel_path=None, replace=True, da_fare_path=None):
    target_excel = excel_path or _configured_inventory_xlsx(da_fare_path)
    return tube_database.import_from_excel(PROJECT_ROOT, target_excel, replace=replace)

def get_tube_database_debug(da_fare_path=None):
    ensure_result = ensure_tube_database(da_fare_path)
    debug = tube_database.database_debug(PROJECT_ROOT)
    debug["ensure_result"] = ensure_result
    debug["configured_excel_path"] = _configured_inventory_xlsx(da_fare_path)
    return debug

def get_tube_database_rows(filters=None, da_fare_path=None):
    ensure_result = ensure_tube_database(da_fare_path)
    if ensure_result.get("status") not in {"success"}:
        return {
            "status": ensure_result.get("status", "error"),
            "message": ensure_result.get("message", "Database Tubi non disponibile."),
            "rows": [],
        }

    rows = tube_database.summarize_inventory(PROJECT_ROOT, filters or {})
    return {
        "status": "success",
        "rows": rows,
        "locations": tube_database.load_locations(PROJECT_ROOT, filters or {}),
        "targets": tube_database.get_location_targets(PROJECT_ROOT),
        "debug": tube_database.database_debug(PROJECT_ROOT),
    }

def validate_tube_database_against_excel(da_fare_path=None):
    excel_path = _configured_inventory_xlsx(da_fare_path)
    return tube_database.validate_against_excel_summaries(PROJECT_ROOT, excel_path)

def add_tube_database_item(payload, da_fare_path=None):
    ensure_result = ensure_tube_database(da_fare_path)
    if ensure_result.get("status") != "success":
        return {"status": "error", "message": ensure_result.get("message", "Database Tubi non disponibile.")}
    return tube_database.add_tube(PROJECT_ROOT, payload or {})

def remove_tube_database_item(payload, da_fare_path=None):
    ensure_result = ensure_tube_database(da_fare_path)
    if ensure_result.get("status") != "success":
        return {"status": "error", "message": ensure_result.get("message", "Database Tubi non disponibile.")}
    return tube_database.remove_tube(PROJECT_ROOT, payload or {})

def set_tube_database_item_quantity(payload, da_fare_path=None):
    ensure_result = ensure_tube_database(da_fare_path)
    if ensure_result.get("status") != "success":
        return {"status": "error", "message": ensure_result.get("message", "Database Tubi non disponibile.")}
    return tube_database.set_tube_quantity(PROJECT_ROOT, payload or {})

def move_tube_database_item(payload, da_fare_path=None):
    ensure_result = ensure_tube_database(da_fare_path)
    if ensure_result.get("status") != "success":
        return {"status": "error", "message": ensure_result.get("message", "Database Tubi non disponibile.")}
    return tube_database.move_tube(PROJECT_ROOT, payload or {})

def import_tube_code_catalog():
    return tube_database.import_codes_from_excel(PROJECT_ROOT)

def set_tube_code_catalog_entry(payload):
    return tube_database.set_code_catalog_entry(PROJECT_ROOT, payload or {})

def reset_adhoc_baseline(da_fare_path=None):
    ensure_result = ensure_tube_database(da_fare_path)
    if ensure_result.get("status") != "success":
        return {"status": "error", "message": ensure_result.get("message", "Database Tubi non disponibile.")}
    return tube_database.reset_adhoc_baseline(PROJECT_ROOT)

def generate_adhoc_docx(da_fare_path=None):
    if Document is None:
        return {"status": "error", "message": "python-docx non e installato."}

    ensure_result = ensure_tube_database(da_fare_path)
    if ensure_result.get("status") != "success":
        return {"status": "error", "message": ensure_result.get("message", "Database Tubi non disponibile.")}

    data = tube_database.get_adhoc_report_data(PROJECT_ROOT)
    if data.get("status") != "success":
        return data

    try:
        output_dir = _get_docx_output_dir_fallback(da_fare_path, "adhoc_docx_output_dir")
        filename = f"Lista ADHOC {datetime.now().strftime('%Y-%m-%d %H%M')}.docx"
        file_path = os.path.join(output_dir, filename)

        doc = Document()
        section = doc.sections[0]
        section.left_margin = Mm(12)
        section.right_margin = Mm(12)
        section.top_margin = Mm(12)
        section.bottom_margin = Mm(12)

        title = doc.add_paragraph("Lista ADHOC")
        run = title.runs[0] if title.runs else title.add_run("Lista ADHOC")
        run.bold = True
        run.font.size = Pt(18)
        doc.add_paragraph(f"Baseline: {data.get('baseline_at') or '-'}")
        doc.add_paragraph(f"Generata: {data.get('generated_at') or datetime.now().isoformat(timespec='seconds')}")

        rows = data.get("rows", [])
        if not rows:
            doc.add_paragraph("Nessuna quantita modificata rispetto alla baseline ADHOC.")
        else:
            table = doc.add_table(rows=1, cols=4)
            table.style = "Table Grid"
            headers = ["Misura tubo", "Codice", "Quantita", "Differenza"]
            for index, header in enumerate(headers):
                cell = table.rows[0].cells[index]
                cell.text = header
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.bold = True

            for row in rows:
                cells = table.add_row().cells
                cells[0].text = str(row.get("misuraTubo") or "")
                cells[1].text = str(row.get("codice") or "CODICE MANCANTE")
                cells[2].text = str(row.get("quantitaLabel") or f"{row.get('quantita', 0)} Verghe")
                cells[3].text = str(row.get("deltaLabel") or "")

        doc.save(file_path)
        os.startfile(file_path)
        return {"status": "success", "path": file_path, "rows_count": len(data.get("rows", []))}
    except Exception:
        traceback.print_exc()
        return {"status": "error", "message": "Errore durante la generazione della Lista ADHOC."}

def generate_tube_codes_docx(da_fare_path=None):
    if Document is None:
        return {"status": "error", "message": "python-docx non e installato."}

    ensure_result = ensure_tube_database(da_fare_path)
    if ensure_result.get("status") != "success":
        return {"status": "error", "message": ensure_result.get("message", "Database Tubi non disponibile.")}

    try:
        rows = tube_database.summarize_inventory(PROJECT_ROOT, {})
        rows = sorted(rows, key=lambda row: bool(row.get("hasCode")))
        missing_codes_count = sum(1 for row in rows if not row.get("hasCode"))

        output_dir = _get_docx_output_dir_fallback(da_fare_path, "codes_docx_output_dir")
        filename = f"Lista Codici Tubi {datetime.now().strftime('%Y-%m-%d %H%M')}.docx"
        file_path = os.path.join(output_dir, filename)

        doc = Document()
        section = doc.sections[0]
        section.left_margin = Mm(12)
        section.right_margin = Mm(12)
        section.top_margin = Mm(12)
        section.bottom_margin = Mm(12)

        title = doc.add_paragraph("Lista Tubi e Codici")
        title_run = title.runs[0] if title.runs else title.add_run("Lista Tubi e Codici")
        title_run.bold = True
        title_run.font.size = Pt(18)
        doc.add_paragraph(f"Generata: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
        doc.add_paragraph(f"Tipi tubo presenti: {len(rows)} - Codici mancanti: {missing_codes_count}")

        if not rows:
            doc.add_paragraph("Nessun tubo presente nel database.")
        else:
            table = doc.add_table(rows=1, cols=3)
            table.style = "Table Grid"
            table.autofit = True

            header_row = table.rows[0]
            headers = ["Misura tubo", "Codice", "Quantita"]
            for index, header in enumerate(headers):
                cell = header_row.cells[index]
                cell.text = header
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.bold = True

            tr_pr = header_row._tr.get_or_add_trPr()
            table_header = OxmlElement("w:tblHeader")
            table_header.set(qn("w:val"), "true")
            tr_pr.append(table_header)

            for row in rows:
                cells = table.add_row().cells
                cells[0].text = tube_database.display_tube_measure(
                    row.get("misura"),
                    row.get("spessore"),
                    row.get("materiale"),
                    row.get("finitura"),
                    include_defaults=True,
                )
                cells[1].text = str(row.get("codice") or "CODICE MANCANTE")
                cells[2].text = str(int(row.get("quantitaTotale") or 0))

                if not row.get("hasCode"):
                    for paragraph in cells[1].paragraphs:
                        for run in paragraph.runs:
                            run.bold = True
                    shading = OxmlElement("w:shd")
                    shading.set(qn("w:fill"), "FFF2CC")
                    cells[1]._tc.get_or_add_tcPr().append(shading)

        doc.save(file_path)
        os.startfile(file_path)
        return {
            "status": "success",
            "path": file_path,
            "rows_count": len(rows),
            "missing_codes_count": missing_codes_count,
        }
    except Exception:
        traceback.print_exc()
        return {"status": "error", "message": "Errore durante la generazione della Lista Codici."}

def _excel_inventory_value(value):
    normalized = tube_database.decimal_number(value)
    return normalized if isinstance(normalized, (int, float)) else str(normalized or "")

def _format_inventory_worksheet(worksheet, table_name):
    worksheet.freeze_panes = "A2"
    worksheet.sheet_view.showGridLines = False
    max_row = worksheet.max_row
    max_column = worksheet.max_column
    if max_row >= 1:
        table_ref = f"A1:{get_column_letter(max_column)}{max_row}"
        table = Table(displayName=table_name, ref=table_ref)
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )
        worksheet.add_table(table)

    for column_index in range(1, max_column + 1):
        values = [str(worksheet.cell(row=row_index, column=column_index).value or "") for row_index in range(1, max_row + 1)]
        worksheet.column_dimensions[get_column_letter(column_index)].width = min(32, max(11, max((len(value) for value in values), default=0) + 2))

    for row in worksheet.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, (int, float)):
                cell.number_format = "General"

def generate_tube_inventory_xlsx(include_missing_codes=True, da_fare_path=None, output_dir_override=None, open_file=True):
    ensure_result = ensure_tube_database(da_fare_path)
    if ensure_result.get("status") != "success":
        return {"status": "error", "message": ensure_result.get("message", "Database Tubi non disponibile.")}

    try:
        rows = tube_database.summarize_inventory(PROJECT_ROOT, {})
        if not include_missing_codes:
            rows = [row for row in rows if row.get("hasCode")]

        round_rows = [row for row in rows if row.get("forma") == "tondo"]
        shaped_rows = [row for row in rows if row.get("forma") != "tondo"]

        workbook = openpyxl.Workbook()
        round_sheet = workbook.active
        round_sheet.title = "Tubi Tondi"
        shaped_sheet = workbook.create_sheet("Tubi Quadri Rettangolari")

        round_sheet.append(["Diametro \u00d8", "Spessore", "Materiale", "Finitura", "Codice", "Quantit\u00e0"])
        for row in round_rows:
            round_sheet.append([
                _excel_inventory_value(row.get("misura")),
                _excel_inventory_value(row.get("spessore")),
                row.get("materiale") or "",
                row.get("finitura") or "",
                row.get("codice") or "CODICE MANCANTE",
                int(row.get("quantitaTotale") or 0),
            ])

        shaped_sheet.append(["Altezza", "Larghezza", "Spessore", "Materiale", "Finitura", "Codice", "Quantit\u00e0"])
        for row in shaped_rows:
            dimensions = list(row.get("dimensioni") or [])
            shaped_sheet.append([
                _excel_inventory_value(dimensions[0] if len(dimensions) > 0 else ""),
                _excel_inventory_value(dimensions[1] if len(dimensions) > 1 else ""),
                _excel_inventory_value(row.get("spessore")),
                row.get("materiale") or "",
                row.get("finitura") or "",
                row.get("codice") or "CODICE MANCANTE",
                int(row.get("quantitaTotale") or 0),
            ])

        _format_inventory_worksheet(round_sheet, "InventarioTubiTondi")
        _format_inventory_worksheet(shaped_sheet, "InventarioTubiQuadriRettangolari")

        output_dir = _get_docx_output_dir_fallback(
            da_fare_path,
            "inventory_xlsx_output_dir",
            output_dir_override=output_dir_override,
        )
        filename = f"Inventario Tubi {datetime.now().strftime('%Y-%m-%d %H%M%S')}.xlsx"
        file_path = os.path.join(output_dir, filename)
        workbook.save(file_path)
        if open_file:
            os.startfile(file_path)
        return {
            "status": "success",
            "path": file_path,
            "round_count": len(round_rows),
            "shaped_count": len(shaped_rows),
            "included_missing_codes": bool(include_missing_codes),
        }
    except Exception:
        traceback.print_exc()
        return {"status": "error", "message": "Errore durante la generazione dell'inventario Excel."}

def _config_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "si", "on"}

def initialize_inventory_request_folder(request_folder):
    request_folder = str(request_folder or "").strip()
    if not request_folder or not os.path.isdir(request_folder):
        return {"status": "error", "message": "Cartella richieste inventario non disponibile."}

    ready_path = os.path.join(request_folder, "1")
    instruction_path = os.path.join(request_folder, "2 senza tubi strani - 3 completo")
    active_names = ("2", "3", ".inventario_in_elaborazione_2", ".inventario_in_elaborazione_3")
    try:
        for mode in ("2", "3"):
            legacy_path = os.path.join(request_folder, f"{mode}.txt")
            request_path = os.path.join(request_folder, mode)
            if os.path.exists(legacy_path):
                if not os.path.exists(request_path):
                    os.replace(legacy_path, request_path)
                else:
                    os.remove(legacy_path)

        legacy_ready_path = os.path.join(request_folder, "1.txt")
        if os.path.exists(legacy_ready_path):
            if not os.path.exists(ready_path) and not any(os.path.exists(os.path.join(request_folder, name)) for name in active_names):
                os.replace(legacy_ready_path, ready_path)
            else:
                os.remove(legacy_ready_path)

        if not os.path.exists(instruction_path):
            with open(instruction_path, "w", encoding="utf-8") as instruction:
                instruction.write("Rinomina 1 in 2 per l'inventario con soli tubi codificati.\n")
                instruction.write("Rinomina 1 in 3 per l'inventario completo.\n")
        if not os.path.exists(ready_path) and not any(os.path.exists(os.path.join(request_folder, name)) for name in active_names):
            with open(ready_path, "w", encoding="utf-8") as marker:
                marker.write("Rinomina in 2 oppure 3 per richiedere l'inventario Excel.\n")
    except OSError as exc:
        return {"status": "error", "message": str(exc)}
    return {"status": "success", "ready_path": ready_path, "instruction_path": instruction_path}

def process_inventory_export_request(config=None, da_fare_path=None):
    config = config or load_config(silent=True) or {}
    if not _config_bool(config.get("inventory_request_enabled"), False):
        return {"status": "disabled"}

    request_folder = str(config.get("inventory_request_folder") or "").strip()
    setup_result = initialize_inventory_request_folder(request_folder)
    if setup_result.get("status") != "success":
        return setup_result

    ready_path = setup_result["ready_path"]
    error_path = os.path.join(request_folder, "ERRORE richiesta inventario")
    request_mode = None
    processing_path = None

    for mode in ("2", "3"):
        candidate = os.path.join(request_folder, f".inventario_in_elaborazione_{mode}")
        if os.path.exists(candidate):
            request_mode = mode
            processing_path = candidate
            break

    if request_mode is None:
        for mode in ("2", "3"):
            request_path = os.path.join(request_folder, mode)
            if not os.path.exists(request_path):
                continue
            processing_path = os.path.join(request_folder, f".inventario_in_elaborazione_{mode}")
            try:
                os.replace(request_path, processing_path)
                request_mode = mode
            except FileNotFoundError:
                return {"status": "idle", "ready_path": ready_path}
            break

    if request_mode is None:
        return {"status": "idle", "ready_path": ready_path}

    result = generate_tube_inventory_xlsx(
        include_missing_codes=request_mode == "3",
        da_fare_path=da_fare_path,
        output_dir_override=request_folder,
        open_file=False,
    )

    try:
        if result.get("status") == "success":
            if os.path.exists(error_path):
                os.remove(error_path)
        else:
            with open(error_path, "w", encoding="utf-8") as error_file:
                error_file.write(f"{datetime.now().isoformat(timespec='seconds')}\n")
                error_file.write(str(result.get("message") or "Errore sconosciuto."))
        if os.path.exists(processing_path):
            os.replace(processing_path, ready_path)
    except OSError:
        traceback.print_exc()

    return {
        **result,
        "ready_path": ready_path,
        "request_folder": request_folder,
        "request_mode": request_mode,
    }

def add_tube_database_scaffolding(payload, da_fare_path=None):
    ensure_result = ensure_tube_database(da_fare_path)
    if ensure_result.get("status") != "success":
        return {"status": "error", "message": ensure_result.get("message", "Database Tubi non disponibile.")}
    name = (payload or {}).get("name")
    return tube_database.add_scaffolding(PROJECT_ROOT, name)

def remove_tube_database_scaffolding(payload, da_fare_path=None):
    ensure_result = ensure_tube_database(da_fare_path)
    if ensure_result.get("status") != "success":
        return {"status": "error", "message": ensure_result.get("message", "Database Tubi non disponibile.")}
    name = (payload or {}).get("name")
    return tube_database.remove_scaffolding(PROJECT_ROOT, name)

def add_tube_database_ripiano(payload, da_fare_path=None):
    ensure_result = ensure_tube_database(da_fare_path)
    if ensure_result.get("status") != "success":
        return {"status": "error", "message": ensure_result.get("message", "Database Tubi non disponibile.")}
    payload = payload or {}
    return tube_database.add_ripiano(PROJECT_ROOT, payload.get("location"), payload.get("ripiano"))

def remove_tube_database_ripiano(payload, da_fare_path=None):
    ensure_result = ensure_tube_database(da_fare_path)
    if ensure_result.get("status") != "success":
        return {"status": "error", "message": ensure_result.get("message", "Database Tubi non disponibile.")}
    payload = payload or {}
    return tube_database.remove_ripiano(PROJECT_ROOT, payload.get("location"), payload.get("ripiano"))

def load_inventory_map(excel_path: str):
    debug = {
        "excel_path": excel_path,
        "excel_found": bool(excel_path and os.path.exists(excel_path)),
        "config_inventory_xlsx_path": (load_config() or {}).get("inventory_xlsx_path"),
        "openpyxl_version": getattr(openpyxl, "__version__", "unknown"),
        "sheetnames": [],
        "tables_found": 0,
        "tables_discarded": 0,
        "rows_loaded": 0,
        "unique_keys": 0,
        "discard_reasons": Counter(),
        "exceptions": [],
    }

    if not excel_path or not os.path.exists(excel_path):
        _INVENTORY_CACHE.update({"path": excel_path, "mtime": None, "map": None, "debug": debug})
        return None

    try:
        mtime = os.path.getmtime(excel_path)
    except Exception as e:
        debug["exceptions"].append(f"mtime error: {e}")
        _INVENTORY_CACHE.update({"path": excel_path, "mtime": None, "map": None, "debug": debug})
        return None

    if (
        _INVENTORY_CACHE["path"] == excel_path
        and _INVENTORY_CACHE["mtime"] == mtime
        and _INVENTORY_CACHE["map"] is not None
    ):
        return _INVENTORY_CACHE["map"]

    try:
        wb = openpyxl.load_workbook(excel_path, data_only=True)
        debug["sheetnames"] = list(wb.sheetnames or [])
    except Exception:
        debug["exceptions"].append("load_workbook failed:\n" + traceback.format_exc())
        _INVENTORY_CACHE.update({"path": excel_path, "mtime": mtime, "map": None, "debug": debug})
        return None

    inventory = defaultdict(list)
    sheetnames = debug["sheetnames"]
    location_sheets = sheetnames[2:] if len(sheetnames) >= 3 else sheetnames

    def _is_non_location_sheet(name: str) -> bool:
        n = str(name or "").strip().lower()
        if not n:
            return True
        if n.startswith("accoda"):
            return True
        if "conteggio" in n or "riepilogo" in n or "totale" in n:
            return True
        return False

    location_sheets = [s for s in location_sheets if not _is_non_location_sheet(s)]

    def _try_header_at(ws, row, col):
        v0 = ws.cell(row=row, column=col).value
        v1 = ws.cell(row=row, column=col + 1).value
        v2 = ws.cell(row=row, column=col + 2).value
        v3 = ws.cell(row=row, column=col + 3).value
        v4 = ws.cell(row=row, column=col + 4).value
        return (
            _is_hdr(v0, "misura")
            and _is_hdr(v1, "spessore")
            and _is_hdr(v2, "materiale")
            and _is_hdr(v3, "finitura")
            and _is_hdr(v4, "quantita")
        )

    for sname in location_sheets:
        try:
            ws = wb[sname]
        except Exception:
            debug["discard_reasons"]["sheet_open_error"] += 1
            continue

        scaff_code = _sheet_to_scaff_code(sname)
        max_r = ws.max_row or 0
        max_c = ws.max_column or 0

        found_any_table = False

        for row in range(1, max_r + 1):
            for col in range(1, max_c + 1):
                if not _is_hdr(ws.cell(row=row, column=col).value, "misura"):
                    continue
                if not _try_header_at(ws, row, col):
                    debug["tables_discarded"] += 1
                    debug["discard_reasons"]["header_mismatch_near_misura"] += 1
                    continue

                found_any_table = True
                debug["tables_found"] += 1

                ripiano_cell = ws.cell(row=row - 1, column=col).value if row > 1 else None
                ripiano_code = _ripiano_name_to_code(ripiano_cell) if ripiano_cell else ""

                if str(scaff_code).strip().lower() == "terra" and str(ripiano_code).strip().lower() == "per terra":
                    location_code = "Per Terra"
                else:
                    location_code = f"{scaff_code} {ripiano_code}".strip()

                r = row + 1
                while r <= max_r:
                    misura_v = ws.cell(row=r, column=col).value
                    spess_v = ws.cell(row=r, column=col + 1).value
                    mat_v = ws.cell(row=r, column=col + 2).value
                    fin_v = ws.cell(row=r, column=col + 3).value
                    qty_v = ws.cell(row=r, column=col + 4).value

                    misura_s = _norm_cell_to_str(misura_v).replace(" ", "")
                    spess_s = _norm_cell_to_str(spess_v)

                    if not misura_s and not spess_s:
                        break

                    misura_s = misura_s.replace(",", ".")
                    spess_s = spess_s.replace(",", ".")

                    mat_s = _norm_cell_to_str(mat_v).upper()
                    fin_s = _norm_cell_to_str(fin_v).upper()
                    qty_s = _norm_cell_to_str(qty_v).replace(",", ".")

                    try:
                        qty_n = int(float(qty_s)) if qty_s != "" else 0
                    except Exception:
                        qty_n = 0

                    key = (misura_s, spess_s, mat_s, fin_s)
                    inventory[key].append((location_code, qty_n))
                    debug["rows_loaded"] += 1
                    r += 1

        if not found_any_table:
            debug["discard_reasons"]["no_table_found_in_sheet"] += 1

    debug["unique_keys"] = len(inventory)
    _INVENTORY_CACHE.update({"path": excel_path, "mtime": mtime, "map": dict(inventory), "debug": debug})
    return _INVENTORY_CACHE["map"]

def get_inventory_debug(da_fare_path=None):
    return {
        "database": get_tube_database_debug(da_fare_path),
        "legacy_excel_cache": _INVENTORY_CACHE.get("debug"),
    }

def _spessore_candidates(sp: str):
    sp = str(sp or "").strip().replace(",", ".")
    sps = set([sp])
    try:
        sp_f = float(sp)
        if abs(sp_f - 1.5) < 0.01:
            sps.add("1.6")
        elif abs(sp_f - 1.6) < 0.01:
            sps.add("1.5")
    except Exception:
        pass
    return sps

def _merge_locations(entries):
    merged = defaultdict(int)
    for loc, qty in entries:
        try:
            q = int(qty)
        except Exception:
            q = 0
        merged[loc] += q
    return [(loc, merged[loc]) for loc in sorted(merged.keys())]

def get_locations_for_tube_type(tube_type: str, da_fare_path=None):
    tokens = str(tube_type).split()
    if len(tokens) < 3:
        return []

    size = tokens[0]
    material = tokens[1].upper()
    finish = tokens[2].upper()

    misura_norm, spess_norm = _parse_size_tokens(size)
    misura_norm = misura_norm.replace(" ", "").replace(",", ".")
    spess_norm = spess_norm.replace(",", ".")

    ensure_result = ensure_tube_database(da_fare_path)
    if ensure_result.get("status") != "success":
        return []

    return tube_database.get_locations_for_tube(PROJECT_ROOT, misura_norm, spess_norm, material, finish)

def _tube_identity_from_tube_type(tube_type: str):
    tokens = str(tube_type or "").split()
    if len(tokens) < 3:
        return None

    size = tokens[0]
    material = tokens[1].upper()
    finish = tokens[2].upper()
    misura_norm, spess_norm = _parse_size_tokens(size)
    return {
        "misura": misura_norm.replace(" ", "").replace(",", "."),
        "spessore": spess_norm.replace(",", "."),
        "materiale": material,
        "finitura": finish,
    }

def get_tube_database_positions_for_tube_type(tube_type: str, da_fare_path=None):
    ensure_result = ensure_tube_database(da_fare_path)
    if ensure_result.get("status") != "success":
        return {"status": "error", "message": ensure_result.get("message", "Database Tubi non disponibile."), "positions": []}

    identity = _tube_identity_from_tube_type(tube_type)
    if not identity:
        return {"status": "error", "message": f"Tipo tubo non valido: {tube_type}", "positions": []}

    positions = tube_database.get_position_records_for_tube(
        PROJECT_ROOT,
        identity["misura"],
        identity["spessore"],
        identity["materiale"],
        identity["finitura"],
    )
    return {"status": "success", "positions": positions, "tube": identity}

def decrement_tube_database_position(payload, da_fare_path=None):
    payload = payload or {}
    identity = _tube_identity_from_tube_type(payload.get("tube_type"))
    if not identity:
        return {"status": "error", "message": "Tipo tubo non valido."}

    remove_payload = {
        **identity,
        "source_location": payload.get("source_location"),
        "source_ripiano": payload.get("source_ripiano"),
        "quantita": 1,
    }
    return remove_tube_database_item(remove_payload, da_fare_path)

# --- Nesting ---
def nest_pieces(pieces_to_nest, rod_length=6000):
    if not pieces_to_nest:
        return []
    pieces_to_nest.sort(key=lambda p: p['length'], reverse=True)
    rods = []
    for piece in pieces_to_nest:
        placed = False
        for rod in rods:
            if piece['length'] <= rod['remaining']:
                rod['remaining'] -= piece['length']
                rod['pieces'].append(piece['id'])
                placed = True
                break
        if not placed:
            rods.append({'remaining': rod_length - piece['length'], 'pieces': [piece['id']]})
    return rods

# --- Main State Aggregation ---
def get_current_state(da_fare_path):
    config = load_config()
    ignore_list = set(folder.lower() for folder in config.get('ignore_folders', []))
    if not da_fare_path or not os.path.isdir(da_fare_path):
        return []

    all_pieces = []
    for root, dirs, files in os.walk(da_fare_path):
        path_parts = set(p.lower() for p in root.replace(da_fare_path, '').split(os.path.sep))
        if not path_parts.isdisjoint(ignore_list):
            dirs[:] = []
            continue

        for file in files:
            if file.lower().endswith(".zzx"):
                file_path = os.path.join(root, file)
                product_folder = get_product_folder_name(file_path, da_fare_path)
                size, material, finish = extract_tube_info(file)
                length, total_qty, completed_qty = extract_length_and_quantity_enhanced(file_path)
                if length is not None and total_qty is not None:
                    all_pieces.append({
                        "id": str(uuid.uuid4()),
                        "logicalKey": make_logical_piece_key(file_path, da_fare_path),
                        "filePath": file_path,
                        "fileName": file,
                        "mainFolder": product_folder,
                        "tubeType": f"{size} {material} {finish}",
                        "length": length,
                        "totalQuantity": total_qty,
                        "completedQuantity": completed_qty,
                        "quantityNeeded": max(0, total_qty - completed_qty)
                    })

    grouped_tubes = defaultdict(list)
    for piece in all_pieces:
        grouped_tubes[piece['tubeType']].append(piece)

    final_state = []
    for tube_type, pieces in grouped_tubes.items():
        flat_pieces_to_nest = []
        for p in pieces:
            for _ in range(p['quantityNeeded']):
                flat_pieces_to_nest.append({'length': p['length'], 'id': p['id']})
        nested_rods = nest_pieces(flat_pieces_to_nest)
        final_state.append({"tubeType": tube_type, "pieces": pieces, "rods": nested_rods})
    return final_state

def _normalized_cut_stem(filename):
    return _stable_cut_stem(filename)

def _expected_zzx_name_for_igs(filename):
    name_part, _ = os.path.splitext(os.path.basename(filename))
    if name_part.lower().endswith(".zzx"):
        return name_part
    return f"{name_part}.zzx"

def _product_scope_for_path(file_path, base_path):
    try:
        relative_path = os.path.relpath(os.path.dirname(file_path), base_path)
    except ValueError:
        return os.path.dirname(file_path)

    first_part = relative_path.split(os.path.sep)[0]
    if first_part in ("", "."):
        return os.path.normpath(base_path)
    return os.path.normpath(os.path.join(base_path, first_part))

def _is_fatto_folder_name(name):
    normalized = re.sub(r"\s+", " ", str(name or "").strip().lower())
    return normalized == "fatto" or normalized.endswith(" - fatto")

def _path_contains_fatto_folder(path):
    parts = os.path.normpath(str(path or "")).split(os.path.sep)
    return any(_is_fatto_folder_name(part) for part in parts)

def find_unmatched_igs_files(da_fare_path):
    if not da_fare_path or not os.path.isdir(da_fare_path):
        return []
    if _path_contains_fatto_folder(da_fare_path):
        return []

    zzx_by_stem = defaultdict(list)
    igs_files = []

    for root, dirs, files in os.walk(da_fare_path):
        in_fatto_branch = _path_contains_fatto_folder(root)
        for file in files:
            lower_file = file.lower()
            file_path = os.path.join(root, file)
            if lower_file.endswith(".zzx"):
                zzx_by_stem[_normalized_cut_stem(file)].append(os.path.normpath(file_path))
            elif lower_file.endswith(".igs") and not in_fatto_branch:
                igs_files.append(os.path.normpath(file_path))

    unmatched = []
    for igs_path in igs_files:
        stem = _normalized_cut_stem(igs_path)
        scope = _product_scope_for_path(igs_path, da_fare_path)
        candidates = zzx_by_stem.get(stem, [])
        has_match = any(
            os.path.commonpath([scope, candidate]) == scope
            for candidate in candidates
        )
        if not has_match:
            unmatched.append({
                "filePath": igs_path,
                "fileName": os.path.basename(igs_path),
                "relativePath": os.path.relpath(igs_path, da_fare_path),
                "mainFolder": get_product_folder_name(igs_path, da_fare_path),
                "expectedZzxName": _expected_zzx_name_for_igs(igs_path),
            })

    unmatched.sort(key=lambda item: item["relativePath"].lower())
    return unmatched

# --- File System Logic ---
def _rename_with_final_count(file_path, new_count):
    directory = os.path.dirname(file_path)
    filename = os.path.basename(file_path)
    base_name = re.sub(r'\s*\(\s*fatti\s+\d+\s*\)', '', filename, flags=re.IGNORECASE).strip()
    name_part, ext_part = os.path.splitext(base_name)
    if new_count > 0:
        new_filename = f"{name_part.strip()} (fatti {new_count}){ext_part}"
    else:
        new_filename = f"{name_part.strip()}{ext_part}"
    new_path = os.path.join(directory, new_filename)
    if file_path != new_path and os.path.exists(file_path):
        os.rename(file_path, new_path)
    return new_path, new_filename

def _is_folder_completed(directory, da_fare_path=None):
    for root, dirs, files in os.walk(directory):
        if _path_contains_fatto_folder(root):
            dirs[:] = []
            continue
        dirs[:] = [child for child in dirs if not _is_fatto_folder_name(child)]
        if any(f.lower().endswith('.zzx') for f in files):
            return False
    if find_unmatched_igs_files(directory):
        return False
    return True

def check_and_archive_recursively(start_dir, da_fare_path):
    current_dir, popup_message = start_dir, None
    while True:
        if os.path.normpath(current_dir) == os.path.normpath(da_fare_path):
            break
        if not _is_folder_completed(current_dir, da_fare_path):
            break

        dir_name, parent_dir = os.path.basename(current_dir), os.path.dirname(current_dir)

        if dir_name.lower() == 'tt':
            new_name = "TT - Fatto"
            destination = os.path.join(parent_dir, new_name)
            if not os.path.exists(destination):
                os.rename(current_dir, destination)
                prod_folder = get_product_folder_name(destination, da_fare_path)
                popup_message = f"Completato! La cartella 'TT' per '{prod_folder}' è stata rinominata."
            break

        fatto_dir = os.path.join(parent_dir, 'fatto')
        os.makedirs(fatto_dir, exist_ok=True)
        destination = os.path.join(fatto_dir, dir_name)
        if os.path.exists(destination):
            destination = f"{destination}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.move(current_dir, destination)
        current_dir = parent_dir
    return popup_message

def mark_piece_fatto(file_path, da_fare_path, final_count=None):
    try:
        if final_count is None:
            _, final_count, _ = extract_length_and_quantity_enhanced(file_path)
        updated_path, updated_filename = _rename_with_final_count(file_path, final_count)
        directory = os.path.dirname(updated_path)

        fatto_folder = os.path.join(directory, 'fatto')
        os.makedirs(fatto_folder, exist_ok=True)

        destination = os.path.join(fatto_folder, updated_filename)
        shutil.move(updated_path, destination)

        popup_message = check_and_archive_recursively(directory, da_fare_path)
        return {"status": "success", "finalPath": destination, "popup_message": popup_message}
    except FileNotFoundError:
        return {"status": "skipped", "message": "File not found"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def update_piece_completion(file_path, new_completed_count):
    try:
        updated_path, _ = _rename_with_final_count(file_path, new_completed_count)
        return {"status": "success", "finalPath": updated_path}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def process_quantity_update(file_path, new_completed_count, da_fare_path):
    try:
        _, total_quantity, _ = extract_length_and_quantity_enhanced(file_path)
        if total_quantity is None:
            return {"status": "error", "message": "Could not determine total quantity from filename."}

        if new_completed_count >= total_quantity:
            print(f"Quantity met for {os.path.basename(file_path)}. Marking as 'fatto'.")
            return mark_piece_fatto(file_path, da_fare_path, new_completed_count)
        else:
            print(f"Quantity not met for {os.path.basename(file_path)}. Updating count only.")
            return update_piece_completion(file_path, new_completed_count)
    except Exception as e:
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

def process_rod_completion(rod_pieces_data, da_fare_path):
    id_to_final_path, popup_message = {}, None
    ids_on_rod = [p['id'] for p in rod_pieces_data]
    counts_on_rod = Counter(ids_on_rod)
    unique_pieces_to_process = {p['id']: p for p in rod_pieces_data}.values()

    for piece in unique_pieces_to_process:
        try:
            new_completed_total = piece['completedQuantity'] + counts_on_rod[piece['id']]
            response = process_quantity_update(piece['filePath'], new_completed_total, da_fare_path)
            if response and response.get("status") == "success":
                id_to_final_path[piece['id']] = response.get("finalPath")
                if response.get("popup_message"):
                    popup_message = response["popup_message"]
        except Exception as e:
            return {"status": "error", "message": f"Failed on {piece['fileName']}: {e}"}

    pieces_for_history_log = []
    for piece_data in rod_pieces_data:
        log_entry = piece_data.copy()
        if id_to_final_path.get(log_entry['id']):
            log_entry['filePath'] = id_to_final_path[log_entry['id']]
        pieces_for_history_log.append(log_entry)

    history_data = load_history()
    new_history_entry = {
        "rodId": str(uuid.uuid4()),
        "completionDate": datetime.now().isoformat(),
        "tubeType": pieces_for_history_log[0]['tubeType'],
        "pieces": pieces_for_history_log
    }
    history_data.append(new_history_entry)
    save_history(history_data)

    return {"status": "success", "message": "Processed rod", "popup_message": popup_message}

def _resolve_locked_segment_path(segment, da_fare_path):
    candidates = [
        segment.get("currentFilePath"),
        segment.get("filePath"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate

    logical_key = segment.get("logicalKey")
    if not logical_key or not da_fare_path or not os.path.isdir(da_fare_path):
        return candidates[0] or candidates[1]

    for root, _, files in os.walk(da_fare_path):
        for file in files:
            if not file.lower().endswith(".zzx"):
                continue
            candidate = os.path.join(root, file)
            if make_logical_piece_key(candidate, da_fare_path) == logical_key:
                return candidate
    return candidates[0] or candidates[1]

def process_locked_segments_completion(segments, da_fare_path):
    segments = [seg for seg in (segments or []) if seg and not seg.get("done")]
    if not segments:
        return {"status": "success", "segments": {}, "message": "Nessun pezzo da aggiornare."}

    grouped = defaultdict(list)
    for segment in segments:
        path = _resolve_locked_segment_path(segment, da_fare_path)
        if not path:
            return {"status": "error", "message": "Percorso file mancante per un pezzo bloccato."}
        grouped[path].append(segment)

    updates = {}
    popup_message = None
    for file_path, path_segments in grouped.items():
        if not os.path.exists(file_path):
            return {"status": "error", "message": f"File non trovato: {file_path}"}

        _, total_quantity, completed_quantity = extract_length_and_quantity_enhanced(file_path)
        if total_quantity is None:
            return {"status": "error", "message": f"Quantita non leggibile dal nome file: {os.path.basename(file_path)}"}

        new_completed_total = completed_quantity + len(path_segments)
        response = process_quantity_update(file_path, new_completed_total, da_fare_path)
        if not response or response.get("status") not in {"success", "skipped"}:
            return {"status": "error", "message": response.get("message", "Errore durante aggiornamento pezzo.") if response else "Errore durante aggiornamento pezzo."}

        final_path = response.get("finalPath") or file_path
        if response.get("popup_message"):
            popup_message = response["popup_message"]
        for segment in path_segments:
            key = segment.get("instanceKey")
            if key:
                updates[key] = {
                    "finalPath": final_path,
                    "fileName": os.path.basename(final_path),
                }

    return {"status": "success", "segments": updates, "popup_message": popup_message}

def log_locked_rod_completion(rod_payload):
    rod_payload = rod_payload or {}
    locked_rod_id = rod_payload.get("rodId")
    segments = rod_payload.get("segments") or []
    if not locked_rod_id or not segments:
        return {"status": "error", "message": "Dati verga bloccata mancanti."}

    history_data = load_history()
    if any(rod.get("lockedRodId") == locked_rod_id for rod in history_data):
        return {"status": "success", "message": "Verga gia presente in cronologia."}

    pieces_for_history_log = []
    for segment in segments:
        file_path = segment.get("currentFilePath") or segment.get("filePath")
        pieces_for_history_log.append({
            "id": segment.get("instanceKey") or str(uuid.uuid4()),
            "logicalKey": segment.get("logicalKey"),
            "filePath": file_path,
            "fileName": os.path.basename(file_path) if file_path else segment.get("fileName", ""),
            "mainFolder": segment.get("mainFolder", ""),
            "tubeType": segment.get("tubeType") or rod_payload.get("tubeType"),
            "length": segment.get("length"),
            "totalQuantity": segment.get("totalQuantity"),
            "completedQuantity": segment.get("completedQuantity"),
            "quantityNeeded": 0,
        })

    new_history_entry = {
        "rodId": str(uuid.uuid4()),
        "lockedRodId": locked_rod_id,
        "completionDate": datetime.now().isoformat(),
        "tubeType": rod_payload.get("tubeType") or pieces_for_history_log[0].get("tubeType"),
        "pieces": pieces_for_history_log,
    }
    history_data.append(new_history_entry)
    save_history(history_data)
    return {"status": "success", "message": "Verga bloccata salvata in cronologia."}

def _unarchive_recursively(start_dir, da_fare_path):
    current_dir = start_dir
    while True:
        if os.path.normpath(current_dir) == os.path.normpath(da_fare_path):
            break

        dir_name, parent_dir = os.path.basename(current_dir), os.path.dirname(current_dir)

        if dir_name.lower() == 'tt - fatto':
            destination = os.path.join(parent_dir, 'TT')
            if not os.path.exists(destination):
                os.rename(current_dir, destination)
                current_dir = destination
            else:
                break

        elif os.path.basename(parent_dir).lower() == 'fatto':
            grandparent_dir = os.path.dirname(parent_dir)
            destination = os.path.join(grandparent_dir, dir_name)
            shutil.move(current_dir, destination)
            current_dir = destination
        else:
            break

def undo_rod_completion(rod_id_to_undo, da_fare_path):
    history_data = load_history()
    rod_to_undo = next((rod for rod in history_data if rod['rodId'] == rod_id_to_undo), None)
    if not rod_to_undo:
        return {"status": "error", "message": "Rod not found in history."}

    ids_on_rod = [p['id'] for p in rod_to_undo['pieces']]
    counts_on_rod = Counter(ids_on_rod)
    unique_pieces_on_rod = {p['id']: p for p in rod_to_undo['pieces']}.values()

    for piece_info in unique_pieces_on_rod:
        try:
            current_path_in_history = piece_info['filePath']
            if not os.path.exists(current_path_in_history):
                continue

            _, total_quantity, current_fatti = extract_length_and_quantity_enhanced(current_path_in_history)
            count_on_this_rod = counts_on_rod[piece_info['id']]
            reverted_count = max(0, current_fatti - count_on_this_rod)

            _, reverted_filename = _rename_with_final_count(current_path_in_history, reverted_count)

            original_directory_of_file = os.path.dirname(os.path.dirname(current_path_in_history))
            final_destination_path = os.path.join(original_directory_of_file, reverted_filename)

            _unarchive_recursively(original_directory_of_file, da_fare_path)

            os.makedirs(os.path.dirname(final_destination_path), exist_ok=True)
            shutil.move(current_path_in_history, final_destination_path)

            if os.path.basename(final_destination_path) != reverted_filename:
                os.rename(final_destination_path, os.path.join(os.path.dirname(final_destination_path), reverted_filename))

            _rename_with_final_count(final_destination_path, reverted_count)

        except Exception as e:
            traceback.print_exc()
            return {"status": "error", "message": f"Failed to undo {piece_info['fileName']}: {e}"}

    updated_history = [rod for rod in history_data if rod['rodId'] != rod_id_to_undo]
    save_history(updated_history)
    return {"status": "success", "message": "Rod successfully reverted."}

def open_file_in_explorer(file_path):
    try:
        normalized_path = os.path.normpath(file_path)
        if not os.path.exists(normalized_path):
            return {"status": "error", "message": f"File does not exist: {normalized_path}"}
        subprocess.run(['explorer', '/select,', normalized_path])
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- History and Summary Functions ---
def get_history_from_log():
    return load_history()

def remove_history_items(rod_ids_to_remove):
    history_data = load_history()
    updated_history = [rod for rod in history_data if rod['rodId'] not in rod_ids_to_remove]
    save_history(updated_history)
    return {"status": "success", "removed_count": len(history_data) - len(updated_history)}

# --- Inventory Search ---
def search_tube_inventory(dimensione, spessore, materiale, finitura, da_fare_path=None):
    try:
        dim = str(dimensione).strip().replace(",", ".").replace(" ", "")
        sp = str(spessore).strip().replace(",", ".")
        mat = str(materiale).upper().strip()
        fin = str(finitura).upper().strip()

        if dim.startswith("Ø") or dim.startswith("ø"):
            dim = dim[1:]

        if not dim or not sp:
            return {"status": "error", "message": "Dimensione o Spessore mancanti."}

        if "x" in dim:
            tube_label = f"{dim}x{sp} {mat} {fin}"
        else:
            tube_label = f"Ø{dim}x{sp} {mat} {fin}"

        ensure_result = ensure_tube_database(da_fare_path)
        if ensure_result.get("status") != "success":
            return {
                "status": "error",
                "message": ensure_result.get("message", "Database Tubi non disponibile."),
            }

        matches = tube_database.get_locations_for_tube(PROJECT_ROOT, dim, sp, mat, fin)
        total_qty = sum(int(qty or 0) for _, qty in matches)

        lines = [f"[{tube_label}]", "Necessarie: -"]
        if total_qty == 0:
            lines.append("Disponibili: 0 verghe")
        else:
            lines.append(f"Disponibili: {total_qty}V")
            for loc, q in matches:
                lines.append(f"{loc} {q}V")

        return {"status": "success", "text": "\n".join(lines)}

    except Exception:
        traceback.print_exc()
        return {"status": "error", "message": "Errore durante la ricerca."}

def search_tube_inventory_by_type(tube_type, da_fare_path=None):
    identity = _tube_identity_from_tube_type(tube_type)
    if not identity:
        return {"status": "error", "message": f"Tipo tubo non valido: {tube_type}"}

    return search_tube_inventory(
        identity["misura"],
        identity["spessore"],
        identity["materiale"],
        identity["finitura"],
        da_fare_path,
    )

# --- Summary generation ---
def generate_summary_text(all_tubes_data, threshold_percent, da_fare_path=None):
    rod_length = 6000.0

    required_map = {}
    for group in all_tubes_data:
        tube_type = group['tubeType']
        rods = group.get('rods', [])
        if not rods:
            continue

        full_rods = 0
        scraps = []

        for rod in rods:
            used_mm = rod_length - rod['remaining']
            if (used_mm / rod_length) * 100.0 >= threshold_percent:
                full_rods += 1
            else:
                scraps.append(int(used_mm))

        required_map[tube_type] = {"full_rods": full_rods, "scraps": scraps}

    sorted_tubes = sorted(required_map.items(), key=lambda item: item[1]['full_rods'], reverse=True)

    lines = [f"Riepilogo Materiale da Prelievo (soglia >{threshold_percent}%):", ""]

    for tube_type, data in sorted_tubes:
        lines.append(f"[{tube_type}]")

        need_parts = []
        if data["full_rods"] > 0:
            need_parts.append(f'{data["full_rods"]}V')
        for scrap_len in sorted(data["scraps"], reverse=True):
            need_parts.append(f"1 spezzone L{scrap_len}")

        lines.append("Necessarie: " + (" + ".join(need_parts) if need_parts else "0V"))

        locations = get_locations_for_tube_type(tube_type, da_fare_path) or []

        nonzero = []
        total_available = 0
        for loc, qty in locations:
            try:
                q = int(qty)
            except Exception:
                q = 0
            if q > 0:
                nonzero.append((loc, q))
                total_available += q

        if total_available == 0:
            lines.append("Disponibili: 0 verghe")
        else:
            lines.append(f"Disponibili: {total_available}V")
            for loc, q in nonzero:
                lines.append(f"{loc} {q}V")

        lines.append("")

    return {"status": "success", "text": "\n".join(lines)}

def save_summary_and_open(summary_text, da_fare_path):
    try:
        filename = f"Riepilogo {datetime.now().strftime('%Y-%m-%d')}.txt"
        output_dir = _get_docx_output_dir_fallback(da_fare_path, "summary_txt_output_dir")
        file_path = os.path.join(output_dir, filename)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(summary_text)
        os.startfile(file_path)
        return {"status": "success", "path": file_path}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- DOCX printout generation ---
def _set_section_a4_two_columns(section, space_between_mm=8):
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    section.left_margin = Mm(10)
    section.right_margin = Mm(10)
    section.top_margin = Mm(10)
    section.bottom_margin = Mm(10)

    sectPr = section._sectPr
    cols = sectPr.xpath('./w:cols')
    if cols:
        cols = cols[0]
    else:
        cols = OxmlElement('w:cols')
        sectPr.append(cols)

    cols.set(qn('w:num'), '2')
    cols.set(qn('w:space'), str(int(space_between_mm * 56.7)))  # approx twips

def _set_keep_flags(paragraph, keep_next=False):
    p = paragraph._p
    pPr = p.get_or_add_pPr()

    keepLines = pPr.find(qn('w:keepLines'))
    if keepLines is None:
        keepLines = OxmlElement('w:keepLines')
        pPr.append(keepLines)

    keepNext = pPr.find(qn('w:keepNext'))
    if keep_next:
        if keepNext is None:
            keepNext = OxmlElement('w:keepNext')
            pPr.append(keepNext)
    else:
        if keepNext is not None:
            pPr.remove(keepNext)

def save_summary_and_open_docx(summary_text, da_fare_path):
    if Document is None:
        return {"status": "error", "message": "python-docx non è installato. Installa 'python-docx' e riprova."}

    try:
        filename = f"Riepilogo Stampa {datetime.now().strftime('%Y-%m-%d')}.docx"
        output_dir = _get_docx_output_dir_fallback(da_fare_path, "summary_docx_output_dir")
        file_path = os.path.join(output_dir, filename)

        doc = Document()
        section = doc.sections[0]
        _set_section_a4_two_columns(section)

        blocks = re.split(r'\n\s*\n', str(summary_text or '').strip())
        blocks = [b.strip() for b in blocks if b.strip()]

        for bi, block in enumerate(blocks):
            lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]

            # Title line (optional)
            if bi == 0 and lines and lines[0].lower().startswith("riepilogo materiale"):
                title = lines.pop(0)
                p = doc.add_paragraph(title)
                run = p.runs[0] if p.runs else p.add_run(title)
                run.font.size = Pt(16)
                run.bold = True
                _set_keep_flags(p, keep_next=False)
                doc.add_paragraph("")
                if not lines:
                    continue

            header = lines[0] if lines else ""
            is_316 = " 316 " in (" " + header.replace("[", " ").replace("]", " ") + " ")

            paras = []

            p = doc.add_paragraph()
            r = p.add_run(header)
            r.font.size = Pt(20)
            r.bold = True
            if is_316:
                r.italic = True
                r.underline = True
            p.paragraph_format.space_after = Pt(0)
            paras.append(p)

            for ln in lines[1:]:
                p2 = doc.add_paragraph(ln)
                for rr in p2.runs:
                    rr.font.size = Pt(15)
                p2.paragraph_format.space_after = Pt(0)
                paras.append(p2)

            for i, p in enumerate(paras):
                _set_keep_flags(p, keep_next=(i < len(paras) - 1))

            doc.add_paragraph("")

        doc.save(file_path)
        os.startfile(file_path)
        return {"status": "success", "path": file_path}
    except Exception:
        traceback.print_exc()
        return {"status": "error", "message": "Errore durante la generazione del DOCX."}

# --- TT DOCX Generator integration ---
_TT_DOCX_MODULE = None


def _load_tt_docx_module():
    """Loads tt_docx_generator.pyw from PROJECT_ROOT (cached)."""
    global _TT_DOCX_MODULE
    if _TT_DOCX_MODULE is not None:
        return _TT_DOCX_MODULE

    tt_path = os.path.join(RESOURCE_ROOT, "tt_docx_generator.pyw")
    if not os.path.exists(tt_path):
        return None

    spec = importlib.util.spec_from_file_location("tt_docx_generator", tt_path)
    if not spec or not spec.loader:
        return None

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _TT_DOCX_MODULE = mod
    return mod


def _get_docx_output_dir_fallback(da_fare_path=None, config_key=None, output_dir_override=None):
    """Returns the configured writable output folder for an export."""
    cfg = load_config() or {}
    candidates = [output_dir_override]
    if config_key:
        candidates.append(cfg.get(config_key))
    candidates.extend([cfg.get("docx_output_dir"), da_fare_path, PROJECT_ROOT])

    for candidate in candidates:
        out_dir = str(candidate or "").strip()
        if not out_dir:
            continue
        try:
            os.makedirs(out_dir, exist_ok=True)
            return out_dir
        except Exception:
            continue

    return PROJECT_ROOT


def generate_tt_docx_from_dialog():
    import tkinter as tk
    from tkinter import filedialog
    import os
    import traceback

    print("\n--- TT DOCX DEBUG START (MULTI SELECT) ---")

    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)

        # Custom multi-folder selection
        selected_folders = []

        while True:
            folder = filedialog.askdirectory(
                title="Select PROD folder (Cancel to finish selection)"
            )

            if not folder:
                break

            if folder not in selected_folders:
                selected_folders.append(folder)

        root.destroy()

        if not selected_folders:
            return {"status": "error", "message": "No folder selected."}

        print("DEBUG: Selected folders =", selected_folders)

        mod = _load_tt_docx_module()
        if mod is None or not hasattr(mod, "generate_docx_for_prod_auto"):
            return {"status": "error", "message": "Generatore DOCX TT non disponibile."}

        output_dir = _get_docx_output_dir_fallback(config_key="tt_docx_output_dir")

        results = []

        for folder in selected_folders:
            print("\n--- Processing folder ---")
            print("Folder:", folder)

            ok, msg = mod.generate_docx_for_prod_auto(
                folder,
                output_dir=output_dir
            )

            print("Result:", ok, msg)

            if not ok:
                return {"status": "error", "message": msg}

            results.append(msg)

        print("--- TT DOCX DEBUG END ---\n")

        return {
            "status": "success",
            "message": "Generated:\n" + "\n".join(results)
        }

    except Exception:
        print("\n!!! TT CRITICAL EXCEPTION !!!")
        traceback.print_exc()
        return {"status": "error", "message": "Unexpected TT DOCX error. See console."}


def generate_tt_docx_from_explorer_selection(da_fare_path=None):
    """Generates TT list DOCX files for folders currently selected in File Explorer."""
    try:
        mod = _load_tt_docx_module()
        if mod is None:
            return {"status": "error", "message": "tt_docx_generator.pyw non trovato."}

        get_selected = getattr(mod, "get_selected_folders", None)
        generate_auto = getattr(mod, "generate_docx_for_prod_auto", None)
        if get_selected is None or generate_auto is None:
            return {"status": "error", "message": "Generatore DOCX TT incompleto."}

        selected_folders = get_selected()
        if not selected_folders:
            return {
                "status": "error",
                "message": "Nessuna cartella selezionata in File Explorer. Seleziona una o piu cartelle PROD e riprova.",
            }

        output_dir = _get_docx_output_dir_fallback(da_fare_path, "tt_docx_output_dir")
        generated = []
        failed = []

        for folder in selected_folders:
            ok, msg = generate_auto(folder, output_dir=output_dir)
            if ok:
                generated.append(msg)
            else:
                failed.append({"folder": folder, "error": msg})

        if generated:
            return {
                "status": "success",
                "generated": generated,
                "failed": failed,
                "save_dir": output_dir,
            }

        first_error = failed[0]["error"] if failed else "Nessun file DOCX generato."
        return {
            "status": "error",
            "message": first_error,
            "failed": failed,
            "save_dir": output_dir,
        }

    except Exception:
        traceback.print_exc()
        return {"status": "error", "message": "Errore durante la generazione DOCX TT."}
