"""Execution engine for tinyvm: fetch-decode-execute over a 64 KiB space.

Memory map (see docs/ISA.md):
  0x0000-0x00FF  IVT (write protected; also blocks self-modifying writes)
  0x0100-0x3FFF  code segment, loaded read-only
  0x4000-0x7FFF  data segment
  0x8000-0xAFFF  heap (first-fit allocator, mmap syscall)
  0xB000-0xFFFF  stack, full descending

Words are 16 bits, big-endian. Instructions are 4 bytes at aligned
addresses. Every boundary violation raises VMTrap.
"""

from __future__ import annotations

import io
from typing import BinaryIO, TextIO

from .errors import TrapKind, VMTrap
from .heap import HeapAllocator
from .isa import (
    CODE_BASE,
    CODE_END,
    DATA_BASE,
    DATA_END,
    FLAG_CARRY,
    FLAG_OVERFLOW,
    FLAG_SIGN,
    FLAG_ZERO,
    HEAP_BASE,
    HEAP_END,
    INSTR_SIZE,
    IVT_BASE,
    IVT_END,
    MEM_SIZE,
    REG_COUNT,
    STACK_BASE,
    STACK_TOP,
    U16_MAX,
    Op,
    to_signed,
    to_unsigned,
)
from .program import Program

__all__ = ["VM"]


class VM:
    """A tinyvm machine instance."""

    def __init__(
        self,
        stdout: TextIO | None = None,
        stdin: TextIO | None = None,
        trace: bool = False,
    ) -> None:
        self.memory = bytearray(MEM_SIZE)
        self.regs = [0] * REG_COUNT
        self.pc = CODE_BASE
        self.sp = STACK_TOP
        self.fp = 0
        self.flags = 0
        self.cycles = 0
        self.halted = False
        self.trace = trace
        self.stdout: TextIO = stdout if stdout is not None else io.StringIO()
        self.stdin: TextIO = stdin if stdin is not None else io.StringIO()
        self.heap = HeapAllocator()
        self._code_start = CODE_BASE
        self._code_end = CODE_BASE
        self._ivt: dict[int, int] = {}

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reset all machine state (memory cleared)."""
        self.memory = bytearray(MEM_SIZE)
        self.regs = [0] * REG_COUNT
        self.pc = CODE_BASE
        self.sp = STACK_TOP
        self.fp = 0
        self.flags = 0
        self.cycles = 0
        self.halted = False
        self.heap = HeapAllocator()
        self._code_start = CODE_BASE
        self._code_end = CODE_BASE
        self._ivt = {}

    def load_program(self, program: Program) -> None:
        """Load code, data and IVT entries into memory and set PC."""
        self.reset()
        code = program.code
        if CODE_BASE + len(code) > CODE_END + 1:
            raise VMTrap(TrapKind.PC_OUT_OF_BOUNDS, "code image too large")
        self.memory[CODE_BASE : CODE_BASE + len(code)] = code
        self._code_start = program.entry
        self._code_end = CODE_BASE + len(code)
        data = program.data
        if DATA_BASE + len(data) > DATA_END + 1:
            raise VMTrap(TrapKind.MEMORY_OUT_OF_BOUNDS, "data image too large")
        self.memory[DATA_BASE : DATA_BASE + len(data)] = data
        self._ivt = dict(program.ivt)
        for number, handler in program.ivt.items():
            self._write_word(IVT_BASE + number * 2, handler)
        self.pc = program.entry

    # ------------------------------------------------------------------
    # memory primitives
    # ------------------------------------------------------------------

    def _trap(self, kind: TrapKind, message: str = "") -> None:
        raise VMTrap(kind, message, self.pc)

    def _read_byte(self, addr: int) -> int:
        addr &= U16_MAX
        return self.memory[addr]

    def _write_byte(self, addr: int, value: int) -> None:
        addr &= U16_MAX
        self.memory[addr] = value & 0xFF

    def _read_word(self, addr: int) -> int:
        addr &= U16_MAX
        if addr >= U16_MAX:
            self._trap(TrapKind.MEMORY_OUT_OF_BOUNDS,
                       f"word read at 0x{addr:04X} crosses 64 KiB")
        return (self.memory[addr] << 8) | self.memory[addr + 1]

    def _write_word(self, addr: int, value: int) -> None:
        addr &= U16_MAX
        if addr >= U16_MAX:
            self._trap(TrapKind.MEMORY_OUT_OF_BOUNDS,
                       f"word write at 0x{addr:04X} crosses 64 KiB")
        self.memory[addr] = (value >> 8) & 0xFF
        self.memory[addr + 1] = value & 0xFF

    def _check_pc(self, addr: int) -> None:
        """Validate that addr is an aligned instruction inside the image."""
        if addr & (INSTR_SIZE - 1):
            self._trap(TrapKind.PC_OUT_OF_BOUNDS,
                       f"misaligned pc 0x{addr:04X}")
        if not self._code_start <= addr < self._code_end:
            self._trap(TrapKind.PC_OUT_OF_BOUNDS,
                       f"pc 0x{addr:04X} outside code image")

    def _check_writable_word(self, addr: int) -> None:
        """Validate a word write: data/heap/stack only, no code or IVT."""
        addr &= U16_MAX
        if addr >= U16_MAX:
            self._trap(TrapKind.MEMORY_OUT_OF_BOUNDS,
                       f"write at 0x{addr:04X} crosses 64 KiB")
        if IVT_BASE <= addr <= IVT_END or IVT_BASE <= addr + 1 <= IVT_END:
            self._trap(TrapKind.SELF_MODIFYING_CODE,
                       f"write to IVT at 0x{addr:04X}")
        if CODE_BASE <= addr <= CODE_END or CODE_BASE <= addr + 1 <= CODE_END:
            self._trap(TrapKind.SELF_MODIFYING_CODE,
                       f"write to code segment at 0x{addr:04X}")

    # ------------------------------------------------------------------
    # stack
    # ------------------------------------------------------------------

    def _push(self, value: int) -> None:
        if self.sp - 2 < STACK_BASE:
            self._trap(TrapKind.STACK_OVERFLOW,
                       f"sp would drop below 0x{STACK_BASE:04X}")
        self.sp -= 2
        self._write_word(self.sp, value)

    def _pop(self) -> int:
        if self.sp >= STACK_TOP:
            self._trap(TrapKind.STACK_UNDERFLOW, "pop with empty stack")
        value = self._read_word(self.sp)
        self.sp += 2
        return value

    # ------------------------------------------------------------------
    # flags
    # ------------------------------------------------------------------

    def _set_logic_flags(self, result: int) -> None:
        """ZERO + SIGN flags used by data-processing instructions."""
        result &= U16_MAX
        self.flags &= ~(FLAG_ZERO | FLAG_SIGN)
        if result == 0:
            self.flags |= FLAG_ZERO
        if result & 0x8000:
            self.flags |= FLAG_SIGN

    # ------------------------------------------------------------------
    # fetch / run
    # ------------------------------------------------------------------

    def run(self, max_cycles: int = 10_000_000) -> None:
        """Run until HALT/syscall halt, a trap, or the cycle limit."""
        while not self.halted:
            if self.cycles >= max_cycles:
                self._trap(TrapKind.CYCLE_LIMIT,
                           f"exceeded {max_cycles} cycles")
            self.step()

    def step(self) -> None:
        """Fetch, decode and execute one instruction."""
        self._check_pc(self.pc)
        opcode = self.memory[self.pc]
        b1 = self.memory[self.pc + 1]
        b2 = self.memory[self.pc + 2]
        b3 = self.memory[self.pc + 3]
        if self.trace:
            self._emit_trace(opcode, b1, b2, b3)
        next_pc = self.pc + INSTR_SIZE
        self.pc = next_pc
        self.cycles += 1
        self._execute(opcode, b1, b2, b3, next_pc)

    def _emit_trace(self, opcode: int, b1: int, b2: int, b3: int) -> None:
        word16 = (b2 << 8) | b3
        print(
            f"cycle={self.cycles:<7} pc=0x{self.pc:04X} "
            f"op=0x{opcode:02X} b=({b1:2d},{b2:3d},{b3:3d}) imm=0x{word16:04X} "
            f"sp=0x{self.sp:04X} fl=0x{self.flags:02X} "
            f"R0={self.regs[0]} R1={self.regs[1]} R2={self.regs[2]}",
            file=self.stdout,
        )

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    def _execute(self, opcode: int, b1: int, b2: int, b3: int,
                 next_pc: int) -> None:
        try:
            op = Op(opcode)
        except ValueError:
            self._trap(TrapKind.INVALID_OPCODE, f"0x{opcode:02X}")
        dispatch = {
            Op.ADD: lambda: self._op_add(b1, b2, b3),
            Op.SUB: lambda: self._op_sub(b1, b2, b3),
            Op.MUL: lambda: self._op_mul(b1, b2, b3),
            Op.DIV: lambda: self._op_div(b1, b2, b3),
            Op.MOD: lambda: self._op_mod(b1, b2, b3),
            Op.AND: lambda: self._op_and(b1, b2, b3),
            Op.OR: lambda: self._op_or(b1, b2, b3),
            Op.XOR: lambda: self._op_xor(b1, b2, b3),
            Op.SHL: lambda: self._op_shl(b1, b2, b3),
            Op.SHR: lambda: self._op_shr(b1, b2, b3),
            Op.CMP: lambda: self._op_cmp(b1, b2),
            Op.NEG: lambda: self._op_neg(b1, b2),
            Op.NOT: lambda: self._op_not(b1, b2),
            Op.INC: lambda: self._op_inc(b1),
            Op.DEC: lambda: self._op_dec(b1),
            Op.JMP: lambda: self._jump((b2 << 8) | b3),
            Op.JEQ: lambda: self._cond_jump(self.flags & FLAG_ZERO,
                                            (b2 << 8) | b3),
            Op.JNE: lambda: self._cond_jump(not (self.flags & FLAG_ZERO),
                                            (b2 << 8) | b3),
            Op.JLT: lambda: self._cond_jump(self._signed_less(),
                                            (b2 << 8) | b3),
            Op.JGT: lambda: self._cond_jump(self._signed_greater(),
                                            (b2 << 8) | b3),
            Op.JLE: lambda: self._cond_jump(
                self._signed_less() or bool(self.flags & FLAG_ZERO),
                (b2 << 8) | b3),
            Op.JGE: lambda: self._cond_jump(
                self._signed_greater() or bool(self.flags & FLAG_ZERO),
                (b2 << 8) | b3),
            Op.CALL: lambda: self._call((b2 << 8) | b3),
            Op.RET: self._ret,
            Op.INT: lambda: self._int(b1),
            Op.IRET: self._iret,
            Op.LOAD_IMM: lambda: self._op_load_imm(b1, (b2 << 8) | b3),
            Op.LOAD_REG: lambda: self._op_load_reg(b1, b2),
            Op.LOAD_MEM: lambda: self._op_load_mem(b1, b2),
            Op.STORE_MEM: lambda: self._op_store_mem(b1, b2),
            Op.PUSH: lambda: self._push(self.regs[b1]),
            Op.POP: lambda: self._op_pop_into(b1),
            Op.LEA: lambda: self._op_lea(b1, (b2 << 8) | b3),
            Op.SYSCALL: lambda: self._syscall(b1),
        }
        dispatch[op]()

    def _signed_less(self) -> bool:
        """Signed '<' result of the last CMP, from SIGN/OVERFLOW."""
        return bool((self.flags & FLAG_SIGN) ^ bool(self.flags & FLAG_OVERFLOW))

    def _signed_greater(self) -> bool:
        """Signed '>' result of the last CMP."""
        equal = bool(self.flags & FLAG_ZERO)
        return not equal and not self._signed_less()

    # ------------------------------------------------------------------
    # arithmetic / logic
    # ------------------------------------------------------------------

    def _flags_add(self, a: int, b: int, result: int) -> None:
        self.flags &= ~(FLAG_ZERO | FLAG_SIGN | FLAG_CARRY | FLAG_OVERFLOW)
        if result > U16_MAX:
            self.flags |= FLAG_CARRY
        result &= U16_MAX
        if result == 0:
            self.flags |= FLAG_ZERO
        if result & 0x8000:
            self.flags |= FLAG_SIGN
        sa, sb, sr = a & 0x8000, b & 0x8000, result & 0x8000
        if sa == sb and sr != sa:
            self.flags |= FLAG_OVERFLOW

    def _op_add(self, d: int, s: int, t: int) -> None:
        a, b = self.regs[s], self.regs[t]
        result = a + b
        self._flags_add(a, b, result)
        self.regs[d] = result & U16_MAX

    def _op_sub(self, d: int, s: int, t: int) -> None:
        a, b = self.regs[s], self.regs[t]
        result = a - b
        self.flags &= ~(FLAG_ZERO | FLAG_SIGN | FLAG_CARRY | FLAG_OVERFLOW)
        if a < b:
            self.flags |= FLAG_CARRY  # unsigned borrow
        wrapped = result & U16_MAX
        if wrapped == 0:
            self.flags |= FLAG_ZERO
        if wrapped & 0x8000:
            self.flags |= FLAG_SIGN
        sa, sb, sr = a & 0x8000, b & 0x8000, wrapped & 0x8000
        if sa != sb and sr == sb:
            self.flags |= FLAG_OVERFLOW
        self.regs[d] = wrapped

    def _op_mul(self, d: int, s: int, t: int) -> None:
        a, b = self.regs[s], self.regs[t]
        product = a * b
        result = product & U16_MAX
        signed_product = to_signed(a) * to_signed(b)
        self._set_logic_flags(result)
        self.flags &= ~(FLAG_CARRY | FLAG_OVERFLOW)
        if product > U16_MAX:
            self.flags |= FLAG_CARRY
        if not -0x8000 <= signed_product <= 0x7FFF:
            self.flags |= FLAG_OVERFLOW
        self.regs[d] = result

    def _op_div(self, d: int, s: int, t: int) -> None:
        a, b = to_signed(self.regs[s]), to_signed(self.regs[t])
        if b == 0:
            self._trap(TrapKind.DIVIDE_BY_ZERO, "DIV by zero")
        if a == -0x8000 and b == -1:
            self._trap(TrapKind.OVERFLOW, "signed division overflow")
        self.regs[d] = to_unsigned(int(a / b))

    def _op_mod(self, d: int, s: int, t: int) -> None:
        a, b = to_signed(self.regs[s]), to_signed(self.regs[t])
        if b == 0:
            self._trap(TrapKind.DIVIDE_BY_ZERO, "MOD by zero")
        # truncation toward zero: remainder has the sign of the dividend
        self.regs[d] = to_unsigned(a - int(a / b) * b)

    def _op_and(self, d: int, s: int, t: int) -> None:
        result = self.regs[s] & self.regs[t]
        self.regs[d] = result
        self.flags &= ~(FLAG_CARRY | FLAG_OVERFLOW)
        self._set_logic_flags(result)

    def _op_or(self, d: int, s: int, t: int) -> None:
        result = self.regs[s] | self.regs[t]
        self.regs[d] = result
        self.flags &= ~(FLAG_CARRY | FLAG_OVERFLOW)
        self._set_logic_flags(result)

    def _op_xor(self, d: int, s: int, t: int) -> None:
        result = self.regs[s] ^ self.regs[t]
        self.regs[d] = result
        self.flags &= ~(FLAG_CARRY | FLAG_OVERFLOW)
        self._set_logic_flags(result)

    def _op_shl(self, d: int, s: int, t: int) -> None:
        value, count = self.regs[s], self.regs[t] & 0xF
        result = (value << count) & U16_MAX
        self.flags &= ~(FLAG_ZERO | FLAG_SIGN | FLAG_CARRY | FLAG_OVERFLOW)
        if count > 0 and (value & (1 << (16 - count))):
            self.flags |= FLAG_CARRY
        if (to_signed(value) << count) < -0x8000 or (to_signed(value) << count) > 0x7FFF:
            self.flags |= FLAG_OVERFLOW
        self._set_logic_flags(result)
        self.flags |= (FLAG_CARRY if count > 0 and (value & (1 << (16 - count))) else 0)
        self.regs[d] = result

    def _op_shr(self, d: int, s: int, t: int) -> None:
        value, count = self.regs[s], self.regs[t] & 0xF
        result = (value >> count) & U16_MAX
        self.flags &= ~(FLAG_ZERO | FLAG_SIGN | FLAG_CARRY | FLAG_OVERFLOW)
        carry = bool(count > 0 and (value & (1 << (count - 1))))
        if carry:
            self.flags |= FLAG_CARRY
        self._set_logic_flags(result)
        self.regs[d] = result

    def _op_cmp(self, s: int, t: int) -> None:
        a, b = self.regs[s], self.regs[t]
        wrapped = (a - b) & U16_MAX
        self.flags &= ~(FLAG_ZERO | FLAG_SIGN | FLAG_CARRY | FLAG_OVERFLOW)
        if a < b:
            self.flags |= FLAG_CARRY  # unsigned borrow
        if wrapped == 0:
            self.flags |= FLAG_ZERO
        if wrapped & 0x8000:
            self.flags |= FLAG_SIGN
        sa, sb, sr = a & 0x8000, b & 0x8000, wrapped & 0x8000
        if sa != sb and sr == sb:
            self.flags |= FLAG_OVERFLOW

    def _op_neg(self, d: int, s: int) -> None:
        value = self.regs[s]
        result = (-to_signed(value)) & U16_MAX
        self.regs[d] = result
        self.flags &= ~(FLAG_CARRY | FLAG_OVERFLOW)
        self._set_logic_flags(result)
        if to_signed(value) == -0x8000:
            self.flags |= FLAG_OVERFLOW

    def _op_not(self, d: int, s: int) -> None:
        result = (~self.regs[s]) & U16_MAX
        self.regs[d] = result
        self.flags &= ~(FLAG_CARRY | FLAG_OVERFLOW)
        self._set_logic_flags(result)

    def _op_inc(self, d: int) -> None:
        value = self.regs[d]
        result = (value + 1) & U16_MAX
        self.regs[d] = result
        carry_kept = self.flags & FLAG_CARRY
        self.flags &= ~(FLAG_ZERO | FLAG_SIGN | FLAG_OVERFLOW)
        self.flags |= carry_kept
        if result == 0:
            self.flags |= FLAG_ZERO
        if result & 0x8000:
            self.flags |= FLAG_SIGN
        if value == 0x7FFF:
            self.flags |= FLAG_OVERFLOW

    def _op_dec(self, d: int) -> None:
        value = self.regs[d]
        result = (value - 1) & U16_MAX
        self.regs[d] = result
        carry_kept = self.flags & FLAG_CARRY
        self.flags &= ~(FLAG_ZERO | FLAG_SIGN | FLAG_OVERFLOW)
        self.flags |= carry_kept
        if result == 0:
            self.flags |= FLAG_ZERO
        if result & 0x8000:
            self.flags |= FLAG_SIGN
        if value == 0x8000:
            self.flags |= FLAG_OVERFLOW

    # ------------------------------------------------------------------
    # control flow
    # ------------------------------------------------------------------

    def _jump(self, target: int) -> None:
        self._check_pc(target)
        self.pc = target

    def _cond_jump(self, taken: bool, target: int) -> None:
        if taken:
            self._jump(target)

    def _call(self, target: int) -> None:
        self._check_pc(target)
        self._push(self.pc)  # pc already advanced past CALL in step()
        self.pc = target

    def _ret(self) -> None:
        return_addr = self._pop()
        self._check_pc(return_addr)
        self.pc = return_addr

    def _int(self, number: int) -> None:
        handler = self._ivt.get(number)
        if handler is None:
            self._trap(TrapKind.UNHANDLED_INTERRUPT,
                       f"interrupt {number} has no IVT entry")
        self._check_pc(handler)
        # save interrupted context: return pc, then flags
        self._push(self.pc)
        self._push(self.flags)
        self.pc = handler

    def _iret(self) -> None:
        self.flags = self._pop()
        return_addr = self._pop()
        self._check_pc(return_addr)
        self.pc = return_addr

    # ------------------------------------------------------------------
    # memory / register ops
    # ------------------------------------------------------------------

    def _op_load_imm(self, d: int, value: int) -> None:
        self.regs[d] = value

    def _op_load_reg(self, d: int, s: int) -> None:
        self.regs[d] = self.regs[s]

    def _op_lea(self, d: int, address: int) -> None:
        self.regs[d] = address

    def _op_load_mem(self, d: int, s: int) -> None:
        address = self.regs[s]
        if not 0 <= address <= U16_MAX - 1:
            self._trap(TrapKind.MEMORY_OUT_OF_BOUNDS,
                       f"load word at 0x{address:04X}")
        self.regs[d] = self._read_word(address)

    def _op_store_mem(self, d: int, s: int) -> None:
        address = self.regs[d]
        if not 0 <= address <= U16_MAX - 1:
            self._trap(TrapKind.MEMORY_OUT_OF_BOUNDS,
                       f"store word at 0x{address:04X}")
        self._check_writable_word(address)
        self._write_word(address, self.regs[s])

    def _op_pop_into(self, d: int) -> None:
        self.regs[d] = self._pop()

    # ------------------------------------------------------------------
    # syscalls
    # ------------------------------------------------------------------

    def _syscall(self, number: int) -> None:
        if number == 0:  # PRINT: signed decimal of R0 + newline
            self.stdout.write(str(to_signed(self.regs[0])) + "\n")
        elif number == 1:  # READ: integer token -> R0
            text = self.stdin.readline()
            token = text.strip()
            if not token:
                self._trap(TrapKind.INPUT_EXHAUSTED, "no input for READ")
            try:
                self.regs[0] = to_unsigned(int(token, 0))
            except ValueError:
                self._trap(TrapKind.INVALID_INPUT, f"not an integer: {token!r}")
        elif number == 2:  # MMAP: R1==0 malloc(R0); else free(R1)
            if self.regs[1] == 0:
                size = self.regs[0]
                if size > self.heap.largest_free_block():
                    self.regs[0] = 0
                else:
                    self.regs[0] = self.heap.malloc(size)
            else:
                ok = self.heap.free(self.regs[1])
                self.regs[0] = 0 if ok else U16_MAX
        elif number == 3:  # WRITE: R1 raw bytes from buffer at R0
            start = self.regs[0]
            length = self.regs[1]
            if start + length > MEM_SIZE:
                self._trap(TrapKind.MEMORY_OUT_OF_BOUNDS,
                           f"write {length} bytes from 0x{start:04X}")
            data = bytes(self.memory[start : start + length])
            self.stdout.write(data.decode("latin-1"))
        elif number == 4:  # HALT
            self.halted = True
        else:
            self._trap(TrapKind.INVALID_SYSCALL, f"syscall {number}")
