"""Tests for the tinyvm two-pass assembler.

Covers:
* label definitions, forward/backward references, duplicate/undefined labels
* .code/.data sections and .string/.word/.byte/.space directives
* comments (full-line, trailing, indented)
* 4-byte fixed-width instruction encoding for every operand layout
* operand range errors with accurate 1-based line/column positions
* public API: Token / Tokenizer / Assembler / assemble
"""

from __future__ import annotations

import pytest

from tinyvm.assembler import (
    TOKEN_DIRECTIVE,
    TOKEN_LABEL,
    TOKEN_OP,
    TOKEN_OPERAND,
    Assembler,
    Tokenizer,
    assemble,
)
from tinyvm.errors import AssemblerError
from tinyvm.isabits import encode_tagged_immediate


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------
def expect_error(source: str):
    """pytest.raises wrapper returning the caught AssemblerError."""
    return pytest.raises(AssemblerError, match="line")


def code_of(source: str) -> bytes:
    return assemble(source).code


# ---------------------------------------------------------------------
# public tokenizer API
# ---------------------------------------------------------------------
def test_tokenizer_classifies_tokens():
    tokens = Tokenizer("loop: ADD R1, R2, #3 ; note").tokenize()
    kinds = [(t.kind, t.value) for t in tokens]
    assert kinds == [
        (TOKEN_LABEL, "loop"),
        (TOKEN_OP, "ADD"),
        (TOKEN_OPERAND, "R1"),
        (TOKEN_OPERAND, "R2"),
        (TOKEN_OPERAND, "#3"),
    ]
    assert tokens[0].line == 1 and tokens[0].column == 1
    assert tokens[2].column == 11


def test_tokenizer_directive_and_comments():
    rows = Tokenizer("; full line\n  .data\nm: .byte 1 ; tail\n").tokenize_lines()
    assert len(rows) == 2
    assert rows[0][0].kind == TOKEN_DIRECTIVE
    assert [(t.kind, t.value) for t in rows[1]] == [
        (TOKEN_LABEL, "m"),
        (TOKEN_DIRECTIVE, ".byte"),
        (TOKEN_OPERAND, "1"),
    ]


def test_tokenizer_unterminated_string_reports_position():
    with pytest.raises(AssemblerError) as info:
        Tokenizer('.data\nm: .string "abc').tokenize()
    assert info.value.line == 2


# ---------------------------------------------------------------------
# public Assembler facade
# ---------------------------------------------------------------------
def test_assembler_facade_two_passes():
    asm = Assembler()
    labels = asm.pass_one(".code\nfoo: INC R0\n")
    assert labels["FOO"] == 0x0100
    assert asm.code_size == 4
    program = asm.pass_two()
    assert program.code == bytes([0x0E, 0x00, 0x00, 0x00])
    assert asm.program is program


def test_assembler_pass_two_without_pass_one_raises():
    with pytest.raises(RuntimeError):
        Assembler().pass_two()


def test_assemble_convenience_matches_facade():
    source = ".code\n_start: SYSCALL 4\n"
    assert assemble(source).serialize() == Assembler().assemble(source).serialize()


# ---------------------------------------------------------------------
# pass 1: labels, sections, addresses
# ---------------------------------------------------------------------
def test_forward_and_backward_labels():
    program = assemble(""".code
.entry begin
begin:
  JMP forward
back:
  JMP begin
forward:
  JMP back
  SYSCALL 4
""")
    code = program.code
    assert program.entry == 0x0100
    # JMP forward  -> target 0x0108 (3rd instruction)
    assert code[0:4] == bytes([0x10, 0x08, 0x01, 0x00])
    # JMP begin    -> 0x0100
    assert code[4:8] == bytes([0x10, 0x00, 0x01, 0x00])
    # JMP back     -> 0x0104
    assert code[8:12] == bytes([0x10, 0x04, 0x01, 0x00])


def test_default_entry_is_start_label():
    program = assemble(""".code
_start:
  SYSCALL 4
""")
    assert program.entry == 0x0100


def test_default_entry_falls_back_to_first_instruction():
    program = assemble(""".code
  INC R0
  SYSCALL 4
""")
    assert program.entry == 0x0100


def test_entry_directive_resolves_forward_label():
    program = assemble(""".code
.entry main
  JMP main
main:
  SYSCALL 4
""")
    assert program.entry == 0x0104


def test_labels_are_case_insensitive():
    program = assemble(""".code
.entry MyLabel
MyLabel:
  JMP MYLABEL
  SYSCALL 4
""")
    assert program.code[0:4] == bytes([0x10, 0x00, 0x01, 0x00])


def test_label_plus_offset_expression():
    program = assemble(""".code
base:
  JMP base+4
  SYSCALL 4
""")
    assert program.code[0:4] == bytes([0x10, 0x04, 0x01, 0x00])


def test_every_instruction_is_four_bytes():
    source = "  .code\n" + "\n".join(
        ["  INC R0"] * 7
    ) + "\n"
    code = assemble(source).code
    assert len(code) % 4 == 0
    assert len(code) == 28


def test_code_segment_overflow_detected():
    # code region holds (0x4000-0x100)/4 = 4032 instructions
    source = ".code\n" + "\n".join(["INC R0"] * 4033) + "\n"
    with pytest.raises(AssemblerError, match="code segment overflow"):
        assemble(source)


# ---------------------------------------------------------------------
# data directives
# ---------------------------------------------------------------------
def test_byte_directive():
    program = assemble(".data\nm: .byte 0x01, 2, 255\n")
    assert program.data_base == 0x4000
    assert program.data == bytes([1, 2, 255])


def test_word_directive_little_endian_and_signed():
    program = assemble(".data\nw: .word 0x1234, -1, 0\n")
    assert program.data == bytes([0x34, 0x12, 0xFF, 0xFF, 0x00, 0x00])


def test_word_accepts_label_address():
    program = assemble(""".data
base: .word base
""")
    assert program.data == bytes([0x00, 0x40])


def test_string_directive_nul_terminated_with_escapes():
    program = assemble(r'.data' + "\n" + r's: .string "ab\n", "x"' + "\n")
    # "ab\n\0" then "x\0"
    assert program.data == b"ab\n\x00x\x00"


def test_space_directive_emits_zeros_and_advances_address():
    program = assemble(""".data
a: .byte 0xaa
pad: .space 3
b: .word 7
""")
    assert program.data == bytes([0xAA, 0x00, 0x00, 0x00, 0x07, 0x00])
    labels = Assembler().pass_one(""".data
a: .byte 0xaa
pad: .space 3
b: .word 7
""")
    assert labels["A"] == 0x4000
    assert labels["PAD"] == 0x4001
    assert labels["B"] == 0x4004


def test_data_addresses_start_at_0x4000():
    program = assemble(".data\nfirst: .word 1\n")
    labels = Assembler().pass_one(".data\nfirst: .word 1\n")
    assert labels["FIRST"] == 0x4000
    assert program.data_base == 0x4000


def test_section_switch_back_to_code():
    asm = Assembler()
    labels = asm.pass_one(""".code
c1: INC R0
.data
d1: .byte 1
.code
c2: INC R0
""")
    assert labels["C1"] == 0x0100
    assert labels["D1"] == 0x4000
    assert labels["C2"] == 0x0104
    assert asm.code_size == 8 and asm.data_size == 1


def test_data_segment_overflow_detected():
    source = ".data\nbig: .space " + str(0x4000) + "\n"
    with pytest.raises(AssemblerError, match="data segment overflow"):
        assemble(source)


# ---------------------------------------------------------------------
# pass 2: 4-byte machine-code encoding
# ---------------------------------------------------------------------
def test_load_imm_16bit_constant():
    assert code_of(".code\nLOAD_IMM R3, 0x78, 0x56\n") == \
        bytes([0x20, 0x03, 0x78, 0x56])


def test_alu_register_three_operand_layout():
    # ADD rd, rs, Rt -> A=rd B=rs C=Rt
    assert code_of(".code\nADD R1, R2, R3\n") == bytes([0x01, 1, 2, 3])
    assert code_of(".code\nXOR R15, R0, R15\n") == bytes([0x08, 15, 0, 15])


def test_alu_tagged_immediate_layout():
    # ADD rd, rs, #imm -> C carries the tagged byte
    assert code_of(".code\nADD R1, R2, #63\n") == \
        bytes([0x01, 1, 2, encode_tagged_immediate(63)])
    assert code_of(".code\nSUB R4, R5, #-64\n") == \
        bytes([0x02, 4, 5, encode_tagged_immediate(-64)])


def test_every_arith_opcode_round_trips_byte_layout():
    ops = [
        ("ADD", 0x01), ("SUB", 0x02), ("MUL", 0x03), ("DIV", 0x04),
        ("MOD", 0x05), ("AND", 0x06), ("OR", 0x07), ("XOR", 0x08),
        ("SHL", 0x09), ("SHR", 0x0A),
    ]
    for mnemonic, opcode in ops:
        word = code_of(f".code\n{mnemonic} R1, R2, R3\n")
        assert word == bytes([opcode, 1, 2, 3]), mnemonic


def test_unary_and_inc_dec_encoding():
    assert code_of(".code\nNEG R1, R2\n") == bytes([0x0C, 1, 2, 0])
    assert code_of(".code\nNOT R3, R4\n") == bytes([0x0D, 3, 4, 0])
    assert code_of(".code\nINC R5\n") == bytes([0x0E, 5, 0, 0])
    assert code_of(".code\nDEC R6\n") == bytes([0x0F, 6, 0, 0])
    assert code_of(".code\nCMP R7, #-5\n") == \
        bytes([0x0B, 7, encode_tagged_immediate(-5), 0])


def test_branch_and_call_addresses_are_little_endian():
    program = assemble(""".code
.entry s
s:
  JEQ t
  JNE t
  JLT t
  JGT t
  JLE t
  JGE t
  CALL t
t:
  SYSCALL 4
""")
    target = bytes([0x1C, 0x01])  # t = 0x011c, little endian
    for i in range(7):
        chunk = program.code[i * 4:i * 4 + 4]
        assert chunk[1:3] == target, f"branch {i}"


def test_ret_int_iret_encoding():
    assert code_of(".code\nRET\n") == bytes([0x18, 0, 0, 0])
    assert code_of(".code\nINT 17\n") == bytes([0x19, 17, 0, 0])
    assert code_of(".code\nIRET\n") == bytes([0x1A, 0, 0, 0])


def test_load_store_and_lea_address_layout():
    program = assemble(""".code
  LOAD_MEM R7, 0x1234
  STORE_MEM 0x1234, R8
  LEA R9, 0x5678
""")
    # LOAD_MEM: A=rd B=lo C=hi
    assert program.code[0:4] == bytes([0x22, 7, 0x34, 0x12])
    # STORE_MEM: A=lo B=hi C=rs
    assert program.code[4:8] == bytes([0x23, 0x34, 0x12, 8])
    # LEA: A=rd B=lo C=hi
    assert program.code[8:12] == bytes([0x26, 9, 0x78, 0x56])


def test_push_pop_load_reg_syscall_encoding():
    assert code_of(".code\nPUSH #12\n") == \
        bytes([0x24, encode_tagged_immediate(12), 0, 0])
    assert code_of(".code\nPUSH R3\n") == bytes([0x24, 3, 0, 0])
    assert code_of(".code\nPOP R10\n") == bytes([0x25, 10, 0, 0])
    assert code_of(".code\nLOAD_REG R11, R12\n") == bytes([0x21, 11, 12, 0])
    assert code_of(".code\nSYSCALL 4\n") == bytes([0x27, 4, 0, 0])


# ---------------------------------------------------------------------
# errors: all must carry accurate 1-based line/column
# ---------------------------------------------------------------------
def test_undefined_jump_label_reports_line_column():
    with pytest.raises(AssemblerError) as info:
        assemble("\n  JMP missing\n")
    err = info.value
    assert err.line == 2 and err.column == 7
    assert "missing" in str(err)


def test_undefined_label_in_each_position():
    cases = [
        ("  JMP x\n", 7),
        ("  CALL x\n", 8),
        ("  JEQ x\n", 7),
        ("  LOAD_MEM R1, x\n", 16),
        ("  STORE_MEM x, R2\n", 13),
        ("  LEA R3, x\n", 11),
        ("\n  .word x\n" if False else ".data\nw: .word 1, x\n", 13),
    ]
    for source, column in cases:
        with pytest.raises(AssemblerError) as info:
            assemble(source)
        assert info.value.column == column, (source, info.value)


def test_undefined_entry_and_vector_labels():
    with pytest.raises(AssemblerError) as info:
        assemble(".code\n.entry ghost\n")
    assert info.value.line == 2 and info.value.column == 8
    with pytest.raises(AssemblerError) as info:
        assemble(".code\n.vector 9, ghost\n")
    assert info.value.line == 2 and info.value.column == 12


def test_duplicate_label_reports_line_column():
    source = ".code\nfoo: SYSCALL 4\nfoo: SYSCALL 4\n"
    with pytest.raises(AssemblerError) as info:
        assemble(source)
    err = info.value
    assert err.line == 3 and err.column == 1
    assert "duplicate label 'foo'" in str(err)


def test_duplicate_label_across_sections():
    with pytest.raises(AssemblerError, match="duplicate label"):
        assemble(".code\nx: SYSCALL 4\n.data\nx: .byte 1\n")


def test_register_operand_out_of_range():
    with pytest.raises(AssemblerError) as info:
        assemble(".code\nADD R1, R16, #0\n")
    assert info.value.line == 2 and info.value.column == 9
    with pytest.raises(AssemblerError) as info:
        assemble(".code\nPOP R20\n")
    assert info.value.column == 5


def test_immediate_out_of_range_tagged():
    with pytest.raises(AssemblerError) as info:
        assemble(".code\nADD R1, R2, #64\n")
    assert info.value.line == 2 and info.value.column == 13
    assert "out of range" in str(info.value)
    with pytest.raises(AssemblerError) as info:
        assemble(".code\nCMP R1, #-65\n")
    assert info.value.column == 9


def test_u8_operand_out_of_range():
    with pytest.raises(AssemblerError) as info:
        assemble(".code\nLOAD_IMM R1, 256, 0\n")
    assert info.value.column == 14
    with pytest.raises(AssemblerError) as info:
        assemble(".code\nINT 256\n")
    assert info.value.column == 5


def test_byte_word_operand_ranges():
    with pytest.raises(AssemblerError, match="out of range"):
        assemble(".data\n.byte 256\n")
    with pytest.raises(AssemblerError, match=r"\.word value"):
        assemble(".data\n.word 70000\n")
    with pytest.raises(AssemblerError, match=r"\.word value"):
        assemble(".data\n.word -32769\n")


def test_wrong_operand_count():
    with pytest.raises(AssemblerError) as info:
        assemble(".code\nADD R1, R2\n")
    assert info.value.line == 2
    assert "expects 3 operand" in str(info.value)
    with pytest.raises(AssemblerError, match="expects"):
        assemble(".code\nINC R1, R2\n")


def test_unknown_mnemonic_and_directive():
    with pytest.raises(AssemblerError) as info:
        assemble(".code\nBOGUS R1\n")
    assert info.value.line == 2 and info.value.column == 1
    with pytest.raises(AssemblerError) as info:
        assemble(".data\n.bogus 1\n")
    assert info.value.line == 2


def test_immediate_where_register_expected():
    with pytest.raises(AssemblerError, match="expected register"):
        assemble(".code\nADD #1, R2, R3\n")
    with pytest.raises(AssemblerError, match="expected register"):
        assemble(".code\nPOP #3\n")
