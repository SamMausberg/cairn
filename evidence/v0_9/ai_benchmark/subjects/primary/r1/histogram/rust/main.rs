use std::io::{self, Read, Write};
use std::thread;

fn read_input() -> Vec<u8> {
    let mut bytes = Vec::new();
    io::stdin().read_to_end(&mut bytes).unwrap();
    bytes
}

fn main() {
    let bytes = read_input();
    let mut it = bytes
        .split(|&b| b == b' ' || b == b'\n' || b == b'\t' || b == b'\r')
        .filter(|s| !s.is_empty())
        .map(|s| std::str::from_utf8(s).unwrap().parse::<u64>().unwrap());

    let n = it.next().unwrap_or(0) as usize;
    let shift = it.next().unwrap_or(0) as u32;
    let values: Vec<u32> = it.take(n).map(|v| v as u32).collect();

    let num_threads = std::cmp::min(
        4,
        std::cmp::max(1, thread::available_parallelism().map(|p| p.get()).unwrap_or(1)),
    );
    let num_threads = if values.len() < num_threads { 1.max(values.len().min(num_threads)) } else { num_threads };
    let num_threads = num_threads.max(1);

    let chunk_size = (values.len() + num_threads - 1) / num_threads.max(1);
    let chunk_size = chunk_size.max(1);

    let counts: [u64; 256] = thread::scope(|s| {
        let mut handles = Vec::new();
        for chunk in values.chunks(chunk_size) {
            handles.push(s.spawn(move || {
                let mut local = [0u64; 256];
                for &v in chunk {
                    let bin = ((v >> shift) & 255) as usize;
                    local[bin] += 1;
                }
                local
            }));
        }
        let mut total = [0u64; 256];
        for h in handles {
            let local = h.join().unwrap();
            for i in 0..256 {
                total[i] += local[i];
            }
        }
        total
    });

    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());
    for (bin, &count) in counts.iter().enumerate() {
        if count != 0 {
            writeln!(out, "{} {}", bin, count).unwrap();
        }
    }
}
