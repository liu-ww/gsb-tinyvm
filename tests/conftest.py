"""Shared helpers for tinyvm tests."""

from __future__ import annotations

import io

from tinyvm.assembler import assemble
from tinyvm.vm import VM


def run_asm(source: str, max_cycles: int = 1_000_000, stdin_text: str = "") -> VM:
    """Assemble source, run it to halt, and return the finished VM."""
    program = assemble(source)
    vm = VM(stdout=io.StringIO(), stdin=io.StringIO(stdin_text))
    vm.load_program(program)
    vm.run(max_cycles=max_cycles)
    return vm


def make_vm(source: str, stdin_text: str = "") -> VM:
    """Assemble and load source without running it (for trap tests)."""
    program = assemble(source)
    vm = VM(stdout=io.StringIO(), stdin=io.StringIO(stdin_text))
    vm.load_program(program)
    return vm
