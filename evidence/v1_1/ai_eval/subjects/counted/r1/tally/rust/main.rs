// Tallies the values above a threshold on k threads, one contiguous chunk each.
use std::io::{self, Read};
use std::thread;

#[derive(Clone, Copy)]
struct Tally {
    above: u64,
    sum: u64,
    max: u64,
}

fn tally_chunk(chunk: &[u64], t: u64) -> Tally {
    let mut total = Tally { above: 0, sum: 0, max: 0 };
    for &v in chunk {
        if v > t {
            total.above += 1;
            total.sum = total.sum.wrapping_add(v);
        }
        total.max = total.max.max(v);
    }
    total
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut tokens = input.split_ascii_whitespace();
    let k: usize = tokens.next().unwrap().parse().unwrap();
    let n: usize = tokens.next().unwrap().parse().unwrap();
    let t: u64 = tokens.next().unwrap().parse().unwrap();
    let values: Vec<u64> = tokens.take(n).map(|v| v.parse().unwrap()).collect();

    let totals = thread::scope(|s| {
        let handles: Vec<_> = (0..k)
            .map(|j| {
                let chunk = &values[n * j / k..n * (j + 1) / k];
                s.spawn(move || tally_chunk(chunk, t))
            })
            .collect();
        handles.into_iter().map(|h| h.join().unwrap()).collect::<Vec<_>>()
    });

    let total = totals.into_iter().fold(
        Tally { above: 0, sum: 0, max: 0 },
        |acc, part| Tally {
            above: acc.above + part.above,
            sum: acc.sum.wrapping_add(part.sum),
            max: acc.max.max(part.max),
        },
    );
    println!("above {}\nsum {}", total.above, total.sum);
    if n == 0 {
        println!("max none");
    } else {
        println!("max {}", total.max);
    }
}
