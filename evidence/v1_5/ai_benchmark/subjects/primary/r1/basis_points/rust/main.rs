// Reports each usage as a share of its quota in basis points, rounded down.
use std::io::{self, Read, Write};

// floor(used * 10000 / total).
fn basis_points(used: u64, total: u64) -> u64 {
    (used as u128 * 10000 / total as u128) as u64
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut tokens = input.split_ascii_whitespace().map(|t| t.parse::<u64>().unwrap());
    let m = tokens.next().unwrap();
    let mut out = io::stdout().lock();
    for _ in 0..m {
        let used = tokens.next().unwrap();
        let total = tokens.next().unwrap();
        if total == 0 {
            writeln!(out, "undefined").unwrap();
        } else {
            writeln!(out, "{} bp", basis_points(used, total)).unwrap();
        }
    }
}
