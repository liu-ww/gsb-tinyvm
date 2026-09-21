"""Heap allocator tests: malloc/free, coalescing, exhaustion, mmap syscall."""

from tinyvm.heap import HeapAllocator
from tinyvm.isa import HEAP_BASE, HEAP_END, HEAP_SIZE

from conftest import run_asm


def test_malloc_first_fit():
    heap = HeapAllocator()
    a = heap.malloc(100)
    assert a == HEAP_BASE
    b = heap.malloc(50)
    assert b == HEAP_BASE + 100


def test_malloc_aligns_to_two_bytes():
    heap = HeapAllocator()
    a = heap.malloc(3)
    b = heap.malloc(3)
    assert b - a == 4


def test_malloc_zero_or_negative_returns_zero():
    heap = HeapAllocator()
    assert heap.malloc(0) == 0
    assert heap.malloc(-5) == 0


def test_heap_exhausted_returns_zero():
    heap = HeapAllocator()
    assert heap.malloc(HEAP_SIZE) == HEAP_BASE
    assert heap.malloc(2) == 0


def test_oversized_request_returns_zero():
    heap = HeapAllocator()
    assert heap.malloc(HEAP_SIZE + 2) == 0


def test_free_coalesces_adjacent_blocks():
    heap = HeapAllocator()
    a = heap.malloc(100)
    b = heap.malloc(100)
    c = heap.malloc(100)
    assert heap.free(b)
    assert heap.free(a)
    # a and b must have merged into a single 200-byte free block
    assert any(addr == a and size == 200 and free for addr, size, free in heap.blocks)
    assert heap.free(c)
    assert heap.free_bytes() == HEAP_SIZE
    assert heap.malloc(HEAP_SIZE) == HEAP_BASE


def test_free_invalid_address_fails():
    heap = HeapAllocator()
    assert not heap.free(HEAP_BASE + 10)
    a = heap.malloc(16)
    assert not heap.free(a + 2)  # interior pointer
    assert heap.free(a)
    assert not heap.free(a)  # double free


def test_fragmentation_then_full_coalescing():
    heap = HeapAllocator()
    blocks = [heap.malloc(1000) for _ in range(12)]  # 12000 of 12288 bytes
    assert all(blocks)
    # Free every other block: fragmented, no contiguous 2000-byte hole.
    for blk in blocks[::2]:
        assert heap.free(blk)
    assert heap.malloc(2000) == 0
    # Free the rest: everything coalesces back to one big block.
    for blk in blocks[1::2]:
        assert heap.free(blk)
    assert heap.free_bytes() == HEAP_SIZE
    assert heap.malloc(HEAP_SIZE) == HEAP_BASE


def test_syscall_mmap_malloc():
    vm = run_asm("""
        LOAD_IMM R0, 64
        LOAD_IMM R1, 0
        SYSCALL 2
        SYSCALL 4
    """)
    assert HEAP_BASE <= vm.regs[0] <= HEAP_END


def test_syscall_mmap_oom_returns_zero():
    vm = run_asm("""
        LOAD_IMM R0, 0x3001
        LOAD_IMM R1, 0
        SYSCALL 2
        SYSCALL 4
    """)
    assert vm.regs[0] == 0


def test_syscall_mmap_free():
    vm = run_asm("""
        LOAD_IMM R0, 64
        LOAD_IMM R1, 0
        SYSCALL 2
        LOAD_REG R1, R0
        SYSCALL 2
        SYSCALL 4
    """)
    assert vm.regs[0] == 0  # free succeeded
