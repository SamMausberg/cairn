# Freestanding image, v0.8.0

`examples/embedded` and its `trap` project built as bare-metal AArch64 images and run under QEMU virt on the AArch64 GH200 of the `v0.8.0` milestone, with compiler 0.6.0. `transcript.txt` is what the board printed, `size-nm.txt` each image's size and its undefined symbols (none), `versions.txt` every tool that touched it, and `summary.json` the claims and what they do not cover.

`capture.py` is part of the record: it is the script that built the images and wrote these files. It imports the package as it was laid out then (`cairn.build`, `cairn.project`, `cairn.toolchain`, since moved under `cairn.projects`), so it does not run against this tree. `make embedded` runs `tests/projects/test_freestanding.py`, which builds the same image and holds its UART transcript today.
