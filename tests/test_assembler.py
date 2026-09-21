"""Assembler tests: labels, directives, encoding, and error reporting."""

import pytest

from tinyvm.assembler import assemble
from tinyvm.errors import AssemblerError
from tinyvm.isa import CODE_BASE, DATA_BASE, Op

from conftest import run_asm


def test_forward_and_backward_labels():
    vm = run_asm("""
        JMP mid
    back:
        LOAD_IMM R0, 2
        JMP end
    mid:
        LOAD_IMM R0, 1
        JMP back
    end:
        SYSCALL 4
    """)
    assert vm.regs[0] == 2


def test_label_on_same_line_as_instruction():
    vm = run_asm("""
    start:  LOAD_IMM R0, 7
            SYSCALL 4
    """)
    assert vm.regs[0] == 7


def test_entry_point_is_code_base():
    program = assemble("SYSCALL 4")
    assert program.entry == CODE_BASE


def test_instruction_encoding_layout():
    program = assemble("LOAD_IMM R1, 0x2345")
    assert program.code == bytes([Op.LOAD_IMM, 1, 0x23, 0x45])


def test_data_directives_emit_bytes():
    program = assemble("""
        .data
    w:  .word 0x1234, 5
    b:  .byte 1, 2, 3
    s:  .space 4
    """)
    assert program.data == bytes(
        [0x12, 0x34, 0x00, 0x05, 1, 2, 3, 0, 0, 0, 0]
    )


def test_string_directive_nul_terminated():
    program = assemble("""
        .data
    msg: .string "hi"
    """)
    assert program.data == b"hi\x00"


def test_string_escapes():
    program = assemble(r'''
        .data
    msg: .string "a\n\t"
    ''')
    assert program.data == b"a\n\t\x00"


def test_word_directive_accepts_labels():
    program = assemble("""
    target:
        SYSCALL 4
        .data
    ptr: .word target
    """)
    # target is the first instruction at CODE_BASE (0x0100), big-endian
    assert program.data == bytes([0x01, 0x00])
    # an undefined label in .word must raise
    with pytest.raises(AssemblerError):
        assemble(".data\nptr: .word missing")


def test_data_label_addresses():
    program = assemble("""
        .data
    a:  .word 1
    b:  .word 2
    """)
    vm_program = program
    assert vm_program.data == bytes([0, 1, 0, 2])
    vm = run_asm("""
        LEA R0, a
        LEA R1, b
        SYSCALL 4
        .data
    a:  .word 1
    b:  .word 2
    """)
    assert vm.regs[0] == DATA_BASE
    assert vm.regs[1] == DATA_BASE + 2


def test_comments_semicolon_and_hash():
    vm = run_asm("""
        ; full line comment
        # another full line comment
        LOAD_IMM R0, 3   ; trailing comment
        LOAD_IMM R1, 4   # trailing comment
        ADD R2, R0, R1
        SYSCALL 4
    """)
    assert vm.regs[2] == 7


def test_undefined_label_reports_line():
    src = "LOAD_IMM R0, 1\nJMP nowhere\n"
    with pytest.raises(AssemblerError) as excinfo:
        assemble(src)
    assert excinfo.value.line == 2
    assert "undefined label" in str(excinfo.value)


def test_duplicate_label_reports_line():
    src = "x:\nLOAD_IMM R0, 1\nx:\n"
    with pytest.raises(AssemblerError) as excinfo:
        assemble(src)
    assert excinfo.value.line == 3
    assert "duplicate label" in str(excinfo.value)


def test_register_out_of_range():
    with pytest.raises(AssemblerError) as excinfo:
        assemble("ADD R0, R1, R16")
    assert "register out of range" in str(excinfo.value)
    assert excinfo.value.line == 1


def test_immediate_out_of_range():
    with pytest.raises(AssemblerError) as excinfo:
        assemble("LOAD_IMM R0, 70000")
    assert "out of range" in str(excinfo.value)


def test_byte_out_of_range():
    with pytest.raises(AssemblerError) as excinfo:
        assemble(".data\nx: .byte 300")
    assert "out of range" in str(excinfo.value)


def test_unknown_instruction():
    with pytest.raises(AssemblerError) as excinfo:
        assemble("FOOBAR R0")
    assert "unknown instruction" in str(excinfo.value)


def test_unknown_directive():
    with pytest.raises(AssemblerError) as excinfo:
        assemble(".bogus 1")
    assert "unknown directive" in str(excinfo.value)


def test_instruction_in_data_segment_rejected():
    with pytest.raises(AssemblerError) as excinfo:
        assemble(".data\nADD R0, R1, R2")
    assert excinfo.value.line == 2


def test_data_directive_in_code_segment_rejected():
    with pytest.raises(AssemblerError) as excinfo:
        assemble(".word 1")
    assert excinfo.value.line == 1


def test_error_carries_line_and_col():
    src = "LOAD_IMM R0, 1\nLOAD_IMM R1, 2\nADD R0, R1, R99\n"
    with pytest.raises(AssemblerError) as excinfo:
        assemble(src)
    err = excinfo.value
    assert err.line == 3
    assert err.col >= 1
    assert "line 3" in str(err)


def test_wrong_operand_count():
    with pytest.raises(AssemblerError):
        assemble("ADD R0, R1")
    with pytest.raises(AssemblerError):
        assemble("PUSH R0, R1")


def test_ivt_directive():
    program = assemble("""
        .ivt 1, handler
        SYSCALL 4
    handler:
        IRET
    """)
    assert program.ivt == {1: CODE_BASE + 4}
