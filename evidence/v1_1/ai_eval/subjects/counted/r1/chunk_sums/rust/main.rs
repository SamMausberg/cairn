use std::io::{self, Read};
use std::thread;

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).expect("failed to read stdin");
    let mut it = input.split_ascii_whitespace();

    let k: usize = it.next().unwrap().parse().unwrap();
    let n: usize = it.next().unwrap().parse().unwrap();
    let values: Vec<i64> = (0..n).map(|_| it.next().unwrap().parse().unwrap()).collect();

    let mut bounds = Vec::with_capacity(k + 1);
    for j in 0..=k {
        bounds.push((j * n) / k);
    }

    let results: Vec<Option<i64>> = thread::scope(|scope| {
        let mut handles = Vec::with_capacity(k);
        for j in 0..k {
            let slice = &values[bounds[j]..bounds[j + 1]];
            handles.push(scope.spawn(move || {
                let mut sum: i64 = 0;
                for &v in slice {
                    match sum.checked_add(v) {
                        Some(s) => sum = s,
                        None => return None,
                    }
                }
                Some(sum)
            }));
        }
        handles.into_iter().map(|h| h.join().unwrap()).collect()
    });

    let mut total_overflow = false;
    let mut total: i64 = 0;
    for (j, r) in results.iter().enumerate() {
        match r {
            Some(s) => println!("chunk {} {}", j, s),
            None => {
                println!("chunk {} overflow", j);
                total_overflow = true;
            }
        }
    }

    if !total_overflow {
        for r in &results {
            let s = r.unwrap();
            match total.checked_add(s) {
                Some(t) => total = t,
                None => {
                    total_overflow = true;
                    break;
                }
            }
        }
    }

    if total_overflow {
        println!("total overflow");
    } else {
        println!("total {}", total);
    }
}
