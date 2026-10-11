#pragma once
// Issue #12: reading the reply to the sensor node's upload,
// POST /rest/v1/rpc/ingest_readings (supabase/migrations/0025_device_tokens.sql).
// A 200 reply is {"accepted": n, "rejected": [...], "format": "v1", ...};
// any other status means nothing was stored. Kept free of the Arduino core
// so the host tests can build it.
#include <stdlib.h>
#include <string.h>

// True when the call stored the batch (HTTP 200 with an "accepted" count);
// accepted is then the number of readings stored. Rejected readings are in
// the reply's "rejected" list and are not retried.
inline bool ingestAccepted(int httpStatus, const char* body, int& accepted) {
  if (httpStatus != 200 || body == nullptr) return false;
  const char* p = strstr(body, "\"accepted\"");
  if (p == nullptr) return false;
  p += strlen("\"accepted\"");
  while (*p == ' ' || *p == ':') p++;
  char* end = nullptr;
  long n = strtol(p, &end, 10);
  if (end == p || n < 0) return false;
  accepted = (int)n;
  return true;
}

// What the owner should check, for the serial log.
inline const char* ingestStatusText(int httpStatus) {
  switch (httpStatus) {
    case 200: return "stored";
    case 400: return "batch refused (a field other than sensor_type/data1, or not a list)";
    case 401: return "device token refused (check SENSOR_DEVICE_TOKEN in secrets.h; it may be revoked or expired)";
    case 404: return "ingest_readings not found (migration 0025 not applied)";
    case 413: return "too many readings in one batch";
    case 429: return "rate limited (more than 6 uploads a minute)";
    default:  return httpStatus < 0 ? "no connection" : "upload failed";
  }
}
