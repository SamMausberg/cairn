use std::io::{self, Read};

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut lines = input.lines();
    let cap: usize = lines.next().unwrap().trim().parse().unwrap();
    let hex = lines.next().unwrap_or("").trim();

    let hex_bytes = hex.as_bytes();
    let mut data = Vec::with_capacity(hex_bytes.len() / 2);
    for chunk in hex_bytes.chunks(2) {
        let s = std::str::from_utf8(chunk).unwrap();
        data.push(u8::from_str_radix(s, 16).unwrap());
    }

    let mut output: Vec<u8> = Vec::new();
    let mut i = 0usize;
    while i < data.len() {
        let offset = i;
        let c = data[i];
        if c < 128 {
            let n = (c as usize) + 1;
            if i + 1 + n > data.len() {
                println!("err truncated {}", offset);
                return;
            }
            if output.len() + n > cap {
                println!("err full {}", offset);
                return;
            }
            output.extend_from_slice(&data[i + 1..i + 1 + n]);
            i += 1 + n;
        } else {
            let n = (c as usize) - 126;
            if i + 2 > data.len() {
                println!("err truncated {}", offset);
                return;
            }
            if output.len() + n > cap {
                println!("err full {}", offset);
                return;
            }
            let b = data[i + 1];
            for _ in 0..n {
                output.push(b);
            }
            i += 2;
        }
    }

    let mut hash: u64 = 14695981039346656037;
    for &b in &output {
        hash ^= b as u64;
        hash = hash.wrapping_mul(1099511628211);
    }
    println!("ok {} {:016x}", output.len(), hash);
}
