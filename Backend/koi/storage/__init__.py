"""All database and file I/O for the backend, behind one interface.

    storage = build_storage(settings)   # SupabaseStorage or MemoryStorage

See koi/storage/base.py for the Storage interface and StorageError.
"""
from __future__ import annotations

from koi.settings import Settings
from koi.storage.base import StaleSnapshotError, Storage, StorageError, fail_soft
from koi.storage.memory import MemoryStorage
from koi.storage.supabase_storage import SupabaseStorage


def build_storage(settings: Settings) -> Storage:
    """The storage settings.storage selects: "supabase" (the live project)
    or "memory" (empty in-process tables)."""
    if settings.storage == "memory":
        return MemoryStorage()
    return SupabaseStorage(settings)


__all__ = ["MemoryStorage", "StaleSnapshotError", "Storage", "StorageError", "SupabaseStorage", "build_storage",
           "fail_soft"]
