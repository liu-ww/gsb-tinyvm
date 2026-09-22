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

from .isabits import (
    IMM_MAX,
    IMM_MIN,
    OP_STORE_MEM,
    SIGNATURES,
    encode_tagged_immediate,
)


class EncodeError(ValueError):
    pass


def encode_instruction(opcode: int, operands: Sequence) -> bytes:
    sig = SIGNATURES.get(opcode)
    if sig is None:
        raise EncodeError(f"unknown opcode 0x{opcode:02x}")

    words = [opcode, 0, 0, 0]
    values = list(operands)

    # STORE_MEM has two forms:
    #   * absolute:      (address, src_reg)
    #                    -> A=addr.lo, B=addr.hi, C=src_reg
    #   * reg-indirect:  ("indirect", addr_reg, src_reg)
    #                    -> A=addr_reg, B=src_reg, C=0
    # Runtime disambiguates via B <= 0x0F (register) vs the absolute
    # form's data high byte (>= 0x40 for every reachable target).
    indirect = (
        opcode == OP_STORE_MEM and len(values) == 1
        and isinstance(values[0], tuple) and values[0]
        and values[0][0] == "indirect"
    )
    if indirect:
        values = list(values[0])
    if opcode == OP_STORE_MEM and values and values[0] == "indirect":
        if len(values) != 3:
            raise EncodeError(
                "register-indirect STORE_MEM needs ('indirect', addr, src)"
            )
        _, addr_reg, src_reg = values
        if not (0 <= addr_reg <= 15 and 0 <= src_reg <= 15):
            raise EncodeError("register-indirect operands must be R0..R15")
        return bytes([opcode, addr_reg, src_reg, 0])

    expected = [kind for kind in sig if kind]
    if len(values) != len(expected):
        raise EncodeError(
            f"operand count mismatch: expected {len(expected)}, got {len(values)}"
        )

    if opcode == OP_STORE_MEM:
        address, register = values
        if not (0 <= address <= 0xFFFF):
            raise EncodeError(f"address 0x{address:x} out of range")
        if not (0 <= register <= 15):
            raise EncodeError(f"register {register} out of range (0..15)")
        return bytes([opcode, address & 0xFF, (address >> 8) & 0xFF, register])

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

    for index, (kind, raw) in enumerate(zip(expected, values)):
        if kind == "r":
            if not (0 <= raw <= 15):
                raise EncodeError(f"register {raw} out of range (0..15)")
            words[index + 1] = raw
        elif kind == "ri":
            put_tagged(index + 1, raw)
        elif kind == "u8":
            if not (0 <= raw <= 255):
                raise EncodeError(f"immediate {raw} out of range (0..255)")
            words[index + 1] = raw
        elif kind == "addr16":
            if not (0 <= raw <= 0xFFFF):
                raise EncodeError(f"address 0x{raw:x} out of range")
            words[index + 1] = raw & 0xFF
            words[index + 2] = (raw >> 8) & 0xFF
        else:  # pragma: no cover
            raise EncodeError(f"internal error: unknown operand kind {kind!r}")
    return bytes(words)
