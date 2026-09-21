"""Error and trap types for tinyvm."""

from __future__ import annotations

from enum import Enum


class TrapKind(Enum):
    """Kinds of runtime traps the execution engine can raise."""

    DIVIDE_BY_ZERO = "divide by zero"
    STACK_OVERFLOW = "stack overflow"
    STACK_UNDERFLOW = "stack underflow"
    SELF_MODIFYING_CODE = "self-modifying code"
    PC_OUT_OF_BOUNDS = "pc out of bounds"
    MEMORY_OUT_OF_BOUNDS = "memory out of bounds"
    INVALID_OPCODE = "invalid opcode"
    INVALID_SYSCALL = "invalid syscall"
    UNHANDLED_INTERRUPT = "unhandled interrupt"
    INPUT_EXHAUSTED = "input exhausted"
    INVALID_INPUT = "invalid input"
    CYCLE_LIMIT = "cycle limit exceeded"


class VMTrap(Exception):
    """Raised when the VM hits a runtime trap."""

    def __init__(self, kind: TrapKind, message: str = "", pc: int | None = None) -> None:
        self.kind = kind
        self.pc = pc
        text = message or kind.value
        if pc is not None:
            text = f"{text} (pc=0x{pc:04X})"
        super().__init__(text)


class AssemblerError(Exception):
    """Raised for assembly-time errors, carrying the source position."""

    def __init__(self, message: str, line: int, col: int = 1) -> None:
        self.message = message
        self.line = line
        self.col = col
        super().__init__(f"line {line}, col {col}: {message}")
