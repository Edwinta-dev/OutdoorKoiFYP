#pragma once
// Issue #44: store-and-forward for the sensor node's failed uploads.
// Kept free of the Arduino core so the host tests can build it.
//
// Two rings, oldest cycles in the second:
//   - PendingCycles (sample_cycle.h) in RTC memory, PENDING_CYCLE_CAPACITY
//     cycles. Survives deep sleep, lost on power loss or reset.
//   - A spill ring in NVS, SPILL_CAPACITY cycles, one blob per slot plus a
//     small header blob. A cycle reaches it only when the RTC ring is full
//     and a newer cycle arrives, so flash is written only during an outage
//     longer than the RTC ring. It survives power loss.
// Every cycle in the spill is older than every cycle in the RTC ring, so
// draining the spill first and then the RTC ring sends oldest first.
//
// Crash safety: the header is written only after the slot it points at, and
// a slot is reused only after the header has stopped pointing at it, so the
// slots between head and head + count are always whole cycles. A power loss
// between the two writes loses at most the cycle being written. A batch that
// was stored but not yet removed is sent again; the database's unique index
// on (device, boot_id, cycle_seq, sensor_type) stores it once.
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "sample_cycle.h"

// ingest_readings' c_max_rows (supabase/migrations/0028_battery_low.sql);
// a bigger batch is refused whole with 413.
const int INGEST_MAX_ROWS = 32;
// Every cycle carries at least TDS and pH, so 32 rows hold at most 16 cycles.
const int UPLOAD_MAX_CYCLES = 16;
const int SPILL_CAPACITY = 24;

const uint16_t SPILL_MAGIC = 0x4B53;   // "KS"
const uint8_t  SPILL_VERSION = 1;
const char* const SPILL_META_KEY = "meta";

// The NVS blobs, behind an interface so the host tests can use memory.
class SpillStorage {
 public:
  virtual ~SpillStorage() {}
  // False when the key is missing or holds a different number of bytes.
  virtual bool read(const char* key, void* out, size_t len) = 0;
  virtual bool write(const char* key, const void* data, size_t len) = 0;
};

// The header blob. capacity and recordSize tie stored slots to this build's
// layout: a header from another layout reads as an empty spill.
struct SpillMeta {
  uint16_t magic;
  uint8_t  version;
  uint8_t  head;       // slot of the oldest spilled cycle
  uint8_t  count;
  uint8_t  capacity;
  uint16_t recordSize;
};

inline SpillMeta spillEmpty() {
  SpillMeta m;
  memset(&m, 0, sizeof m);
  m.magic = SPILL_MAGIC;
  m.version = SPILL_VERSION;
  m.capacity = (uint8_t)SPILL_CAPACITY;
  m.recordSize = (uint16_t)sizeof(PendingCycle);
  return m;
}

inline void spillSlotKey(int slot, char out[8]) { snprintf(out, 8, "c%d", slot); }

inline SpillMeta spillLoad(SpillStorage& s) {
  SpillMeta m;
  if (!s.read(SPILL_META_KEY, &m, sizeof m) || m.magic != SPILL_MAGIC || m.version != SPILL_VERSION ||
      m.capacity != SPILL_CAPACITY || m.recordSize != sizeof(PendingCycle) ||
      m.head >= SPILL_CAPACITY || m.count > SPILL_CAPACITY) {
    return spillEmpty();
  }
  return m;
}

// Writes the header; m changes only when the write succeeded.
inline bool spillSaveMeta(SpillStorage& s, SpillMeta& m, uint8_t head, uint8_t count) {
  SpillMeta next = m;
  next.head = head;
  next.count = count;
  if (!s.write(SPILL_META_KEY, &next, sizeof next)) return false;
  m = next;
  return true;
}

// The i-th oldest spilled cycle, 0 <= i < m.count. False when the slot is
// missing or the wrong size.
inline bool spillRead(SpillStorage& s, const SpillMeta& m, int i, PendingCycle& out) {
  char key[8];
  spillSlotKey((m.head + i) % SPILL_CAPACITY, key);
  return s.read(key, &out, sizeof out);
}

enum class SpillAppend : uint8_t { Stored, StoredDroppedOldest, Failed };

// Appends c as the newest spilled cycle. When the spill is full the oldest
// is released first (header written), then the slot, then the header again.
inline SpillAppend spillAppend(SpillStorage& s, SpillMeta& m, const PendingCycle& c) {
  bool dropped = false;
  if (m.count >= SPILL_CAPACITY) {
    if (!spillSaveMeta(s, m, (uint8_t)((m.head + 1) % SPILL_CAPACITY), (uint8_t)(m.count - 1)))
      return SpillAppend::Failed;
    dropped = true;
  }
  char key[8];
  spillSlotKey((m.head + m.count) % SPILL_CAPACITY, key);
  if (!s.write(key, &c, sizeof c)) return SpillAppend::Failed;
  if (!spillSaveMeta(s, m, m.head, (uint8_t)(m.count + 1))) return SpillAppend::Failed;
  return dropped ? SpillAppend::StoredDroppedOldest : SpillAppend::Stored;
}

enum class BufferPush : uint8_t {
  Kept,                 // room in RTC memory
  Spilled,              // the oldest RTC cycle moved to NVS
  SpilledDroppedOldest, // ... and the NVS ring was full: its oldest is gone
  DroppedOldest         // the NVS write failed: the oldest RTC cycle is gone
};

// Adds a new cycle to the RTC ring, moving the oldest to NVS when full.
inline BufferPush bufferPush(PendingCycles& rtc, SpillStorage& s, SpillMeta& m, const PendingCycle& c) {
  if (rtc.count < PENDING_CYCLE_CAPACITY) {
    pendingPush(rtc, c);
    return BufferPush::Kept;
  }
  SpillAppend r = spillAppend(s, m, pendingAt(rtc, 0));
  pendingPush(rtc, c);   // drops the oldest, now in NVS (or lost)
  if (r == SpillAppend::Stored) return BufferPush::Spilled;
  if (r == SpillAppend::StoredDroppedOldest) return BufferPush::SpilledDroppedOldest;
  return BufferPush::DroppedOldest;
}

inline int bufferedCycles(const PendingCycles& rtc, const SpillMeta& m) { return rtc.count + m.count; }

// One upload request: the oldest cycles that fit in maxRows readings.
struct UploadBatch {
  PendingCycle cycles[UPLOAD_MAX_CYCLES];
  int nCycles;
  int rows;
  int fromSpill;   // spill slots used, including unreadable ones (skipped)
  int fromRtc;     // RTC cycles used
};

inline bool batchTake(UploadBatch& b, const PendingCycle& c, int maxRows) {
  if (b.nCycles >= UPLOAD_MAX_CYCLES) return false;
  if (b.nCycles > 0 && b.rows + c.nReadings > maxRows) return false;
  b.cycles[b.nCycles++] = c;
  b.rows += c.nReadings;
  return true;
}

// Fills b with the oldest buffered cycles: spill first, then RTC. Removes
// nothing; batchCommit does that once the outcome is known.
inline void batchPlan(const PendingCycles& rtc, SpillStorage& s, const SpillMeta& m, UploadBatch& b,
                      int maxRows = INGEST_MAX_ROWS) {
  b.nCycles = 0; b.rows = 0; b.fromSpill = 0; b.fromRtc = 0;
  for (int i = 0; i < m.count; i++) {
    PendingCycle c;
    if (!spillRead(s, m, i, c)) { b.fromSpill++; continue; }
    if (!batchTake(b, c, maxRows)) return;
    b.fromSpill++;
  }
  for (int i = 0; i < rtc.count; i++) {
    if (!batchTake(b, pendingAt(rtc, i), maxRows)) return;
    b.fromRtc++;
  }
}

inline bool batchEmpty(const UploadBatch& b) { return b.fromSpill == 0 && b.fromRtc == 0; }

// What to do with a batch after its upload: Stored and Refused remove it
// (Refused also ends this wake's uploads), Retry keeps everything.
enum class BatchOutcome : uint8_t { Stored, Retry, Refused };

inline BatchOutcome batchOutcome(bool stored, int httpStatus) {
  if (stored) return BatchOutcome::Stored;
  return ingestShouldRetry(httpStatus) ? BatchOutcome::Retry : BatchOutcome::Refused;
}

// Removes the batch's cycles (stored, or refused for good). False when the
// NVS header could not be written: those spilled cycles are sent again.
inline bool batchCommit(PendingCycles& rtc, SpillStorage& s, SpillMeta& m, const UploadBatch& b) {
  bool ok = true;
  if (b.fromSpill > 0) {
    uint8_t n = (uint8_t)(b.fromSpill > m.count ? m.count : b.fromSpill);
    ok = spillSaveMeta(s, m, (uint8_t)((m.head + n) % SPILL_CAPACITY), (uint8_t)(m.count - n));
  }
  int n = b.fromRtc > rtc.count ? rtc.count : b.fromRtc;
  rtc.head = (uint8_t)((rtc.head + n) % PENDING_CYCLE_CAPACITY);
  rtc.count = (uint8_t)(rtc.count - n);
  return ok;
}
