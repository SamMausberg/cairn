use std::io::{self, Read, Write};
use std::thread;

fn sum_chunk(values: &[i64]) -> Option<i64> {
    let mut acc: i64 = 0;
    for &v in values {
        match acc.checked_add(v) {
            Some(x) => acc = x,
            None => return None,
        }
    }
    Some(acc)
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).expect("failed to read stdin");
    let mut it = input.split_ascii_whitespace();

    let k: usize = it.next().expect("missing k").parse().expect("invalid k");
    let n: usize = it.next().expect("missing n").parse().expect("invalid n");

    let values: Vec<i64> = (0..n)
        .map(|_| it.next().expect("missing value").parse().expect("invalid value"))
        .collect();

    let mut bounds: Vec<usize> = Vec::with_capacity(k + 1);
    for j in 0..=k {
        bounds.push(j * n / k);
    }

    let chunks: Vec<&[i64]> = (0..k)
        .map(|j| &values[bounds[j]..bounds[j + 1]])
        .collect();

    let results: Vec<Option<i64>> = thread::scope(|scope| {
        let handles: Vec<_> = chunks
            .iter()
            .map(|&chunk| scope.spawn(move || sum_chunk(chunk)))
            .collect();
        handles.into_iter().map(|h| h.join().expect("thread panicked")).collect()
    });

    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());

    let mut total: Option<i64> = Some(0);
    for (j, r) in results.iter().enumerate() {
        match r {
            Some(s) => writeln!(out, "chunk {} {}", j, s).unwrap(),
            None => writeln!(out, "chunk {} overflow", j).unwrap(),
        }
        total = match (total, r) {
            (Some(t), Some(s)) => t.checked_add(*s),
            _ => None,
        };
    }

    match total {
        Some(t) => writeln!(out, "total {}", t).unwrap(),
        None => writeln!(out, "total overflow").unwrap(),
    }
}
