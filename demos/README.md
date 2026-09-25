# Demos

Each demo runs from a fresh checkout with one command and prints what happened, and `tests/projects/test_demos.py` runs each one again.

| Demo | What it shows | Command |
|---|---|---|
| [repair](repair/README.md) | An agent fixes a bug through the edit host. The host refuses an edit that prints and a tidy-up that changes behaviour, and says why. `cairn diff` then names the one function that changed and the input where it differs. | `make demo-repair` |
| [numeric](numeric/README.md) | A sweep of a heat plate, written once, runs on host lanes and as CUDA lanes. The host result stays inside a stated error bound against an f64 reference, and has the same bits as a C++ loop. | `make demo-numeric` |
| [visual](visual/README.md) | An agent asks what a program draws, reads in the layout record that a colour bar covers the plot, moves the bar, and gets back the new frames and the effect rows the edit changed. | `make demo-visual` |
| [implement](implement/README.md) | An agent writes faster implementations of a sum of squares through `cairn mcp`. The host refuses a looser tolerance, and validation refuses a candidate that drops the tail, shrunk to `n = 5`. The corrected one and a parameterized one validate, and `cairn tune` times them on this host, writes the fastest, and reports the difference with each line labelled. | `make demo-implement` |

The agents in these demos are scripted: their replies are written down and replayed. Everything the host, the compiler, Z3, the timer and the programs say is computed on every run. `python3 demos/repair/run.py --live MODEL` sends the same packets to a real model through `claude -p`, and `demos/repair/live-sonnet-5.json` is one such run.

`transcript.py` is what the four scripts share: `cairn` run from the repository root as a reader types it, a path as the reader types it, and the CPU a timing ran on.
