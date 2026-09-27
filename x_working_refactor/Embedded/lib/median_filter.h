#pragma once
// Extracted from Embedded/FullSketch/FullSketch.ino: readMedianMv()'s core
// (the sampling loop needs analogReadMilliVolts(), so only the sort+pick is here).

// In-place insertion sort, returns the median of n samples.
// n is expected small (FullSketch uses 31), so O(n^2) is not a real cost:
// worst case ~31*31 = 961 compares, sub-microsecond territory on an ESP32.
inline int medianOfSamples(int *buf, int n) {
  for (int i = 1; i < n; i++) {
    int k = buf[i], j = i - 1;
    while (j >= 0 && buf[j] > k) { buf[j + 1] = buf[j]; j--; }
    buf[j + 1] = k;
  }
  return buf[n / 2];
}
