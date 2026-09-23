use std::io::{self, Read, Write};

struct Slot {
    generation: u64,
    data: Option<Vec<u8>>,
}

fn is_live(slots: &[Slot], i: usize, g: u64) -> bool {
    i < slots.len() && slots[i].generation == g && slots[i].data.is_some()
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());

    let mut slots: Vec<Slot> = Vec::new();

    for line in input.lines() {
        let mut tok = line.split_ascii_whitespace();
        let cmd = match tok.next() {
            Some(c) => c,
            None => continue,
        };
        match cmd {
            "new" => {
                let s: usize = tok.next().unwrap().parse().unwrap();
                let mut idx = None;
                for (i, slot) in slots.iter().enumerate() {
                    if slot.data.is_none() {
                        idx = Some(i);
                        break;
                    }
                }
                let idx = match idx {
                    Some(i) => i,
                    None => {
                        slots.push(Slot {
                            generation: 0,
                            data: None,
                        });
                        slots.len() - 1
                    }
                };
                slots[idx].generation += 1;
                slots[idx].data = Some(vec![0u8; s]);
                writeln!(out, "h {} {}", idx, slots[idx].generation).unwrap();
            }
            "set" => {
                let i: usize = tok.next().unwrap().parse().unwrap();
                let g: u64 = tok.next().unwrap().parse().unwrap();
                let off: u64 = tok.next().unwrap().parse().unwrap();
                let v: u8 = tok.next().unwrap().parse().unwrap();
                if !is_live(&slots, i, g) {
                    writeln!(out, "stale").unwrap();
                } else {
                    let data = slots[i].data.as_mut().unwrap();
                    if off >= data.len() as u64 {
                        writeln!(out, "bounds").unwrap();
                    } else {
                        data[off as usize] = v;
                        writeln!(out, "ok").unwrap();
                    }
                }
            }
            "sum" => {
                let i: usize = tok.next().unwrap().parse().unwrap();
                let g: u64 = tok.next().unwrap().parse().unwrap();
                if !is_live(&slots, i, g) {
                    writeln!(out, "stale").unwrap();
                } else {
                    let data = slots[i].data.as_ref().unwrap();
                    let sum: u64 = data.iter().map(|&b| b as u64).sum();
                    writeln!(out, "{}", sum).unwrap();
                }
            }
            "move" => {
                let i: usize = tok.next().unwrap().parse().unwrap();
                let g: u64 = tok.next().unwrap().parse().unwrap();
                let j: usize = tok.next().unwrap().parse().unwrap();
                let h: u64 = tok.next().unwrap().parse().unwrap();
                if !is_live(&slots, i, g) || !is_live(&slots, j, h) {
                    writeln!(out, "stale").unwrap();
                } else if i == j && g == h {
                    writeln!(out, "same").unwrap();
                } else {
                    let src = slots[i].data.take().unwrap();
                    let dst = slots[j].data.as_mut().unwrap();
                    dst.extend_from_slice(&src);
                    let n = dst.len();
                    writeln!(out, "ok {}", n).unwrap();
                }
            }
            "free" => {
                let i: usize = tok.next().unwrap().parse().unwrap();
                let g: u64 = tok.next().unwrap().parse().unwrap();
                if !is_live(&slots, i, g) {
                    writeln!(out, "stale").unwrap();
                } else {
                    slots[i].data = None;
                    writeln!(out, "ok").unwrap();
                }
            }
            _ => {}
        }
    }

    let mut live_count: u64 = 0;
    let mut live_bytes: u64 = 0;
    for slot in &slots {
        if let Some(data) = &slot.data {
            live_count += 1;
            live_bytes += data.len() as u64;
        }
    }
    writeln!(out, "live {} {}", live_count, live_bytes).unwrap();
}
