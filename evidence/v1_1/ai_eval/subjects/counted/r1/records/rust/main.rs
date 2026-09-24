use std::io::{self, Read, Write};

fn read_input() -> Vec<u8> {
    let mut bytes = Vec::new();
    io::stdin().read_to_end(&mut bytes).unwrap();
    bytes
}

fn split_lines(bytes: &[u8]) -> Vec<&[u8]> {
    if bytes.is_empty() {
        return Vec::new();
    }
    let mut lines: Vec<&[u8]> = bytes.split(|&b| b == b'\n').collect();
    if bytes.last() == Some(&b'\n') {
        lines.pop();
    }
    lines
}

// Strips leading '0' bytes from a decimal digit slice.
fn strip_zeros(s: &[u8]) -> &[u8] {
    let mut i = 0;
    while i < s.len() && s[i] == b'0' {
        i += 1;
    }
    &s[i..]
}

// Compares a stripped (no leading zero) decimal digit slice against a max value's
// decimal digits (also with no leading zeros), returning true if s <= max.
fn le_bignum(s: &[u8], max: &[u8]) -> bool {
    if s.len() != max.len() {
        s.len() < max.len()
    } else {
        s <= max
    }
}

struct FieldErr {
    col: usize,
    kind: &'static str,
}

fn check_int_field(field: &[u8], start_col: usize, max: &[u8]) -> Result<u64, FieldErr> {
    if field.is_empty() {
        return Err(FieldErr {
            col: start_col,
            kind: "empty",
        });
    }
    for (i, &b) in field.iter().enumerate() {
        if !b.is_ascii_digit() {
            return Err(FieldErr {
                col: start_col + i,
                kind: "digit",
            });
        }
    }
    let stripped = strip_zeros(field);
    if !le_bignum(stripped, max) {
        return Err(FieldErr {
            col: start_col,
            kind: "range",
        });
    }
    let mut value: u64 = 0;
    for &b in stripped {
        value = value * 10 + (b - b'0') as u64;
    }
    Ok(value)
}

fn check_price_field(field: &[u8], start_col: usize) -> Result<i64, FieldErr> {
    if field.is_empty() {
        return Err(FieldErr {
            col: start_col,
            kind: "empty",
        });
    }
    for (i, &b) in field.iter().enumerate() {
        if !(b.is_ascii_digit() || b == b'.') {
            return Err(FieldErr {
                col: start_col + i,
                kind: "digit",
            });
        }
    }
    let dot_positions: Vec<usize> = field
        .iter()
        .enumerate()
        .filter(|&(_, &b)| b == b'.')
        .map(|(i, _)| i)
        .collect();
    if dot_positions.len() != 1 {
        return Err(FieldErr {
            col: start_col,
            kind: "format",
        });
    }
    let dot = dot_positions[0];
    let before = &field[..dot];
    let after = &field[dot + 1..];
    if before.is_empty() || after.len() != 2 {
        return Err(FieldErr {
            col: start_col,
            kind: "format",
        });
    }
    let stripped_int = strip_zeros(before);
    let mut combined: Vec<u8> = Vec::with_capacity(stripped_int.len() + 2);
    combined.extend_from_slice(stripped_int);
    combined.extend_from_slice(after);
    let normalized = strip_zeros(&combined);
    const MAX_CENTS: &[u8] = b"9223372036854775807";
    if !le_bignum(normalized, MAX_CENTS) {
        return Err(FieldErr {
            col: start_col,
            kind: "range",
        });
    }
    let mut value: i64 = 0;
    for &b in normalized {
        value = value * 10 + (b - b'0') as i64;
    }
    Ok(value)
}

fn process_line(line: &[u8], out: &mut String, line_no: usize) {
    let commas: Vec<usize> = line
        .iter()
        .enumerate()
        .filter(|&(_, &b)| b == b',')
        .map(|(i, _)| i)
        .collect();

    if commas.len() < 2 {
        let col = line.len() + 1;
        out.push_str(&format!("err {} {} missing\n", line_no, col));
        return;
    }
    if commas.len() > 2 {
        let col = commas[2] + 1;
        out.push_str(&format!("err {} {} extra\n", line_no, col));
        return;
    }

    let id_field = &line[..commas[0]];
    let qty_field = &line[commas[0] + 1..commas[1]];
    let price_field = &line[commas[1] + 1..];

    let id_start = 1;
    let qty_start = commas[0] + 2;
    let price_start = commas[1] + 2;

    let id_val = match check_int_field(id_field, id_start, b"4294967295") {
        Ok(v) => v,
        Err(e) => {
            out.push_str(&format!("err {} {} {}\n", line_no, e.col, e.kind));
            return;
        }
    };
    let qty_val = match check_int_field(qty_field, qty_start, b"65535") {
        Ok(v) => v,
        Err(e) => {
            out.push_str(&format!("err {} {} {}\n", line_no, e.col, e.kind));
            return;
        }
    };
    let price_val = match check_price_field(price_field, price_start) {
        Ok(v) => v,
        Err(e) => {
            out.push_str(&format!("err {} {} {}\n", line_no, e.col, e.kind));
            return;
        }
    };

    out.push_str(&format!("ok {} {} {}\n", id_val, qty_val, price_val));
}

fn main() {
    let bytes = read_input();
    let lines = split_lines(&bytes);
    let mut out = String::new();
    for (idx, line) in lines.iter().enumerate() {
        process_line(line, &mut out, idx + 1);
    }
    let stdout = io::stdout();
    let mut handle = stdout.lock();
    handle.write_all(out.as_bytes()).unwrap();
}
