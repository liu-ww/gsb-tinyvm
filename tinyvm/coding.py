"""Shared operand encoding / decoding helpers for assembler and disassembler.

Operand byte layout for the instruction word ``[op][A][B][C]`` is opcode
specific:

* binary ALU ``ADD/SUB/... Rdest, Rsrc, Rsrc|#imm`` -> A=dest, B=src, C=ri
* ``CMP Rsrc, Rsrc|#imm``                            -> A=src,  B=ri
* branches / ``CALL target``                         -> A=lo,   B=hi
* ``STORE_MEM addr, Rs``                             -> A=lo, B=hi, C=Rs
* ``LOAD_MEM/LEA Rd, addr``                          -> A=Rd,  B=lo, C=hi
"""

from __future__ import annotations

from typing import Sequence, Tuple

from .isabits import IMM_MAX, IMM_MIN, SIGNATURES, encode_tagged_immediate


class EncodeError(ValueError):
    pass


def encode_instruction(opcode: int, operands: Sequence) -> bytes:
    sig = SIGNATURES.get(opcode)
    if sig is None:
        raise EncodeError(f"unknown opcode 0x{opcode:02x}")
    expected = [kind for kind in sig if kind]
    if len(operands) != len(expected):
        raise EncodeError(
            f"operand count mismatch: expected {len(expected)}, got {len(operands)}"
        )

    words = [opcode, 0, 0, 0]
    values = list(operands)

    def put_tagged(slot: int, raw: Tuple[str, int] | int) -> None:
        tag, payload = raw if isinstance(raw, tuple) else ("reg", raw)
        if tag == "reg":
            if not (0 <= payload <= 15):
                raise EncodeError(f"register {payload} out of range (0..15)")
            words[slot] = payload
        else:
            if not (IMM_MIN <= payload <= IMM_MAX):
                raise EncodeError(
                    f"immediate {payload} out of range ({IMM_MIN}..{IMM_MAX})"
                )
            words[slot] = encode_tagged_immediate(payload)

    def put_reg(slot: int, raw: int) -> None:
        if not (0 <= raw <= 15):
            raise EncodeError(f"register {raw} out of range (0..15)")
        words[slot] = raw

    def put_u8(slot: int, raw: int) -> None:
        if not (0 <= raw <= 255):
            raise EncodeError(f"immediate {raw} out of range (0..255)")
        words[slot] = raw

    def put_addr(low_slot: int, raw: int) -> None:
        if not (0 <= raw <= 0xFFFF):
            raise EncodeError(f"address 0x{raw:x} out of range")
        words[low_slot] = raw & 0xFF
        words[low_slot + 1] = (raw >> 8) & 0xFF

    # Encoding is position sensitive; drive off logical operand index.
    for index, (kind, raw) in enumerate(zip(expected, values)):
        if kind == "r":
            put_reg(index + 1, raw)
        elif kind == "ri":
            put_tagged(index + 1, raw)
        elif kind == "u8":
            put_u8(index + 1, raw)
        elif kind == "addr16":
            put_addr(index + 1, raw)
        else:  # pragma: no cover
            raise EncodeError(f"internal error: unknown operand kind {kind!r}")
    return bytes(words)
