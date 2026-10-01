#pragma once
// Extracted from Embedded/camera_node/camera_node.ino:
// wifiFailSleep() and the constrain() call in uploadPhoto(). This is the
// sketch's Wi-Fi-down battery-saving backoff - pulled out so it can be reused
// by other network-connected sketches (see Round 2/3 notes) instead of each
// one re-deriving its own retry schedule.

// Retry sooner after a one-off Wi-Fi blip; back off once the outage persists.
inline unsigned long wifiFailSleepSeconds(unsigned long streak, unsigned long defaultSec = 1800) {
  if (streak <= 1) return 300;   // 5 min
  if (streak == 2) return 900;   // 15 min
  return defaultSec;             // router likely down for a while: save battery
}

inline unsigned long clampSleepSeconds(unsigned long s, unsigned long lo, unsigned long hi) {
  if (s < lo) return lo;
  if (s > hi) return hi;
  return s;
}
