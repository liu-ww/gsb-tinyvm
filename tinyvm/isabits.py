"""Static ISA description: opcodes, operand signatures, memory map.

Every instruction is exactly 4 bytes::

    [ opcode ] [ operand A ] [ operand B ] [ operand C ]

Operands do not carry per-byte type bits.  Instead, each opcode fully
defines the meaning of its operands (see ``SIGNATURES``).

Operand kinds
-------------
``"r"``   : register number, raw byte 0..15 (R0..R15)
``"ri"``  : register (raw 0..15) OR signed immediate -64..127, encoded with
            the high bit tag: ``enc = imm ^ 0x80`` for immediates,
            ``enc = reg`` for registers.
``"u8"``  : raw unsigned immediate, 0..255
``"i8"``  : same encoding as ``ri`` but immediate-only (used by INT)
``"addr"``: 16-bit absolute address occupying two operand bytes, little
            endian.  When it is the first logical operand (branches, CALL,
            STORE_MEM source side) it lives in operand A=low, B=high.
            When a destination register comes first (LOAD_MEM/LEA), the
            register is operand A and the address is B=low, C=high.
            Bytes are RAW (no tag) so the full 64 KiB space is addressable.
"""

from __future__ import annotations

from typing import Dict, Tuple

# ---------------------------------------------------------------------------
# Registers
# ---------------------------------------------------------------------------
NUM_GP_REGISTERS = 16
REG_PC = 16
REG_SP = 17
REG_FP = 18
REG_FLAGS = 19
NUM_REGISTERS = 20

REGISTER_NAMES: Dict[str, int] = {
    **{f"R{i}": i for i in range(NUM_GP_REGISTERS)},
    **{f"r{i}": i for i in range(NUM_GP_REGISTERS)},
    "PC": REG_PC,
    "SP": REG_SP,
    "FP": REG_FP,
    "FLAGS": REG_FLAGS,
}

# FLAGS bit masks
FLAG_CARRY = 1 << 0
FLAG_ZERO = 1 << 1
FLAG_SIGN = 1 << 2
FLAG_OVERFLOW = 1 << 3

# ---------------------------------------------------------------------------
# Opcodes
# ---------------------------------------------------------------------------
# arithmetic / logic
OP_ADD = 0x01
OP_SUB = 0x02
OP_MUL = 0x03
OP_DIV = 0x04
OP_MOD = 0x05
OP_AND = 0x06
OP_OR = 0x07
OP_XOR = 0x08
OP_SHL = 0x09
OP_SHR = 0x0A
OP_CMP = 0x0B
OP_NEG = 0x0C
OP_NOT = 0x0D
OP_INC = 0x0E
OP_DEC = 0x0F
# control flow
OP_JMP = 0x10
OP_JEQ = 0x11
OP_JNE = 0x12
OP_JLT = 0x13
OP_JGT = 0x14
OP_JLE = 0x15
OP_JGE = 0x16
OP_CALL = 0x17
OP_RET = 0x18
OP_INT = 0x19
OP_IRET = 0x1A
# memory / I/O
OP_LOAD_IMM = 0x20
OP_LOAD_REG = 0x21
OP_LOAD_MEM = 0x22
OP_STORE_MEM = 0x23
OP_PUSH = 0x24
OP_POP = 0x25
OP_LEA = 0x26
OP_SYSCALL = 0x27

OPCODES: Dict[str, int] = {
    "ADD": OP_ADD,
    "SUB": OP_SUB,
    "MUL": OP_MUL,
    "DIV": OP_DIV,
    "MOD": OP_MOD,
    "AND": OP_AND,
    "OR": OP_OR,
    "XOR": OP_XOR,
    "SHL": OP_SHL,
    "SHR": OP_SHR,
    "CMP": OP_CMP,
    "NEG": OP_NEG,
    "NOT": OP_NOT,
    "INC": OP_INC,
    "DEC": OP_DEC,
    "JMP": OP_JMP,
    "JEQ": OP_JEQ,
    "JNE": OP_JNE,
    "JLT": OP_JLT,
    "JGT": OP_JGT,
    "JLE": OP_JLE,
    "JGE": OP_JGE,
    "CALL": OP_CALL,
    "RET": OP_RET,
    "INT": OP_INT,
    "IRET": OP_IRET,
    "LOAD_IMM": OP_LOAD_IMM,
    "LOAD_REG": OP_LOAD_REG,
    "LOAD_MEM": OP_LOAD_MEM,
    "STORE_MEM": OP_STORE_MEM,
    "PUSH": OP_PUSH,
    "POP": OP_POP,
    "LEA": OP_LEA,
    "SYSCALL": OP_SYSCALL,
}
MNEMONICS: Dict[int, str] = {v: k for k, v in OPCODES.items()}

# Operand signature for every opcode.  See module docstring for kind names.
SIGNATURES: Dict[int, Tuple[str, ...]] = {
    OP_ADD: ("r", "r", "ri"),
    OP_SUB: ("r", "r", "ri"),
    OP_MUL: ("r", "r", "ri"),
    OP_DIV: ("r", "r", "ri"),
    OP_MOD: ("r", "r", "ri"),
    OP_AND: ("r", "r", "ri"),
    OP_OR: ("r", "r", "ri"),
    OP_XOR: ("r", "r", "ri"),
    OP_SHL: ("r", "r", "ri"),
    OP_SHR: ("r", "r", "ri"),
    OP_CMP: ("r", "ri", ""),
    OP_NEG: ("r", "r", ""),
    OP_NOT: ("r", "r", ""),
    OP_INC: ("r", "", ""),
    OP_DEC: ("r", "", ""),
    OP_JMP: ("addr16", "", ""),
    OP_JEQ: ("addr16", "", ""),
    OP_JNE: ("addr16", "", ""),
    OP_JLT: ("addr16", "", ""),
    OP_JGT: ("addr16", "", ""),
    OP_JLE: ("addr16", "", ""),
    OP_JGE: ("addr16", "", ""),
    OP_CALL: ("addr16", "", ""),
    OP_RET: ("", "", ""),
    OP_INT: ("u8", "", ""),
    OP_IRET: ("", "", ""),
    OP_LOAD_IMM: ("r", "u8", "u8"),  # rd, imm low byte, imm high byte
    OP_LOAD_REG: ("r", "r", ""),    # rd, rs (16-bit word at [rs])
    OP_LOAD_MEM: ("r", "addr16", ""),  # rd, abs16 in B/C
    OP_STORE_MEM: ("addr16", "r", ""), # abs16 in A/B, rs
    OP_PUSH: ("ri", "", ""),
    OP_POP: ("r", "", ""),
    OP_LEA: ("r", "addr16", ""),
    OP_SYSCALL: ("u8", "", ""),
}

# ---------------------------------------------------------------------------
# Memory map (unified 16-bit address space, 64 KiB)
# ---------------------------------------------------------------------------
MEMORY_SIZE = 0x10000
IVT_START = 0x0000
IVT_END = 0x0100          # IVT occupies 0x0000..0x00FF
CODE_START = 0x0100       # code segment begins right after the IVT
CODE_END = 0x4000         # code segment:   0x0100..0x3FFF (read-only)
DATA_START = 0x4000
DATA_END = 0x8000         # data segment:   0x4000..0x7FFF
HEAP_START = 0x8000
HEAP_END = 0xB000         # heap grows up:  0x8000..0xAFFF
STACK_TOP = 0x10000       # first push decrements SP to 0xFFFF
STACK_BOTTOM = 0xB000     # stack grows down: 0xB000..0xFFFF
DEFAULT_ENTRY = CODE_START
ENTRY_LABEL = "_start"

INSTRUCTION_SIZE = 4

# ---------------------------------------------------------------------------
# Tagged immediate helpers (kind "ri" / "i8")
# ---------------------------------------------------------------------------
TAG_BIT = 0x80
IMM_MIN = -64
IMM_MAX = 63


def encode_tagged_immediate(value: int) -> int:
    """Encode a signed -64..127 immediate into a tagged operand byte."""
    if not (IMM_MIN <= value <= IMM_MAX):
        raise ValueError(f"immediate {value} out of range ({IMM_MIN}..{IMM_MAX})")
    return (value & 0x7F) ^ TAG_BIT


def decode_tagged_byte(byte: int) -> tuple[str, int]:
    """Return ("reg", n) or ("imm", value) for a tagged operand byte."""
    if byte & TAG_BIT:
        return "imm", sign_extend_7(byte ^ TAG_BIT)
    return "reg", byte


def sign_extend_7(value: int) -> int:
    """Interpret a 7-bit unsigned value as signed (-64..63 style)."""
    value &= 0x7F
    return value - 0x80 if value & 0x40 else value


# ---------------------------------------------------------------------------
# 16-bit arithmetic helpers
# ---------------------------------------------------------------------------
def u16(value: int) -> int:
    return value & 0xFFFF


def s16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value
