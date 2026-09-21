"""Disassembler for tinyvm bytecode.

Produces readable assembly text that round-trips through the assembler:
assemble(disassemble(program)) yields identical code/data/ivt bytes.
Jump/call targets become generated labels (L0, L1, ...), LEA targets into
the data section become data labels (D0, D1, ...).
"""

from __future__ import annotations

from .isa import (
    CODE_BASE,
    DATA_BASE,
    INSTR_SIZE,
    OPCODE_TABLE,
    Op,
    OperandForm,
)
from .program import Program

__all__ = ["disassemble", "format_instruction", "decode_instruction"]


def decode_instruction(op_byte: int) -> tuple[str, OperandForm]:
    """Decode an opcode byte into (mnemonic, operand form)."""
    try:
        op = Op(op_byte)
    except ValueError:
        raise ValueError(f"invalid opcode: 0x{op_byte:02X}") from None
    return OPCODE_TABLE[op]


def _reg(n: int) -> str:
    if not 0 <= n <= 15:
        raise ValueError(f"invalid register number: {n}")
    return f"R{n}"


def format_instruction(
    addr: int,
    data: bytes,
    code_labels: dict[int, str] | None = None,
    data_labels: dict[int, str] | None = None,
) -> str:
    """Format one 4-byte instruction as assembly text.

    code_labels/data_labels map target addresses to label names; targets
    without a label are printed as 0xXXXX hex addresses.
    """
    if len(data) != INSTR_SIZE:
        raise ValueError(f"instruction must be {INSTR_SIZE} bytes, got {len(data)}")
    mnemonic, form = decode_instruction(data[0])
    b1, b2, b3 = data[1], data[2], data[3]
    if form is OperandForm.NONE:
        return mnemonic
    if form is OperandForm.R:
        return f"{mnemonic} {_reg(b1)}"
    if form is OperandForm.RR:
        return f"{mnemonic} {_reg(b1)}, {_reg(b2)}"
    if form is OperandForm.RRR:
        return f"{mnemonic} {_reg(b1)}, {_reg(b2)}, {_reg(b3)}"
    if form is OperandForm.N8:
        return f"{mnemonic} {b1}"
    if form is OperandForm.A16:
        target = (b2 << 8) | b3
        text = (code_labels or {}).get(target, f"0x{target:04X}")
        return f"{mnemonic} {text}"
    if form is OperandForm.RA16:
        target = (b2 << 8) | b3
        labels = data_labels if data_labels and target in data_labels else code_labels
        text = (labels or {}).get(target, f"0x{target:04X}")
        return f"{mnemonic} {_reg(b1)}, {text}"
    if form is OperandForm.RI16:
        return f"{mnemonic} {_reg(b1)}, {(b2 << 8) | b3}"
    if form is OperandForm.R_IND:
        return f"{mnemonic} {_reg(b1)}, [{_reg(b2)}]"
    if form is OperandForm.IND_R:
        return f"{mnemonic} [{_reg(b1)}], {_reg(b2)}"
    raise ValueError(f"unhandled operand form: {form}")


def _is_code_addr(target: int, code_len: int) -> bool:
    return (
        CODE_BASE <= target < CODE_BASE + code_len
        and (target - CODE_BASE) % INSTR_SIZE == 0
    )


def _is_data_addr(target: int, data_len: int) -> bool:
    return DATA_BASE <= target < DATA_BASE + data_len


def disassemble(program: Program) -> str:
    """Disassemble a Program into readable, re-assemblable source text."""
    code = program.code
    if len(code) % INSTR_SIZE != 0:
        raise ValueError(
            f"code section length {len(code)} is not a multiple of {INSTR_SIZE}"
        )

    # Pass 1: collect label targets.
    code_targets: set[int] = set()
    data_targets: set[int] = set()
    for off in range(0, len(code), INSTR_SIZE):
        instr = code[off : off + INSTR_SIZE]
        _, form = decode_instruction(instr[0])
        if form is OperandForm.A16:
            target = (instr[2] << 8) | instr[3]
            if _is_code_addr(target, len(code)):
                code_targets.add(target)
        elif form is OperandForm.RA16:
            target = (instr[2] << 8) | instr[3]
            if _is_data_addr(target, len(program.data)):
                data_targets.add(target)
            elif _is_code_addr(target, len(code)):
                code_targets.add(target)
    for vec in program.ivt.values():
        if _is_code_addr(vec, len(code)):
            code_targets.add(vec)

    code_labels = {addr: f"L{i}" for i, addr in enumerate(sorted(code_targets))}
    data_labels = {addr: f"D{i}" for i, addr in enumerate(sorted(data_targets))}

    # Pass 2: emit text.
    lines: list[str] = []
    for n in sorted(program.ivt):
        vec = program.ivt[n]
        lines.append(f".ivt {n}, {code_labels.get(vec, f'0x{vec:04X}')}")
    if program.ivt:
        lines.append("")

    lines.append(".code")
    for off in range(0, len(code), INSTR_SIZE):
        addr = CODE_BASE + off
        if addr in code_labels:
            lines.append(f"{code_labels[addr]}:")
        text = format_instruction(
            addr, code[off : off + INSTR_SIZE], code_labels, data_labels
        )
        lines.append(f"    {text}")

    if program.data:
        lines.append("")
        lines.append(".data")
        label_offsets = sorted(a - DATA_BASE for a in data_targets)
        offset = 0
        while offset < len(program.data):
            addr = DATA_BASE + offset
            if addr in data_labels:
                lines.append(f"{data_labels[addr]}:")
            ahead = [o for o in label_offsets if o > offset]
            end = min([offset + 16, len(program.data)] + ahead)
            chunk = program.data[offset:end]
            lines.append("    .byte " + ", ".join(str(b) for b in chunk))
            offset = end

    return "\n".join(lines) + "\n"
