"""pond_profile storage (migration 0008) in MemoryStorage (issue #16);
the SupabaseStorage tests are in test_supabase_storage.py.

Run from Backend/: python -m pytest -q -k profile
"""
import pytest

from koi.storage import DuplicateProfileError, MemoryStorage, StorageError

ROW = {"effective_from": "2026-09-01T06:00:00+00:00", "volume_l": 1200.0, "depth_m": None, "biomass_g": 3000.0,
       "fish_type": "Koi", "fish_count": 4, "tap_tds_ppm": None, "tap_nitrate_ppm": None, "aeration": None}


def test_profile_memory_rows_come_back_in_effective_order():
    storage = MemoryStorage()
    storage.insert_pond_profile(4, {**ROW, "effective_from": "2026-09-03T00:00:00+00:00"})
    storage.insert_pond_profile(4, ROW)
    storage.insert_pond_profile(5, ROW)
    rows = storage.fetch_pond_profiles(4)
    assert [r["effective_from"] for r in rows] == ["2026-09-01T06:00:00+00:00", "2026-09-03T00:00:00+00:00"]
    assert all(r["pond_id"] == 4 and r["source"] == "api" and r["created_at"] for r in rows)
    assert storage.fetch_pond_profiles(6) == []


def test_profile_memory_refuses_a_second_row_at_the_same_instant():
    storage = MemoryStorage()
    storage.insert_pond_profile(4, ROW)
    with pytest.raises(DuplicateProfileError) as info:
        storage.insert_pond_profile(4, {**ROW, "effective_from": "2026-09-01T14:00:00+08:00"})
    assert info.value.operation == "insert_pond_profile" and isinstance(info.value, StorageError)
    storage.insert_pond_profile(5, ROW)  # another pond may share the instant


def test_profile_memory_failures_name_the_operation():
    storage = MemoryStorage()
    storage.failing.add("fetch_pond_profiles")
    with pytest.raises(StorageError, match="fetch_pond_profiles"):
        storage.fetch_pond_profiles(4)
