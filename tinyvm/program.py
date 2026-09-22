"""Serialized program container (``.tvm`` files).

Layout (all integers little endian)::

    offset 0  : 4 bytes  magic "TVM1"
    offset 4  : 1 byte   format version (1)
    offset 5  : 2 bytes  entry point address
    offset 7  : 2 bytes  code image base address
    offset 9  : 4 bytes  code image length
    offset 13 : 2 bytes  data image base address
    offset 15 : 4 bytes  data image length
    offset 19 : 2 bytes  number of IVT entries
    then code image, data image, IVT entries (u8 vector, u16 handler addr).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict

from .errors import TinyVMError

MAGIC = b"TVM1"
VERSION = 1
_HEADER_FORMAT = "<4sBHHIHHI"
HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)


class ProgramFormatError(TinyVMError):
    pass


@dataclass
class Program:
    entry: int
    code_base: int
    code: bytes
    data_base: int
    data: bytes = b""
    vectors: Dict[int, int] = field(default_factory=dict)

    def serialize(self) -> bytes:
        if len(self.vectors) > 65535:
            raise ProgramFormatError("too many IVT entries")
        header = struct.pack(
            _HEADER_FORMAT,
            MAGIC,
            VERSION,
            self.entry & 0xFFFF,
            self.code_base & 0xFFFF,
            len(self.code),
            self.data_base & 0xFFFF,
            len(self.data),
            len(self.vectors),
        )
        chunks = [header, self.code, self.data]
        for vector in sorted(self.vectors):
            chunks.append(struct.pack("<BH", vector & 0xFF, self.vectors[vector] & 0xFFFF))
        return b"".join(chunks)

    @classmethod
    def deserialize(cls, blob: bytes) -> "Program":
        if len(blob) < HEADER_SIZE:
            raise ProgramFormatError("file too short to be a .tvm image")
        magic, version, entry, code_base, code_len, data_base, data_len, nvec = \
            struct.unpack_from(_HEADER_FORMAT, blob, 0)
        if magic != MAGIC:
            raise ProgramFormatError(f"bad magic {magic!r}, expected {MAGIC!r}")
        if version != VERSION:
            raise ProgramFormatError(f"unsupported format version {version}")
        offset = HEADER_SIZE
        needed = code_len + data_len + nvec * 3
        if len(blob) - offset < needed:
            raise ProgramFormatError("truncated .tvm image")
        code = bytes(blob[offset:offset + code_len])
        offset += code_len
        data = bytes(blob[offset:offset + data_len])
        offset += data_len
        vectors: Dict[int, int] = {}
        for _ in range(nvec):
            vector, handler = struct.unpack_from("<BH", blob, offset)
            offset += 3
            vectors[vector] = handler
        return cls(entry, code_base, code, data_base, data, vectors)
