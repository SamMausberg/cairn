// Adds n unsigned 64-bit values modulo 2^64, one contiguous chunk per thread.
use std::io::{self, Read};
use std::thread;

fn chunk_sum(values: &[u64]) -> u64 {
    values.iter().fold(0u64, |t, &v| t.wrapping_add(v))
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut tokens = input.split_ascii_whitespace();
    let k: usize = tokens.next().unwrap().parse().unwrap();
    let n: usize = tokens.next().unwrap().parse().unwrap();
    let values: Vec<u64> = tokens.take(n).map(|t| t.parse().unwrap()).collect();

    let size = n / k;
    let partial: Vec<u64> = thread::scope(|s| {
        let handles: Vec<_> = (0..k)
            .map(|j| {
                let lo = j * size;
                let hi = if j + 1 == k { n } else { lo + size };
                let chunk = &values[lo..hi];
                s.spawn(move || chunk_sum(chunk))
            })
            .collect();
        handles.into_iter().map(|h| h.join().unwrap()).collect()
    });

    let total = partial.iter().fold(0u64, |t, &p| t.wrapping_add(p));
    println!("sum {}", total);
}
