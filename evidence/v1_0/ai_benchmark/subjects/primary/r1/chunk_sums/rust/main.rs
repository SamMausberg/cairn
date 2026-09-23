use std::io::{self, Read, Write};
use std::sync::Arc;
use std::thread;

fn sum_chunk(values: &[i64]) -> Option<i64> {
    let mut acc: i64 = 0;
    for &v in values {
        acc = acc.checked_add(v)?;
    }
    Some(acc)
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).expect("failed to read stdin");

    let mut iter = input.split_ascii_whitespace();
    let k: usize = iter.next().expect("missing k").parse().expect("invalid k");
    let n: usize = iter.next().expect("missing n").parse().expect("invalid n");

    let values: Vec<i64> = (0..n)
        .map(|_| iter.next().expect("missing value").parse().expect("invalid value"))
        .collect();

    let values = Arc::new(values);

    let mut handles = Vec::with_capacity(k);
    for j in 0..k {
        let values = Arc::clone(&values);
        let start = (j * n) / k;
        let end = ((j + 1) * n) / k;
        handles.push(thread::spawn(move || sum_chunk(&values[start..end])));
    }

    let results: Vec<Option<i64>> = handles
        .into_iter()
        .map(|h| h.join().expect("thread panicked"))
        .collect();

    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());

    for (j, r) in results.iter().enumerate() {
        match r {
            Some(sum) => writeln!(out, "chunk {} {}", j, sum).unwrap(),
            None => writeln!(out, "chunk {} overflow", j).unwrap(),
        }
    }

    let mut total_overflow = false;
    let mut total: i64 = 0;
    for r in &results {
        match r {
            Some(sum) => {
                match total.checked_add(*sum) {
                    Some(t) => total = t,
                    None => {
                        total_overflow = true;
                        break;
                    }
                }
            }
            None => {
                total_overflow = true;
                break;
            }
        }
    }

    if total_overflow {
        writeln!(out, "total overflow").unwrap();
    } else {
        writeln!(out, "total {}", total).unwrap();
    }
}
