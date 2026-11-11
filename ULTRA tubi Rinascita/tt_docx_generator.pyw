import os
import re
import json
import math
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


def add_title(doc, text, *, paragraph=None):
    p = paragraph if paragraph is not None else doc.add_paragraph()
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(22)
    p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.keep_with_next = True


def add_measure_heading(doc, text, *, reuse_trailing=False):
    # Nested tables automatically end with an empty paragraph in Word.
    # Reuse it as the next measure heading rather than adding blank lines.
    if reuse_trailing and getattr(doc, "paragraphs", None):
        last = doc.paragraphs[-1]
        p = last if not last.text else doc.add_paragraph()
    else:
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
    # The preceding measure heading already uses keep_with_next.
    # Do not add w:tblpPr here: that property FLOATS the table and may
    # reorder nested TD content ahead of its heading in Word/LibreOffice.

    hdr = table.rows[0].cells
    hdr[0].text = "OK"
    hdr[1].text = "File"
    hdr[2].text = "Qta"
    hdr[3].text = "Firma Magazzino"

    # Preserve the original column proportions (14:104:28:44) while
    # fitting A4's 180 mm printable width with 15 mm page margins.
    # Set BOTH grid columns and per-cell widths: nested DOCX tables
    # otherwise render as four equal columns in some Word/LibreOffice builds.
    original_widths = (14.0, 104.0, 28.0, 44.0)
    scale = 180.0 / sum(original_widths)
    widths = [Mm(value * scale) for value in original_widths]

    for i, w in enumerate(widths):
        table.columns[i].width = w
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


# A4 height: 297 mm minus 15 mm margins on both sides = 267 mm.
# Reserve 12 mm for pagination differences between Word/LibreOffice and
# for the small paragraph preceding an explicit page break. The old 235 mm
# cap wasted 32 mm even before its pessimistic per-row estimates.
MAX_SINGLE_PAGE_TD_MM = 255.0


def _td_group_estimated_height_mm(grouped):
    """Estimate *actual fixed-table geometry* for page bin packing.

    The exported rows have minimum height 10 mm (w:trHeight atLeast).
    A 22 pt TD title plus its 8 pt after-space occupies around 12 mm.
    A 16 pt measure heading plus 6 pt before-space and 10 mm table header
    occupies around 19 mm. Unlike the previous heuristic, do not charge
    another 12 mm for every row when the DOCX explicitly uses 10 mm rows.

    Text in the 98.5 mm file column can wrap; estimate its extra height
    only when more than two lines are expected. Leave a small allowance
    for row borders, Word's line metrics and the compulsory closing para.
    This is a placement estimate, never a request to shrink existing rows.
    """
    height = 14.0  # title + required closing paragraph allowance
    for _measure, filenames in grouped.items():
        height += 19.5  # 16 pt measure heading + 10 mm header row
        for filename in filenames:
            name, _quantity = split_name_and_qty(filename)
            line_count = max(1, math.ceil(len(name) / 55.0))
            # Two 11 pt lines generally still fit the 10 mm minimum.
            # Longer descriptions need genuinely taller rows.
            height += max(10.0, 5.1 * line_count) + 0.35
    return height


def _zero_cell_margins(cell):
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = tc_pr.find(qn("w:tcMar"))
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for side in ("top", "bottom", "left", "right", "start", "end"):
        el = tc_mar.find(qn("w:" + side))
        if el is None:
            el = OxmlElement("w:" + side)
            tc_mar.append(el)
        el.set(qn("w:w"), "0")
        el.set(qn("w:type"), "dxa")


def _add_td_cut_line(container, *, reuse_trailing=False):
    """Thin dashed cutting guide after a complete TD group."""
    if reuse_trailing and getattr(container, "paragraphs", None):
        last = container.paragraphs[-1]
        p = last if not last.text else container.add_paragraph()
    else:
        p = container.add_paragraph()

    # Give the border a visible line box; empty 1 pt paragraphs can make
    # LibreOffice and some Word versions suppress the dashed border.
    run = p.add_run(" ")
    run.font.size = Pt(8)
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.line_spacing = Pt(8)
    p_pr = p._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "dashed")
    bottom.set(qn("w:sz"), "7")
    bottom.set(qn("w:color"), "888888")
    bottom.set(qn("w:space"), "1")
    borders.append(bottom)
    p_pr.append(borders)


def _measure_row_height_mm(filename):
    """Match the placement estimator used for ordinary, unshrunk rows."""
    name, _quantity = split_name_and_qty(filename)
    line_count = max(1, math.ceil(len(name) / 55.0))
    return max(10.0, 5.1 * line_count) + 0.35


def _measure_chunks(filenames, *, page_budget_mm=MAX_SINGLE_PAGE_TD_MM):
    """Partition genuinely oversized measure tables into whole printable tables.

    A 40-row table cannot physically fit on A4 without scaling. Rather than
    allow Word to cut it between two pages, create smaller *complete* tables,
    each with its own heading and repeated header row.
    """
    # The title + measure heading + header row are repeated on each chunk.
    available = float(page_budget_mm) - 14.0 - 19.5
    if available <= 0:
        raise ValueError("Area stampabile A4 insufficiente.")
    chunks, current, used = [], [], 0.0
    for name in filenames:
        height = _measure_row_height_mm(name)
        if height > available:
            raise ValueError(
                "Una riga del DOCX TT è troppo alta per una pagina A4 "
                f"senza ridurne il carattere: {name}"
            )
        if current and used + height > available:
            chunks.append(current)
            current, used = [], 0.0
        current.append(name)
        used += height
    if current:
        chunks.append(current)
    return chunks


def _write_atomic_measure(doc, section_title, measure, filenames, *,
                          part_number=None, part_count=None):
    """A complete dimension table inside one indivisible Word row.

    This is essential for large TDs: cantSplit on each individual file row
    does NOT prevent Word from putting half the same table on another page.
    """
    wrapper = doc.add_table(rows=1, cols=1)
    wrapper.autofit = False
    wrapper.columns[0].width = Mm(180)
    tr_pr = wrapper.rows[0]._tr.get_or_add_trPr()
    tr_pr.append(OxmlElement("w:cantSplit"))
    cell = wrapper.cell(0, 0)
    _zero_cell_margins(cell)
    add_title(cell, section_title, paragraph=cell.paragraphs[0])
    label = measure
    if part_count is not None and part_count > 1:
        label += f" (parte {part_number}/{part_count})"
    add_measure_heading(cell, label, reuse_trailing=True)
    add_table_for_files(cell, filenames)
    if cell.paragraphs and not cell.paragraphs[-1].text:
        trailing = cell.paragraphs[-1]
        trailing.paragraph_format.space_after = Pt(0)
        trailing.paragraph_format.line_spacing = Pt(1)


def write_section(doc, section_title, file_paths, *, show_cut_line=False):
    """Paginate oversized TDs strictly BETWEEN dimension tables, never within.

    If a single measure is longer than an A4 sheet, split it into separately
    labelled chunks with repeated column headers, each itself unsplittable.
    Never shrink the printed rows or omit a file.
    """
    grouped = group_files_by_measure(file_paths)
    for measure, names in grouped.items():
        chunks = _measure_chunks(names)
        for index, chunk in enumerate(chunks, 1):
            _write_atomic_measure(
                doc, section_title, measure, chunk,
                part_number=index, part_count=len(chunks),
            )
    if show_cut_line:
        _add_td_cut_line(doc)


def _write_compact_td(doc, section_title, grouped, *, show_cut_line):
    """One unsplittable table row makes Word move complete TDs together.

    This keeps existing measure headings and inner tables unchanged. Word
    decides whether the whole TD fits in the remaining space and moves the
    group to the next page if it does not.
    """
    outer = doc.add_table(rows=1, cols=1)
    outer.autofit = False
    outer.columns[0].width = Mm(180)
    row = outer.rows[0]
    tr_pr = row._tr.get_or_add_trPr()
    no_split = OxmlElement("w:cantSplit")
    tr_pr.append(no_split)

    cell = row.cells[0]
    _zero_cell_margins(cell)
    add_title(cell, section_title, paragraph=cell.paragraphs[0])
    for measure, names in grouped.items():
        add_measure_heading(cell, measure, reuse_trailing=True)
        add_table_for_files(cell, names)

    if show_cut_line:
        _add_td_cut_line(cell, reuse_trailing=True)
    elif cell.paragraphs and not cell.paragraphs[-1].text:
        # Word requires a closing paragraph after a nested table.
        trailing = cell.paragraphs[-1]
        trailing.paragraph_format.space_after = Pt(0)
        trailing.paragraph_format.line_spacing = Pt(1)


# Reserved page-height allowance for a dashed separation between TDs.
# The conservative per-group estimate also reserves some whitespace.
TD_SEPARATOR_BUDGET_MM = 9.0


def _plan_td_pages(prod_name, taglio_files, other_section_files,
                   *, page_budget_mm=MAX_SINGLE_PAGE_TD_MM):
    """Pseudo-nest whole TD groups vertically using best-fit decreasing.

    The stock tables use almost the entire printable page width, so placing
    two next to one another would make file names and signature boxes
    unreadably narrow. Instead, reorder *complete* TD groups by their
    estimated heights to fill the vertical gaps on A4 sheets.

    Keep Taglio Principale first and retain each group's internal file order.
    Oversized groups receive dedicated pages and may flow over multiple
    physical sheets. Never reserve an empty or split group for a smaller one.
    """
    source = []
    if taglio_files:
        source.append((f"{prod_name}: Taglio Principale", taglio_files))
    source.extend(
        (f"{prod_name}: {name}", files)
        for name, files in other_section_files
    )

    items = []
    for index, (title, files) in enumerate(source):
        grouped = group_files_by_measure(files)
        items.append({
            "title": title,
            "files": files,
            "grouped": grouped,
            "height_mm": _td_group_estimated_height_mm(grouped),
            "original_index": index,
        })

    if not items:
        return []

    pages = []
    page_heights = []

    def new_page(item):
        pages.append([item])
        page_heights.append(item["height_mm"])

    # Always print Taglio Principale first when it exists.
    start = 0
    if taglio_files:
        new_page(items[0])
        start = 1

    # Place larger TDs before smaller ones, then fill the smallest available
    # remainder. Stable original-index tie breaking is deterministic.
    for item in sorted(
        items[start:],
        key=lambda value: (-value["height_mm"], value["original_index"]),
    ):
        if item["height_mm"] > page_budget_mm:
            new_page(item)
            continue

        best_page = None
        best_unused = None
        for page_index, page in enumerate(pages):
            if any(
                placed["height_mm"] > page_budget_mm
                for placed in page
            ):
                continue
            separator = TD_SEPARATOR_BUDGET_MM if page else 0.0
            used = page_heights[page_index] + separator + item["height_mm"]
            if used > page_budget_mm:
                continue
            unused = page_budget_mm - used
            if best_unused is None or unused < best_unused:
                best_page = page_index
                best_unused = unused

        if best_page is None:
            new_page(item)
        else:
            page_heights[best_page] += (
                TD_SEPARATOR_BUDGET_MM + item["height_mm"]
            )
            pages[best_page].append(item)

    return pages


def render_td_groups(doc, prod_name, taglio_files, other_section_files):
    """Pack TDs onto pages, drawing cut lines *only* between page-mates."""
    pages = _plan_td_pages(
        prod_name, taglio_files, other_section_files,
    )
    for page_index, page in enumerate(pages):
        if page_index:
            doc.add_page_break()

        for group_index, group in enumerate(page):
            # No dashed line after the final TD on any page, even if the
            # next TD starts a new sheet (or there are no more TDs).
            show_cut_line = group_index < len(page) - 1
            if group["height_mm"] <= MAX_SINGLE_PAGE_TD_MM:
                _write_compact_td(
                    doc,
                    group["title"],
                    group["grouped"],
                    show_cut_line=show_cut_line,
                )
            else:
                # One exceptionally tall TD may span several real pages.
                # It has its own page, so there must be no cut separator.
                write_section(
                    doc,
                    group["title"],
                    group["files"],
                    show_cut_line=False,
                )


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

    render_td_groups(doc, prod_name, taglio_files, other_section_files)

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

        render_td_groups(doc, prod_name, taglio_files, other_section_files)

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
