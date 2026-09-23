// Summarizes runs of equal readings in a sorted list: each distinct reading with its count.
use std::io::{self, Read, Write};

fn runs(readings: &[i64]) -> Vec<(i64, u64)> {
    let mut out = Vec::new();
    let n = readings.len();
    if n == 0 {
        return out;
    }
    let mut count = 1u64;
    for i in 0..n - 1 {
        if readings[i + 1] == readings[i] {
            count += 1;
        } else {
            out.push((readings[i], count));
            count = 1;
        }
    }
    out.push((readings[n - 1], count));
    out
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut tokens = input.split_ascii_whitespace();
    let n: usize = tokens.next().unwrap().parse().unwrap();
    let readings: Vec<i64> = tokens.take(n).map(|t| t.parse().unwrap()).collect();
    let summary = runs(&readings);
    let mut out = io::stdout().lock();
    for (value, count) in &summary {
        writeln!(out, "{} x{}", value, count).unwrap();
    }
    writeln!(out, "distinct {}", summary.len()).unwrap();
}
