# TinyVM Instruction Set Architecture

TinyVM is a self-contained 16-bit bytecode virtual machine implemented with
the Python 3 standard library only. This document describes the ISA executed
by the fetch-decode-execute engine in `tinyvm/vm.py`.

## Execution model

* Fixed-width instructions: every instruction is exactly **4 bytes**.
* Little-endian 16-bit data; word accesses need no alignment.
* GP/SP/FP registers hold 16-bit values with wrap-around on overflow.
* The machine runs an explicit **fetch -> decode -> execute** loop and counts
  every executed instruction in a cycle counter.
* Illegal operations raise typed traps (see [Traps](#traps)).

## Registers

| Name            | Count/Width | Purpose                                   |
|-----------------|-------------|-------------------------------------------|
| `R0` .. `R15`   | 16 x 16-bit | General purpose                           |
| `PC`            | 16-bit      | Program counter (must be 4-byte aligned)  |
| `SP`            | 17-bit*     | Stack pointer, grows downward             |
| `FP`            | 17-bit*     | Frame pointer                             |
| `FLAGS`         | 16-bit      | Status flags                              |

\* SP/FP use `0x10000` internally as the empty-stack (one-past-top) sentinel;
their register-file mirror reads back as `0x0000`.

### FLAGS bits

| Bit | Name       | Set when                                             |
|-----|------------|------------------------------------------------------|
| 0   | `CARRY`    | unsigned add carry / sub borrow / shift-out bit      |
| 1   | `ZERO`     | result is zero                                       |
| 2   | `SIGN`     | result sign bit (bit 15) is set                      |
| 3   | `OVERFLOW` | signed two's-complement overflow                     |

## Instruction encoding

```
+-----------+-------------+-------------+-------------+
|  opcode   | operand A   | operand B   | operand C   |
|  1 byte   |   1 byte    |   1 byte    |   1 byte    |
+-----------+-------------+-------------+-------------+
```

Operand meaning is fully determined by the opcode.

* `r`   — register number, raw byte `0..15` (`R0`..`R15`).
* `ri`  — register **or** small signed immediate. The high bit is a tag:
  bytes `0..15` encode registers; bytes `>= 0x80` encode an immediate.
  The immediate payload is 7-bit sign-extended, range **-64..63**, encoded
  as `(imm & 0x7f) ^ 0x80`. Larger constants use `LOAD_IMM`.
* `u8`  — raw unsigned byte `0..255`.
* `addr16` — 16-bit absolute address, two raw little-endian bytes.

### Operand layout conventions

* Binary ALU `OP rd, rs, ri`: A=`rd`, B=`rs`, C=`ri`.
* `CMP rs, ri`: A=`rs`, B=`ri`.
* Branches / `CALL target`: A=target low, B=target high.
* `LOAD_IMM rd, lo, hi`: A=`rd`, B=low byte, C=high byte.
* `LOAD_MEM rd, addr`: A=`rd`, B=addr low, C=addr high.
* `STORE_MEM`:
  - absolute: `STORE_MEM addr, rs` -> A=addr low, B=addr high, C=`rs`.
  - register-indirect: `STORE_MEM Raddr, rs` -> A=address register,
    B=`rs`, C=0. The runtime selects this form when B `<= 0x0F`; an
    absolute data/heap/stack address always has a high byte `>= 0x40`,
    so the encodings never overlap for reachable memory.
* `LEA rd, addr`: A=`rd`, B=addr low, C=addr high.

## Opcodes

### Arithmetic / logic

| Mnemonic | Hex | Operands          | Operation                      |
|----------|-----|-------------------|--------------------------------|
| `ADD`    | 01  | `rd, rs, ri`      | `rd = rs + ri`                 |
| `SUB`    | 02  | `rd, rs, ri`      | `rd = rs - ri`                 |
| `MUL`    | 03  | `rd, rs, ri`      | `rd = rs * ri` (low 16 bits)   |
| `DIV`    | 04  | `rd, rs, ri`      | `rd = rs / ri`, trunc toward 0 |
| `MOD`    | 05  | `rd, rs, ri`      | `rd = rs mod ri`               |
| `AND`    | 06  | `rd, rs, ri`      | bitwise AND                    |
| `OR`     | 07  | `rd, rs, ri`      | bitwise OR                     |
| `XOR`    | 08  | `rd, rs, ri`      | bitwise XOR                    |
| `SHL`    | 09  | `rd, rs, ri`      | logical shift left             |
| `SHR`    | 0A  | `rd, rs, ri`      | logical shift right            |
| `CMP`    | 0B  | `rs, ri`          | set FLAGS from `rs - ri`       |
| `NEG`    | 0C  | `rd, rs`          | `rd = -rs` (two's complement)  |
| `NOT`    | 0D  | `rd, rs`          | `rd = ~rs`                     |
| `INC`    | 0E  | `rd`              | `rd = rd + 1`                  |
| `DEC`    | 0F  | `rd`              | `rd = rd - 1`                  |

`DIV`/`MOD` trap on a zero divisor. `MUL` sets CARRY when the unsigned
32-bit product has a non-zero high half; shifts set CARRY to the last bit
shifted out (amount 0 never sets it; amount >= 16 yields 0).

### Control flow

| Mnemonic | Hex | Operands   | Condition / effect                                |
|----------|-----|------------|---------------------------------------------------|
| `JMP`    | 10  | `addr`     | unconditional jump                                |
| `JEQ`    | 11  | `addr`     | jump if ZERO (`rs == ri`)                         |
| `JNE`    | 12  | `addr`     | jump if not ZERO                                  |
| `JLT`    | 13  | `addr`     | signed less-than  (`SIGN != OVERFLOW`)           |
| `JGT`    | 14  | `addr`     | signed greater-than (not LT and not ZERO)         |
| `JLE`    | 15  | `addr`     | LT or ZERO                                        |
| `JGE`    | 16  | `addr`     | not LT (GT or equal)                              |
| `CALL`   | 17  | `addr`     | push `PC+4`, then jump                            |
| `RET`    | 18  | —          | pop return address into PC                        |
| `INT`    | 19  | `u8 vec`   | push PC, push FLAGS, jump to IVT handler          |
| `IRET`   | 1A  | —          | pop FLAGS, pop return PC                          |

All branch and CALL targets must be 4-byte aligned and within the code
segment; otherwise a `PCFault` trap is raised. A conditional branch that is
**not taken** performs no target validation.

### Memory / I/O

| Mnemonic   | Hex | Operands          | Operation                                  |
|------------|-----|-------------------|--------------------------------------------|
| `LOAD_IMM` | 20  | `rd, u8, u8`      | `rd = byte2*256 + byte1` (16-bit constant) |
| `LOAD_REG` | 21  | `rd, rs`          | `rd = word_at(rs)` (register-indirect)     |
| `LOAD_MEM` | 22  | `rd, addr16`      | `rd = word_at(addr)`                       |
| `STORE_MEM`| 23  | `addr, rs` / `Raddr, rs` | store word (absolute or indirect) |
| `PUSH`     | 24  | `ri`              | decrement SP by 2, store word              |
| `POP`      | 25  | `rd`              | read word at SP, increment SP by 2         |
| `LEA`      | 26  | `rd, addr16`      | `rd = addr` (address, not contents)        |
| `SYSCALL`  | 27  | `u8 n`            | invoke syscall `n`                         |

## Memory map

A single unified 16-bit address space, 64 KiB:

| Range               | Segment          | Access            |
|---------------------|------------------|-------------------|
| `0x0000`-`0x00FF`   | IVT              | read-only to guest|
| `0x0100`-`0x3FFF`   | Code             | execute + read, no write |
| `0x4000`-`0x7FFF`   | Data             | read/write        |
| `0x8000`-`0xAFFF`   | Heap (grows up)  | read/write        |
| `0xB000`-`0xFFFF`   | Stack (grows down)| read/write       |

Data accesses that touch the IVT/code range, or wrap past `0xFFFF`, trap.
Any guest write into the IVT/code segment is a self-modifying-code trap.

## Syscalls

`SYSCALL n` dispatches on `n`; arguments/results use fixed registers.

| n | Name  | Inputs                                   | Outputs / effects                        |
|---|-------|------------------------------------------|------------------------------------------|
| 0 | print | R1=format, R2=value/address              | write to stdout                          |
| 1 | read  | R1=format                                | result in R2                             |
| 2 | mmap  | R1=op, R2=size/pointer                   | malloc/free via the heap allocator       |
| 3 | write | R1=address, R2=length                    | write memory to stdout, count in R3      |
| 4 | halt  | —                                        | stop the machine                         |

* print formats (R1): `0` signed integer in R2, `1` single byte/char,
  `2` NUL-terminated string whose address is in R2.
* read formats (R1): `0` one whitespace-delimited signed integer into R2
  (traps on non-numeric input), `1` one byte into R2 (`0xFFFF` on EOF).
* mmap ops (R1): `0` malloc of R2 bytes -> pointer in R1, status in R2
  (`0` success, `1` ENOMEM with pointer `0`); `1` free the pointer in R2.

## Interrupts

The Interrupt Vector Table at `0x0000`-`0x00FF` stores one little-endian
handler address per vector (`address = vector * 2`). Vectors are declared
with the `.vector n, handler` assembler directive and installed into memory
when a program loads.

* `INT n` pushes the return PC then the current FLAGS, and jumps to the
  registered handler; an unregistered vector traps.
* `IRET` pops FLAGS and then the return PC, restoring the interrupted
  context. Interrupt handlers may nest.

## Traps

| Exception              | Raised when                                                  |
|------------------------|--------------------------------------------------------------|
| `DivideByZero`         | DIV/MOD divisor is zero                                      |
| `StackOverflow`        | a push grows SP below `0xB000`                               |
| `StackUnderflow`       | POP/RET/IRET runs past the empty-stack sentinel              |
| `MemoryFault`          | data access in IVT/code, wrapping range, or write to RO seg  |
| `PCFault`              | PC/branch/CALL/RET target misaligned or outside code segment |
| `InvalidOpcode`        | the fetched byte is not an opcode                            |
| `HeapError`            | free(NULL), double free, non-heap pointer, bad size          |
| `Trap`                 | unknown syscall/vector, bad read input, cycle limit, etc.    |

The engine also enforces a configurable `max_cycles` cap; exceeding it
raises `Trap` so runaway programs cannot hang the host.
