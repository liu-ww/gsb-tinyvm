# tinyvm ISA

A 16-bit, fixed-instruction-length virtual machine implemented in pure
Python 3 (standard library only).

## Registers

| Register | Purpose |
| --- | --- |
| `R0`–`R15` | 16 general-purpose registers |
| `PC` | program counter (byte address, 4-byte aligned) |
| `SP` | stack pointer; full descending, starts at `0x10000` (exclusive) |
| `FP` | frame pointer (available to software) |
| `FLAGS` | status flags |

All registers and memory words are 16 bits. Arithmetic wraps modulo
2^16; comparisons and conditional jumps interpret operands as signed.

`FLAGS` bit layout:

| Bit | Flag | Meaning |
| --- | --- | --- |
| 0 | `ZERO` | result was zero (operands equal for CMP) |
| 1 | `SIGN` | sign bit of the result (bit 15) |
| 2 | `CARRY` | unsigned carry/borrow out of bit 15 |
| 3 | `OVERFLOW` | signed two's-complement overflow |

## Instruction encoding

Every instruction is exactly **4 bytes**, big-endian:

```
[ opcode : 1 byte ][ operand1 : 1 byte ][ operand2 : 1 byte ][ operand3 : 1 byte ]
```

Depending on the opcode, operand bytes hold register numbers (`0`–`15`),
an 8-bit immediate (interrupt/syscall number), or the high/low bytes of a
16-bit immediate/address.

Operand forms:

| Form | Example | Bytes |
| --- | --- | --- |
| `RRR` | `ADD Rd, Rs, Rt` | op, d, s, t |
| `RR` | `NEG Rd, Rs` / `CMP Rs, Rt` | op, d, s, - |
| `R` | `INC Rd` | op, d, -, - |
| `NONE` | `RET` / `IRET` | op, -, -, - |
| `N8` | `INT n` / `SYSCALL n` | op, n, -, - |
| `A16` | `JMP addr` | op, -, hi(addr), lo(addr) |
| `RA16` | `LEA Rd, addr` | op, d, hi(addr), lo(addr) |
| `RI16` | `LOAD_IMM Rd, imm` | op, d, hi(imm), lo(imm) |
| `R_IND` | `LOAD_MEM Rd, [Rs]` | op, d, s, - |
| `IND_R` | `STORE_MEM [Rd], Rs` | op, d, s, - |

## Memory map

Unified 16-bit address space, 64 KiB:

| Range | Region | Notes |
| --- | --- | --- |
| `0x0000`–`0x00FF` | IVT | 128 x 2-byte handler vectors; write protected |
| `0x0100`–`0x3FFF` | code | loaded read-only; `PC` must stay inside, 4-aligned |
| `0x4000`–`0x7FFF` | data | initialized from the program image |
| `0x8000`–`0xAFFF` | heap | grows upward; first-fit allocator with coalescing |
| `0xB000`–`0xFFFF` | stack | grows downward (`PUSH` pre-decrements `SP`) |

Words in memory are stored big-endian. A word access whose high byte is
at `0xFFFF` traps (`MEMORY_OUT_OF_BOUNDS`).

Writing any byte of the IVT or code segment traps with
`SELF_MODIFYING_CODE`. Any control transfer outside the loaded code image
(or misaligned) traps with `PC_OUT_OF_BOUNDS`.

## Arithmetic and logic (0x01–0x0F)

| Mnemonic | Op | Semantics |
| --- | --- | --- |
| `ADD Rd, Rs, Rt` | 0x01 | `Rd = Rs + Rt` |
| `SUB Rd, Rs, Rt` | 0x02 | `Rd = Rs - Rt` (CARRY = unsigned borrow) |
| `MUL Rd, Rs, Rt` | 0x03 | `Rd = Rs * Rt (mod 65536)` |
| `DIV Rd, Rs, Rt` | 0x04 | signed quotient, truncation toward zero |
| `MOD Rd, Rs, Rt` | 0x05 | signed remainder, sign follows dividend |
| `AND/OR/XOR` | 0x06–0x08 | bitwise; clear CARRY and OVERFLOW |
| `SHL Rd, Rs, Rt` | 0x09 | logical left by `Rt & 0xF`; CARRY = last bit out |
| `SHR Rd, Rs, Rt` | 0x0A | logical right by `Rt & 0xF`; CARRY = last bit out |
| `CMP Rs, Rt` | 0x0B | set FLAGS as `Rs - Rt`, no register write |
| `NEG Rd, Rs` | 0x0C | two's-complement negate |
| `NOT Rd, Rs` | 0x0D | bitwise complement |
| `INC Rd` / `DEC Rd` | 0x0E/0x0F | `Rd += 1` / `Rd -= 1`; preserve CARRY |

`DIV`/`MOD` with a zero divisor trap with `DIVIDE_BY_ZERO`.

## Control flow (0x20–0x2A)

| Mnemonic | Op | Branch condition |
| --- | --- | --- |
| `JMP addr` | 0x20 | unconditional |
| `JEQ` / `JNE` | 0x21/0x22 | ZERO set / clear |
| `JLT` / `JGE` | 0x23/0x26 | signed `<` / `>=` (SIGN xor OVERFLOW) |
| `JGT` / `JLE` | 0x24/0x25 | signed `>` / `<=` |
| `CALL addr` | 0x27 | push `PC+4`, then jump |
| `RET` | 0x28 | pop return address into `PC` |
| `INT n` | 0x29 | invoke IVT vector `n` |
| `IRET` | 0x2A | restore FLAGS and return address |

### Interrupts

The IVT holds 128 two-byte handler addresses at `0x0000` + `2*n`.
`INT n` looks up vector `n`; an unprogrammed vector traps with
`UNHANDLED_INTERRUPT`. It pushes the interrupted `PC`, then the current
`FLAGS`, and jumps to the handler. `IRET` pops `FLAGS` then `PC`.
Interrupts may nest.

## Memory and I/O (0x40–0x47)

| Mnemonic | Op | Semantics |
| --- | --- | --- |
| `LOAD_IMM Rd, imm16` | 0x40 | load 16-bit immediate |
| `LOAD_REG Rd, Rs` | 0x41 | register copy |
| `LOAD_MEM Rd, [Rs]` | 0x42 | load word from address in `Rs` |
| `STORE_MEM [Rd], Rs` | 0x43 | store word to address in `Rd` |
| `PUSH Rs` | 0x44 | pre-decrement `SP` by 2, store word |
| `POP Rd` | 0x45 | load word at `SP`, post-increment by 2 |
| `LEA Rd, addr16` | 0x46 | load effective address |
| `SYSCALL n` | 0x47 | system call, see below |

### Syscalls

| n | Name | Behavior |
| --- | --- | --- |
| 0 | print | write signed decimal of `R0` plus newline |
| 1 | read | parse one integer token from stdin into `R0` |
| 2 | mmap | `R1==0`: `R0 = malloc(R0)` (0 on exhaustion); `R1!=0`: `free(R1)`, `R0 = 0/0xFFFF` |
| 3 | write | output `R1` raw bytes from buffer at `R0` |
| 4 | halt | stop the machine |

## Traps

`DIVIDE_BY_ZERO`, `STACK_OVERFLOW`, `STACK_UNDERFLOW`,
`SELF_MODIFYING_CODE`, `PC_OUT_OF_BOUNDS`, `MEMORY_OUT_OF_BOUNDS`,
`INVALID_OPCODE`, `INVALID_SYSCALL`, `UNHANDLED_INTERRUPT`,
`INPUT_EXHAUSTED`, `INVALID_INPUT`, `CYCLE_LIMIT`.
Every trap reports the offending `PC`.
