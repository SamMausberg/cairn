use std::io::{self, Read, Write};
use std::thread;

// Reads the whole of standard input.
fn read_input() -> Vec<u8> {
    let mut bytes = Vec::new();
    io::stdin().read_to_end(&mut bytes).unwrap();
    bytes
}

fn parse_n(bytes: &[u8]) -> usize {
    let text = std::str::from_utf8(bytes).unwrap();
    text.trim().parse::<usize>().unwrap()
}

// Simple single-threaded sieve, used only to find the small primes up to
// sqrt(n) that seed the parallel sieve below.
fn small_primes_up_to(limit: usize) -> Vec<usize> {
    let mut is_composite = vec![false; limit + 1];
    if limit >= 1 {
        is_composite[0] = true;
    }
    if limit >= 1 {
        is_composite[1] = true;
    }
    let mut p = 2usize;
    while p * p <= limit {
        if !is_composite[p] {
            let mut m = p * p;
            while m <= limit {
                is_composite[m] = true;
                m += p;
            }
        }
        p += 1;
    }
    (2..=limit).filter(|&i| !is_composite[i]).collect()
}

// Sieves 2..=n, returning a bool array where index i is true when i is composite.
// The marking of composites across the range is spread over multiple threads,
// each thread owning a disjoint mutable slice of the array.
fn sieve(n: usize) -> Vec<bool> {
    let mut is_composite = vec![false; n + 1];
    if n < 2 {
        return is_composite;
    }
    is_composite[0] = true;
    is_composite[1] = true;

    let mut limit = (n as f64).sqrt() as usize;
    while (limit + 1) * (limit + 1) <= n {
        limit += 1;
    }
    while limit > 0 && limit * limit > n {
        limit -= 1;
    }
    let base_primes = small_primes_up_to(limit);

    let start_idx = 2usize;
    let len = n + 1 - start_idx;

    let available = thread::available_parallelism()
        .map(|v| v.get())
        .unwrap_or(2);
    let nthreads = available.max(2).min(8);
    let chunk_size = (len + nthreads - 1) / nthreads.max(1);
    let chunk_size = chunk_size.max(1);

    let full = &mut is_composite[start_idx..=n];
    thread::scope(|scope| {
        for (i, chunk) in full.chunks_mut(chunk_size).enumerate() {
            let offset = start_idx + i * chunk_size;
            let base_primes = &base_primes;
            scope.spawn(move || {
                let chunk_len = chunk.len();
                for &p in base_primes {
                    let mut v = if p * p > offset {
                        p * p
                    } else {
                        let rem = offset % p;
                        if rem == 0 {
                            offset
                        } else {
                            offset + (p - rem)
                        }
                    };
                    while v < offset + chunk_len {
                        chunk[v - offset] = true;
                        v += p;
                    }
                }
            });
        }
    });

    is_composite
}

fn main() {
    let input = read_input();
    let n = parse_n(&input);

    let is_composite = sieve(n);

    let mut count: u64 = 0;
    let mut sum: u64 = 0;
    let mut last: Option<usize> = None;
    let mut prev: Option<usize> = None;
    let mut max_gap: u64 = 0;
    let mut max_gap_prime: Option<usize> = None;

    if n >= 2 {
        for i in 2..=n {
            if !is_composite[i] {
                count += 1;
                sum += i as u64;
                if let Some(p) = prev {
                    let gap = (i - p) as u64;
                    if gap > max_gap {
                        max_gap = gap;
                        max_gap_prime = Some(p);
                    }
                }
                prev = Some(i);
                last = Some(i);
            }
        }
    }

    let stdout = io::stdout();
    let mut out = stdout.lock();
    writeln!(out, "count {}", count).unwrap();
    writeln!(out, "sum {}", sum).unwrap();
    match last {
        Some(l) => writeln!(out, "last {}", l).unwrap(),
        None => writeln!(out, "last none").unwrap(),
    }
    match max_gap_prime {
        Some(p) => writeln!(out, "gap {} {}", max_gap, p).unwrap(),
        None => writeln!(out, "gap none").unwrap(),
    }
}
