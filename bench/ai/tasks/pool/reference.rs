use std::io::{self, BufRead, Write};

// A slot keeps its generation after its buffer is freed, so an old handle can never name the next buffer.
#[derive(Default)]
struct Slot {
    generation: u64,
    bytes: Option<Vec<u8>>,
}

fn live(slots: &[Slot], i: u64, g: u64) -> Option<usize> {
    let i = usize::try_from(i).ok()?;
    let s = slots.get(i)?;
    (s.bytes.is_some() && s.generation == g).then_some(i)
}

fn main() {
    let stdin = io::stdin();
    let mut out = io::stdout().lock();
    let mut slots: Vec<Slot> = Vec::new();
    for line in stdin.lock().lines() {
        let line = line.unwrap();
        let mut parts = line.split(' ');
        let op = parts.next().unwrap_or("");
        let a: Vec<u64> = parts.map(|t| t.parse().unwrap()).collect();
        match op {
            "new" => {
                let i = slots.iter().position(|s| s.bytes.is_none()).unwrap_or(slots.len());
                if i == slots.len() {
                    slots.push(Slot::default());
                }
                slots[i].generation += 1;
                slots[i].bytes = Some(vec![0; a[0] as usize]);
                writeln!(out, "h {} {}", i, slots[i].generation).unwrap();
            }
            "set" => match live(&slots, a[0], a[1]) {
                None => writeln!(out, "stale").unwrap(),
                Some(i) => {
                    let bytes = slots[i].bytes.as_mut().unwrap();
                    match usize::try_from(a[2]).ok().filter(|&off| off < bytes.len()) {
                        None => writeln!(out, "bounds").unwrap(),
                        Some(off) => {
                            bytes[off] = a[3] as u8;
                            writeln!(out, "ok").unwrap();
                        }
                    }
                }
            },
            "sum" => match live(&slots, a[0], a[1]) {
                None => writeln!(out, "stale").unwrap(),
                Some(i) => {
                    let total: u64 = slots[i].bytes.as_ref().unwrap().iter().map(|&b| u64::from(b)).sum();
                    writeln!(out, "{}", total).unwrap();
                }
            },
            "move" => match (live(&slots, a[0], a[1]), live(&slots, a[2], a[3])) {
                (Some(i), Some(j)) if i == j => writeln!(out, "same").unwrap(),
                (Some(i), Some(j)) => {
                    let moved = slots[i].bytes.take().unwrap();
                    let to = slots[j].bytes.as_mut().unwrap();
                    to.extend_from_slice(&moved);
                    writeln!(out, "ok {}", to.len()).unwrap();
                }
                _ => writeln!(out, "stale").unwrap(),
            },
            "free" => match live(&slots, a[0], a[1]) {
                None => writeln!(out, "stale").unwrap(),
                Some(i) => {
                    slots[i].bytes = None;
                    writeln!(out, "ok").unwrap();
                }
            },
            _ => {}
        }
    }
    let held: Vec<&Vec<u8>> = slots.iter().filter_map(|s| s.bytes.as_ref()).collect();
    let bytes: usize = held.iter().map(|b| b.len()).sum();
    writeln!(out, "live {} {}", held.len(), bytes).unwrap();
}
