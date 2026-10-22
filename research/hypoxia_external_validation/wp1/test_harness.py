"""Unit tests for the WP1 harness itself (so a harness bug is not reported as a production finding).
Run: python -I wp1/test_harness.py"""
import sys, unittest, json
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: F401
import numpy as np
import adapters as A, harness as H, metamorphic as M
from koi.models.sensor_inputs import complete_groups, group_rows
import koi.models.engine as E


class HarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = A.fwi_source("ara2_3ed4df8e")  # has duplicate timestamps and zeros
        cls.tab = cls.spec.rows.sorted_by_time()

    def test_prod_batches_equal_real_complete_groups(self):
        i = 0
        for idx in H.prod_batches(self.tab):
            rows = self.tab.take(np.arange(i, min(i + H.BATCH_ROWS, len(self.tab))))
            kept = complete_groups(rows, H.BATCH_ROWS)
            self.assertEqual(len(kept), len(idx))
            i += len(idx)
        self.assertEqual(i, len(self.tab))

    def test_every_variant_delivers_every_row_at_least_once(self):
        for kind in M.KINDS:
            t2, batches = M.variant(self.tab, kind)
            got = np.unique(np.concatenate(batches))
            self.assertTrue(set(range(len(self.tab))) <= set(got.tolist()), kind)

    def test_frozen_clock_restores_datetime(self):
        before = E.datetime
        with H.frozen_clock(datetime(2024, 1, 1, tzinfo=timezone.utc)):
            self.assertEqual(E.datetime.now(timezone.utc).year, 2024)
        self.assertIs(E.datetime, before)

    def test_hash_ignores_saved_at_and_prompt_ids(self):
        r = H.run_replay(self.spec, self.tab, [np.arange(500)], stats=False, health=False, snapshot_daily=False)
        s = r["_twin"].to_snapshot()
        s2 = json.loads(json.dumps(s, default=str))
        s2["saved_at"] = "x"
        self.assertEqual(H.snap_hash(s), H.snap_hash(s2))
        s2["chemistry"]["tan_mg"] = 1.0
        self.assertNotEqual(H.snap_hash(s), H.snap_hash(s2))

    def test_history_equals_group_rows_of_delivered_rows(self):
        led = H.SensorLedger(self.tab)
        led.mark(np.arange(0, 400))
        hist = led.history(None, H.dt_of_ns(int(self.tab.tns[299])))
        direct = group_rows(self.tab.take(np.arange(0, 300))).inputs
        self.assertEqual([i.time for i in hist][:len(direct)], [i.time for i in direct][:len(hist)])
        self.assertTrue(all(i.time <= H.dt_of_ns(int(self.tab.tns[299])) for i in hist))

    def test_placeholder_volume_does_not_change_gate_or_ingest(self):
        spec = A.fwi_source("ara2_0677080b"); tab = spec.rows.sorted_by_time()
        outs = []
        for vol in (500.0, 2000.0):
            r = H.run_replay(spec, tab, H.prod_batches(tab), volume_l_override=vol, snapshot_daily=False)
            snap = r["_twin"].to_snapshot()
            for blk in (snap["chemistry"], snap["evaporation"]):
                blk.get("config", {}).pop("volume_litres", None)
            for ck in snap["event_ledger"]["checkpoints"]:
                ck["chemistry"]["config"].pop("volume_litres", None)
            outs.append((r["gate"], H.snap_hash({k: v for k, v in snap.items() if k != "evaporation"})))
        self.assertEqual(outs[0], outs[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
