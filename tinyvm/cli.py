"""Command line interface: ``python -m tinyvm {asm,disasm,run,bench}``."""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence

from .assembler import assemble
from .disassembler import disassemble
from .errors import AssemblerError, TinyVMError, Trap
from .program import Program
from .vm import VM


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tinyvm", description="A tiny bytecode virtual machine (pure stdlib)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    asm_p = sub.add_parser("asm", help="assemble a .asm file into a .tvm image")
    asm_p.add_argument("source")
    asm_p.add_argument("-o", "--output")

    dis_p = sub.add_parser("disasm", help="disassemble a .tvm image")
    dis_p.add_argument("image")
    dis_p.add_argument("-o", "--output")

    run_p = sub.add_parser("run", help="execute a .tvm image")
    run_p.add_argument("image")
    run_p.add_argument("--trace", action="store_true",
                       help="snapshot every instruction to stderr")
    run_p.add_argument("--max-cycles", type=int, default=100_000_000)

    sub.add_parser("bench", help="benchmark: fibonacci(30)")
    args = parser.parse_args(argv)

    try:
        if args.command == "asm":
            return _cmd_asm(args)
        if args.command == "disasm":
            return _cmd_disasm(args)
        if args.command == "run":
            return _cmd_run(args)
        if args.command == "bench":
            return _cmd_bench()
    except AssemblerError as exc:
        print(f"asm error: {exc}", file=sys.stderr)
        return 2
    except Trap as exc:
        print(f"vm trap: {exc}", file=sys.stderr)
        return 3
    except TinyVMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _default_output(path: str, suffix: str) -> str:
    return str(Path(path).with_suffix(suffix))


def _cmd_asm(args: argparse.Namespace) -> int:
    source = _read(args.source)
    program = assemble(source)
    output = args.output or _default_output(args.source, ".tvm")
    Path(output).write_bytes(program.serialize())
    print(f"assembled {args.source} -> {output} "
          f"({len(program.code)} code, {len(program.data)} data bytes)")
    return 0


def _cmd_disasm(args: argparse.Namespace) -> int:
    blob = Path(args.image).read_bytes()
    text = disassemble(Program.deserialize(blob))
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    blob = Path(args.image).read_bytes()
    program = Program.deserialize(blob)
    trace = sys.stderr if args.trace else None
    vm = VM(program, max_cycles=args.max_cycles)
    started = time.perf_counter()
    vm.run(trace=trace)
    elapsed = time.perf_counter() - started
    qps = vm.cycles / elapsed if elapsed > 0 else float("inf")
    print(
        f"[tinyvm] halted after {vm.cycles} instructions in "
        f"{elapsed:.4f}s ({qps:,.0f} instructions/s)",
        file=sys.stderr,
    )
    return 0


def _cmd_bench() -> int:
    # The embedded source above is intentionally simple; use a dedicated
    # correct iterative+recursive program built below for reliable numbers.
    source = _BENCH_SOURCE
    program = assemble(source)
    vm = VM(program)
    started = time.perf_counter()
    vm.run()
    elapsed = time.perf_counter() - started
    qps = vm.cycles / elapsed if elapsed > 0 else float("inf")
    print(f"fib(30) = {vm.r(2)}")
    print(f"instructions: {vm.cycles}")
    print(f"elapsed: {elapsed:.4f}s")
    print(f"qps: {qps:,.0f} instructions/s")
    expected = 832040 & 0xFFFF
    if vm.r(2) != expected:
        print(f"bench failed: fib(30) mod 65536 = {vm.r(2)}, expected {expected}",
              file=sys.stderr)
        return 1
    return 0


# Recursive fibonacci without a MOV opcode: ADD Rd, Rs, #0 is used.
_BENCH_SOURCE = """\
; fibonacci(30) benchmark for tinyvm (iterative: ~30 loop iterations).
; The 16-bit register file wraps, so the result is the unsigned mod 65536
; value; the host side compares against 832040 & 0xffff.
; R1=n counter, R3=a, R4=b, R5=tmp
.code
.entry _start
_start:
  LOAD_IMM R3, 1, 0            ; a = 1
  LOAD_IMM R4, 0, 0            ; b = 0
  LOAD_IMM R1, 30, 0           ; n = 30
loop:
  CMP R1, #0
  JEQ done
  ADD R5, R3, R4               ; tmp = a + b (mod 65536)
  ADD R3, R4, #0               ; a = b
  ADD R4, R5, #0               ; b = tmp
  DEC R1
  JMP loop
done:
  ADD R12, R4, #0              ; keep answer across I/O
  LOAD_IMM R0, 0, 0
  LOAD_IMM R1, 0, 0
  ADD R2, R4, #0
  SYSCALL 0                    ; print unsigned fib(30) mod 65536
  LOAD_IMM R0, 3, 0
  LEA R1, nl
  LOAD_IMM R2, 1, 0
  SYSCALL 3
  ADD R2, R12, #0
  SYSCALL 4

.data
nl: .byte 10
"""
