# tinyvm formats

## Binary container (`.tvmb`)

Magic **`TVMB`**, big-endian throughout.

```
offset  size  field
0       4     magic = "TVMB"
4       1     version = 1
5       1     flags   = 0 (reserved)
6       2     entry point (u16), normally 0x0100
8       2     code length  C (bytes)
10      2     data length  D (bytes)
12      2     IVT entry count V
14      C     code bytes, loaded at 0x0100
14+C    D     data bytes, loaded at 0x4000
14+C+D  3*V   IVT records: [n:u8][handler:u16], sorted by n
```

`C` must be a multiple of 4 (one instruction each). IVT record `n` is in
`0..127`; its handler address is written to memory `0x0000 + 2*n` on load.

Python API:

```python
from tinyvm.program import Program
blob = Program(code=..., data=..., entry=0x0100, ivt={1: 0x110}).to_bytes()
program = Program.from_bytes(blob)
```

## Assembly source syntax

One instruction or directive per line. Tokens are separated by
whitespace; operands are comma-separated.

### Comments

Both `;` and `#` start a comment that runs to end of line.

### Labels

An identifier followed by `:` defines a label bound to the current
address. It may share a line with an instruction (`loop: DEC R0`).
Labels are case-sensitive and may be referenced before definition.
Duplicate definitions and undefined references are errors with line and
column.

### Numbers and registers

- Registers: `R0`–`R15` (case-insensitive).
- Numbers: decimal (`42`, `-7`) or hexadecimal (`0x2A`).
- `LOAD_IMM` accepts `-32768..65535`; addresses accept `0..65535`.

### Directives

| Directive | Effect |
| --- | --- |
| `.code` | switch to the code section (default) |
| `.data` | switch to the data section (base 0x4000) |
| `.word v, ...` | 16-bit big-endian values; a label emits its address |
| `.byte v, ...` | raw bytes (`0..255`) |
| `.string "..."` | UTF-8 bytes plus a NUL terminator; escapes `\n \t \r \0 \" \\` |
| `.space n` | `n` zero bytes |
| `.ivt n, label` | map interrupt `n` (`0..127`) to a code label |

Instructions are illegal in the data section and data directives are
illegal in the code section.

### Examples

```
        LOAD_IMM R0, 10
loop:   DEC R0
        JNE loop
        SYSCALL 4
```

Indirect memory operands use brackets:

```
LEA R8, buf
LOAD_IMM R1, 0x1234
STORE_MEM [R8], R1
LOAD_MEM R2, [R8]
buf: .space 2          ; data section
```

## Disassembler and round-trip

`disassemble(program)` produces source text that re-assembles to the
identical program: code bytes, data bytes, entry and IVT records all
match. Jump/CALL/IVT targets that land inside the code image become
generated code labels `L0`, `L1`, ...; `LEA` targets inside the data
image become `D0`, `D1`, ...; other addresses print as `0xXXXX`.

## Command line

```
python -m tinyvm asm    source.asm [-o out.tvmb]
python -m tinyvm disasm program.tvmb|source.asm [-o out.asm]
python -m tinyvm run    program.tvmb|source.asm [--trace] [--max-cycles N]
python -m tinyvm bench  [--duration SECONDS]
```

`run --trace` prints one snapshot per instruction (cycle, PC, opcode,
operand bytes, decoded immediate, SP, FLAGS and selected registers).
`bench` runs the built-in iterative fib(30) program and reports
instructions per run and instructions/second.

End-to-end example:

```
python -m tinyvm asm examples/bubble.asm -o /tmp/bubble.tvmb
python -m tinyvm run /tmp/bubble.tvmb
```
