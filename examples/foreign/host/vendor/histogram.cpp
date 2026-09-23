// A 256-bin histogram of the low byte of each sample. Four tables take every fourth sample, so a run of
// equal bytes does not queue on one counter's store and reload, and the four are summed at the end. The
// entry has C linkage and takes the counts first, then the samples, as a C caller passes them.
#include <cstddef>
#include <cstdint>

extern "C" void histogram_u32_interleaved(std::size_t n, std::uint64_t* out, const std::uint32_t* x) {
  std::uint64_t table[4][256] = {};
  std::size_t i = 0;
  for (; i + 4 <= n; i += 4) {
    ++table[0][x[i] & 255u];
    ++table[1][x[i + 1] & 255u];
    ++table[2][x[i + 2] & 255u];
    ++table[3][x[i + 3] & 255u];
  }
  for (; i < n; ++i) ++table[0][x[i] & 255u];
  for (int b = 0; b < 256; ++b) out[b] = table[0][b] + table[1][b] + table[2][b] + table[3][b];
}
