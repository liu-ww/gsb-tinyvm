"""Interrupt tests: INT/IRET via the IVT."""

import pytest

from tinyvm.errors import TrapKind, VMTrap

from conftest import make_vm, run_asm


def test_int_invokes_handler_and_iret_returns():
    vm = run_asm("""
        .ivt 1, handler
        LOAD_IMM R5, 0
        INT 1
        INT 1
        SYSCALL 4
    handler:
        INC R5
        IRET
    """)
    assert vm.regs[5] == 2


def test_iret_restores_flags():
    vm = run_asm("""
        .ivt 1, handler
        LOAD_IMM R0, 5
        LOAD_IMM R1, 5
        LOAD_IMM R6, 0
        CMP R0, R1
        INT 1
        JEQ was_zero
        JMP end
    was_zero:
        LOAD_IMM R6, 1
    end:
        SYSCALL 4
    handler:
        LOAD_IMM R2, 1
        LOAD_IMM R3, 2
        ADD R4, R2, R3
        IRET
    """)
    assert vm.regs[6] == 1


def test_unhandled_interrupt_traps():
    vm = make_vm("INT 7")
    with pytest.raises(VMTrap) as excinfo:
        vm.run()
    assert excinfo.value.kind is TrapKind.UNHANDLED_INTERRUPT


def test_nested_interrupts():
    vm = run_asm("""
        .ivt 1, outer
        .ivt 2, inner
        LOAD_IMM R5, 0
        INT 1
        SYSCALL 4
    outer:
        INC R5
        INT 2
        INC R5
        IRET
    inner:
        INC R5
        IRET
    """)
    assert vm.regs[5] == 3
