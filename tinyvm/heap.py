"""Heap allocator for the tinyvm heap segment (0x8000-0xAFFF).

First-fit free-list allocator over a Python-side block list. Adjacent free
blocks are coalesced on free() to fight fragmentation. Allocation addresses
refer to the VM's unified address space; block contents live in VM memory,
the allocator only manages the layout metadata.
"""

from __future__ import annotations

from .isa import HEAP_BASE, HEAP_SIZE


class HeapAllocator:
    """First-fit allocator with coalescing over the heap segment."""

    def __init__(self, base: int = HEAP_BASE, size: int = HEAP_SIZE) -> None:
        self.base = base
        self.size = size
        # Each block: [address, size, is_free]
        self._blocks: list[list[object]] = [[base, size, True]]

    @staticmethod
    def _align(n: int) -> int:
        return (n + 1) & ~1  # 2-byte alignment

    def malloc(self, n: int) -> int:
        """Allocate n bytes; return the heap address, or 0 if exhausted."""
        if n <= 0:
            return 0
        n = self._align(n)
        for i, (addr, size, free) in enumerate(self._blocks):
            if free and size >= n:
                if size > n:
                    self._blocks[i] = [addr, n, False]
                    self._blocks.insert(i + 1, [addr + n, size - n, True])
                else:
                    self._blocks[i][2] = False
                return addr
        return 0

    def free(self, addr: int) -> bool:
        """Free a block previously returned by malloc. Returns success."""
        for block in self._blocks:
            if block[0] == addr and not block[2]:
                block[2] = True
                self._coalesce()
                return True
        return False

    def _coalesce(self) -> None:
        merged: list[list[object]] = []
        for block in self._blocks:
            if (
                merged
                and merged[-1][2]
                and block[2]
                and merged[-1][0] + merged[-1][1] == block[0]
            ):
                merged[-1][1] += block[1]
            else:
                merged.append(list(block))
        self._blocks = merged

    def free_bytes(self) -> int:
        """Total number of free bytes in the heap."""
        return sum(size for _, size, free in self._blocks if free)

    def largest_free_block(self) -> int:
        """Size of the largest contiguous free block."""
        return max((size for _, size, free in self._blocks if free), default=0)

    @property
    def blocks(self) -> list[tuple[int, int, bool]]:
        """Snapshot of the block list as (address, size, is_free) tuples."""
        return [(addr, size, free) for addr, size, free in self._blocks]
