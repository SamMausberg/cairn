// Tallies the values above a threshold on k threads, one contiguous chunk each.
use std::io::{self, Read};
use std::thread;

#[derive(Clone, Copy)]
struct Tally {
    above: u64,
    sum: u64,
    max: u64,
}

static mut TOTAL: Tally = Tally { above: 0, sum: 0, max: 0 }; // shared by every thread

fn tally_chunk(chunk: &[u64], t: u64) {
    for &v in chunk {
        let total = unsafe { &mut *std::ptr::addr_of_mut!(TOTAL) };
        if v > t {
            total.above += 1;
            total.sum = total.sum.wrapping_add(v);
        }
        total.max = total.max.max(v);
    }
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut tokens = input.split_ascii_whitespace();
    let k: usize = tokens.next().unwrap().parse().unwrap();
    let n: usize = tokens.next().unwrap().parse().unwrap();
    let t: u64 = tokens.next().unwrap().parse().unwrap();
    let values: Vec<u64> = tokens.take(n).map(|v| v.parse().unwrap()).collect();

    thread::scope(|s| {
        for j in 0..k {
            let chunk = &values[n * j / k..n * (j + 1) / k];
            s.spawn(move || tally_chunk(chunk, t));
        }
    });

    let total = unsafe { *std::ptr::addr_of!(TOTAL) };
    println!("above {}\nsum {}", total.above, total.sum);
    if n == 0 {
        println!("max none");
    } else {
        println!("max {}", total.max);
    }
}
