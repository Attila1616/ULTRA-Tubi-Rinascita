"""Global, configurable BMP-to-PDF hotkey within the running TubeNest app."""
from __future__ import annotations

import ctypes
import re
import threading
import traceback

from bmp_pdf_merger import (
    format_merge_result,
    get_selected_bmp_files,
    merge_selected_bmps,
)


DEFAULT_BMP_PDF_HOTKEY = "ctrl+alt+b"

_MODIFIERS = {"ctrl", "alt", "shift"}
_KEYS = set("abcdefghijklmnopqrstuvwxyz0123456789")
_KEYS.update(f"f{number}" for number in range(1, 13))
_ALIASES = {"control": "ctrl", "left ctrl": "ctrl", "right ctrl": "ctrl"}


def normalize_bmp_hotkey(value):
    """Accept deliberate two-modifier global shortcuts, not bare letters."""
    value = str(value or "").strip().lower()
    if not value:
        raise ValueError("Inserisci una scorciatoia per BMP → PDF.")
    tokens = [
        _ALIASES.get(token.strip(), token.strip())
        for token in re.split(r"\s*\+\s*", value)
    ]
    if not tokens or any(not token for token in tokens):
        raise ValueError("Formato hotkey non valido. Esempio: Ctrl+Alt+B.")
    if len(tokens) != len(set(tokens)):
        raise ValueError("La scorciatoia contiene tasti duplicati.")
    modifiers = [token for token in tokens if token in _MODIFIERS]
    others = [token for token in tokens if token not in _MODIFIERS]
    if len(modifiers) < 2 or len(others) != 1 or others[0] not in _KEYS:
        raise ValueError(
            "Usa due modificatori tra Ctrl, Alt, Shift e una lettera, "
            "numero o tasto F1-F12. Esempio: Ctrl+Alt+B."
        )
    order = ["ctrl", "alt", "shift"]
    return "+".join([m for m in order if m in modifiers] + others)


def _native_message(message, *, error=False):
    """Native message rather than a webview modal (Explorer has focus)."""
    print("[TubeNest BMP->PDF] " + message, flush=True)
    try:
        flags = 0x00040000 | (0x00000010 if error else 0x00000040)
        ctypes.windll.user32.MessageBoxW(
            None, message, "TubeNest - BMP in PDF", flags,
        )
    except (AttributeError, OSError):
        pass


class BmpPdfHotkey:
    def __init__(self, keyboard_backend=None):
        self._keyboard = keyboard_backend
        self._handle = None
        self._shortcut = None
        self._state_lock = threading.RLock()
        self._merge_lock = threading.Lock()

    @property
    def shortcut(self):
        return self._shortcut

    def configure(self, shortcut):
        normalized = normalize_bmp_hotkey(shortcut)
        with self._state_lock:
            if normalized == self._shortcut and self._handle is not None:
                return normalized
            if self._keyboard is None:
                import keyboard
                self._keyboard = keyboard

            # Register first so an invalid/blocked replacement cannot
            # unexpectedly disable the old shortcut.
            handle = self._keyboard.add_hotkey(
                normalized, self._on_trigger, suppress=False,
            )
            old_handle = self._handle
            self._handle = handle
            self._shortcut = normalized
            if old_handle is not None:
                try:
                    self._keyboard.remove_hotkey(old_handle)
                except (ValueError, KeyError):
                    pass
            print(
                f"[TubeNest BMP->PDF] Hotkey active: {normalized}",
                flush=True,
            )
            return normalized

    def stop(self):
        with self._state_lock:
            handle = self._handle
            self._handle = None
            self._shortcut = None
            if handle is not None and self._keyboard is not None:
                try:
                    self._keyboard.remove_hotkey(handle)
                except (ValueError, KeyError):
                    pass

    def _on_trigger(self):
        # Capture foreground Explorer HWND *before* opening a dialog or
        # scheduling the worker, to avoid accidentally reading another tab.
        try:
            hwnd = int(ctypes.windll.user32.GetForegroundWindow())
        except (AttributeError, OSError):
            hwnd = 0
        if not self._merge_lock.acquire(blocking=False):
            return

        worker = threading.Thread(
            target=self._run,
            args=(hwnd,),
            name="bmp-to-pdf-hotkey",
            daemon=True,
        )
        worker.start()

    def _run(self, foreground_hwnd):
        try:
            selected = get_selected_bmp_files(foreground_hwnd=foreground_hwnd)
            if not selected:
                _native_message(
                    "Seleziona i BMP in una finestra di Esplora file "
                    "attiva e riprova.", error=True,
                )
                return
            result = merge_selected_bmps(selected)
            _native_message(
                format_merge_result(result),
                error=result["status"] != "success",
            )
        except Exception as exc:
            traceback.print_exc()
            _native_message(f"Conversione BMP → PDF non riuscita: {exc}", error=True)
        finally:
            self._merge_lock.release()
