use std::cmp::Reverse;
use std::collections::BinaryHeap;
use std::io::{self, Read, Write};

struct Slot {
    generation: u64,
    buffer: Option<Vec<u8>>,
}

fn is_live(slots: &[Slot], i: usize, g: u64) -> bool {
    match slots.get(i) {
        Some(s) => s.generation == g && s.buffer.is_some(),
        None => false,
    }
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();

    let mut slots: Vec<Slot> = Vec::new();
    let mut free_slots: BinaryHeap<Reverse<usize>> = BinaryHeap::new();

    let mut out = String::new();

    for line in input.lines() {
        let mut tok = line.split_whitespace();
        let cmd = match tok.next() {
            Some(c) => c,
            None => continue,
        };

        match cmd {
            "new" => {
                let s: u64 = tok.next().unwrap().parse().unwrap();
                let idx = if let Some(Reverse(i)) = free_slots.pop() {
                    i
                } else {
                    slots.push(Slot {
                        generation: 0,
                        buffer: None,
                    });
                    slots.len() - 1
                };
                slots[idx].generation += 1;
                slots[idx].buffer = Some(vec![0u8; s as usize]);
                out.push_str(&format!("h {} {}\n", idx, slots[idx].generation));
            }
            "set" => {
                let i: usize = tok.next().unwrap().parse().unwrap();
                let g: u64 = tok.next().unwrap().parse().unwrap();
                let off: u64 = tok.next().unwrap().parse().unwrap();
                let v: u64 = tok.next().unwrap().parse().unwrap();
                if !is_live(&slots, i, g) {
                    out.push_str("stale\n");
                } else {
                    let buf = slots[i].buffer.as_mut().unwrap();
                    if off >= buf.len() as u64 {
                        out.push_str("bounds\n");
                    } else {
                        buf[off as usize] = v as u8;
                        out.push_str("ok\n");
                    }
                }
            }
            "sum" => {
                let i: usize = tok.next().unwrap().parse().unwrap();
                let g: u64 = tok.next().unwrap().parse().unwrap();
                if !is_live(&slots, i, g) {
                    out.push_str("stale\n");
                } else {
                    let buf = slots[i].buffer.as_ref().unwrap();
                    let sum: u64 = buf.iter().map(|&b| b as u64).sum();
                    out.push_str(&format!("{}\n", sum));
                }
            }
            "move" => {
                let i: usize = tok.next().unwrap().parse().unwrap();
                let g: u64 = tok.next().unwrap().parse().unwrap();
                let j: usize = tok.next().unwrap().parse().unwrap();
                let h: u64 = tok.next().unwrap().parse().unwrap();
                if !is_live(&slots, i, g) || !is_live(&slots, j, h) {
                    out.push_str("stale\n");
                } else if i == j && g == h {
                    out.push_str("same\n");
                } else {
                    let src = slots[i].buffer.take().unwrap();
                    free_slots.push(Reverse(i));
                    let dst = slots[j].buffer.as_mut().unwrap();
                    dst.extend_from_slice(&src);
                    let n = dst.len();
                    out.push_str(&format!("ok {}\n", n));
                }
            }
            "free" => {
                let i: usize = tok.next().unwrap().parse().unwrap();
                let g: u64 = tok.next().unwrap().parse().unwrap();
                if !is_live(&slots, i, g) {
                    out.push_str("stale\n");
                } else {
                    slots[i].buffer = None;
                    free_slots.push(Reverse(i));
                    out.push_str("ok\n");
                }
            }
            _ => {}
        }
    }

    let mut l: u64 = 0;
    let mut b: u64 = 0;
    for s in &slots {
        if let Some(buf) = &s.buffer {
            l += 1;
            b += buf.len() as u64;
        }
    }
    out.push_str(&format!("live {} {}\n", l, b));

    io::stdout().write_all(out.as_bytes()).unwrap();
}
