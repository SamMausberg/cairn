use std::io::{self, Read};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Barrier;
use std::thread;

const BLOCK_SIZE: usize = 256;

// Reads the whole of standard input.
fn read_input() -> Vec<u8> {
    let mut bytes = Vec::new();
    io::stdin().read_to_end(&mut bytes).unwrap();
    bytes
}

// The doubling offsets used by a Hillis-Steele inclusive scan of a block of
// length m: 1, 2, 4, ... up to (but excluding) m. Depends only on m, so every
// lane in the team computes the same sequence independently.
fn scan_offsets(m: usize) -> Vec<usize> {
    let mut offsets = Vec::new();
    let mut off = 1usize;
    while off < m {
        offsets.push(off);
        off *= 2;
    }
    offsets
}

fn main() {
    let input = read_input();
    let text = String::from_utf8(input).unwrap();
    let mut nums = text.split_ascii_whitespace().map(|s| s.parse::<u64>().unwrap());

    let n = nums.next().unwrap_or(0) as usize;
    let values: Vec<u64> = (0..n).map(|_| nums.next().unwrap()).collect();

    if n == 0 {
        return;
    }

    let num_blocks = (n + BLOCK_SIZE - 1) / BLOCK_SIZE;
    // Team size: enough lanes to cover a block's elements, but capped to the
    // machine's parallelism so the barrier isn't shared by far more threads
    // than can actually run at once. Lanes are reused for every block
    // instead of respawning threads each time; a lane covers several
    // elements per step when the team is smaller than the block.
    let cpus = thread::available_parallelism().map(|p| p.get()).unwrap_or(1);
    let team_size = std::cmp::min(BLOCK_SIZE, n).min(cpus.max(1));

    let results: Vec<AtomicU64> = (0..n).map(|_| AtomicU64::new(0)).collect();
    // Two scratch buffers the whole team shares, ping-ponged between scan
    // steps so a step's reads never race with that same step's writes.
    let buf_a: Vec<AtomicU64> = (0..BLOCK_SIZE).map(|_| AtomicU64::new(0)).collect();
    let buf_b: Vec<AtomicU64> = (0..BLOCK_SIZE).map(|_| AtomicU64::new(0)).collect();
    let barrier = Barrier::new(team_size);
    let running_offset = AtomicU64::new(0);

    let values_ref = &values;
    let results_ref = &results;
    let buf_a_ref = &buf_a;
    let buf_b_ref = &buf_b;
    let barrier_ref = &barrier;
    let running_offset_ref = &running_offset;

    thread::scope(|s| {
        for lane in 0..team_size {
            s.spawn(move || {
                for block_idx in 0..num_blocks {
                    let start = block_idx * BLOCK_SIZE;
                    let end = std::cmp::min(start + BLOCK_SIZE, n);
                    let m = end - start;

                    // Load this block's values into the shared scratch array.
                    let mut idx = lane;
                    while idx < m {
                        buf_a_ref[idx].store(values_ref[start + idx], Ordering::SeqCst);
                        idx += team_size;
                    }
                    barrier_ref.wait();

                    let offsets = scan_offsets(m);
                    for (step, &off) in offsets.iter().enumerate() {
                        let (read_buf, write_buf) = if step % 2 == 0 {
                            (buf_a_ref, buf_b_ref)
                        } else {
                            (buf_b_ref, buf_a_ref)
                        };
                        let mut idx = lane;
                        while idx < m {
                            let own = read_buf[idx].load(Ordering::SeqCst);
                            let new_val = if idx >= off {
                                own + read_buf[idx - off].load(Ordering::SeqCst)
                            } else {
                                own
                            };
                            write_buf[idx].store(new_val, Ordering::SeqCst);
                            idx += team_size;
                        }
                        barrier_ref.wait();
                    }

                    let result_buf = if offsets.len() % 2 == 0 {
                        buf_a_ref
                    } else {
                        buf_b_ref
                    };

                    // Offset each element by the total of all earlier blocks.
                    let offset_val = running_offset_ref.load(Ordering::SeqCst);
                    let mut idx = lane;
                    while idx < m {
                        let scanned = result_buf[idx].load(Ordering::SeqCst);
                        results_ref[start + idx].store(scanned + offset_val, Ordering::SeqCst);
                        idx += team_size;
                    }
                    barrier_ref.wait();

                    // One lane folds this block's total into the running
                    // offset for the next block, only after every lane has
                    // already read (and used) the old value above.
                    if lane == 0 {
                        let block_total = result_buf[m - 1].load(Ordering::SeqCst);
                        running_offset_ref.fetch_add(block_total, Ordering::SeqCst);
                    }
                    barrier_ref.wait();
                }
            });
        }
    });

    let mut out = String::with_capacity(n * 12);
    for r in &results {
        out.push_str(&r.load(Ordering::SeqCst).to_string());
        out.push('\n');
    }
    print!("{}", out);
}
