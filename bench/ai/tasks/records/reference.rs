use std::io::{self, Read, Write};

// The decimal value of `field`, which holds only digits, or None when it passes `limit`.
fn value(field: &[u8], limit: u64) -> Option<u64> {
    field.iter().try_fold(0u64, |v, &b| {
        let v = v.checked_mul(10)?.checked_add(u64::from(b - b'0'))?;
        if v > limit {
            None
        } else {
            Some(v)
        }
    })
}

// The first error of `field` before its range is checked: empty, or a byte that is not allowed.
fn scan(field: &[u8], start: usize, price: bool) -> Result<(), (usize, &'static str)> {
    if field.is_empty() {
        return Err((start, "empty"));
    }
    match field.iter().position(|&b| !(b.is_ascii_digit() || (price && b == b'.'))) {
        Some(i) => Err((start + i, "digit")),
        None => Ok(()),
    }
}

fn record(line: &[u8]) -> Result<[u64; 3], (usize, &'static str)> {
    let fields: Vec<&[u8]> = line.split(|&b| b == b',').collect();
    if fields.len() < 3 {
        return Err((line.len() + 1, "missing"));
    }
    if fields.len() > 3 {
        return Err((fields[0].len() + fields[1].len() + fields[2].len() + 3, "extra"));
    }
    let mut values = [0u64; 3];
    let mut start = 1;
    for (i, limit) in [(0, 4294967295u64), (1, 65535)] {
        scan(fields[i], start, false)?;
        values[i] = value(fields[i], limit).ok_or((start, "range"))?;
        start += fields[i].len() + 1;
    }
    let price = fields[2];
    scan(price, start, true)?;
    let dot = price.iter().position(|&b| b == b'.');
    let shaped = matches!(dot, Some(d) if d > 0 && price.len() - d == 3 && !price[d + 1..].contains(&b'.'));
    if !shaped {
        return Err((start, "format"));
    }
    let d = dot.unwrap();
    let max = i64::MAX as u64;
    let whole = value(&price[..d], max / 100).ok_or((start, "range"))?;
    let cents = value(&price[d + 1..], 99).unwrap();
    values[2] = (whole * 100).checked_add(cents).filter(|&v| v <= max).ok_or((start, "range"))?;
    Ok(values)
}

fn main() {
    let mut input = Vec::new();
    io::stdin().read_to_end(&mut input).unwrap();
    let mut out = io::stdout().lock();
    let body = input.strip_suffix(b"\n").unwrap_or(&input);
    if input.is_empty() {
        return;
    }
    for (i, line) in body.split(|&b| b == b'\n').enumerate() {
        match record(line) {
            Ok([id, qty, cents]) => writeln!(out, "ok {} {} {}", id, qty, cents).unwrap(),
            Err((column, kind)) => writeln!(out, "err {} {} {}", i + 1, column, kind).unwrap(),
        }
    }
}
