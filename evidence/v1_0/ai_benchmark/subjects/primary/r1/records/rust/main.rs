use std::io::{self, Read, Write};

const MAX_CENTS: u64 = 9_223_372_036_854_775_807;

fn read_input() -> Vec<u8> {
    let mut bytes = Vec::new();
    io::stdin().read_to_end(&mut bytes).unwrap();
    bytes
}

fn check_id(field: &[u8], start: usize) -> Result<u32, (usize, &'static str)> {
    if field.is_empty() {
        return Err((start + 1, "empty"));
    }
    for (i, &b) in field.iter().enumerate() {
        if !b.is_ascii_digit() {
            return Err((start + i + 1, "digit"));
        }
    }
    match std::str::from_utf8(field).unwrap().parse::<u32>() {
        Ok(v) => Ok(v),
        Err(_) => Err((start + 1, "range")),
    }
}

fn check_qty(field: &[u8], start: usize) -> Result<u16, (usize, &'static str)> {
    if field.is_empty() {
        return Err((start + 1, "empty"));
    }
    for (i, &b) in field.iter().enumerate() {
        if !b.is_ascii_digit() {
            return Err((start + i + 1, "digit"));
        }
    }
    match std::str::from_utf8(field).unwrap().parse::<u16>() {
        Ok(v) => Ok(v),
        Err(_) => Err((start + 1, "range")),
    }
}

fn check_price(field: &[u8], start: usize) -> Result<u64, (usize, &'static str)> {
    if field.is_empty() {
        return Err((start + 1, "empty"));
    }
    for (i, &b) in field.iter().enumerate() {
        if !(b.is_ascii_digit() || b == b'.') {
            return Err((start + i + 1, "digit"));
        }
    }
    let dots: Vec<usize> = field
        .iter()
        .enumerate()
        .filter(|&(_, &b)| b == b'.')
        .map(|(i, _)| i)
        .collect();
    if dots.len() != 1 {
        return Err((start + 1, "format"));
    }
    let dot = dots[0];
    if dot == 0 || field.len() - dot - 1 != 2 {
        return Err((start + 1, "format"));
    }
    let mut cents_str = String::with_capacity(field.len() - 1);
    cents_str.push_str(std::str::from_utf8(&field[0..dot]).unwrap());
    cents_str.push_str(std::str::from_utf8(&field[dot + 1..]).unwrap());
    match cents_str.parse::<u64>() {
        Ok(v) if v <= MAX_CENTS => Ok(v),
        _ => Err((start + 1, "range")),
    }
}

fn process_line(line: &[u8], line_no: usize, out: &mut String) {
    let commas: Vec<usize> = line
        .iter()
        .enumerate()
        .filter(|&(_, &b)| b == b',')
        .map(|(i, _)| i)
        .collect();

    if commas.len() < 2 {
        out.push_str(&format!("err {} {} missing\n", line_no, line.len() + 1));
        return;
    }
    if commas.len() > 2 {
        out.push_str(&format!("err {} {} extra\n", line_no, commas[2] + 1));
        return;
    }

    let c0 = commas[0];
    let c1 = commas[1];
    let id_field = &line[0..c0];
    let qty_field = &line[c0 + 1..c1];
    let price_field = &line[c1 + 1..line.len()];

    let id = match check_id(id_field, 0) {
        Ok(v) => v,
        Err((col, kind)) => {
            out.push_str(&format!("err {} {} {}\n", line_no, col, kind));
            return;
        }
    };
    let qty = match check_qty(qty_field, c0 + 1) {
        Ok(v) => v,
        Err((col, kind)) => {
            out.push_str(&format!("err {} {} {}\n", line_no, col, kind));
            return;
        }
    };
    let cents = match check_price(price_field, c1 + 1) {
        Ok(v) => v,
        Err((col, kind)) => {
            out.push_str(&format!("err {} {} {}\n", line_no, col, kind));
            return;
        }
    };

    out.push_str(&format!("ok {} {} {}\n", id, qty, cents));
}

fn main() {
    let mut bytes = read_input();
    let mut out = String::new();

    if !bytes.is_empty() {
        if *bytes.last().unwrap() == b'\n' {
            bytes.pop();
        }
        let mut line_no = 1usize;
        for line in bytes.split(|&b| b == b'\n') {
            process_line(line, line_no, &mut out);
            line_no += 1;
        }
    }

    let stdout = io::stdout();
    let mut lock = stdout.lock();
    lock.write_all(out.as_bytes()).unwrap();
}
