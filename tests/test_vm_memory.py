"""Tests for memory instructions, syscalls and the heap allocator."""

from __future__ import annotations

import io

import pytest

from tinyvm.assembler import assemble
from tinyvm.errors import HeapError, MemoryFault, Trap
from tinyvm.isabits import HEAP_END, HEAP_START, STACK_TOP
from tinyvm.memory import HeapAllocator, Memory
from tinyvm.vm import VM


def run(source: str, stdin: bytes = b""):
    vm = VM(
        assemble(source),
        stdin=io.BytesIO(stdin),
        stdout=io.BytesIO(),
    )
    vm.run()
    return vm


# ---------------------------------------------------------------------
# LOAD_IMM / LEA
# ---------------------------------------------------------------------
def test_load_imm_full_16bit():
    vm = run(".code\n.entry s\ns:\nLOAD_IMM R1, 0x34, 0x12\nSYSCALL 4\n")
    assert vm.r(1) == 0x1234


def test_lea_returns_address_not_value():
    vm = run(".code\n.entry s\ns:\nLEA R1, slot\nSYSCALL 4\n.data\nslot: .word 0\n")
    assert vm.r(1) == 0x4000


# ---------------------------------------------------------------------
# LOAD_MEM / STORE_MEM (absolute addressing)
# ---------------------------------------------------------------------
def test_store_and_load_absolute_data_word():
    src = """
.code
.entry s
s:
  LOAD_IMM R2, 0x78, 0x9A
  STORE_MEM slot, R2
  LOAD_MEM R3, slot
  SYSCALL 4
.data
slot: .word 0
"""
    vm = run(src)
    assert vm.r(3) == 0x9A78
    assert vm.memory.read_word(0x4000) == 0x9A78


def test_load_mem_and_store_mem_at_top_of_memory():
    src = """
.code
.entry s
s:
  LOAD_IMM R2, 0xCD, 0xAB
  STORE_MEM 0xFFFE, R2
  LOAD_MEM R3, 0xFFFE
  SYSCALL 4
"""
    vm = run(src)
    assert vm.r(3) == 0xABCD
    assert vm.memory.read_word(0xFFFE) == 0xABCD


# ---------------------------------------------------------------------
# LOAD_REG (register-indirect)
# ---------------------------------------------------------------------
def test_load_reg_register_indirect():
    src = """
.code
.entry s
s:
  LEA R1, slot
  LOAD_REG R2, R1
  SYSCALL 4
.data
slot: .word 0xBEEF
"""
    vm = run(src)
    assert vm.r(2) == 0xBEEF


# ---------------------------------------------------------------------
# PUSH / POP
# ---------------------------------------------------------------------
def test_push_immediate_and_register_then_pop():
    vm = run(".code\n.entry s\ns:\n"
             "PUSH #42\nPOP R1\nPUSH R1\nPOP R2\nSYSCALL 4\n")
    assert vm.r(1) == 42 and vm.r(2) == 42
    assert vm.sp == STACK_TOP


# ---------------------------------------------------------------------
# segment / wrapping bounds
# ---------------------------------------------------------------------
@pytest.mark.parametrize("insn", [
    "LOAD_MEM R1, 0x0100",
    "STORE_MEM 0x0100, R0",
    "LOAD_MEM R1, 0xFFFF",
    "STORE_MEM 0xFFFF, R0",
])
def test_data_word_access_traps_on_segment_or_wrap(insn):
    with pytest.raises(MemoryFault):
        run(f".code\n.entry s\ns:\n{insn}\nSYSCALL 4\n")


def test_load_reg_into_code_segment_traps():
    src = ".code\n.entry s\ns:\nLEA R1, s\nLOAD_REG R2, R1\nSYSCALL 4\n"
    with pytest.raises(MemoryFault):
        run(src)


def test_load_reg_wrapping_word_traps():
    src = ".code\n.entry s\ns:\nLOAD_IMM R1, 255, 255\nLOAD_REG R2, R1\nSYSCALL 4\n"
    with pytest.raises(MemoryFault):
        run(src)


def test_store_into_code_segment_is_self_modifying_trap():
    src = ".code\n.entry s\ns:\nSTORE_MEM 0x0104, R0\nSYSCALL 4\n"
    with pytest.raises(MemoryFault):
        run(src)


# ---------------------------------------------------------------------
# SYSCALL 0: print
# ---------------------------------------------------------------------
def run_capture(code: str, stdin: bytes = b""):
    buf = io.BytesIO()
    vm = VM(
        assemble(".code\n.entry s\ns:\n" + code + "\nSYSCALL 4\n"),
        stdin=io.BytesIO(stdin),
        stdout=buf,
    )
    vm.run()
    return vm, buf.getvalue()


def test_print_signed_integer_positive_and_negative():
    _, out = run_capture(
        "LOAD_IMM R0,0,0\nLOAD_IMM R1,0,0\nLOAD_IMM R2,65,0\nSYSCALL 0"
    )
    assert out == b"65"
    _, out = run_capture(
        "LOAD_IMM R0,0,0\nLOAD_IMM R1,0,0\nLOAD_IMM R2,244,255\nSYSCALL 0"
    )
    assert out == b"-12"


def test_print_single_character():
    _, out = run_capture(
        "LOAD_IMM R0,0,0\nLOAD_IMM R1,1,0\nLOAD_IMM R2,65,0\nSYSCALL 0"
    )
    assert out == b"A"


def test_print_nul_terminated_string():
    src = """
.code
.entry s
s:
  LOAD_IMM R0,0,0
  LOAD_IMM R1,2,0
  LEA R2, msg
  SYSCALL 0
  SYSCALL 4
.data
msg: .string "hello"
"""
    buf = io.BytesIO()
    vm = VM(assemble(src), stdout=buf)
    vm.run()
    assert buf.getvalue() == b"hello"


def test_print_cstring_in_code_segment_traps():
    with pytest.raises(MemoryFault):
        run_capture("LOAD_IMM R0,0,0\nLOAD_IMM R1,2,0\nLEA R2,s\nSYSCALL 0")


def test_print_unknown_format_traps():
    with pytest.raises(Trap):
        run_capture("LOAD_IMM R0,0,0\nLOAD_IMM R1,9,0\nSYSCALL 0")


# ---------------------------------------------------------------------
# SYSCALL 1: read
# ---------------------------------------------------------------------
def test_read_signed_integer():
    src = (".code\n.entry s\ns:\n"
           "LOAD_IMM R0,0,0\nLOAD_IMM R1,0,0\nSYSCALL 1\nSYSCALL 4\n")
    buf = io.BytesIO()
    vm = VM(assemble(src), stdin=io.BytesIO(b" -427 "), stdout=buf)
    vm.run()
    assert vm.r(2) == (-427 & 0xFFFF)


def test_read_single_byte_and_eof_sentinel():
    src = (".code\n.entry s\ns:\n"
           "LOAD_IMM R0,0,0\nLOAD_IMM R1,1,0\nSYSCALL 1\nSYSCALL 4\n")
    buf = io.BytesIO()
    vm = VM(assemble(src), stdin=io.BytesIO(b"Z"), stdout=buf)
    vm.run()
    assert vm.r(2) == ord("Z")
    vm = VM(assemble(src), stdin=io.BytesIO(b""), stdout=io.BytesIO())
    vm.run()
    assert vm.r(2) == 0xFFFF


def test_read_non_integer_traps():
    src = (".code\n.entry s\ns:\n"
           "LOAD_IMM R0,0,0\nLOAD_IMM R1,0,0\nSYSCALL 1\nSYSCALL 4\n")
    vm = VM(assemble(src), stdin=io.BytesIO(b"abc"), stdout=io.BytesIO())
    with pytest.raises(Trap):
        vm.run()


# ---------------------------------------------------------------------
# SYSCALL 2: mmap (malloc/free) backed by HeapAllocator
# ---------------------------------------------------------------------
def test_mmap_malloc_returns_heap_pointer():
    vm = run(".code\n.entry s\ns:\n"
             "LOAD_IMM R0,2,0\nLOAD_IMM R1,0,0\nLOAD_IMM R2,16,0\n"
             "SYSCALL 2\nSYSCALL 4\n")
    assert HEAP_START <= vm.r(1) < HEAP_END
    assert vm.r(2) == 0  # success status


def test_mmap_two_allocations_do_not_overlap():
    src = (
        ".code\n.entry s\ns:\n"
        "LOAD_IMM R0,2,0\nLOAD_IMM R1,0,0\nLOAD_IMM R2,8,0\nSYSCALL 2\n"
        "ADD R10, R1, #0\n"
        "LOAD_IMM R0,2,0\nLOAD_IMM R1,0,0\nLOAD_IMM R2,8,0\nSYSCALL 2\n"
        "ADD R11, R1, #0\nSYSCALL 4\n"
    )
    vm = run(src)
    p1, p2 = vm.r(10), vm.r(11)
    assert p1 != p2 and p2 >= p1 + 8


def test_mmap_malloc_failure_returns_null_and_enomem():
    # 0xD000 bytes cannot fit in the 0x3000-byte heap; must not trap.
    vm = run(".code\n.entry s\ns:\n"
             "LOAD_IMM R0,2,0\nLOAD_IMM R1,0,0\nLOAD_IMM R2,0,208\n"
             "SYSCALL 2\nSYSCALL 4\n")
    assert vm.r(1) == 0 and vm.r(2) == 1


def test_mmap_free_null_traps():
    with pytest.raises(HeapError):
        run(".code\n.entry s\ns:\n"
            "LOAD_IMM R0,2,0\nLOAD_IMM R1,1,0\nLOAD_IMM R2,0,0\n"
            "SYSCALL 2\nSYSCALL 4\n")


def test_mmap_unknown_operation_traps():
    with pytest.raises(Trap):
        run(".code\n.entry s\ns:\n"
            "LOAD_IMM R0,2,0\nLOAD_IMM R1,7,0\nSYSCALL 2\n")


# ---------------------------------------------------------------------
# HeapAllocator: split, coalesce, exhaustion, invalid frees
# ---------------------------------------------------------------------
def test_allocator_splits_and_tracks_free_bytes():
    heap = HeapAllocator(Memory())
    first = heap.malloc(100)
    assert first == HEAP_START
    second = heap.malloc(100)
    assert second >= first + 100
    assert heap.free_bytes() == (HEAP_END - HEAP_START) - 200


def test_allocator_coalesces_adjacent_freed_blocks():
    heap = HeapAllocator(Memory())
    a = heap.malloc(100)
    heap.malloc(100)
    c = heap.malloc(100)
    heap.free(c)
    heap.free(a)
    # a and c are not adjacent (middle still allocated): no merge across it
    # free both middle too and confirm full reclaim
    middle = a + 100
    heap.free(middle)
    assert heap.largest_free_block() == HEAP_END - HEAP_START
    assert heap.malloc(0x2000) != 0


def test_allocator_free_coalesces_immediate_neighbors():
    heap = HeapAllocator(Memory())
    a = heap.malloc(100)
    b = heap.malloc(100)
    heap.free(b)
    heap.free(a)
    assert heap.free_bytes() == HEAP_END - HEAP_START
    assert heap.malloc(200) == a


def test_allocator_exhaustion_returns_null():
    heap = HeapAllocator(Memory())
    assert heap.malloc(HEAP_END - HEAP_START + 1) == 0


def test_allocator_rejects_invalid_and_double_free():
    heap = HeapAllocator(Memory())
    for bad in (0, 0x4000, 0xB000):
        with pytest.raises(HeapError):
            heap.free(bad)
    pointer = heap.malloc(4)
    heap.free(pointer)
    with pytest.raises(HeapError):
        heap.free(pointer)


def test_allocator_rejects_non_positive_size():
    heap = HeapAllocator(Memory())
    with pytest.raises(HeapError):
        heap.malloc(0)


# ---------------------------------------------------------------------
# SYSCALL 3: write (memory -> stdout)
# ---------------------------------------------------------------------
def test_syswrite_outputs_memory_and_reports_length():
    src = """
.code
.entry s
s:
  LOAD_IMM R0,3,0
  LEA R1, data
  LOAD_IMM R2,4,0
  SYSCALL 3
  SYSCALL 4
.data
data: .string "abcd"
"""
    buf = io.BytesIO()
    vm = VM(assemble(src), stdout=buf)
    vm.run()
    assert buf.getvalue() == b"abcd"
    assert vm.r(3) == 4


# ---------------------------------------------------------------------
# SYSCALL 4: halt
# ---------------------------------------------------------------------
def test_halt_stops_execution_at_next_instruction():
    vm = VM(assemble(".code\n.entry s\ns:\nINC R0\nSYSCALL 4\nINC R0\n"),
            stdout=io.BytesIO())
    vm.run()
    assert vm.halted and vm.r(0) == 1
    assert vm.pc == 0x0108


def test_unknown_syscall_number_traps():
    with pytest.raises(Trap, match="9"):
        run(".code\n.entry s\ns:\nSYSCALL 9\n")
