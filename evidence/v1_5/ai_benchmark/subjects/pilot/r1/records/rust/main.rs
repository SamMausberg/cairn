use std::io::{self, Read, Write};

fn find_disallowed(field: &[u8], allow_dot: bool) -> Option<usize> {
    for (i, &b) in field.iter().enumerate() {
        let ok = b.is_ascii_digit() || (allow_dot && b == b'.');
        if !ok {
            return Some(i);
        }
    }
    None
}

fn parse_u128_checked(digits: &[u8]) -> Option<u128> {
    let mut v: u128 = 0;
    for &b in digits {
        let d = (b - b'0') as u128;
        v = v.checked_mul(10)?.checked_add(d)?;
    }
    Some(v)
}

fn process_line(line: &[u8], line_no: usize, out: &mut impl Write) {
    let commas: Vec<usize> = line
        .iter()
        .enumerate()
        .filter(|&(_, &b)| b == b',')
        .map(|(i, _)| i)
        .collect();

    if commas.len() < 2 {
        let col = line.len() + 1;
        writeln!(out, "err {} {} missing", line_no, col).unwrap();
        return;
    }
    if commas.len() > 2 {
        let col = commas[2] + 1;
        writeln!(out, "err {} {} extra", line_no, col).unwrap();
        return;
    }

    let c0 = commas[0];
    let c1 = commas[1];
    let fields: [(&[u8], usize); 3] = [
        (&line[0..c0], 1),
        (&line[c0 + 1..c1], c0 + 2),
        (&line[c1 + 1..], c1 + 2),
    ];

    let mut values: [u128; 3] = [0; 3];

    for (idx, &(field, start_col)) in fields.iter().enumerate() {
        let is_price = idx == 2;

        if field.is_empty() {
            writeln!(out, "err {} {} empty", line_no, start_col).unwrap();
            return;
        }

        if let Some(bad) = find_disallowed(field, is_price) {
            writeln!(out, "err {} {} digit", line_no, start_col + bad).unwrap();
            return;
        }

        if is_price {
            let dot_pos = field.iter().position(|&b| b == b'.');
            let shape_ok = match dot_pos {
                Some(p) => {
                    p >= 1
                        && field.len() - p - 1 == 2
                        && field[..p].iter().all(|&b| b.is_ascii_digit())
                        && field[p + 1..].iter().all(|&b| b.is_ascii_digit())
                }
                None => false,
            };
            if !shape_ok {
                writeln!(out, "err {} {} format", line_no, start_col).unwrap();
                return;
            }
            let p = dot_pos.unwrap();
            let int_val = parse_u128_checked(&field[..p]);
            let frac_val = parse_u128_checked(&field[p + 1..]);
            let cents = match (int_val, frac_val) {
                (Some(iv), Some(fv)) => iv.checked_mul(100).and_then(|x| x.checked_add(fv)),
                _ => None,
            };
            match cents {
                Some(c) if c <= 9223372036854775807u128 => {
                    values[idx] = c;
                }
                _ => {
                    writeln!(out, "err {} {} range", line_no, start_col).unwrap();
                    return;
                }
            }
        } else {
            let max: u128 = if idx == 0 { 4294967295 } else { 65535 };
            match parse_u128_checked(field) {
                Some(v) if v <= max => {
                    values[idx] = v;
                }
                _ => {
                    writeln!(out, "err {} {} range", line_no, start_col).unwrap();
                    return;
                }
            }
        }
    }

    writeln!(out, "ok {} {} {}", values[0], values[1], values[2]).unwrap();
}

fn main() {
    let mut input = Vec::new();
    io::stdin().read_to_end(&mut input).expect("read stdin");

    let lines: Vec<&[u8]> = if input.is_empty() {
        Vec::new()
    } else {
        let mut v: Vec<&[u8]> = input.split(|&b| b == b'\n').collect();
        if *input.last().unwrap() == b'\n' {
            v.pop();
        }
        v
    };

    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());

    for (i, line) in lines.iter().enumerate() {
        process_line(line, i + 1, &mut out);
    }
}
