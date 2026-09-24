use std::io::{self, Read, Write};

fn read_input() -> Vec<u8> {
    let mut bytes = Vec::new();
    io::stdin().read_to_end(&mut bytes).unwrap();
    bytes
}

fn main() {
    let bytes = read_input();
    let text = String::from_utf8_lossy(&bytes);
    let mut tokens = text.split_ascii_whitespace();

    let n: usize = tokens.next().unwrap().parse().unwrap();
    let shift: u32 = tokens.next().unwrap().parse().unwrap();

    let values: Vec<u32> = (0..n)
        .map(|_| tokens.next().unwrap().parse().unwrap())
        .collect();

    let num_threads = std::thread::available_parallelism()
        .map(|p| p.get())
        .unwrap_or(1)
        .max(2);

    let chunk_size = (n + num_threads - 1) / num_threads.max(1);
    let chunk_size = chunk_size.max(1);

    let partials: Vec<[u64; 256]> = std::thread::scope(|scope| {
        let mut handles = Vec::new();
        for chunk in values.chunks(chunk_size) {
            handles.push(scope.spawn(move || {
                let mut counts = [0u64; 256];
                for &v in chunk {
                    let bin = ((v >> shift) & 255) as usize;
                    counts[bin] += 1;
                }
                counts
            }));
        }
        handles.into_iter().map(|h| h.join().unwrap()).collect()
    });

    let mut totals = [0u64; 256];
    for partial in &partials {
        for i in 0..256 {
            totals[i] += partial[i];
        }
    }

    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());
    for (bin, &count) in totals.iter().enumerate() {
        if count != 0 {
            writeln!(out, "{} {}", bin, count).unwrap();
        }
    }
}
