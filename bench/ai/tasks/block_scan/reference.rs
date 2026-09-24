// Running totals as a GPU computes them: a team of 256 threads scans each block of 256 values in a shared array,
// meeting at a barrier around every step, and the block offsets are added after the team is done.
use std::fmt::Write as _;
use std::io::{self, Read, Write};
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};
use std::sync::Barrier;
use std::thread;

const BLOCK: usize = 256;

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut tokens = input.split_ascii_whitespace().map(|t| t.parse::<u64>().unwrap());
    let n = tokens.next().unwrap() as usize;
    let x: Vec<u64> = tokens.take(n).collect();

    let blocks = n.div_ceil(BLOCK);
    // The team's shared array and results; the barriers order every access, so relaxed atomics suffice.
    let shared: Vec<AtomicU64> = (0..BLOCK).map(|_| AtomicU64::new(0)).collect();
    let out: Vec<AtomicU64> = (0..n).map(|_| AtomicU64::new(0)).collect();
    let totals: Vec<AtomicU64> = (0..blocks).map(|_| AtomicU64::new(0)).collect();
    let team = Barrier::new(BLOCK);
    thread::scope(|s| {
        for t in 0..BLOCK {
            let (shared, out, totals, team, x) = (&shared, &out, &totals, &team, &x);
            s.spawn(move || {
                for b in 0..blocks {
                    let i = b * BLOCK + t;
                    shared[t].store(if i < n { x[i] } else { 0 }, Relaxed);
                    team.wait();
                    let mut d = 1;
                    while d < BLOCK {
                        let v = if t >= d { shared[t - d].load(Relaxed) } else { 0 };
                        team.wait(); // every thread has read before any writes
                        shared[t].store(shared[t].load(Relaxed) + v, Relaxed);
                        team.wait(); // every thread has written before the next step reads
                        d *= 2;
                    }
                    if i < n {
                        out[i].store(shared[t].load(Relaxed), Relaxed);
                    }
                    if t == BLOCK - 1 {
                        totals[b].store(shared[t].load(Relaxed), Relaxed);
                    }
                    team.wait(); // the block is out before the next one overwrites the shared array
                }
            });
        }
    });

    let mut text = String::new();
    let mut before = 0u64;
    for b in 0..blocks {
        for i in b * BLOCK..n.min((b + 1) * BLOCK) {
            writeln!(text, "{}", out[i].load(Relaxed) + before).unwrap();
        }
        before += totals[b].load(Relaxed);
    }
    io::stdout().write_all(text.as_bytes()).unwrap();
}
