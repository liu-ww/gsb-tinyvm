"""Disassembler: .tvm programs -> assembler text (round-trip compatible).

The text emitted here assembles back to the identical byte images via
:func:`tinyvm.assembler.assemble`.  Because the binary container strips
symbolic information, jumps render as numeric addresses and data renders
as ``.byte``/``.word`` directives.
"""

from __future__ import annotations

from typing import List

from .isabits import MNEMONICS, OP_STORE_MEM, SIGNATURES, decode_tagged_byte
from .program import Program


def disassemble(program: Program, show_comments: bool = True) -> str:
    lines: List[str] = ["; tinyvm disassembly"]
    if show_comments:
        lines.append(f"; entry=0x{program.entry:04x}")
    lines.append(".code")
    lines.extend(_disassemble_code(program))
    if program.data:
        lines.append(".data")
        lines.extend(_disassemble_data(program))
    if program.vectors:
        lines.append("; interrupt vectors")
        for vector in sorted(program.vectors):
            lines.append(f".vector {vector}, 0x{program.vectors[vector]:04x}")
    return "\n".join(lines) + "\n"


def disassemble_bytes(code: bytes, base: int = 0x0100) -> str:
    """Disassemble a raw code image (no container) as a convenience helper."""
    program = Program(entry=base, code_base=base, code=bytes(code))
    return disassemble(program)


def _disassemble_code(program: Program) -> List[str]:
    lines: List[str] = []
    code = program.code
    base = program.code_base
    offset = 0
    while offset + 4 <= len(code):
        address = base + offset
        opcode = code[offset]
        a, b, c = code[offset + 1], code[offset + 2], code[offset + 3]
        rendered = _render_instruction(opcode, a, b, c)
        if rendered is None:
            rendered = f".byte 0x{opcode:02x}, 0x{a:02x}, 0x{b:02x}, 0x{c:02x}"
        lines.append(f"  {rendered:<28} ; 0x{address:04x}")
        offset += 4
    if offset < len(code):  # pragma: no cover - instructions are 4 bytes
        tail = code[offset:]
        lines.append("  .byte " + ", ".join(f"0x{x:02x}" for x in tail))
    return lines


def _render_instruction(opcode: int, a: int, b: int, c: int) -> str | None:
    mnemonic = MNEMONICS.get(opcode)
    signature = SIGNATURES.get(opcode)
    if mnemonic is None or signature is None:
        return None
    kinds = [kind for kind in signature if kind]
    raw = {0: a, 1: b, 2: c}
    parts: List[str] = []
    # STORE_MEM: absolute A/B=addr,C=src (b>=0x40); indirect A=addrreg,B=src.
    if opcode == OP_STORE_MEM:
        if b <= 0x0F:
            return f"STORE_MEM R{a}, R{b}"
        address = a | (b << 8)
        return f"STORE_MEM 0x{address:04x}, R{c}"
    for index, kind in enumerate(kinds):
        byte = raw[index]
        if kind == "r":
            parts.append(f"R{byte}")
        elif kind == "u8":
            parts.append(str(byte))
        elif kind == "ri":
            tag, value = decode_tagged_byte(byte)
            parts.append(f"R{value}" if tag == "reg" else f"#{value}")
        elif kind == "addr16":
            low_byte = raw[index]
            high_byte = raw[index + 1]
            parts.append(f"0x{(low_byte | (high_byte << 8)):04x}")
        else:  # pragma: no cover
            return None
    if parts:
        return f"{mnemonic} {', '.join(parts)}"
    return mnemonic


def _disassemble_data(program: Program) -> List[str]:
    lines: List[str] = []
    data = program.data
    base = program.data_base
    offset = 0
    while offset + 2 <= len(data):
        address = base + offset
        value = data[offset] | (data[offset + 1] << 8)
        lines.append(
            f"  .word 0x{value:04x}                       ; 0x{address:04x}"
        )
        offset += 2
    if offset < len(data):
        lines.append(
            f"  .byte 0x{data[offset]:02x}                       ; 0x{base + offset:04x}"
        )
    return lines
