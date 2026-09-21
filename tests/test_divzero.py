"""Divide-by-zero trap tests."""

import pytest

from tinyvm.errors import TrapKind, VMTrap

from conftest import make_vm


def test_div_by_zero_traps():
    vm = make_vm("""
        LOAD_IMM R0, 10
        LOAD_IMM R1, 0
        DIV R2, R0, R1
    """)
    with pytest.raises(VMTrap) as excinfo:
        vm.run()
    assert excinfo.value.kind is TrapKind.DIVIDE_BY_ZERO


def test_mod_by_zero_traps():
    vm = make_vm("""
        LOAD_IMM R0, 10
        LOAD_IMM R1, 0
        MOD R2, R0, R1
    """)
    with pytest.raises(VMTrap) as excinfo:
        vm.run()
    assert excinfo.value.kind is TrapKind.DIVIDE_BY_ZERO
