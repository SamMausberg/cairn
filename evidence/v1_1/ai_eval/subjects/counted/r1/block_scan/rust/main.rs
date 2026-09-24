use std::io::{self, Read, Write};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Barrier;
use std::thread;

const BLOCK_SIZE: usize = 256;

fn read_input() -> Vec<u8> {
    let mut bytes = Vec::new();
    io::stdin().read_to_end(&mut bytes).unwrap();
    bytes
}

fn main() {
    let bytes = read_input();
    let text = String::from_utf8_lossy(&bytes);
    let mut it = text.split_ascii_whitespace();

    let n: usize = match it.next() {
        Some(tok) => tok.parse().unwrap(),
        None => 0,
    };

    let values: Vec<u64> = (0..n)
        .map(|_| it.next().unwrap().parse::<u64>().unwrap())
        .collect();

    if n == 0 {
        return;
    }

    let num_blocks = (n + BLOCK_SIZE - 1) / BLOCK_SIZE;
    let max_block = n.min(BLOCK_SIZE);
    // The team shares a scratch array sized for one block. Its size tracks
    // the hardware's parallelism (so a block is never oversubscribed with
    // far more threads than cores), but never drops below two threads when
    // there is more than one element to combine.
    let target_parallelism = thread::available_parallelism()
        .map(|p| p.get())
        .unwrap_or(1)
        .max(2);
    let team_size = target_parallelism.min(max_block).max(1);

    // Per-block scratch: each thread's slice holds that slice's own inclusive
    // scan; a second, tiny array carries each thread's chunk total so they
    // can be turned into per-thread offsets and folded back in.
    let scratch: Vec<AtomicU64> = (0..max_block).map(|_| AtomicU64::new(0)).collect();
    let chunk_totals: Vec<AtomicU64> = (0..team_size).map(|_| AtomicU64::new(0)).collect();
    let chunk_offsets: Vec<AtomicU64> = (0..team_size).map(|_| AtomicU64::new(0)).collect();
    // Holds each block's local scan (not yet offset by earlier blocks).
    let local_scan: Vec<AtomicU64> = (0..n).map(|_| AtomicU64::new(0)).collect();
    let barrier = Barrier::new(team_size);

    let values = &values;
    let scratch = &scratch;
    let chunk_totals = &chunk_totals;
    let chunk_offsets = &chunk_offsets;
    let local_scan = &local_scan;
    let barrier = &barrier;

    thread::scope(|scope| {
        for tid in 0..team_size {
            scope.spawn(move || {
                for b in 0..num_blocks {
                    let start = b * BLOCK_SIZE;
                    let end = (start + BLOCK_SIZE).min(n);
                    let size = end - start;
                    let chunk = (size + team_size - 1) / team_size;
                    let cstart = (tid * chunk).min(size);
                    let cend = (cstart + chunk).min(size);

                    // Step 1: each thread does a plain sequential inclusive
                    // scan of its own chunk (no races: only this thread ever
                    // writes these slots) and records its chunk's total.
                    let mut running: u64 = 0;
                    for idx in cstart..cend {
                        running += values[start + idx];
                        scratch[idx].store(running, Ordering::Relaxed);
                    }
                    chunk_totals[tid].store(running, Ordering::Relaxed);
                    barrier.wait();

                    // Step 2: one thread turns chunk totals into exclusive
                    // offsets for each chunk.
                    if tid == 0 {
                        let mut acc: u64 = 0;
                        for i in 0..team_size {
                            chunk_offsets[i].store(acc, Ordering::Relaxed);
                            acc += chunk_totals[i].load(Ordering::Relaxed);
                        }
                    }
                    barrier.wait();

                    // Step 3: each thread folds its chunk's offset into its
                    // own slots, so every element now holds the block-wide
                    // running total up to itself.
                    let off = chunk_offsets[tid].load(Ordering::Relaxed);
                    for idx in cstart..cend {
                        let total = scratch[idx].load(Ordering::Relaxed) + off;
                        local_scan[start + idx].store(total, Ordering::Relaxed);
                    }
                    // Ensure this block's writes are visible before the next
                    // block's threads reuse the scratch arrays.
                    barrier.wait();
                }
            });
        }
    });

    let mut out = String::with_capacity(n * 12);
    let mut offset: u64 = 0;
    for b in 0..num_blocks {
        let start = b * BLOCK_SIZE;
        let end = (start + BLOCK_SIZE).min(n);
        for v in &local_scan[start..end] {
            let total = offset + v.load(Ordering::Relaxed);
            out.push_str(&total.to_string());
            out.push('\n');
        }
        offset += local_scan[end - 1].load(Ordering::Relaxed);
    }
    io::stdout().write_all(out.as_bytes()).unwrap();
}
