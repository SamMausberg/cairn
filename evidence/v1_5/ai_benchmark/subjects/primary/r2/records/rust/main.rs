use std::io::{self, Read, Write};

const MAX_ID: &[u8] = b"4294967295";
const MAX_QTY: &[u8] = b"65535";
const MAX_CENTS: &[u8] = b"9223372036854775807";

// Reads the whole of standard input.
fn read_input() -> Vec<u8> {
    let mut bytes = Vec::new();
    io::stdin().read_to_end(&mut bytes).unwrap();
    bytes
}

// Reduces a non-empty run of ASCII digits to its value with no leading
// zeros, keeping a single "0" when the whole run is zero.
fn strip_leading_zeros(s: &[u8]) -> &[u8] {
    let mut i = 0;
    while i + 1 < s.len() && s[i] == b'0' {
        i += 1;
    }
    &s[i..]
}

// Compares a run of ASCII digits (possibly with leading zeros) against a
// canonical maximum digit string, returning whether digits <= max.
fn le_max(digits: &[u8], max: &[u8]) -> bool {
    let d = strip_leading_zeros(digits);
    if d.len() != max.len() {
        d.len() < max.len()
    } else {
        d <= max
    }
}

fn to_u64(digits: &[u8]) -> u64 {
    std::str::from_utf8(strip_leading_zeros(digits))
        .unwrap()
        .parse()
        .unwrap()
}

fn to_u32(digits: &[u8]) -> u32 {
    std::str::from_utf8(strip_leading_zeros(digits))
        .unwrap()
        .parse()
        .unwrap()
}

fn to_u16(digits: &[u8]) -> u16 {
    std::str::from_utf8(strip_leading_zeros(digits))
        .unwrap()
        .parse()
        .unwrap()
}

enum FieldError {
    Empty,
    Digit(usize),  // 0-indexed offset within the field
    Format,
    Range,
}

// Validates an id/qty field: only digits 0-9 allowed.
fn check_int_field(field: &[u8], max: &[u8]) -> Result<(), FieldError> {
    if field.is_empty() {
        return Err(FieldError::Empty);
    }
    for (i, &b) in field.iter().enumerate() {
        if !b.is_ascii_digit() {
            return Err(FieldError::Digit(i));
        }
    }
    if !le_max(field, max) {
        return Err(FieldError::Range);
    }
    Ok(())
}

// Validates the price field and returns the combined cents digits on
// success (int part with leading zeros stripped, then the two fraction
// digits).
fn check_price_field(field: &[u8]) -> Result<Vec<u8>, FieldError> {
    if field.is_empty() {
        return Err(FieldError::Empty);
    }
    for (i, &b) in field.iter().enumerate() {
        if !b.is_ascii_digit() && b != b'.' {
            return Err(FieldError::Digit(i));
        }
    }
    let dot_count = field.iter().filter(|&&b| b == b'.').count();
    if dot_count != 1 {
        return Err(FieldError::Format);
    }
    let dot_idx = field.iter().position(|&b| b == b'.').unwrap();
    let int_part = &field[..dot_idx];
    let frac_part = &field[dot_idx + 1..];
    if int_part.is_empty() || frac_part.len() != 2 {
        return Err(FieldError::Format);
    }
    let mut combined = strip_leading_zeros(int_part).to_vec();
    combined.extend_from_slice(frac_part);
    if !le_max(&combined, MAX_CENTS) {
        return Err(FieldError::Range);
    }
    Ok(combined)
}

fn process_line(line_no: usize, line: &[u8], out: &mut impl Write) {
    let commas: Vec<usize> = line
        .iter()
        .enumerate()
        .filter(|&(_, &b)| b == b',')
        .map(|(i, _)| i)
        .collect();

    if commas.len() < 2 {
        writeln!(out, "err {} {} missing", line_no, line.len() + 1).unwrap();
        return;
    }
    if commas.len() > 2 {
        writeln!(out, "err {} {} extra", line_no, commas[2] + 1).unwrap();
        return;
    }

    let c1 = commas[0];
    let c2 = commas[1];
    let id_field = &line[..c1];
    let qty_field = &line[c1 + 1..c2];
    let price_field = &line[c2 + 1..];
    let id_start = 1; // column, 1-indexed
    let qty_start = c1 + 2;
    let price_start = c2 + 2;

    macro_rules! report {
        ($start:expr, $err:expr) => {
            let (col, kind) = match $err {
                FieldError::Empty => ($start, "empty"),
                FieldError::Digit(off) => ($start + off, "digit"),
                FieldError::Format => ($start, "format"),
                FieldError::Range => ($start, "range"),
            };
            writeln!(out, "err {} {} {}", line_no, col, kind).unwrap();
            return;
        };
    }

    if let Err(e) = check_int_field(id_field, MAX_ID) {
        report!(id_start, e);
    }
    if let Err(e) = check_int_field(qty_field, MAX_QTY) {
        report!(qty_start, e);
    }
    let cents_digits = match check_price_field(price_field) {
        Ok(c) => c,
        Err(e) => {
            report!(price_start, e);
        }
    };

    let id_val = to_u32(id_field);
    let qty_val = to_u16(qty_field);
    let cents_val = to_u64(&cents_digits);
    writeln!(out, "ok {} {} {}", id_val, qty_val, cents_val).unwrap();
}

fn main() {
    let data = read_input();

    let lines: Vec<&[u8]> = if data.is_empty() {
        Vec::new()
    } else {
        let mut parts: Vec<&[u8]> = data.split(|&b| b == b'\n').collect();
        if data.ends_with(b"\n") {
            parts.pop();
        }
        parts
    };

    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());

    for (idx, line) in lines.iter().enumerate() {
        process_line(idx + 1, line, &mut out);
    }
}
