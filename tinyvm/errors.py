"""TinyVM exception hierarchy."""


class TinyVMError(Exception):
    """Base class for every tinyvm error."""


class AssemblerError(TinyVMError):
    """Raised for assembly errors.

    ``line`` and ``column`` are 1-based positions inside the source file.
    """

    def __init__(self, message: str, line: int = 0, column: int = 0) -> None:
        self.line = line
        self.column = column
        if line:
            location = f"line {line}"
            if column:
                location += f", column {column}"
            super().__init__(f"{location}: {message}")
        else:
            super().__init__(message)


class Trap(TinyVMError):
    """Raised when a running program triggers a hardware trap."""

    def __init__(self, message: str, pc: int = -1) -> None:
        self.pc = pc
        if pc >= 0:
            super().__init__(f"trap at pc=0x{pc:04x}: {message}")
        else:
            super().__init__(f"trap: {message}")


class HeapError(Trap):
    """Raised on invalid heap operations (bad free, corrupted heap)."""


class DivideByZero(Trap):
    def __init__(self, pc: int = -1) -> None:
        super().__init__("division by zero", pc)


class StackOverflow(Trap):
    def __init__(self, pc: int = -1) -> None:
        super().__init__("stack overflow", pc)


class StackUnderflow(Trap):
    def __init__(self, pc: int = -1) -> None:
        super().__init__("stack underflow", pc)


class MemoryFault(Trap):
    def __init__(self, address: int, reason: str = "memory access out of bounds",
                 pc: int = -1) -> None:
        self.address = address
        super().__init__(f"{reason} (addr=0x{address:04x})", pc)


class InvalidOpcode(Trap):
    def __init__(self, opcode: int, pc: int = -1) -> None:
        self.opcode = opcode
        super().__init__(f"invalid opcode 0x{opcode:02x}", pc)


class PCFault(Trap):
    def __init__(self, pc: int) -> None:
        super().__init__("pc outside executable code segment", pc)
