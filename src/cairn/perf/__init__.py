"""Predicted cost: how long a program will take, read from what the checker knows, before anything is built or run.

`work` counts what each function does in the sizes its signature is written in, `profile` holds what one machine
can do, `model` turns the two into a time with the bound that sets it, and `native` refines a loop's cost from the
compiler's own output without running it. A prediction is never a measurement, and every answer says how sure it is.
"""
