"""64 KiB unified memory and the bump/free-list heap allocator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .errors import HeapError, MemoryFault
from .isabits import (
    CODE_END,
    CODE_START,
    DATA_END,
    DATA_START,
    HEAP_END,
    HEAP_START,
    IVT_END,
    MEMORY_SIZE,
)


class Memory:
    """Byte-addressed 16-bit address space with segment permissions."""

    def __init__(self) -> None:
        self.data = bytearray(MEMORY_SIZE)
        self.code_write_protected = True
        # Track which code bytes actually hold an instruction; used so that
        # executing data/IVT holes cannot happen silently.
        self.executable = bytearray(MEMORY_SIZE)

    # -- raw byte access --------------------------------------------------
    def read_byte(self, address: int) -> int:
        address &= 0xFFFF
        return self.data[address]

    def write_byte(self, address: int, value: int) -> None:
        address &= 0xFFFF
        if self.code_write_protected and address < CODE_END:
            raise MemoryFault(address, "write to read-only code/IVT segment")
        self.data[address] = value & 0xFF

    def read_word(self, address: int) -> int:
        """Read a little-endian 16-bit word.  Wraps at 0xFFFF."""
        address &= 0xFFFF
        high = (address + 1) & 0xFFFF
        return self.data[address] | (self.data[high] << 8)

    def write_word(self, address: int, value: int) -> None:
        address &= 0xFFFF
        high = (address + 1) & 0xFFFF
        if self.code_write_protected and (address < CODE_END or high < CODE_END):
            raise MemoryFault(address, "write to read-only code/IVT segment")
        self.data[address] = value & 0xFF
        self.data[high] = (value >> 8) & 0xFF

    # -- image loading ----------------------------------------------------
    def load_image(self, image: bytes, base: int, writable: bool) -> None:
        """Copy a raw segment image into memory."""
        base &= 0xFFFF
        end = base + len(image)
        if end > MEMORY_SIZE:
            raise MemoryFault(base, "image does not fit in address space")
        if not writable and base >= CODE_END:
            raise ValueError("read-only image must map into the code segment")
        self.data[base:end] = image

    def mark_executable(self, start: int, length: int) -> None:
        for i in range(length):
            self.executable[(start + i) & 0xFFFF] = 1


@dataclass
class Block:
    """A heap chunk.  Blocks live outside guest memory (meta allocator)."""

    addr: int
    size: int
    free: bool = True


class HeapAllocator:
    """First-fit allocator over 0x8000..0xAFFF with coalescing on free.

    Allocations are word aligned (2 bytes) with a minimum block of 2 bytes.
    Address 0 is never handed out, so it doubles as the NULL/error value.
    """

    GRANULARITY = 2

    def __init__(self, memory: Memory) -> None:
        self.memory = memory
        self.blocks: List[Block] = [Block(HEAP_START, HEAP_END - HEAP_START, True)]

    # ------------------------------------------------------------------
    def _align(self, size: int) -> int:
        if size <= 0:
            raise HeapError("malloc size must be positive")
        return max(self.GRANULARITY, (size + 1) & ~1)

    def malloc(self, size: int) -> int:
        """Return a heap address, or 0 when the heap is exhausted."""
        need = self._align(size)
        for block in self.blocks:
            if block.free and block.size >= need:
                if block.size - need >= self.GRANULARITY:
                    # split
                    index = self.blocks.index(block)
                    self.blocks.insert(
                        index + 1,
                        Block(block.addr + need, block.size - need, True),
                    )
                    block.size = need
                block.free = False
                return block.addr
        return 0  # heap exhausted / fragmented beyond satisfaction

    def free(self, address: int) -> None:
        if address == 0:
            raise HeapError("free(NULL)")
        block: Optional[Block] = next(
            (b for b in self.blocks if b.addr == address), None
        )
        if block is None:
            raise HeapError(f"free of non-heap pointer 0x{address:04x}")
        if block.free:
            raise HeapError(f"double free at 0x{address:04x}")
        block.free = True
        self._coalesce()

    def _coalesce(self) -> None:
        i = 0
        while i < len(self.blocks) - 1:
            current = self.blocks[i]
            nxt = self.blocks[i + 1]
            if current.free and nxt.free and current.addr + current.size == nxt.addr:
                current.size += nxt.size
                del self.blocks[i + 1]
            else:
                i += 1

    def free_bytes(self) -> int:
        return sum(b.size for b in self.blocks if b.free)

    def largest_free_block(self) -> int:
        return max((b.size for b in self.blocks if b.free), default=0)
