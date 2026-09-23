use std::io::{self, Read};

fn read_input() -> String {
    let mut s = String::new();
    io::stdin().read_to_string(&mut s).unwrap();
    s
}

fn hex_val(b: u8) -> u8 {
    match b {
        b'0'..=b'9' => b - b'0',
        b'a'..=b'f' => b - b'a' + 10,
        _ => 0,
    }
}

fn main() {
    let input = read_input();
    let mut lines = input.split('\n');
    let cap_line = lines.next().unwrap_or("").trim_end_matches('\r').trim();
    let hex_line = lines.next().unwrap_or("").trim_end_matches('\r').trim();

    let cap: usize = cap_line.parse().unwrap();

    let hex_bytes = hex_line.as_bytes();
    let n = hex_bytes.len() / 2;
    let mut data: Vec<u8> = Vec::with_capacity(n);
    for i in 0..n {
        let hi = hex_val(hex_bytes[2 * i]);
        let lo = hex_val(hex_bytes[2 * i + 1]);
        data.push((hi << 4) | lo);
    }

    let mut out_len: usize = 0;
    let mut hash: u64 = 14695981039346656037;
    let mut i: usize = 0;

    while i < data.len() {
        let offset = i;
        let c = data[i];
        if c < 128 {
            let count = (c as usize) + 1;
            if i + 1 + count > data.len() {
                println!("err truncated {}", offset);
                return;
            }
            if out_len + count > cap {
                println!("err full {}", offset);
                return;
            }
            for k in 0..count {
                let b = data[i + 1 + k];
                hash = (hash ^ (b as u64)).wrapping_mul(1099511628211);
            }
            out_len += count;
            i += 1 + count;
        } else {
            let count = (c as usize) - 126;
            if i + 1 >= data.len() {
                println!("err truncated {}", offset);
                return;
            }
            if out_len + count > cap {
                println!("err full {}", offset);
                return;
            }
            let b = data[i + 1];
            for _ in 0..count {
                hash = (hash ^ (b as u64)).wrapping_mul(1099511628211);
            }
            out_len += count;
            i += 2;
        }
    }

    println!("ok {} {:016x}", out_len, hash);
}
