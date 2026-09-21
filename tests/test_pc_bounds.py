"""PC bounds tests."""

import pytest

from tinyvm.assembler import assemble
from tinyvm.errors import TrapKind, VMTrap
from tinyvm.isa import CODE_BASE

from conftest import make_vm


def test_jmp_into_data_segment_traps():
    vm = make_vm("JMP 0x4000")
    with pytest.raises(VMTrap) as excinfo:
        vm.run()
    assert excinfo.value.kind is TrapKind.PC_OUT_OF_BOUNDS


def test_jmp_into_heap_traps():
    vm = make_vm("JMP 0x8000")
    with pytest.raises(VMTrap) as excinfo:
        vm.run()
    assert excinfo.value.kind is TrapKind.PC_OUT_OF_BOUNDS


def test_jmp_misaligned_end_of_code_traps():
    vm = make_vm("JMP 0x3FFE")
    with pytest.raises(VMTrap) as excinfo:
        vm.run()
    assert excinfo.value.kind is TrapKind.PC_OUT_OF_BOUNDS


def test_ret_to_bad_address_traps():
    vm = make_vm("""
        LEA R0, 0x9000
        PUSH R0
        RET
    """)
    with pytest.raises(VMTrap) as excinfo:
        vm.run()
    assert excinfo.value.kind is TrapKind.PC_OUT_OF_BOUNDS


def test_entry_defaults_to_code_base():
    program = assemble("SYSCALL 4")
    assert program.entry == CODE_BASE
