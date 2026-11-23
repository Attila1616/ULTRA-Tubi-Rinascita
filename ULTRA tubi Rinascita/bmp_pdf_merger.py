"""Combine numbered BMP printouts selected in the foreground File Explorer.

Example: PROD.498.1.BMP, PROD.498.2.BMP -> PROD.498.pdf.
Runs inside TubeNest; no extra executable, services or network connection.
The PDF page order is numeric, not Explorer selection order.
"""
from __future__ import annotations

import ctypes
import os
import re
import tempfile
from collections import defaultdict
from pathlib import Path

# The *last* dotted number is the page, not any number inside the PROD code.
_NUMBERED_BMP = re.compile(r"^(?P<base>.+)\.(?P<page>[1-9][0-9]*)\.bmp$", re.I)
MAX_PAGES_PER_GROUP = 500


def get_selected_bmp_files(*, shell=None, foreground_hwnd=None):
    """Read selected BMPs ONLY from the active Windows Explorer window.

    The global hotkey is invoked while Explorer has focus. Selecting from any
    other (background) Explorer window could silently merge the wrong files.
    """
    did_init_com = False
    if foreground_hwnd is None:
        try:
            foreground_hwnd = int(ctypes.windll.user32.GetForegroundWindow())
        except (AttributeError, OSError, ValueError):
            return []
    if not foreground_hwnd:
        return []

    try:
        if shell is None:
            # keyboard callbacks may run on a separate thread. COM requires
            # initializing that exact thread before Shell.Application access.
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            did_init_com = True
            shell = win32com.client.Dispatch("Shell.Application")

        for window in shell.Windows():
            try:
                if int(window.HWND) != int(foreground_hwnd):
                    continue
                full_name = str(getattr(window, "FullName", "") or "")
                if full_name.replace("\\", "/").rsplit("/", 1)[-1].casefold() != "explorer.exe":
                    return []
                selected = window.Document.SelectedItems()
                paths = []
                seen = set()
                for i in range(int(selected.Count)):
                    candidate = str(selected.Item(i).Path or "").strip()
                    if not candidate.lower().endswith(".bmp"):
                        continue
                    candidate = os.path.abspath(candidate)
                    unique = os.path.normcase(candidate)
                    if unique not in seen and os.path.isfile(candidate):
                        paths.append(candidate)
                        seen.add(unique)
                return paths
            except Exception:
                return []
        return []
    finally:
        if did_init_com:
            import pythoncom
            pythoncom.CoUninitialize()


def plan_bmp_groups(file_paths):
    """Return fully validated groups and rejected groups, without writing.

    Each group is keyed by (source folder, base name excluding .N). A folder
    cannot accidentally be mixed with another folder with the same PROD name.
    Missing, duplicated or invalid page numbers reject the *whole* group to
    avoid incomplete/misordered production printouts.
    """
    collected = defaultdict(list)
    skipped = []
    seen_paths = set()
    for raw in file_paths or []:
        path = os.path.abspath(os.fspath(raw))
        unique = os.path.normcase(path)
        if unique in seen_paths:
            continue
        seen_paths.add(unique)

        match = _NUMBERED_BMP.fullmatch(os.path.basename(path))
        if not match:
            skipped.append({
                "file": path,
                "reason": "Il nome deve terminare con .N.BMP (N = numero pagina).",
            })
            continue
        page = int(match["page"])
        if page > MAX_PAGES_PER_GROUP:
            skipped.append({
                "file": path,
                "reason": f"Numero pagina superiore a {MAX_PAGES_PER_GROUP}.",
            })
            continue
        name = match["base"]
        folder = os.path.dirname(path)
        collected[(os.path.normcase(folder), name.casefold())].append(
            (page, path, name, folder)
        )

    groups = []
    errors = []
    for entries in collected.values():
        entries.sort(key=lambda item: (item[0], item[1].casefold()))
        basename = entries[0][2]
        folder = entries[0][3]
        numbers = [entry[0] for entry in entries]
        duplicates = sorted({p for p in numbers if numbers.count(p) > 1})
        if duplicates:
            errors.append({
                "group": basename,
                "folder": folder,
                "reason": "Numeri pagina duplicati: " + ", ".join(map(str, duplicates)),
            })
            continue
        expected = list(range(1, numbers[-1] + 1))
        if numbers != expected:
            missing = sorted(set(expected) - set(numbers))
            errors.append({
                "group": basename,
                "folder": folder,
                "reason": "Pagine mancanti: " + ", ".join(map(str, missing)),
            })
            continue
        groups.append({
            "name": basename,
            "folder": folder,
            "files": [entry[1] for entry in entries],
            "pages": numbers,
        })

    groups.sort(key=lambda g: (g["folder"].casefold(), g["name"].casefold()))
    return groups, errors, skipped


def _unused_pdf_path(folder, base):
    # Names cannot contain OS-disallowed characters if they originated from a
    # real BMP filename. Never overwrite a prior merged PDF.
    initial = Path(folder) / (base + ".pdf")
    if not initial.exists():
        return initial
    suffix = 2
    while True:
        candidate = Path(folder) / f"{base} ({suffix}).pdf"
        if not candidate.exists():
            return candidate
        suffix += 1


def _write_bmp_pages(pdf_path, image_paths, title):
    """Embed every BMP losslessly into an A4 PDF, one image per page."""
    from PIL import Image
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(os.fspath(pdf_path), pagesize=A4, pageCompression=1)
    pdf.setTitle(title)
    pdf.setAuthor("TubeNest")
    for file_path in image_paths:
        with Image.open(file_path) as image:
            if image.format != "BMP":
                raise ValueError(f"File non BMP: {os.path.basename(file_path)}")
            image.load()
            if image.width <= 0 or image.height <= 0:
                raise ValueError(f"Immagine non valida: {file_path}")
            page_w, page_h = (
                landscape(A4) if image.width > image.height else A4
            )
            pdf.setPageSize((page_w, page_h))
            scale = min(page_w / image.width, page_h / image.height)
            width = image.width * scale
            height = image.height * scale
            pdf.drawImage(
                ImageReader(image),
                (page_w - width) / 2,
                (page_h - height) / 2,
                width=width,
                height=height,
            )
            pdf.showPage()
    pdf.save()


def merge_selected_bmps(file_paths):
    """Generate one PDF per PROD (or other filename base) from selected BMPs.

    Each PDF is written to a temporary file in its source directory, then
    moved into place only if every page was processed successfully.
    """
    groups, problems, skipped = plan_bmp_groups(file_paths)
    created = []
    for group in groups:
        missing_sources = [
            path for path in group["files"] if not os.path.isfile(path)
        ]
        if missing_sources:
            problems.append({
                "group": group["name"],
                "folder": group["folder"],
                "reason": "Uno o più BMP selezionati non sono più disponibili.",
            })
            continue
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".tubenest-bmp-", suffix=".pdf.tmp",
                dir=group["folder"], delete=False,
            ) as temp:
                temp_path = Path(temp.name)
            _write_bmp_pages(temp_path, group["files"], group["name"])
            output = _unused_pdf_path(group["folder"], group["name"])
            os.replace(temp_path, output)
            created.append({
                "group": group["name"],
                "path": os.fspath(output),
                "pages": len(group["files"]),
            })
        except Exception as exc:
            problems.append({
                "group": group["name"],
                "folder": group["folder"],
                "reason": str(exc),
            })
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    return {
        "status": "success" if created and not problems and not skipped
                  else "partial" if created else "error",
        "created": created,
        "errors": problems,
        "skipped": skipped,
    }


def format_merge_result(result):
    outputs = result.get("created") or []
    errors = result.get("errors") or []
    skipped = result.get("skipped") or []
    lines = []
    if outputs:
        lines.append(f"PDF generati: {len(outputs)}")
        for item in outputs[:20]:
            lines.append(f"{item['group']}: {item['pages']} pagine")
            lines.append(item["path"])
        if len(outputs) > 20:
            lines.append(f"...e altri {len(outputs) - 20} PDF")
    if errors:
        lines.append("\nGruppi NON generati:")
        for item in errors[:10]:
            lines.append(f"{item['group']}: {item['reason']}")
    if skipped:
        lines.append(f"\nBMP ignorati (nome non valido): {len(skipped)}")
        for item in skipped[:5]:
            lines.append(os.path.basename(item["file"]))
    if not lines:
        return "Nessun BMP numerato selezionato in Esplora file."
    return "\n".join(lines)
