"""Tests for control flow: branches, CALL/RET, INT/IRET and the traps."""

from __future__ import annotations

import io

import pytest

from tinyvm.assembler import assemble
from tinyvm.errors import (
    MemoryFault,
    PCFault,
    StackOverflow,
    StackUnderflow,
    Trap,
)
from tinyvm.isabits import STACK_TOP
from tinyvm.vm import VM


def vm_of(source: str, **kwargs) -> VM:
    return VM(assemble(source), stdout=io.BytesIO(), **kwargs)


def branch_taken(setup: str, jump: str) -> int:
    src = (
        ".code\n.entry s\ns:\n  " + setup + "\n  " + jump + " taken\n"
        "  LOAD_IMM R5, 0, 0\n  JMP done\n"
        "taken:\n  LOAD_IMM R5, 1, 0\ndone:\n  SYSCALL 4\n"
    )
    vm = vm_of(src)
    vm.run()
    return vm.r(5)


EQUAL = "LOAD_IMM R1, 5, 0\n  CMP R1, #5"
GREATER = "LOAD_IMM R1, 5, 0\n  CMP R1, #3"
LESS = "LOAD_IMM R1, 1, 0\n  CMP R1, #3"


# ---------------------------------------------------------------------
# conditional branches
# ---------------------------------------------------------------------
def test_jeq_jne():
    assert branch_taken(EQUAL, "JEQ") == 1
    assert branch_taken(GREATER, "JEQ") == 0
    assert branch_taken(EQUAL, "JNE") == 0
    assert branch_taken(GREATER, "JNE") == 1


def test_signed_branches_lt_gt_le_ge():
    assert branch_taken(LESS, "JLT") == 1
    assert branch_taken(GREATER, "JLT") == 0
    assert branch_taken(GREATER, "JGT") == 1
    assert branch_taken(LESS, "JGT") == 0
    for setup in (EQUAL, LESS):
        assert branch_taken(setup, "JLE") == 1
    assert branch_taken(GREATER, "JLE") == 0
    for setup in (EQUAL, GREATER):
        assert branch_taken(setup, "JGE") == 1
    assert branch_taken(LESS, "JGE") == 0


def test_signed_branches_handle_negative_values():
    # R1 = 0xFFFF (signed -1); tagged immediate #2 is positive 2.
    neg_vs_pos = "LOAD_IMM R1, 255, 255\n  CMP R1, #2"
    assert branch_taken(neg_vs_pos, "JLT") == 1
    assert branch_taken(neg_vs_pos, "JGT") == 0
    # -1 > -2
    neg_order = "LOAD_IMM R1, 255, 255\n  CMP R1, #-2"
    assert branch_taken(neg_order, "JGT") == 1
    assert branch_taken(neg_order, "JLT") == 0


def test_jmp_is_unconditional():
    src = (".code\n.entry s\ns:\n  JMP t\n  LOAD_IMM R5, 9, 0\n"
           "t:\n  SYSCALL 4\n")
    vm = vm_of(src)
    vm.run()
    assert vm.r(5) == 0


# ---------------------------------------------------------------------
# CALL / RET
# ---------------------------------------------------------------------
def test_call_ret_basic_and_return_address():
    src = (""".code
.entry go
go:
  CALL proc
  LOAD_IMM R6, 1, 0
  SYSCALL 4
proc:
  RET
""")
    vm = vm_of(src)
    vm.run()
    assert vm.r(6) == 1
    assert vm.sp == STACK_TOP  # 0x10000 empty sentinel


def test_nested_calls_and_stack_balance():
    src = """
.code
.entry go
go:
  LOAD_IMM R1, 0, 0
  CALL level3
  CMP R1, #3
  JNE fail
  LOAD_IMM R5, 1, 0
  JMP end
fail:
  LOAD_IMM R5, 0, 0
end:
  SYSCALL 4
level3:
  INC R1
  CALL level2
  RET
level2:
  INC R1
  CALL level1
  RET
level1:
  INC R1
  RET
"""
    vm = vm_of(src)
    vm.run()
    assert vm.r(1) == 3 and vm.r(5) == 1
    assert vm.sp == STACK_TOP


def test_call_target_must_be_valid_code():
    with pytest.raises(PCFault):
        vm_of(".code\n.entry s\ns:\n  CALL 0x5000\n").run()


# ---------------------------------------------------------------------
# INT / IRET
# ---------------------------------------------------------------------
def test_int_iret_runs_handler_and_returns():
    src = """
.code
.entry go
.vector 3, isr
go:
  INT 3
  CMP R2, #1
  JNE bad
  LOAD_IMM R5, 1, 0
  JMP out
bad:
  LOAD_IMM R5, 0, 0
out:
  SYSCALL 4
isr:
  LOAD_IMM R2, 1, 0
  IRET
"""
    vm = vm_of(src)
    vm.run()
    assert vm.r(5) == 1 and vm.r(2) == 1
    assert vm.sp == STACK_TOP


def test_iret_restores_saved_flags():
    # caller sets ZERO=1; handler clobbers it; IRET must restore it.
    src = """
.code
.entry go
.vector 4, isr
go:
  LOAD_IMM R1, 9, 0
  CMP R1, #9
  INT 4
  JEQ zset
  LOAD_IMM R5, 0, 0
  JMP done
zset:
  LOAD_IMM R5, 1, 0
done:
  SYSCALL 4
isr:
  LOAD_IMM R3, 6, 0
  CMP R3, #9            ; ZERO=0 here
  IRET
"""
    vm = vm_of(src)
    vm.run()
    assert vm.r(5) == 1


def test_nested_interrupts_balance_frames():
    src = """
.code
.entry go
.vector 1, isr1
.vector 2, isr2
go:
  INT 1
  CMP R4, #3
  JNE bad
  LOAD_IMM R5, 1, 0
  JMP done
bad:
  LOAD_IMM R5, 0, 0
done:
  SYSCALL 4
isr1:
  INT 2
  INC R4
  IRET
isr2:
  INC R4
  INC R4
  IRET
"""
    vm = vm_of(src)
    vm.run()
    assert vm.r(4) == 3 and vm.r(5) == 1
    assert vm.sp == STACK_TOP


def test_ivt_entries_are_installed_in_memory():
    src = """
.code
.entry go
.vector 3, isr
go:
  SYSCALL 4
isr:
  IRET
"""
    vm = vm_of(src)
    # IVT vector 3 lives at 0x0000 + 3*2 = 0x0006, little-endian handler.
    assert vm.memory.read_word(0x06) == vm.interrupt_handlers[3]


def test_unregistered_interrupt_vector_traps():
    with pytest.raises(Trap, match="99"):
        vm_of(".code\n.entry s\ns:\n  INT 99\n  SYSCALL 4\n").run()


# ---------------------------------------------------------------------
# stack overflow / underflow
# ---------------------------------------------------------------------
def test_stack_overflow_on_unbounded_push_loop():
    src = ".code\n.entry s\ns:\n  PUSH R0\n  JMP s\n"
    vm = vm_of(src, max_cycles=1_000_000)
    with pytest.raises(StackOverflow):
        vm.run()
    # stack region holds 10240 words; the overflowing push is one past that
    assert vm.sp == 0xB000


def test_single_push_lands_at_0xfffe():
    vm = vm_of(".code\n.entry s\ns:\n  PUSH R0\n  SYSCALL 4\n")
    vm.run()
    assert vm.sp == 0xFFFE


def test_stack_underflow_ret_pop_iret():
    with pytest.raises(StackUnderflow):
        vm_of(".code\n.entry s\ns:\n  RET\n").run()
    with pytest.raises(StackUnderflow):
        vm_of(".code\n.entry s\ns:\n  POP R1\n").run()
    with pytest.raises(StackUnderflow):
        vm_of(".code\n.entry s\ns:\n  IRET\n").run()


def test_balanced_stack_then_extra_pop_underflows():
    src = ".code\n.entry s\ns:\n  PUSH R0\n  POP R1\n  POP R2\n"
    with pytest.raises(StackUnderflow):
        vm_of(src).run()


# ---------------------------------------------------------------------
# self-modifying code / IVT write protection
# ---------------------------------------------------------------------
def test_store_into_code_segment_traps():
    with pytest.raises(MemoryFault):
        vm_of(".code\n.entry s\ns:\n  STORE_MEM 0x0104, R0\n  SYSCALL 4\n").run()


def test_store_into_ivt_traps():
    with pytest.raises(MemoryFault):
        vm_of(".code\n.entry s\ns:\n  STORE_MEM 0x0010, R0\n  SYSCALL 4\n").run()


def test_data_load_from_code_segment_traps():
    with pytest.raises(MemoryFault):
        vm_of(".code\n.entry s\ns:\n  LEA R1, s\n  LOAD_REG R2, R1\n  SYSCALL 4\n").run()


# ---------------------------------------------------------------------
# PC bounds / alignment
# ---------------------------------------------------------------------
@pytest.mark.parametrize("target", ["0x0050", "0x4000", "0x8000", "0xAFFF",
                                    "0xFFFF", "0x3FFC"])
def test_jump_outside_code_segment_traps(target):
    with pytest.raises(PCFault):
        vm_of(f".code\n.entry s\ns:\n  JMP {target}\n").run()


def test_misaligned_branch_target_traps():
    with pytest.raises(PCFault):
        vm_of(".code\n.entry s\ns:\n  JMP 0x0101\n").run()


def test_untaken_conditional_does_not_trap_on_bad_target():
    # CMP R0,#0 sets ZERO -> JNE is not taken, so bad target is never used.
    vm = vm_of(".code\n.entry s\ns:\n  CMP R0, #0\n  JNE 0x5000\n  SYSCALL 4\n")
    vm.run()  # must complete without a trap
    assert vm.halted


def test_taken_conditional_to_bad_target_traps():
    # R1=2 vs #1 -> not equal -> JNE taken -> bad target traps.
    with pytest.raises(PCFault):
        vm_of(".code\n.entry s\ns:\n  LOAD_IMM R1, 2, 0\n  CMP R1, #1\n"
              "  JNE 0x5000\n").run()


def test_ret_to_invalid_address_traps():
    # push a bogus return address then RET.
    src = ".code\n.entry s\ns:\n  LOAD_IMM R1, 0, 0\n  PUSH R1\n  RET\n"
    with pytest.raises(PCFault):
        vm_of(src).run()
