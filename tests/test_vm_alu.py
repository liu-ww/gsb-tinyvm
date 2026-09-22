"""Tests for the 15 arithmetic/logic instructions and FLAGS semantics."""

from __future__ import annotations

import io

import pytest

from tinyvm.assembler import assemble
from tinyvm.errors import DivideByZero
from tinyvm.isabits import (
    FLAG_CARRY as C,
    FLAG_OVERFLOW as O,
    FLAG_SIGN as S,
    FLAG_ZERO as Z,
)
from tinyvm.vm import VM


def ld(reg: str, value: int) -> str:
    return f"LOAD_IMM {reg}, {value & 0xFF}, {(value >> 8) & 0xFF}"


def run(lines):
    src = ".code\n.entry s\ns:\n" + "\n".join(lines) + "\nSYSCALL 4\n"
    vm = VM(assemble(src), stdout=io.BytesIO())
    vm.run()
    return vm


# ---------------------------------------------------------------------
# result correctness
# ---------------------------------------------------------------------
def test_add_sub_mul_basic():
    assert run(["ADD R1, R2, #5"]).r(1) == 5
    assert run([ld("R2", 10), "ADD R1, R2, R2"]).r(1) == 20
    assert run([ld("R1", 7), "SUB R1, R1, #3"]).r(1) == 4
    assert run([ld("R1", 6), "MUL R1, R1, #7"]).r(1) == 42


def test_div_mod_unsigned_and_truncation_toward_zero():
    assert run([ld("R1", 20), "DIV R1, R1, #6"]).r(1) == 3
    assert run([ld("R1", 20), "MOD R1, R1, #6"]).r(1) == 2
    assert run([ld("R1", 20), "DIV R1, R1, #-6"]).r(1) == (-3 & 0xFFFF)
    assert run([ld("R1", 20), "MOD R1, R1, #-6"]).r(1) == 2
    assert run([ld("R2", 6), "NEG R1, R2", "DIV R1, R1, #4"]).r(1) == (-1 & 0xFFFF)
    assert run([ld("R2", 6), "NEG R1, R2", "MOD R1, R1, #4"]).r(1) == (-2 & 0xFFFF)


def test_bitwise_operations():
    assert run([ld("R1", 12), "AND R1, R1, #10"]).r(1) == 8
    assert run([ld("R1", 12), "OR R1, R1, #3"]).r(1) == 15
    assert run([ld("R1", 12), "XOR R1, R1, #10"]).r(1) == 6


def test_shifts():
    assert run([ld("R1", 1), "SHL R1, R1, #4"]).r(1) == 16
    assert run([ld("R1", 240), "SHR R1, R1, #4"]).r(1) == 15
    assert run([ld("R1", 0xFFFF), "SHL R1, R1, #4"]).r(1) == 0xFFF0
    assert run([ld("R1", 0xFFFF), "SHR R1, R1, #16"]).r(1) == 0


def test_neg_not_inc_dec():
    assert run([ld("R2", 5), "NEG R1, R2"]).r(1) == 0xFFFB
    assert run([ld("R2", 0), "NOT R1, R2"]).r(1) == 0xFFFF
    assert run([ld("R1", 41), "INC R1"]).r(1) == 42
    assert run([ld("R1", 41), "DEC R1"]).r(1) == 40


def test_results_are_16bit_wrapping():
    assert run([ld("R1", 0xFFFF), "INC R1"]).r(1) == 0
    assert run([ld("R1", 0), "DEC R1"]).r(1) == 0xFFFF


# ---------------------------------------------------------------------
# FLAGS
# ---------------------------------------------------------------------
def test_zero_and_sign_flags():
    assert run([ld("R1", 5), "SUB R1, R1, #5"]).flags & Z
    assert run([ld("R1", 5), "SUB R2, R1, #6"]).flags & S
    assert run([ld("R2", 0), "NOT R1, R2"]).flags & S


def test_carry_flag_add_sub_inc_dec():
    vm = run([ld("R1", 0xFFFF), "ADD R1, R1, #1"])
    assert vm.flags & C and vm.flags & Z
    vm = run([ld("R1", 0xFFFF), "INC R1"])
    assert vm.flags & C and vm.flags & Z
    vm = run([ld("R1", 0), "DEC R1"])
    assert vm.flags & C and vm.flags & S
    vm = run([ld("R1", 1), "DEC R1"])
    assert (vm.flags & Z) and not (vm.flags & C)
    vm = run([ld("R1", 5), "SUB R1, R1, #3"])
    assert not (vm.flags & C)


def test_overflow_flag_signed():
    vm = run([ld("R1", 0x7FFF), "ADD R1, R1, #1"])   # 32767+1
    assert vm.flags & O and vm.flags & S
    vm = run([ld("R1", 0x8000), "SUB R1, R1, #1"])   # -32768-1
    assert vm.flags & O
    vm = run([ld("R1", 0x7FFF), "INC R1"])
    assert vm.flags & O
    vm = run([ld("R1", 0x8000), "DEC R1"])
    assert vm.flags & O
    vm = run([ld("R1", 10), "ADD R1, R1, #5"])
    assert not (vm.flags & O)


def test_mul_carry_is_unsigned_32bit_overflow():
    vm = run([ld("R1", 256), ld("R2", 256), "MUL R1, R1, R2"])
    assert vm.r(1) == 0 and (vm.flags & C)
    vm = run([ld("R1", 20), ld("R2", 3), "MUL R1, R1, R2"])
    assert vm.r(1) == 60 and not (vm.flags & C)


def test_shift_carry_is_last_shifted_bit():
    vm = run([ld("R1", 0x8001), "SHL R1, R1, #1"])
    assert vm.r(1) == 2 and (vm.flags & C)
    vm = run([ld("R1", 3), "SHR R1, R1, #1"])
    assert vm.r(1) == 1 and (vm.flags & C)
    vm = run([ld("R1", 3), "SHL R1, R1, #0"])
    assert not (vm.flags & C)
    vm = run([ld("R1", 1), "SHL R1, R1, #16"])
    assert vm.r(1) == 0 and not (vm.flags & C)


def test_neg_overflow_only_on_min_int():
    assert run([ld("R2", 0x8000), "NEG R1, R2"]).flags & O
    assert not (run([ld("R2", 5), "NEG R1, R2"]).flags & O)


# ---------------------------------------------------------------------
# CMP driving the conditional branches (signed semantics)
# ---------------------------------------------------------------------
def relation(x, y, jump):
    src = (
        ".code\n.entry s\ns:\n" + ld("R1", x) + "\n"
        f"CMP R1, #{y}\n{jump} taken\nLOAD_IMM R5, 0, 0\nJMP done\n"
        "taken: LOAD_IMM R5, 1, 0\ndone: SYSCALL 4\n"
    )
    vm = VM(assemble(src), stdout=io.BytesIO())
    vm.run()
    return vm.r(5)


def test_signed_compare_less_equal_greater():
    cases = [
        (1, 2, (1, 0, 0)),
        (2, 2, (0, 1, 0)),
        (3, 2, (0, 0, 1)),
        ((-1) & 0xffff, 2, (1, 0, 0)),     # -1 < 2
        ((-5) & 0xffff, -2, (1, 0, 0)),   # -5 < -2
        ((-1) & 0xffff, -2, (0, 0, 1)),   # -1 > -2
    ]
    for x, y, (lt, eq, gt) in cases:
        assert relation(x, y, "JLT") == lt
        assert relation(x, y, "JEQ") == eq
        assert relation(x, y, "JGT") == gt


def test_jle_jge_relations():
    # 2 <= 2 and 2 >= 2 both true; 3 <= 2 false; 1 >= 2 false
    assert relation(2, 2, "JLE") == 1 and relation(2, 2, "JGE") == 1
    assert relation(3, 2, "JLE") == 0
    assert relation(1, 2, "JGE") == 0
    assert relation((-1) & 0xffff, -2, "JLE") == 0
    assert relation((-2) & 0xffff, -1, "JGE") == 0


def test_cmp_does_not_modify_operands():
    vm = run([ld("R1", 42), "CMP R1, #7"])
    assert vm.r(1) == 42


# ---------------------------------------------------------------------
# divide / modulo by zero trap
# ---------------------------------------------------------------------
@pytest.mark.parametrize("mnemonic", ["DIV", "MOD"])
def test_divide_by_zero_traps(mnemonic):
    src = (
        ".code\n.entry s\ns:\n" + ld("R1", 9) + "\n"
        f"{mnemonic} R1, R1, #0\nSYSCALL 4\n"
    )
    vm = VM(assemble(src), stdout=io.BytesIO())
    with pytest.raises(DivideByZero):
        vm.run()
    # the trap fires before the destination register is written
    assert vm.r(1) == 9
