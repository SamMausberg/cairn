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

fn decode_hex(s: &str) -> Vec<u8> {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len() / 2);
    let mut i = 0;
    while i + 1 < bytes.len() {
        out.push((hex_val(bytes[i]) << 4) | hex_val(bytes[i + 1]));
        i += 2;
    }
    out
}

fn main() {
    let input = read_input();
    let mut lines = input.lines();
    let cap: usize = lines.next().unwrap_or("").trim().parse().unwrap();
    let hex_line = lines.next().unwrap_or("").trim();
    let data = decode_hex(hex_line);

    let mut output: Vec<u8> = Vec::new();
    let mut o = 0usize;

    loop {
        if o >= data.len() {
            break;
        }
        let c = data[o];
        if c < 128 {
            let len = c as usize + 1;
            if o + 1 + len > data.len() {
                println!("err truncated {}", o);
                return;
            }
            if output.len() + len > cap {
                println!("err full {}", o);
                return;
            }
            output.extend_from_slice(&data[o + 1..o + 1 + len]);
            o += 1 + len;
        } else {
            let count = c as usize - 126;
            if o + 1 >= data.len() {
                println!("err truncated {}", o);
                return;
            }
            if output.len() + count > cap {
                println!("err full {}", o);
                return;
            }
            let byte = data[o + 1];
            for _ in 0..count {
                output.push(byte);
            }
            o += 2;
        }
    }

    let mut hash: u64 = 14695981039346656037;
    for &b in &output {
        hash ^= b as u64;
        hash = hash.wrapping_mul(1099511628211);
    }

    println!("ok {} {:016x}", output.len(), hash);
}
