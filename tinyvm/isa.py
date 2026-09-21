"""Instruction set architecture constants for tinyvm.

Instruction format (fixed 4 bytes, big-endian):
    [opcode: 1B] [operand1: 1B] [operand2: 1B] [operand3: 1B]
"""

from __future__ import annotations

from enum import Enum, IntEnum

# ---------------------------------------------------------------------------
# Machine geometry
# ---------------------------------------------------------------------------

WORD_SIZE = 2            # a machine word is 16 bits
INSTR_SIZE = 4           # fixed instruction length in bytes
MEM_SIZE = 0x10000       # unified 16-bit address space (64 KiB)
REG_COUNT = 16           # R0-R15

# Memory map
IVT_BASE = 0x0000        # interrupt vector table: 0x0000-0x00FF
IVT_END = 0x00FF
IVT_ENTRY_SIZE = 2
IVT_MAX_ENTRIES = (IVT_END - IVT_BASE + 1) // IVT_ENTRY_SIZE  # 128

CODE_BASE = 0x0100       # code segment: 0x0100-0x3FFF (IVT occupies 0x0000-0x00FF)
CODE_END = 0x3FFF
DATA_BASE = 0x4000       # data segment: 0x4000-0x7FFF
DATA_END = 0x7FFF
HEAP_BASE = 0x8000       # heap: 0x8000-0xAFFF (grows upward)
HEAP_END = 0xAFFF
HEAP_SIZE = HEAP_END - HEAP_BASE + 1
STACK_BASE = 0xB000      # stack: 0xB000-0xFFFF (grows downward)
STACK_TOP = 0x10000      # exclusive; SP starts here

# FLAGS bits
FLAG_ZERO = 0x01
FLAG_SIGN = 0x02
FLAG_CARRY = 0x04
FLAG_OVERFLOW = 0x08

U16_MAX = 0xFFFF
S16_MIN = -0x8000
S16_MAX = 0x7FFF


def to_signed(value: int) -> int:
    """Interpret a 16-bit unsigned word as signed."""
    value &= U16_MAX
    return value - 0x10000 if value >= 0x8000 else value


def to_unsigned(value: int) -> int:
    """Mask an integer down to a 16-bit unsigned word."""
    return value & U16_MAX


# ---------------------------------------------------------------------------
# Opcodes
# ---------------------------------------------------------------------------


class Op(IntEnum):
    # Arithmetic / logic
    ADD = 0x01
    SUB = 0x02
    MUL = 0x03
    DIV = 0x04
    MOD = 0x05
    AND = 0x06
    OR = 0x07
    XOR = 0x08
    SHL = 0x09
    SHR = 0x0A
    CMP = 0x0B
    NEG = 0x0C
    NOT = 0x0D
    INC = 0x0E
    DEC = 0x0F
    # Control flow
    JMP = 0x20
    JEQ = 0x21
    JNE = 0x22
    JLT = 0x23
    JGT = 0x24
    JLE = 0x25
    JGE = 0x26
    CALL = 0x27
    RET = 0x28
    INT = 0x29
    IRET = 0x2A
    # Memory / I/O
    LOAD_IMM = 0x40
    LOAD_REG = 0x41
    LOAD_MEM = 0x42
    STORE_MEM = 0x43
    PUSH = 0x44
    POP = 0x45
    LEA = 0x46
    SYSCALL = 0x47


class Syscall(IntEnum):
    PRINT = 0     # print R0 as signed decimal + newline
    READ = 1      # read one integer from stdin into R0
    MMAP = 2      # R1==0: R0 = malloc(R0 bytes); else: free(R1), R0 = status
    WRITE = 3     # write R1 bytes from buffer at R0 to stdout
    HALT = 4      # halt the machine


class OperandForm(Enum):
    """Operand layout of an instruction (bytes 1-3)."""

    NONE = "none"        # RET, IRET
    R = "r"              # INC Rd
    RR = "rr"            # NEG Rd, Rs / CMP R1, R2
    RRR = "rrr"          # ADD Rd, Rs, Rt
    N8 = "n8"            # INT n / SYSCALL n
    A16 = "a16"          # JMP addr
    RA16 = "ra16"        # LEA Rd, addr
    RI16 = "ri16"        # LOAD_IMM Rd, imm16
    R_IND = "r_ind"      # LOAD_MEM Rd, [Rs]
    IND_R = "ind_r"      # STORE_MEM [Rd], Rs


# mnemonic -> (opcode, operand form)
INSTRUCTIONS: dict[str, tuple[Op, OperandForm]] = {
    "ADD": (Op.ADD, OperandForm.RRR),
    "SUB": (Op.SUB, OperandForm.RRR),
    "MUL": (Op.MUL, OperandForm.RRR),
    "DIV": (Op.DIV, OperandForm.RRR),
    "MOD": (Op.MOD, OperandForm.RRR),
    "AND": (Op.AND, OperandForm.RRR),
    "OR": (Op.OR, OperandForm.RRR),
    "XOR": (Op.XOR, OperandForm.RRR),
    "SHL": (Op.SHL, OperandForm.RRR),
    "SHR": (Op.SHR, OperandForm.RRR),
    "CMP": (Op.CMP, OperandForm.RR),
    "NEG": (Op.NEG, OperandForm.RR),
    "NOT": (Op.NOT, OperandForm.RR),
    "INC": (Op.INC, OperandForm.R),
    "DEC": (Op.DEC, OperandForm.R),
    "JMP": (Op.JMP, OperandForm.A16),
    "JEQ": (Op.JEQ, OperandForm.A16),
    "JNE": (Op.JNE, OperandForm.A16),
    "JLT": (Op.JLT, OperandForm.A16),
    "JGT": (Op.JGT, OperandForm.A16),
    "JLE": (Op.JLE, OperandForm.A16),
    "JGE": (Op.JGE, OperandForm.A16),
    "CALL": (Op.CALL, OperandForm.A16),
    "RET": (Op.RET, OperandForm.NONE),
    "INT": (Op.INT, OperandForm.N8),
    "IRET": (Op.IRET, OperandForm.NONE),
    "LOAD_IMM": (Op.LOAD_IMM, OperandForm.RI16),
    "LOAD_REG": (Op.LOAD_REG, OperandForm.RR),
    "LOAD_MEM": (Op.LOAD_MEM, OperandForm.R_IND),
    "STORE_MEM": (Op.STORE_MEM, OperandForm.IND_R),
    "PUSH": (Op.PUSH, OperandForm.R),
    "POP": (Op.POP, OperandForm.R),
    "LEA": (Op.LEA, OperandForm.RA16),
    "SYSCALL": (Op.SYSCALL, OperandForm.N8),
}

# opcode -> (mnemonic, operand form), reverse of INSTRUCTIONS
OPCODE_TABLE: dict[Op, tuple[str, OperandForm]] = {
    op: (name, form) for name, (op, form) in INSTRUCTIONS.items()
}
