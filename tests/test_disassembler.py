"""Disassembler tests: output shape and assembler round-trip."""

import pytest

from tinyvm.assembler import assemble
from tinyvm.disassembler import disassemble
from tinyvm.program import Program


def round_trip(source: str):
    p1 = assemble(source)
    text = disassemble(p1)
    p2 = assemble(text)
    return p1, p2, text


def test_round_trip_code_only():
    p1, p2, _ = round_trip("""
        LOAD_IMM R0, 10
        LOAD_IMM R1, 20
        ADD R2, R0, R1
        PUSH R2
        POP R3
        SYSCALL 4
    """)
    assert p1.code == p2.code
    assert p1.data == p2.data
    assert p1.ivt == p2.ivt
    assert p1.entry == p2.entry


def test_round_trip_with_data_and_ivt():
    p1, p2, _ = round_trip("""
        .ivt 1, handler
    start:
        LOAD_IMM R0, 1
        LEA R1, arr
        CALL helper
        JMP done
    helper:
        ADD R0, R0, R0
        RET
    handler:
        IRET
    done:
        SYSCALL 4
        .data
    arr: .word 1, 2, 3
    msg: .string "hi"
    """)
    assert p1.code == p2.code
    assert p1.data == p2.data
    assert p1.ivt == p2.ivt


def test_round_trip_all_instruction_forms():
    p1, p2, _ = round_trip("""
        LOAD_IMM R0, 1
        LOAD_REG R1, R0
        NEG R2, R1
        NOT R3, R1
        INC R0
        DEC R0
        SHL R4, R0, R1
        SHR R5, R0, R1
        CMP R0, R1
        JNE away
    away:
        INT 1
        IRET
        RET
        SYSCALL 4
    """)
    assert p1.code == p2.code


def test_jump_targets_become_labels():
    program = assemble("""
    loop:
        DEC R0
        JMP loop
    """)
    text = disassemble(program)
    assert "L0:" in text
    assert "JMP L0" in text


def test_data_referenced_by_lea_gets_label():
    program = assemble("""
        LEA R1, buf
        SYSCALL 4
        .data
    buf: .space 4
    """)
    text = disassemble(program)
    assert "D0:" in text
    assert "LEA R1, D0" in text


def test_invalid_opcode_raises():
    program = Program(code=bytes([0xEE, 0, 0, 0]))
    with pytest.raises(ValueError):
        disassemble(program)


def test_code_length_not_multiple_of_four_raises():
    program = Program(code=b"\x01\x02")
    with pytest.raises(ValueError):
        disassemble(program)
