"""Unit tests for wp1/adapters.py. Run: python -I wp1/test_adapters.py"""
import sys, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: F401
import pandas as pd
import adapters as A
from koi.models.sensor_inputs import group_rows, CHANNELS

UTC = timezone.utc


class Synthetic(unittest.TestCase):
    def test_fwi_ist_to_utc_nan_absent_zero_policy(self):
        d = pd.DataFrame({"ts": ["2025-12-14 08:00:00", "2025-12-14 08:15:00", "2025-12-14 08:30:00"],
                          "do": [5.0, 0.0, 4.0], "ph": [8.5, 0.0, float("nan")], "temp": [25.0, 25.1, 0.0],
                          "qc_dt": None, "qc_do": None, "qc_ph": None})
        d["t_local"] = pd.to_datetime(d["ts"]); d["src_index"] = range(3)
        keep = A.fwi_source("x", "keep", frame=d)
        self.assertEqual(sorted({r["sensor_type"] for r in keep.rows}), ["pH", "temp"])  # no DO, no lux, no TDS
        self.assertEqual(len(keep.rows), 5)  # ph NaN absent
        first = min(keep.rows, key=lambda r: r["created_at"])
        self.assertEqual(first["created_at"], datetime(2025, 12, 14, 2, 30, tzinfo=UTC))  # 08:00 IST
        self.assertEqual(first["created_at"].utcoffset(), timedelta(0))
        self.assertEqual(first["sampled_at"], first["created_at"])
        ab = A.fwi_source("x", "absent", frame=d)
        self.assertEqual(len(ab.rows), 3)  # ph 0, temp 0 removed too (+ph NaN)
        self.assertEqual(ab.meta["zero_values_in_source"], {"ph": 1, "temp": 1})
        ids = [r["id"] for r in keep.rows]
        self.assertEqual(len(ids), len(set(ids)))

    def test_group_rows_sees_missing_lux_and_sample_basis(self):
        d = pd.DataFrame({"ts": ["2025-12-14 08:00:00"], "do": [5.0], "ph": [8.5], "temp": [25.0],
                          "qc_dt": None, "qc_do": None, "qc_ph": None})
        d["t_local"] = pd.to_datetime(d["ts"]); d["src_index"] = range(1)
        inp = group_rows(A.fwi_source("x", frame=d).rows).inputs
        self.assertEqual(len(inp), 1)
        self.assertEqual(inp[0].time_basis, "sample")
        self.assertIsNone(inp[0].channels["lux"]); self.assertIsNone(inp[0].channels["tds"])
        self.assertEqual(inp[0].missing, ["tds", "lux"])

    def test_oman_gmt4_and_5min_selection_is_a_real_row(self):
        d = pd.DataFrame({"timestamp": ["2026-04-01T13:57:04.5", "2026-04-01T13:58:10.0", "2026-04-01T14:01:00.0"],
                          "temp_c": [28.1, 28.2, 28.3], "ph": [8.0, 8.1, 8.2], "do_mgL": [7, 7, 7]})
        d["t_local"] = pd.to_datetime(d["timestamp"], format="ISO8601"); d["src_index"] = range(3)
        nat = A.oman_source("tank", "native", frame=d)
        self.assertEqual(min(r["created_at"] for r in nat.rows), datetime(2026, 4, 1, 9, 57, 4, 500000, tzinfo=UTC))
        g = A.oman_source("tank", "5min", frame=d)
        vals = sorted((r["sensor_type"], r["value"]) for r in g.rows)
        self.assertEqual(vals, [("pH", 8.1), ("pH", 8.2), ("temp", 28.2), ("temp", 28.3)])  # last of each bin; no mean
        self.assertEqual(nat.volume_l, 180.0)
        self.assertIsNone(A.oman_source("open", "native", frame=d).volume_l)

    def test_idn_timezone_assumptions(self):
        d = pd.DataFrame({"id": [1, 2], "created_date": ["1/26/2023 11:24", "1/26/2023 11:24"],
                          "water_pH": [7.6, 7.7], "TDS": [250, 251], "water_temp": [24.0, 24.1]})
        d["t_local"] = pd.to_datetime(d["created_date"], format="%m/%d/%Y %H:%M")
        jk = A.idn_source("raw", "jakarta", frame=d); ut = A.idn_source("raw", "utc", frame=d)
        self.assertEqual(jk.rows[0]["created_at"], datetime(2023, 1, 26, 4, 24, tzinfo=UTC))
        self.assertEqual(ut.rows[0]["created_at"], datetime(2023, 1, 26, 11, 24, tzinfo=UTC))
        self.assertEqual(jk.volume_l, 700.0)
        g = group_rows(jk.rows)
        self.assertEqual(len(g.inputs), 1)  # both source rows share the minute
        self.assertEqual(g.inputs[0].channels["ph"], 7.7)  # highest id wins
        self.assertEqual(sum(1 for l in g.ledger if l["disposition"] == "superseded"), 3)
        self.assertEqual(set(CHANNELS) - {c for c, v in g.inputs[0].channels.items() if v is not None}, {"lux"})


class RealData(unittest.TestCase):
    def test_every_source_is_aware_utc_without_do_or_lux(self):
        for spec in [A.fwi_source(A.fwi_ponds()[0]), A.oman_source("tank", "5min"), A.idn_source("clean")]:
            types = {r["sensor_type"] for r in spec.rows}
            self.assertLessEqual(types, {"pH", "temp", "TDS"}, spec.name)
            for r in spec.rows[:2000]:
                self.assertEqual(r["created_at"].utcoffset(), timedelta(0)); self.assertIs(r["created_at"], r["sampled_at"])
            self.assertEqual(len({r["id"] for r in spec.rows}), len(spec.rows))

    def test_fwi_row_count_matches_non_nan_cells(self):
        p = A.fwi_ponds()[0]; d = A.load_fwi_frame(p)
        spec = A.fwi_source(p, frame=d)
        self.assertEqual(len(spec.rows), int(d["ph"].notna().sum() + d["temp"].notna().sum()))

    def test_oman_events_have_no_invented_quantity(self):
        ev = A.oman_tank_events()
        self.assertTrue(ev)
        for e in ev:
            if e["event_type"] == "FEEDING":
                self.assertIsNone(e["food_grams"]); self.assertIsNone(e["protein_percentage"])
            self.assertLess(e["created_at"], e["event_timestamp"] + timedelta(days=1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
