"""tinyvm: a self-contained 16-bit bytecode virtual machine."""

from .assembler import assemble
from .disassembler import disassemble
from .errors import (
    AssemblerError,
    DivideByZero,
    HeapError,
    InvalidOpcode,
    MemoryFault,
    PCFault,
    StackOverflow,
    StackUnderflow,
    Trap,
)
from .program import Program
from .vm import VM

__all__ = [
    "VM",
    "Program",
    "assemble",
    "disassemble",
    "AssemblerError",
    "Trap",
    "DivideByZero",
    "HeapError",
    "InvalidOpcode",
    "MemoryFault",
    "PCFault",
    "StackOverflow",
    "StackUnderflow",
]
