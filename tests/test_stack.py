"""Stack operation tests: PUSH/POP, overflow and underflow traps."""

import pytest

from tinyvm.errors import TrapKind, VMTrap
from tinyvm.isa import STACK_BASE, STACK_TOP

from conftest import make_vm, run_asm


def test_push_pop_roundtrip():
    vm = run_asm("""
        LOAD_IMM R0, 0x1234
        PUSH R0
        LOAD_IMM R0, 0
        POP R1
        SYSCALL 4
    """)
    assert vm.regs[1] == 0x1234
    assert vm.sp == STACK_TOP


def test_push_pop_lifo_order():
    vm = run_asm("""
        LOAD_IMM R0, 1
        LOAD_IMM R1, 2
        LOAD_IMM R2, 3
        PUSH R0
        PUSH R1
        PUSH R2
        POP R3
        POP R4
        POP R5
        SYSCALL 4
    """)
    assert (vm.regs[3], vm.regs[4], vm.regs[5]) == (3, 2, 1)


def test_sp_moves_down_on_push():
    vm = run_asm("""
        LOAD_IMM R0, 7
        PUSH R0
        PUSH R0
        SYSCALL 4
    """)
    assert vm.sp == STACK_TOP - 4


def test_pop_underflow_traps():
    vm = make_vm("POP R0")
    with pytest.raises(VMTrap) as excinfo:
        vm.run()
    assert excinfo.value.kind is TrapKind.STACK_UNDERFLOW


def test_push_overflow_traps():
    vm = make_vm("""
    loop:
        PUSH R0
        JMP loop
    """)
    with pytest.raises(VMTrap) as excinfo:
        vm.run()
    assert excinfo.value.kind is TrapKind.STACK_OVERFLOW
    assert vm.sp <= STACK_BASE
