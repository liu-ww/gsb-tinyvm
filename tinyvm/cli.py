"""Command line interface: python -m tinyvm asm|disasm|run|bench."""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

from .disassembler import disassemble
from .errors import AssemblerError, VMTrap
from .program import Program

# Benchmark program: iterative fib(30). Result lands in R0, computed
# mod 2^16 because machine words are 16 bits (fib(30) = 832040 -> 45608).
FIB_PROGRAM = """\
; fib(30), iterative
.code
start:
    LOAD_IMM R0, 30
    LOAD_IMM R1, 0
    LOAD_IMM R2, 1
    LOAD_IMM R3, 0
loop:
    CMP R0, R3
    JEQ done
    ADD R4, R1, R2
    LOAD_REG R1, R2
    LOAD_REG R2, R4
    DEC R0
    JMP loop
done:
    LOAD_REG R0, R1
    SYSCALL 4
"""


def _load_program(path: str) -> Program:
    """Load a program from a .tvmb binary or assemble a .asm source file."""
    p = Path(path)
    if p.suffix == ".asm":
        from .assembler import assemble

        return assemble(p.read_text())
    return Program.from_bytes(p.read_bytes())


def _cmd_asm(args: argparse.Namespace) -> int:
    from .assembler import assemble

    src_path = Path(args.source)
    try:
        program = assemble(src_path.read_text())
    except AssemblerError as exc:
        print(f"{src_path}:{exc}", file=sys.stderr)
        return 1
    out = args.output or str(src_path.with_suffix(".tvmb"))
    Path(out).write_bytes(program.to_bytes())
    print(
        f"assembled {src_path} -> {out} "
        f"(code={len(program.code)}B data={len(program.data)}B ivt={len(program.ivt)})"
    )
    return 0


def _cmd_disasm(args: argparse.Namespace) -> int:
    try:
        program = _load_program(args.input)
        text = disassemble(program)
    except (AssemblerError, ValueError) as exc:
        print(f"{args.input}: {exc}", file=sys.stderr)
        return 1
    if args.output:
        Path(args.output).write_text(text)
        print(f"disassembled {args.input} -> {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from .vm import VM

    try:
        program = _load_program(args.input)
    except (AssemblerError, ValueError) as exc:
        print(f"{args.input}: {exc}", file=sys.stderr)
        return 1
    vm = VM(stdout=sys.stdout, stdin=sys.stdin)
    vm.trace = args.trace
    vm.load_program(program)
    try:
        vm.run(max_cycles=args.max_cycles)
    except VMTrap as exc:
        print(f"trap: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_bench(args: argparse.Namespace) -> int:
    from .assembler import assemble
    from .vm import VM

    program = assemble(FIB_PROGRAM)
    vm = VM(stdout=io.StringIO())
    runs = 0
    start = time.perf_counter()
    elapsed = 0.0
    while elapsed < args.duration or runs == 0:
        vm.reset()
        vm.load_program(program)
        vm.run()
        runs += 1
        elapsed = time.perf_counter() - start
    per_run = vm.cycles
    total = per_run * runs
    qps = total / elapsed if elapsed > 0 else 0.0
    print(f"fib(30) = {vm.regs[0]} (mod 2^16)")
    print(f"runs: {runs}")
    print(f"instructions/run: {per_run}")
    print(f"total instructions: {total}")
    print(f"elapsed: {elapsed:.3f}s")
    print(f"QPS: {qps:,.0f} instructions/sec")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tinyvm", description="tinyvm bytecode virtual machine"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_asm = sub.add_parser("asm", help="assemble .asm source into a .tvmb binary")
    p_asm.add_argument("source", help="assembly source file")
    p_asm.add_argument("-o", "--output", help="output binary (default: <source>.tvmb)")
    p_asm.set_defaults(func=_cmd_asm)

    p_dis = sub.add_parser("disasm", help="disassemble a .tvmb binary (or .asm source)")
    p_dis.add_argument("input", help="input file")
    p_dis.add_argument("-o", "--output", help="output file (default: stdout)")
    p_dis.set_defaults(func=_cmd_disasm)

    p_run = sub.add_parser("run", help="run a .tvmb binary or .asm source file")
    p_run.add_argument("input", help="program file")
    p_run.add_argument(
        "--trace", action="store_true", help="print a snapshot per instruction"
    )
    p_run.add_argument(
        "--max-cycles",
        type=int,
        default=10_000_000,
        help="cycle limit before trapping (default: 10000000)",
    )
    p_run.set_defaults(func=_cmd_run)

    p_bench = sub.add_parser("bench", help="run the fib(30) benchmark")
    p_bench.add_argument(
        "--duration",
        type=float,
        default=1.0,
        help="benchmark duration in seconds (default: 1.0)",
    )
    p_bench.set_defaults(func=_cmd_bench)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
