"""Memory tests: LOAD/STORE, boundaries, self-modifying code trap."""

import pytest

from tinyvm.errors import TrapKind, VMTrap
from tinyvm.isa import DATA_BASE

from conftest import make_vm, run_asm


def test_store_load_data_segment_roundtrip():
    vm = run_asm("""
        LEA R0, buf
        LOAD_IMM R1, 0xBEEF
        STORE_MEM [R0], R1
        LOAD_MEM R2, [R0]
        SYSCALL 4
    .data
    buf: .space 2
    """)
    assert vm.regs[2] == 0xBEEF


def test_load_word_directive():
    vm = run_asm("""
        LEA R0, val
        LOAD_MEM R1, [R0]
        SYSCALL 4
    .data
    val: .word 0x1234
    """)
    assert vm.regs[1] == 0x1234


def test_string_bytes_in_memory():
    vm = run_asm("""
        LEA R0, msg
        LOAD_MEM R1, [R0]
        SYSCALL 4
    .data
    msg: .string "AB"
    """)
    assert vm.regs[1] == 0x4142  # "AB" big-endian


def test_lea_loads_data_address():
    vm = run_asm("""
        LEA R1, val
        SYSCALL 4
    .data
    val: .word 1
    """)
    assert vm.regs[1] == DATA_BASE


def test_load_mem_out_of_bounds_traps():
    vm = make_vm("""
        LEA R0, 0xFFFF
        LOAD_MEM R1, [R0]
    """)
    with pytest.raises(VMTrap) as excinfo:
        vm.run()
    assert excinfo.value.kind is TrapKind.MEMORY_OUT_OF_BOUNDS


def test_store_mem_out_of_bounds_traps():
    vm = make_vm("""
        LEA R0, 0xFFFF
        LOAD_IMM R1, 1
        STORE_MEM [R0], R1
    """)
    with pytest.raises(VMTrap) as excinfo:
        vm.run()
    assert excinfo.value.kind is TrapKind.MEMORY_OUT_OF_BOUNDS


def test_self_modifying_code_traps():
    vm = make_vm("""
    start:
        LEA R0, start
        LOAD_IMM R1, 1
        STORE_MEM [R0], R1
    """)
    with pytest.raises(VMTrap) as excinfo:
        vm.run()
    assert excinfo.value.kind is TrapKind.SELF_MODIFYING_CODE


def test_ivt_region_is_write_protected():
    vm = make_vm("""
        LEA R0, 0x0010
        LOAD_IMM R1, 1
        STORE_MEM [R0], R1
    """)
    with pytest.raises(VMTrap) as excinfo:
        vm.run()
    assert excinfo.value.kind is TrapKind.SELF_MODIFYING_CODE


def test_heap_segment_is_writable():
    vm = run_asm("""
        LEA R0, 0x8000
        LOAD_IMM R1, 0xCAFE
        STORE_MEM [R0], R1
        LOAD_MEM R2, [R0]
        SYSCALL 4
    """)
    assert vm.regs[2] == 0xCAFE
