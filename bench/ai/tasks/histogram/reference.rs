use std::io::{self, Read, Write};
use std::thread;

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut tokens = input.split_ascii_whitespace().map(|t| t.parse::<u64>().unwrap());
    let n = tokens.next().unwrap() as usize;
    let shift = tokens.next().unwrap() as u32;
    let values: Vec<u32> = tokens.take(n).map(|v| v as u32).collect();

    // Each worker counts its own contiguous part into a row it returns; the rows are added after the joins.
    const WORKERS: usize = 4;
    let rows: Vec<[u64; 256]> = thread::scope(|s| {
        let handles: Vec<_> = (0..WORKERS)
            .map(|w| {
                let part = &values[n * w / WORKERS..n * (w + 1) / WORKERS];
                s.spawn(move || {
                    let mut counts = [0u64; 256];
                    for &v in part {
                        counts[((v >> shift) & 255) as usize] += 1;
                    }
                    counts
                })
            })
            .collect();
        handles.into_iter().map(|h| h.join().unwrap()).collect()
    });

    let mut out = io::stdout().lock();
    for b in 0..256 {
        let total: u64 = rows.iter().map(|r| r[b]).sum();
        if total != 0 {
            writeln!(out, "{} {}", b, total).unwrap();
        }
    }
}
