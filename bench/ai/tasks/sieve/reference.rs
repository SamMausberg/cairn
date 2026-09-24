// Primes up to N by a segmented sieve: each thread sieves its own segment in storage of its own.
use std::io::{self, Read};
use std::thread;

#[derive(Clone, Copy, Default)]
struct Segment {
    count: u64,
    sum: u64,
    first: u64,
    last: u64,
    gap: u64,
    gap_at: u64,
}

// The primes in [lo, hi), sieved by the base primes up to the square root of the bound.
fn sieve_segment(lo: u64, hi: u64, base: &[u64]) -> Segment {
    let mut s = Segment::default();
    if lo >= hi {
        return s;
    }
    let mut composite = vec![false; (hi - lo) as usize];
    for &p in base {
        if p * p >= hi {
            break;
        }
        let mut m = (p * p).max((lo + p - 1) / p * p);
        while m < hi {
            composite[(m - lo) as usize] = true;
            m += p;
        }
    }
    for v in lo.max(2)..hi {
        if composite[(v - lo) as usize] {
            continue;
        }
        if s.count == 0 {
            s.first = v;
        } else if v - s.last > s.gap {
            s.gap = v - s.last;
            s.gap_at = s.last;
        }
        s.last = v;
        s.count += 1;
        s.sum += v;
    }
    s
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let n: u64 = input.trim().parse().unwrap();
    let mut root = 1u64;
    while (root + 1) * (root + 1) <= n {
        root += 1;
    }
    let mut small = vec![false; root as usize + 1];
    let mut base = Vec::new();
    for p in 2..=root {
        if small[p as usize] {
            continue;
        }
        base.push(p);
        let mut m = p * p;
        while m <= root {
            small[m as usize] = true;
            m += p;
        }
    }

    const WORKERS: u64 = 8;
    let parts: Vec<Segment> = thread::scope(|s| {
        let handles: Vec<_> = (0..WORKERS)
            .map(|w| {
                let (lo, hi) = ((n + 1) * w / WORKERS, (n + 1) * (w + 1) / WORKERS);
                let base = &base;
                s.spawn(move || sieve_segment(lo, hi, base))
            })
            .collect();
        handles.into_iter().map(|h| h.join().unwrap()).collect()
    });

    let mut all = Segment::default();
    for s in parts.iter().filter(|s| s.count > 0) {
        if all.count != 0 && s.first - all.last > all.gap {
            all.gap = s.first - all.last;
            all.gap_at = all.last;
        }
        if s.gap > all.gap {
            all.gap = s.gap;
            all.gap_at = s.gap_at;
        }
        all.last = s.last;
        all.count += s.count;
        all.sum += s.sum;
    }
    println!("count {}\nsum {}", all.count, all.sum);
    if all.count == 0 {
        println!("last none");
    } else {
        println!("last {}", all.last);
    }
    if all.count < 2 {
        println!("gap none");
    } else {
        println!("gap {} {}", all.gap, all.gap_at);
    }
}
