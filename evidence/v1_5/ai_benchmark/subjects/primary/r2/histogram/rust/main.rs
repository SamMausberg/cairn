use std::io::{self, Read, Write};
use std::thread;

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut it = input.split_ascii_whitespace();

    let n: usize = it.next().unwrap().parse().unwrap();
    let shift: u32 = it.next().unwrap().parse().unwrap();

    let mut values: Vec<u32> = Vec::with_capacity(n);
    for _ in 0..n {
        values.push(it.next().unwrap().parse().unwrap());
    }

    let mut totals = [0u32; 256];

    if n > 0 {
        let num_threads = thread::available_parallelism()
            .map(|x| x.get())
            .unwrap_or(4)
            .clamp(2, 8);
        let chunk_size = (n + num_threads - 1) / num_threads;

        let partials: Vec<[u32; 256]> = thread::scope(|s| {
            let handles: Vec<_> = values
                .chunks(chunk_size)
                .map(|chunk| {
                    s.spawn(move || {
                        let mut local = [0u32; 256];
                        for &v in chunk {
                            let bin = ((v >> shift) & 255) as usize;
                            local[bin] += 1;
                        }
                        local
                    })
                })
                .collect();
            handles.into_iter().map(|h| h.join().unwrap()).collect()
        });

        for local in partials {
            for i in 0..256 {
                totals[i] += local[i];
            }
        }
    }

    let mut out = String::new();
    for (bin, &count) in totals.iter().enumerate() {
        if count != 0 {
            out.push_str(&bin.to_string());
            out.push(' ');
            out.push_str(&count.to_string());
            out.push('\n');
        }
    }
    io::stdout().write_all(out.as_bytes()).unwrap();
}
