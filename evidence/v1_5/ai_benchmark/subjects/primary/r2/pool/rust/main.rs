use std::collections::BTreeSet;
use std::io::{self, Read, Write};

struct Slot {
    gen: u64,
    data: Option<(Vec<u8>, u64)>, // (bytes, sum of byte values)
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();

    let mut slots: Vec<Slot> = Vec::new();
    let mut free_set: BTreeSet<usize> = BTreeSet::new();

    let mut live_count: u64 = 0;
    let mut live_bytes: u64 = 0;

    let mut out = String::new();

    for line in input.lines() {
        let mut it = line.split(' ').filter(|s| !s.is_empty());
        let cmd = match it.next() {
            Some(c) => c,
            None => continue,
        };

        match cmd {
            "new" => {
                let s: usize = it.next().unwrap().parse().unwrap();
                let idx = if let Some(&i) = free_set.iter().next() {
                    free_set.remove(&i);
                    i
                } else {
                    slots.push(Slot { gen: 0, data: None });
                    slots.len() - 1
                };
                slots[idx].gen += 1;
                slots[idx].data = Some((vec![0u8; s], 0));
                live_count += 1;
                live_bytes += s as u64;
                out.push_str(&format!("h {} {}\n", idx, slots[idx].gen));
            }
            "set" => {
                let i: usize = it.next().unwrap().parse().unwrap();
                let g: u64 = it.next().unwrap().parse().unwrap();
                let off: u64 = it.next().unwrap().parse().unwrap();
                let v: u64 = it.next().unwrap().parse().unwrap();
                let live = i < slots.len() && slots[i].gen == g && slots[i].data.is_some();
                if !live {
                    out.push_str("stale\n");
                } else {
                    let (buf, sum) = slots[i].data.as_mut().unwrap();
                    let off_usize = usize::try_from(off).ok();
                    if off_usize.is_none() || off_usize.unwrap() >= buf.len() {
                        out.push_str("bounds\n");
                    } else {
                        let off_usize = off_usize.unwrap();
                        let old = buf[off_usize] as u64;
                        buf[off_usize] = v as u8;
                        *sum = *sum - old + v;
                        out.push_str("ok\n");
                    }
                }
            }
            "sum" => {
                let i: usize = it.next().unwrap().parse().unwrap();
                let g: u64 = it.next().unwrap().parse().unwrap();
                let live = i < slots.len() && slots[i].gen == g && slots[i].data.is_some();
                if !live {
                    out.push_str("stale\n");
                } else {
                    let (_, sum) = slots[i].data.as_ref().unwrap();
                    out.push_str(&format!("{}\n", sum));
                }
            }
            "move" => {
                let i: usize = it.next().unwrap().parse().unwrap();
                let g: u64 = it.next().unwrap().parse().unwrap();
                let j: usize = it.next().unwrap().parse().unwrap();
                let h: u64 = it.next().unwrap().parse().unwrap();
                let live_src = i < slots.len() && slots[i].gen == g && slots[i].data.is_some();
                let live_dst = j < slots.len() && slots[j].gen == h && slots[j].data.is_some();
                if !live_src || !live_dst {
                    out.push_str("stale\n");
                } else if i == j && g == h {
                    out.push_str("same\n");
                } else {
                    let (src_buf, src_sum) = slots[i].data.take().unwrap();
                    {
                        let (dst_buf, dst_sum) = slots[j].data.as_mut().unwrap();
                        dst_buf.extend_from_slice(&src_buf);
                        *dst_sum += src_sum;
                    }
                    free_set.insert(i);
                    live_count -= 1;
                    let new_size = slots[j].data.as_ref().unwrap().0.len();
                    out.push_str(&format!("ok {}\n", new_size));
                }
            }
            "free" => {
                let i: usize = it.next().unwrap().parse().unwrap();
                let g: u64 = it.next().unwrap().parse().unwrap();
                let live = i < slots.len() && slots[i].gen == g && slots[i].data.is_some();
                if !live {
                    out.push_str("stale\n");
                } else {
                    let (buf, _) = slots[i].data.take().unwrap();
                    live_count -= 1;
                    live_bytes -= buf.len() as u64;
                    free_set.insert(i);
                    out.push_str("ok\n");
                }
            }
            _ => {}
        }
    }

    out.push_str(&format!("live {} {}\n", live_count, live_bytes));

    io::stdout().write_all(out.as_bytes()).unwrap();
}
