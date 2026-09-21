"""Assembled program container and its binary file format (see docs/FORMAT.md)."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .isa import CODE_BASE, IVT_MAX_ENTRIES

MAGIC = b"TVMB"
VERSION = 1
HEADER_SIZE = 14  # magic(4) + version(1) + flags(1) + 4 x u16


@dataclass
class Program:
    """An assembled tinyvm program: code, data, entry point and IVT entries."""

    code: bytes = b""
    data: bytes = b""
    entry: int = CODE_BASE
    ivt: dict[int, int] = field(default_factory=dict)

    def to_bytes(self) -> bytes:
        """Serialize to the TVMB binary container format."""
        header = (
            MAGIC
            + bytes([VERSION, 0])
            + struct.pack(">HHHH", self.entry, len(self.code), len(self.data), len(self.ivt))
        )
        ivt_blob = b"".join(
            bytes([n]) + struct.pack(">H", addr) for n, addr in sorted(self.ivt.items())
        )
        return header + self.code + self.data + ivt_blob

    @classmethod
    def from_bytes(cls, blob: bytes) -> "Program":
        """Parse a TVMB binary container."""
        if len(blob) < HEADER_SIZE:
            raise ValueError("file too small to be a TVMB program")
        if blob[:4] != MAGIC:
            raise ValueError("bad magic: not a TVMB program")
        version = blob[4]
        if version != VERSION:
            raise ValueError(f"unsupported TVMB version: {version}")
        entry, code_len, data_len, ivt_len = struct.unpack(">HHHH", blob[6:14])
        pos = HEADER_SIZE
        end = pos + code_len + data_len + ivt_len * 3
        if len(blob) < end:
            raise ValueError("truncated TVMB program")
        code = blob[pos : pos + code_len]
        pos += code_len
        data = blob[pos : pos + data_len]
        pos += data_len
        ivt: dict[int, int] = {}
        for _ in range(ivt_len):
            n = blob[pos]
            (addr,) = struct.unpack(">H", blob[pos + 1 : pos + 3])
            pos += 3
            if n >= IVT_MAX_ENTRIES:
                raise ValueError(f"IVT entry out of range: {n}")
            ivt[n] = addr
        return cls(code=code, data=data, entry=entry, ivt=ivt)
