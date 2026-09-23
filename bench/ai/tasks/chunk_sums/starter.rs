use std::io::{self, Read};

// Reads the whole of standard input.
fn read_input() -> Vec<u8> {
    let mut bytes = Vec::new();
    io::stdin().read_to_end(&mut bytes).unwrap();
    bytes
}

fn main() {
    // Read the input and print the output that TASK.md describes.
}
