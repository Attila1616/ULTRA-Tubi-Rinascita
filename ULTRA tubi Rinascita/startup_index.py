"""Persistent, stat-validated metadata cache for ULTRA's startup ZZX scanner.

A cold scan must parse all ZZX files once. Subsequent launches reuse their
parsed geometry/parts and validation results until the file changes. The cache
contains derived metadata only and is never authoritative for machine export.
"""
from __future__ import annotations

import gzip
import json
import os
import threading
import uuid


CACHE_SCHEMA_VERSION = 1


class StartupZzxIndex:
    def __init__(self, path, *, parser_revision=""):
        self.path = os.fspath(path)
        self.parser_revision = str(parser_revision)
        self._entries = {}
        self._lock = threading.RLock()
        self._dirty = False
        self.hits = 0
        self.misses = 0
        self._load()

    def _load(self):
        try:
            with gzip.open(self.path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            if (
                payload.get("version") == CACHE_SCHEMA_VERSION
                and payload.get("parserRevision") == self.parser_revision
                and isinstance(payload.get("entries"), dict)
            ):
                self._entries = payload["entries"]
        except (OSError, EOFError, ValueError, TypeError):
            # No cache, truncated cache, old schema: simply rebuild from ZZX.
            self._entries = {}

    def get(self, file_path, field, loader):
        """Read cached metadata if the file's size and mtime still match.

        Never cache a failed geometry/parts parse: a transient network error
        should be retried on the next scan instead of being made persistent.
        """
        path = os.path.normcase(os.path.abspath(os.fspath(file_path)))
        stat = os.stat(path)
        signature = [int(stat.st_size), int(stat.st_mtime_ns)]
        with self._lock:
            entry = self._entries.get(path)
            if not isinstance(entry, dict) or entry.get("signature") != signature:
                entry = {"signature": signature}
                self._entries[path] = entry
                self._dirty = True
            if field in entry:
                self.hits += 1
                return entry[field]

            self.misses += 1
            value = loader()
            if (
                field in {"geometry", "parts"}
                and (not isinstance(value, dict) or value.get("status") != "ok")
            ):
                return value
            entry[field] = value
            self._dirty = True
            return value

    def prune_to_paths(self, paths):
        """Discard removed/renamed files so the on-disk index cannot grow forever."""
        active = {
            os.path.normcase(os.path.abspath(os.fspath(path)))
            for path in paths
        }
        with self._lock:
            if len(active) == len(self._entries) and all(
                path in active for path in self._entries
            ):
                return
            for path in list(self._entries):
                if path not in active:
                    del self._entries[path]
                    self._dirty = True

    def save(self):
        """Atomic best-effort update; cache write failures must not block startup."""
        with self._lock:
            if not self._dirty:
                return
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            temp_path = self.path + "." + uuid.uuid4().hex + ".tmp"
            try:
                with gzip.open(
                    temp_path, "wt", encoding="utf-8", compresslevel=3
                ) as handle:
                    json.dump(
                        {
                            "version": CACHE_SCHEMA_VERSION,
                            "parserRevision": self.parser_revision,
                            "entries": self._entries,
                        },
                        handle, ensure_ascii=False, separators=(",", ":"),
                    )
                os.replace(temp_path, self.path)
                self._dirty = False
            except (OSError, ValueError, TypeError) as exc:
                print(f"[TubeNest STARTUP] Metadata cache save skipped: {exc}", flush=True)
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
