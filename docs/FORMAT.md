# TinyVM File Formats

Two formats are documented here:

1. **`.tvm` binary container** produced by `python -m tinyvm asm` and loaded
   by `run` / `disasm` (implemented in `tinyvm/program.py`).
2. **Assembly source language** accepted by the two-pass assembler
   (`tinyvm/assembler.py`).

## The `.tvm` container

All multi-byte integers are little endian. The file is the header, followed
by the raw code image, the raw data image, and the IVT entries.

### Header

Layout, in order:

| Offset | Size | Field        | Type | Meaning                          |
|--------|------|--------------|------|----------------------------------|
| 0      | 4    | magic        | char | always `"TVM1"`                  |
| 4      | 1    | version      | u8   | container version, currently `1` |
| 5      | 2    | entry        | u16  | initial PC                       |
| 7      | 2    | code_base    | u16  | load address of the code image   |
| 9      | 4    | code_len     | u32  | code image length in bytes       |
| 13     | 2    | data_base    | u16  | load address of the data image   |
| 15     | 4    | data_len     | u32  | data image length in bytes       |
| 19     | 2    | vector_count | u16  | number of IVT entries            |

Header total: **21 bytes** (`struct` format `<4sBHHIHHI>`). The length is
derived with `struct.calcsize`, so it always matches the packed fields.

### Payload

| Offset                       | Content                                        |
|------------------------------|------------------------------------------------|
| `21`                         | code image, `code_len` bytes                   |
| `21 + code_len`              | data image, `data_len` bytes                   |
| after data                   | `vector_count` records, each 3 bytes: `u8 vector`, `u16 handler` (little endian) |

The code image is copied to `code_base` (normally `0x0100`) and marked
read-only/executable; the data image is copied to `data_base` (normally
`0x4000`). Every IVT record writes the handler address into
`0x0000 + vector*2`.

### Serialization example

```python
from tinyvm.assembler import assemble
from tinyvm.program import Program

program = assemble(open("examples/bubble.asm").read())
blob = program.serialize()                 # bytes
restored = Program.deserialize(blob)
assert restored.serialize() == blob        # stable round trip
```

Truncated files, a bad magic and an unknown version raise
`ProgramFormatError`.

## Assembly source

One statement per physical line:

```
[label:]   [mnemonic | directive [operands]]   [; comment]
```

* Comments run from `;` to end of line; comment-only lines are allowed.
* Labels, mnemonics and register names are case-insensitive.
* A label matches `[A-Za-z_][A-Za-z0-9_]*` and is optionally followed by `:`.
* Numbers: decimal (`42`, `-7`), hexadecimal (`0x4000`), binary (`0b1010`).
* Small immediates in `ri` slots use a `#` prefix and range `-64..63`
  (e.g. `ADD R1, R1, #-3`). Use `LOAD_IMM` for larger 16-bit constants.
* Operands are comma/whitespace separated; strings use `"` or `'`.

### Directives

| Directive  | Section | Meaning                                             |
|------------|---------|-----------------------------------------------------|
| `.code`    | —       | switch to the code section                          |
| `.data`    | —       | switch to the data section                          |
| `.org addr`| —       | set the current code/data address within a segment  |
| `.entry l` | code    | declare the entry point (label or address)          |
| `.vector n, handler` | code | bind interrupt vector `n` (0..255) to handler |
| `.byte v, ...` | data | emit one byte per value (0..255)                  |
| `.word v, ...` | data | emit little-endian 16-bit words (signed int or label) |
| `.string "..." [, ...]` | data | emit each string plus a trailing NUL byte    |
| `.space n` | data    | reserve `n` zero-filled bytes                        |

Label references accept an optional constant offset, e.g. `JMP loop`,
`LOAD_MEM R1, table+2`, `.word arr`.

### Two-pass assembly

* **Pass 1** walks every line, tracks the current section address, records
  each label's address, lays out instructions (4 bytes each) and data
  directives, and validates segment capacity.
* **Pass 2** resolves labels to concrete addresses and emits the code and
  data images. Forward references work because label resolution happens
  after pass 1.

Errors carry a 1-based line and column, including: an undefined label, a
duplicate label, operands out of range (`R16`, `#64`, u8 > 255, etc.),
wrong operand counts, unknown mnemonics/directives and section overflow.

## Disassembler and round-tripping

`disassemble(program)` emits assembler text that re-assembles to an
identical container:

```python
from tinyvm.assembler import assemble
from tinyvm.disassembler import disassemble

program = assemble(source)
again = assemble(disassemble(program))
assert again.serialize() == program.serialize()
```

Because the container stores no symbols, the rendered text uses numeric
addresses for jumps and `.word`/.byte directives for the data image.
