import os
import re
import json
import keyboard
import time
from collections import defaultdict, OrderedDict
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

from docx import Document
from docx.shared import Pt, Mm
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

import win32com.client
import runtime_paths


APP_TITLE = "TT DOCX Generator"

# ----------------------------
# Config
# ----------------------------
DEFAULT_CONFIG = {
    "hotkey": "CTRL+ALT+G"
}

def load_config():
    cfg_path = runtime_paths.CONFIG_FILE
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()


# ----------------------------
# Tk helpers (safe messageboxes from hotkey thread)
# ----------------------------
def _tk_root():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    return root


def _show_info(title, msg):
    root = _tk_root()
    try:
        messagebox.showinfo(title, msg, parent=root)
    finally:
        root.destroy()


def _show_warn(title, msg):
    root = _tk_root()
    try:
        messagebox.showwarning(title, msg, parent=root)
    finally:
        root.destroy()


def _show_error(title, msg):
    root = _tk_root()
    try:
        messagebox.showerror(title, msg, parent=root)
    finally:
        root.destroy()


# ----------------------------
# Explorer selection
# ----------------------------
def get_selected_folders():
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        for w in shell.Windows():
            try:
                window_name = str(getattr(w, "Name", "") or "").lower()
                window_full_name = str(getattr(w, "FullName", "") or "").lower()
                window_class = str(getattr(w, "ClassName", "") or "").lower()
                is_explorer = (
                    "explorer" in window_name
                    or window_full_name.endswith("\\explorer.exe")
                    or "cabinetwclass" in window_class
                )
                if not is_explorer:
                    continue

                selected_items = w.Document.SelectedItems()
                paths = []
                for i in range(selected_items.Count):
                    path = str(selected_items.Item(i).Path or "").strip()
                    if os.path.isdir(path) and path not in paths:
                        paths.append(path)
                if paths:
                    return paths
            except Exception:
                pass
    except Exception:
        pass
    return []


# ----------------------------
# Name parsing
# ----------------------------
def extract_prod_name(folder_path):
    base = os.path.basename(os.path.abspath(folder_path)).strip()
    m = re.search(r"(?i)\bprod\s*(\d+)\b", base)
    if m:
        return f"PROD {m.group(1)}"

    root = _tk_root()
    try:
        val = simpledialog.askstring(APP_TITLE, "Insert name:", initialvalue=base, parent=root)
    finally:
        root.destroy()

    return val or base


def find_tt_folder(prod_root):
    direct = os.path.join(prod_root, "TT")
    if os.path.isdir(direct):
        return direct

    for root, dirs, _ in os.walk(prod_root):
        for d in dirs:
            if d.lower() == "tt":
                return os.path.join(root, d)
    return None


# ----------------------------
# Measure parsing
# ----------------------------
def normalize_num(s):
    return s.replace(",", ".").strip()

def extract_measure_from_filename(name):
    # round
    m = re.search(r"[Øø]\s*(\d+(?:[.,]\d+)?)\s*x\s*(\d+(?:[.,]\d+)?)", name)
    if m:
        return f"Ø{normalize_num(m.group(1))}x{normalize_num(m.group(2))}"

    # square/rect
    m = re.search(r"\b(\d+(?:[.,]\d+)?)x(\d+(?:[.,]\d+)?)x(\d+(?:[.,]\d+)?)", name)
    if m:
        return f"{normalize_num(m.group(1))}x{normalize_num(m.group(2))}x{normalize_num(m.group(3))}"

    return "UNKNOWN"


# ----------------------------
# Scan
# ----------------------------
def collect_zzx_files(root_path):
    out = []
    for r, _, files in os.walk(root_path):
        for f in files:
            if f.lower().endswith(".zzx"):
                out.append(os.path.join(r, f))
    return out


def split_tt_sections(tt):
    taglio = []
    other = []

    for name in sorted(os.listdir(tt), key=str.lower):
        p = os.path.join(tt, name)
        if not os.path.isdir(p):
            continue

        low = name.lower()
        if low.startswith("tubo") or low.startswith("tubolare"):
            taglio.append(p)
        else:
            other.append((name, p))

    return taglio, other


# ----------------------------
# DOCX layout (FINAL TABLE SIZING)
# ----------------------------
def set_running_header(doc, text):
    """
    Sets a repeating header for the CURRENT LAST section only,
    not linked to previous sections, with large title styling.
    """
    section = doc.sections[-1]

    try:
        section.header.is_linked_to_previous = False
    except Exception:
        pass

    header = section.header

    # clear existing header content
    for p in header.paragraphs:
        p.clear()

    p = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    run = p.add_run(text)
    run.bold = True
    run.font.size = Pt(22)

    p.paragraph_format.space_after = Pt(6)
    

def _keep_table_with_previous(table):
    """
    Makes Word try hard to keep the table on the same page
    as the paragraph before it (measure heading).
    """
    tbl = table._tbl
    tblPr = tbl.tblPr

    keepNext = OxmlElement('w:tblpPr')
    tblPr.append(keepNext)


def add_section_break(doc):
    """
    Starts a new section (new page) and ensures its header
    is NOT linked to the previous section.
    """
    doc.add_section()
    section = doc.sections[-1]
    try:
        section.header.is_linked_to_previous = False
    except Exception:
        pass
    

def _repeat_table_header(row):
    tr = row._tr
    trPr = tr.get_or_add_trPr()
    tblHeader = OxmlElement('w:tblHeader')
    tblHeader.set(qn('w:val'), "true")
    trPr.append(tblHeader)


def _center_cell(cell):
    # horizontal
    for p in cell.paragraphs:
        p.alignment = 1  # center

    # vertical
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    vAlign = OxmlElement('w:vAlign')
    vAlign.set(qn('w:val'), 'center')
    tcPr.append(vAlign)


def _set_row_height(row, height_mm):
    tr = row._tr
    trPr = tr.get_or_add_trPr()

    # height
    trHeight = OxmlElement('w:trHeight')
    twips = int(height_mm * 56.6929)
    trHeight.set(qn('w:val'), str(twips))
    trHeight.set(qn('w:hRule'), 'atLeast')
    trPr.append(trHeight)

    # do not split row across pages
    cantSplit = OxmlElement('w:cantSplit')
    trPr.append(cantSplit)


def set_a4(doc):
    s = doc.sections[0]
    s.page_width = Mm(210)
    s.page_height = Mm(297)
    s.top_margin = Mm(15)
    s.bottom_margin = Mm(15)
    s.left_margin = Mm(15)
    s.right_margin = Mm(15)


def add_title(doc, text):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(22)
    p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.keep_with_next = True


def add_measure_heading(doc, text):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(16)

    fmt = p.paragraph_format
    fmt.space_before = Pt(6)
    fmt.keep_with_next = True     # glue to table
    fmt.keep_together = True
    fmt.widow_control = True


def _set_table_fixed_layout(table):
    tbl = table._tbl
    tblPr = tbl.tblPr
    # avoid duplicates
    for el in tblPr.findall(qn('w:tblLayout')):
        tblPr.remove(el)
    tblLayout = OxmlElement('w:tblLayout')
    tblLayout.set(qn('w:type'), 'fixed')
    tblPr.append(tblLayout)


def _set_col_width(cell, width_mm_float):
    """
    width_mm_float: float in millimeters
    """
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()

    # remove existing tcW to avoid stacking multiple widths
    for el in tcPr.findall(qn('w:tcW')):
        tcPr.remove(el)

    # mm -> twips (approx) : 1 inch=25.4mm, 1 inch=1440 twips -> 56.6929 twips/mm
    twips = int(float(width_mm_float) * 56.6929)

    tcW = OxmlElement('w:tcW')
    tcW.set(qn('w:w'), str(twips))
    tcW.set(qn('w:type'), 'dxa')
    tcPr.append(tcW)


def split_name_and_qty(filename):
    """
    Returns (name_without_qty_ext_parenthesis, qty_text)

    - strips .zzx
    - removes anything in (...) like (fatti 3)
    - extracts 3pz → '3 pezzi'
    - extracts 1pz → '1 pezzo'
    """

    # remove extension
    base = re.sub(r"(?i)\.zzx$", "", filename).strip()

    # remove any (...) block (fatti/fatto/etc)
    base = re.sub(r"\([^)]*\)", "", base).strip()

    m = re.search(r"(?i)\b(\d+)\s*pz\b", base)
    if not m:
        return base, ""

    n = int(m.group(1))

    if n == 1:
        qty = "1 pezzo"
    else:
        qty = f"{n} pezzi"

    name = re.sub(r"(?i)\b\d+\s*pz\b", "", base).strip()
    return name, qty


def add_table_for_files(doc, filenames):
    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    _set_table_fixed_layout(table)
    _keep_table_with_previous(table)   # ⭐ stick to heading

    hdr = table.rows[0].cells
    hdr[0].text = "OK"
    hdr[1].text = "File"
    hdr[2].text = "Qta"
    hdr[3].text = "Firma Magazzino"

    widths = [Mm(14), Mm(104), Mm(28), Mm(44)]

    for i, w in enumerate(widths):
        _set_col_width(hdr[i], w.mm)
        _center_cell(hdr[i])

    header_row = table.rows[0]
    _set_row_height(header_row, 10)
    _repeat_table_header(header_row)

    # also prevent header paragraph from breaking away
    for cell in header_row.cells:
        for p in cell.paragraphs:
            p.paragraph_format.keep_with_next = True
            p.paragraph_format.keep_together = True

    for fn in filenames:
        name, qty = split_name_and_qty(fn)

        cells = table.add_row().cells
        cells[0].text = "☐"
        cells[1].text = name
        cells[2].text = qty
        cells[3].text = ""

        row = table.rows[-1]
        _set_row_height(row, 10)

        for i, w in enumerate(widths):
            _set_col_width(cells[i], w.mm)
            _center_cell(cells[i])


# ----------------------------
# Missing piece: grouping by measure (THIS FIXES YOUR CRASH)
# ----------------------------
def group_files_by_measure(file_paths):
    """
    measure -> list of base filenames (keeps original, qty parsing happens later)
    """
    m = defaultdict(list)

    for fp in file_paths:
        base = os.path.basename(fp)
        measure = extract_measure_from_filename(base)
        m[measure].append(base)

    out = OrderedDict()
    for measure in sorted(m.keys(), key=lambda x: (x == "UNKNOWN", x.lower())):
        out[measure] = sorted(m[measure], key=lambda x: x.lower())
    return out


def write_section(doc, section_title, file_paths):
    # header only — no body title
    set_running_header(doc, section_title)

    grouped = group_files_by_measure(file_paths)

    for measure, names in grouped.items():
        add_measure_heading(doc, measure)
        add_table_for_files(doc, names)

    doc.add_paragraph("")


# ----------------------------
# Generator
# ----------------------------
def generate_docx_for_prod(prod_root):
    tt = find_tt_folder(prod_root)
    if not tt:
        return False, "TT folder not found"

    prod_name = extract_prod_name(prod_root)
    taglio, other = split_tt_sections(tt)

    # --- collect Taglio Principale files ---
    taglio_files = []
    for r in taglio:
        taglio_files += collect_zzx_files(r)

    for f in os.listdir(tt):
        p = os.path.join(tt, f)
        if os.path.isfile(p) and f.lower().endswith(".zzx"):
            taglio_files.append(p)

    # --- collect other sections ---
    other_section_files = []
    for name, path in other:
        files = collect_zzx_files(path)
        if files:  # skip empty sections too
            other_section_files.append((name, files))

    # nothing at all
    if not taglio_files and not other_section_files:
        return False, "No ZZX files found under TT"

    doc = Document()
    set_a4(doc)

    wrote_any_section = False

    # --- Taglio Principale only if it has files ---
    if taglio_files:
        write_section(doc, f"{prod_name}: Taglio Principale", taglio_files)
        wrote_any_section = True

    # --- other sections ---
    for name, files in other_section_files:
        if wrote_any_section:
            add_section_break(doc)
        write_section(doc, f"{prod_name}: {name}", files)
        wrote_any_section = True

    # --- save dialog ---
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    save = filedialog.asksaveasfilename(
        defaultextension=".docx",
        initialdir=prod_root,
        initialfile=f"{prod_name} Lista TT.docx",
        filetypes=[("Word", "*.docx")]
    )

    root.destroy()

    if not save:
        return False, "Cancelled"

    doc.save(save)
    return True, save


def generate_docx_for_prod_auto(prod_root, output_dir=None):
    """
    Auto-save variant (no Save As dialog). Used by the main Tubi program.

    output_dir:
      - if provided and valid, DOCX is saved there
      - if None/invalid, saves inside prod_root

    Returns (ok, saved_path_or_error).
    """
    try:
        prod_root = os.path.abspath(prod_root)

        tt = find_tt_folder(prod_root)
        if not tt:
            return False, f"TT folder not found inside:\n{prod_root}"

        prod_name = extract_prod_name(prod_root)
        taglio, other = split_tt_sections(tt)

        # --- collect taglio principale ---
        taglio_files = []
        for r in taglio:
            taglio_files += collect_zzx_files(r)

        for f in os.listdir(tt):
            p = os.path.join(tt, f)
            if os.path.isfile(p) and f.lower().endswith('.zzx'):
                taglio_files.append(p)

        # --- collect other sections ---
        other_section_files = []
        for name, pth in other:
            files = collect_zzx_files(pth)
            if files:
                other_section_files.append((name, files))

        if not taglio_files and not other_section_files:
            return False, 'No ZZX files found under TT'

        doc = Document()
        set_a4(doc)

        wrote_any_section = False

        # IMPORTANT: don't generate an empty Taglio Principale section
        if taglio_files:
            write_section(doc, f"{prod_name}: Taglio Principale", taglio_files)
            wrote_any_section = True

        for name, files in other_section_files:
            if wrote_any_section:
                add_section_break(doc)
            write_section(doc, f"{prod_name}: {name}", files)
            wrote_any_section = True

        out = output_dir if output_dir and os.path.isdir(output_dir) else prod_root
        os.makedirs(out, exist_ok=True)

        base_name = f"{prod_name} Lista TT.docx"
        safe_name = re.sub(r"[\\/:*?\"<>|]", '_', base_name)
        save_path = os.path.join(out, safe_name)

        if os.path.exists(save_path):
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            save_path = os.path.join(out, re.sub(r'\.docx$', f' {stamp}.docx', safe_name, flags=re.IGNORECASE))

        doc.save(save_path)
        return True, save_path

    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, str(e)



# ----------------------------
# Hotkey runner
# ----------------------------
def process_selected_folders():
    sel = get_selected_folders()
    if not sel:
        _show_warn(APP_TITLE, "No folder selected in File Explorer.")
        return

    for p in sel:
        ok, msg = generate_docx_for_prod(p)
        if ok:
            _show_info(APP_TITLE, msg)
        else:
            _show_warn(APP_TITLE, msg)


def run_background_hotkey_loop():
    hk = load_config().get("hotkey", "ctrl+alt+g").lower()
    print("Hotkey active:", hk)
    keyboard.add_hotkey(hk, process_selected_folders)
    while True:
        time.sleep(1)


if __name__ == "__main__":
    run_background_hotkey_loop()
