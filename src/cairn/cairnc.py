"""Native compilation API and compatibility CLI."""
from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path
from typing import Any
from .syntax import *
from .expansion import derive_wire, specialize
from .checking import Binding, Checker
from .codegen import Emitter, RUNTIME

def compile_source(source:str)->tuple[str,dict[str,Any]]:
    p=specialize(derive_wire(Parser(source).parse()))
    checker=Checker(p); receipts=checker.check()
    cpp=Emitter(p).emit()
    manifest={"compiler":VERSION,"source_sha256":hashlib.sha256(source.encode()).hexdigest(),
              "generated_sha256":hashlib.sha256(cpp.encode()).hexdigest(),
              "runtime_sha256":hashlib.sha256(RUNTIME.encode()).hexdigest(),
              "function_count":len(p.functions),"families":[list(x) for x in p.families],
              "wire_derivations":p.derivations,
              "trusted_lowering_rules":["bounded-collector/1","unsigned-little-endian-wire/1"],
              "functions":receipts,"formal_status":"not-verified",
              "ffi_requires":"Each nonempty view describes live, initialized, correctly typed storage for its stated extent throughout the call; no concurrent external mutation.",
              "target_profile":"64-bit host, C++20, GCC/Clang overflow builtins, strict floating mode"}
    return cpp,manifest

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source",type=Path)
    ap.add_argument("-o","--output",type=Path)
    ap.add_argument("--check",action="store_true",help="Check and report without writing generated files")
    ap.add_argument("--receipt",type=Path)
    args=ap.parse_args()
    try:
        source=args.source.read_text(encoding="utf-8")
        cpp,receipt=compile_source(source)
        if args.output and not args.check:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(cpp)
            (args.output.parent/"cairn_runtime.hpp").write_text(RUNTIME)
        if args.receipt: args.receipt.write_text(json.dumps(receipt,indent=2)+"\n")
        if not args.output or args.check:
            print(json.dumps({"status":"accepted","compiler":VERSION,"functions":receipt["function_count"],"formal_status":"not-verified"}))
        return 0
    except Diagnostic as e:
        print(json.dumps(e.data),file=sys.stderr); return 1
    except (OSError,UnicodeError,RecursionError,ValueError,OverflowError) as e:
        print(json.dumps({"protocol":"cairn.diagnostic/1","status":"unknown","code":"E-RESOURCE-OR-IO","message":str(e)}),file=sys.stderr); return 2

if __name__=="__main__": sys.exit(main())
