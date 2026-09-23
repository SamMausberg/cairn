use std::io::{self, Read, Write};
use std::thread;

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut tokens = input.split_ascii_whitespace();
    let k: usize = tokens.next().unwrap().parse().unwrap();
    let n: usize = tokens.next().unwrap().parse().unwrap();
    let values: Vec<i64> = tokens.take(n).map(|t| t.parse().unwrap()).collect();

    // Each chunk's left-to-right sum, or None when a partial sum leaves the i64 range.
    let sums: Vec<Option<i64>> = thread::scope(|s| {
        let handles: Vec<_> = (0..k)
            .map(|j| {
                let chunk = &values[j * n / k..(j + 1) * n / k];
                s.spawn(move || chunk.iter().try_fold(0i64, |acc, &v| acc.checked_add(v)))
            })
            .collect();
        handles.into_iter().map(|h| h.join().unwrap()).collect()
    });

    let mut out = io::stdout().lock();
    let mut total = Some(0i64);
    for (j, sum) in sums.iter().enumerate() {
        match sum {
            Some(v) => writeln!(out, "chunk {} {}", j, v).unwrap(),
            None => writeln!(out, "chunk {} overflow", j).unwrap(),
        }
        total = match (total, sum) {
            (Some(t), Some(v)) => t.checked_add(*v),
            _ => None,
        };
    }
    match total {
        Some(t) => writeln!(out, "total {}", t).unwrap(),
        None => writeln!(out, "total overflow").unwrap(),
    }
}
