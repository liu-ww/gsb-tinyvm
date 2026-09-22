"""Fetch-decode-execute engine for tinyvm.

Registers R0..R15, SP and FP are 16-bit.  FLAGS holds four bits
(CARRY/ZERO/SIGN/OVERFLOW).  The machine is little endian; word accesses
need no alignment.
"""

from __future__ import annotations

from typing import BinaryIO, Callable, Dict, List, Optional, TextIO

from .errors import (
    DivideByZero,
    InvalidOpcode,
    MemoryFault,
    PCFault,
    StackOverflow,
    StackUnderflow,
    Trap,
)
from .isabits import (
    CODE_END,
    CODE_START,
    DATA_START,
    FLAG_CARRY,
    FLAG_OVERFLOW,
    FLAG_SIGN,
    FLAG_ZERO,
    HEAP_END,
    HEAP_START,
    INSTRUCTION_SIZE,
    IVT_END,
    IVT_START,
    MNEMONICS,
    NUM_GP_REGISTERS,
    REG_FLAGS,
    REG_FP,
    REG_PC,
    REG_SP,
    SIGNATURES,
    STACK_BOTTOM,
    STACK_TOP,
    decode_tagged_byte,
    s16,
    u16,
)
from .memory import HeapAllocator, Memory
from .program import Program

# syscall numbers
SYS_PRINT = 0
SYS_READ = 1
SYS_MMAP = 2
SYS_WRITE = 3
SYS_HALT = 4

MAX_CYCLES_DEFAULT = 100_000_000


class VM:
    def __init__(
        self,
        program: Optional[Program] = None,
        stdin: Optional[BinaryIO] = None,
        stdout: Optional[BinaryIO] = None,
        max_cycles: int = MAX_CYCLES_DEFAULT,
        install_ivt: bool = True,
    ) -> None:
        import sys

        self.memory = Memory()
        self.heap = HeapAllocator(self.memory)
        # R0..R15 plus PC, FP, FLAGS live in the 16-bit register file.
        # SP needs to represent the one-past-top sentinel 0x10000, so it is
        # tracked as a plain integer and mirrored into the register file.
        self.registers = [0] * 20
        self._sp = STACK_TOP
        self._fp = STACK_TOP
        self.pc = CODE_START
        self.fp = STACK_TOP
        self.flags = 0
        self.cycles = 0
        self.halted = False
        self.stdin = stdin if stdin is not None else sys.stdin.buffer
        self.stdout = stdout if stdout is not None else sys.stdout.buffer
        self.max_cycles = max_cycles
        self.trace_sink: Optional[TextIO] = None
        self.interrupt_handlers: Dict[int, int] = {}
        self._handlers: Dict[int, Callable[[int, int, int, int], None]] = {
            0x01: self._op_add, 0x02: self._op_sub, 0x03: self._op_mul,
            0x04: self._op_div, 0x05: self._op_mod, 0x06: self._op_and,
            0x07: self._op_or, 0x08: self._op_xor, 0x09: self._op_shl,
            0x0A: self._op_shr, 0x0B: self._op_cmp, 0x0C: self._op_neg,
            0x0D: self._op_not, 0x0E: self._op_inc, 0x0F: self._op_dec,
            0x10: self._op_jmp, 0x11: self._op_jeq, 0x12: self._op_jne,
            0x13: self._op_jlt, 0x14: self._op_jgt, 0x15: self._op_jle,
            0x16: self._op_jge, 0x17: self._op_call, 0x18: self._op_ret,
            0x19: self._op_int, 0x1A: self._op_iret,
            0x20: self._op_load_imm, 0x21: self._op_load_reg,
            0x22: self._op_load_mem, 0x23: self._op_store_mem,
            0x24: self._op_push, 0x25: self._op_pop, 0x26: self._op_lea,
            0x27: self._op_syscall,
        }
        if program is not None:
            self.load_program(program, install_ivt=install_ivt)

    # ------------------------------------------------------------------
    # register plumbing
    # ------------------------------------------------------------------
    @property
    def pc(self) -> int:
        return self.registers[REG_PC]

    @pc.setter
    def pc(self, value: int) -> None:
        self.registers[REG_PC] = value & 0xFFFF

    @property
    def sp(self) -> int:
        return self._sp

    @sp.setter
    def sp(self, value: int) -> None:
        # The empty stack sentinel 0x10000 is representable here; the
        # register-file mirror holds it as 0x0000 (one-past-top).
        value %= 0x10001
        self._sp = value
        self.registers[REG_SP] = value & 0xFFFF

    @property
    def fp(self) -> int:
        return self._fp

    @fp.setter
    def fp(self, value: int) -> None:
        value %= 0x10001
        self._fp = value
        self.registers[REG_FP] = value & 0xFFFF

    @property
    def flags(self) -> int:
        return self.registers[REG_FLAGS]

    @flags.setter
    def flags(self, value: int) -> None:
        self.registers[REG_FLAGS] = value & 0xFFFF

    def r(self, index: int) -> int:
        return self.registers[index]

    def set_r(self, index: int, value: int) -> None:
        self.registers[index] = value & 0xFFFF

    # ------------------------------------------------------------------
    # program loading
    # ------------------------------------------------------------------
    def load_program(self, program: Program, install_ivt: bool = True) -> None:
        self.memory = Memory()
        self.heap = HeapAllocator(self.memory)
        self.memory.load_image(program.code, program.code_base, writable=False)
        self.memory.mark_executable(
            program.code_base, len(program.code)
        )
        if program.data:
            self.memory.load_image(program.data, program.data_base, writable=True)
        self.pc = program.entry
        self._sp = STACK_TOP
        self.registers[REG_SP] = 0
        self._fp = STACK_TOP
        self.registers[REG_FP] = 0
        self.fp = STACK_TOP
        self.flags = 0
        for i in range(NUM_GP_REGISTERS):
            self.registers[i] = 0
        self.halted = False
        self.cycles = 0
        self.interrupt_handlers = {}
        if install_ivt:
            for vector, handler in program.vectors.items():
                self.set_vector(vector, handler)

    def set_vector(self, vector: int, handler: int) -> None:
        if not (0 <= vector <= 255):
            raise ValueError("vector must be 0..255")
        if not (CODE_START <= handler < CODE_END):
            raise ValueError("handler must live in the code segment")
        self.interrupt_handlers[vector] = handler
        addr = IVT_START + vector * 2
        self.memory.code_write_protected = False
        self.memory.write_word(addr, handler)
        self.memory.code_write_protected = True

    # ------------------------------------------------------------------
    # stack helpers
    # ------------------------------------------------------------------
    def _push_word(self, value: int) -> None:
        new_sp = self.sp - 2
        if new_sp < STACK_BOTTOM:
            raise StackOverflow(self.pc)
        self.sp = new_sp
        self.memory.write_word(new_sp, value)

    def _pop_word(self) -> int:
        if self.sp >= STACK_TOP:
            raise StackUnderflow(self.pc)
        value = self.memory.read_word(self.sp)
        # Use the 0x10000 sentinel (not & 0xFFFF) so a fully drained stack
        # reports the empty-stack pointer instead of 0.
        self.sp = self.sp + 2
        return value

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------
    def run(self, trace: Optional[TextIO] = None) -> int:
        self.trace_sink = trace
        while not self.halted:
            self.step()
        return self.registers[0]

    def step(self) -> None:
        if self.halted:
            return
        if self.cycles >= self.max_cycles:
            raise Trap(f"cycle limit ({self.max_cycles}) exceeded", self.pc)
        pc = self.pc
        self._validate_pc(pc)
        opcode = self.memory.read_byte(pc)
        a = self.memory.read_byte((pc + 1) & 0xFFFF)
        b = self.memory.read_byte((pc + 2) & 0xFFFF)
        c = self.memory.read_byte((pc + 3) & 0xFFFF)
        handler = self._handlers.get(opcode)
        if handler is None:
            raise InvalidOpcode(opcode, pc)
        if self.trace_sink is not None:
            self._emit_trace(pc, opcode, a, b, c)
        self.pc = (pc + INSTRUCTION_SIZE) & 0xFFFF
        self.cycles += 1
        handler(opcode, a, b, c)

    def _validate_pc(self, pc: int) -> None:
        if pc & 3:
            raise PCFault(pc)
        if not (CODE_START <= pc < CODE_END):
            raise PCFault(pc)
        if not self.memory.executable[pc]:
            raise PCFault(pc)

    # ------------------------------------------------------------------
    # operand helpers
    # ------------------------------------------------------------------
    def _reg_or_imm(self, byte: int) -> int:
        kind, value = decode_tagged_byte(byte)
        return value if kind == "imm" else self.registers[value]


    def _addr(self, low: int, high: int) -> int:
        return low | (high << 8)

    def _set_flags(self, result: int, *, zero: Optional[bool] = None,
                   sign: Optional[bool] = None, carry: bool = False,
                   overflow: bool = False) -> None:
        value = u16(result)
        flags = 0
        z = zero if zero is not None else value == 0
        s = sign if sign is not None else bool(value & 0x8000)
        if z:
            flags |= FLAG_ZERO
        if s:
            flags |= FLAG_SIGN
        if carry:
            flags |= FLAG_CARRY
        if overflow:
            flags |= FLAG_OVERFLOW
        self.flags = flags

    # ------------------------------------------------------------------
    # arithmetic / logic
    # ------------------------------------------------------------------
    def _binary_operands(self, a: int, b: int, c: int):
        """Return ``(rs_value, ri_value, dest)`` for ``OP rd, rs, ri``.

        Operand A selects the destination register, operand B the first
        source register and operand C the second source as either a
        register or a tagged immediate.
        """
        return self.r(b), self._reg_or_imm(c), a

    def _op_add(self, _op: int, a: int, b: int, c: int) -> None:
        x, y, dest = self._binary_operands(a, b, c)
        unsigned = x + y
        result = u16(unsigned)
        self.set_r(dest, result)
        self._set_flags(
            result,
            carry=unsigned > 0xFFFF,
            overflow=((~(x ^ y)) & (x ^ result) & 0x8000) != 0,
        )

    def _op_sub(self, _op: int, a: int, b: int, c: int) -> None:
        x, y, dest = self._binary_operands(a, b, c)
        unsigned = x - y
        result = u16(unsigned)
        self.set_r(dest, result)
        self._set_flags(
            result,
            carry=unsigned < 0,
            overflow=((x ^ y) & (x ^ result) & 0x8000) != 0,
        )

    def _op_mul(self, _op: int, a: int, b: int, c: int) -> None:
        # Operands are treated as unsigned 16-bit values for the stored
        # product and the CARRY flag (CARRY set when the 32-bit product's
        # upper half is non-zero); the low 16 bits match signed multiply.
        x = self.r(b)
        y = self._reg_or_imm(c)
        product = x * y
        result = product & 0xFFFF
        self.set_r(a, result)
        self._set_flags(result, carry=product > 0xFFFF)

    def _op_div(self, _op: int, a: int, b: int, c: int) -> None:
        divisor = self._reg_or_imm(c)
        if divisor == 0:
            raise DivideByZero(self.pc)
        dividend = s16(self.r(b))
        divisor = s16(divisor)
        # Integer truncation toward zero, matching C-like division.
        quotient = abs(dividend) // abs(divisor)
        if (dividend < 0) != (divisor < 0):
            quotient = -quotient
        overflow = not (-0x8000 <= quotient <= 0x7FFF)
        result = u16(quotient)
        self.set_r(a, result)
        self._set_flags(result, overflow=overflow)

    def _op_mod(self, _op: int, a: int, b: int, c: int) -> None:
        divisor = self._reg_or_imm(c)
        if divisor == 0:
            raise DivideByZero(self.pc)
        dividend = s16(self.r(b))
        remainder = abs(dividend) % abs(s16(divisor))
        if dividend < 0:
            remainder = -remainder
        result = u16(remainder)
        self.set_r(a, result)
        self._set_flags(result)

    def _op_and(self, _op: int, a: int, b: int, c: int) -> None:
        result = self.r(b) & self._reg_or_imm(c)
        self.set_r(a, result)
        self._set_flags(result)

    def _op_or(self, _op: int, a: int, b: int, c: int) -> None:
        result = self.r(b) | self._reg_or_imm(c)
        self.set_r(a, result)
        self._set_flags(result)

    def _op_xor(self, _op: int, a: int, b: int, c: int) -> None:
        result = self.r(b) ^ self._reg_or_imm(c)
        self.set_r(a, result)
        self._set_flags(result)

    def _op_shl(self, _op: int, a: int, b: int, c: int) -> None:
        amount = self._reg_or_imm(c) & 0xFFFF
        source = self.r(b)
        if amount >= 16:
            result, carry = 0, False
        elif amount == 0:
            result, carry = source, False
        else:
            shifted = source << amount
            result = shifted & 0xFFFF
            # CARRY = last bit shifted out (bit 15 of the pre-truncated value)
            carry = bool(shifted & 0x10000)
        self.set_r(a, result)
        self._set_flags(result, carry=carry)

    def _op_shr(self, _op: int, a: int, b: int, c: int) -> None:
        amount = self._reg_or_imm(c) & 0xFFFF
        source = self.r(b)
        if amount >= 16:
            result, carry = 0, False
        elif amount == 0:
            result, carry = source, False
        else:
            result = source >> amount
            carry = bool(source & (1 << (amount - 1)))
        self.set_r(a, result)
        self._set_flags(result, carry=carry)

    def _op_cmp(self, _op: int, a: int, b: int, c: int) -> None:
        x = self.r(a)
        y = self._reg_or_imm(b)
        unsigned = x - y
        result = u16(unsigned)
        self._set_flags(
            result,
            carry=unsigned < 0,
            overflow=((x ^ y) & (x ^ result) & 0x8000) != 0,
        )

    def _op_neg(self, _op: int, a: int, b: int, c: int) -> None:
        source = self.r(b)
        result = u16(-source)
        self.set_r(a, result)
        self._set_flags(result, overflow=(source == 0x8000))

    def _op_not(self, _op: int, a: int, b: int, c: int) -> None:
        result = u16(~self.r(b))
        self.set_r(a, result)
        self._set_flags(result)

    def _op_inc(self, _op: int, a: int, b: int, c: int) -> None:
        x = self.r(a)
        result = u16(x + 1)
        self.set_r(a, result)
        self._set_flags(result, carry=x == 0xFFFF, overflow=x == 0x7FFF)

    def _op_dec(self, _op: int, a: int, b: int, c: int) -> None:
        x = self.r(a)
        unsigned = x - 1
        result = u16(unsigned)
        # CARRY is the subtraction borrow (x - 1 wrapped); OVERFLOW on 0x8000.
        self.set_r(a, result)
        self._set_flags(result, carry=unsigned < 0, overflow=x == 0x8000)

    # ------------------------------------------------------------------
    # control flow
    # ------------------------------------------------------------------
    def _branch(self, target: int, taken: bool) -> None:
        if taken:
            if target & 3 or not (CODE_START <= target < CODE_END):
                raise PCFault(target)
            self.pc = target

    def _op_jmp(self, _op: int, a: int, b: int, c: int) -> None:
        self._branch(self._addr(a, b), True)

    def _op_jeq(self, _op: int, a: int, b: int, c: int) -> None:
        self._branch(self._addr(a, b), bool(self.flags & FLAG_ZERO))

    def _op_jne(self, _op: int, a: int, b: int, c: int) -> None:
        self._branch(self._addr(a, b), not (self.flags & FLAG_ZERO))

    def _signed_less(self) -> bool:
        # Signed less-than after CMP/SUB: SF xor OF (two's complement rule).
        sign = bool(self.flags & FLAG_SIGN)
        overflow = bool(self.flags & FLAG_OVERFLOW)
        return sign != overflow

    def _op_jlt(self, _op: int, a: int, b: int, c: int) -> None:
        self._branch(self._addr(a, b), self._signed_less())

    def _op_jgt(self, _op: int, a: int, b: int, c: int) -> None:
        less = self._signed_less()
        equal = bool(self.flags & FLAG_ZERO)
        self._branch(self._addr(a, b), not less and not equal)

    def _op_jle(self, _op: int, a: int, b: int, c: int) -> None:
        equal = bool(self.flags & FLAG_ZERO)
        self._branch(self._addr(a, b), self._signed_less() or equal)

    def _op_jge(self, _op: int, a: int, b: int, c: int) -> None:
        equal = bool(self.flags & FLAG_ZERO)
        self._branch(self._addr(a, b), equal or not self._signed_less())

    def _op_call(self, _op: int, a: int, b: int, c: int) -> None:
        target = self._addr(a, b)
        if target & 3 or not (CODE_START <= target < CODE_END):
            raise PCFault(target)
        self._push_word(self.pc)  # pc already advanced past CALL
        self.pc = target

    def _op_ret(self, _op: int, a: int, b: int, c: int) -> None:
        target = self._pop_word()
        if target & 3 or not (CODE_START <= target < CODE_END):
            raise PCFault(target)
        self.pc = target

    def _op_int(self, _op: int, a: int, b: int, c: int) -> None:
        vector = a
        handler = self.interrupt_handlers.get(vector)
        if handler is None:
            raise Trap(f"unregistered interrupt vector {vector}", self.pc)
        self._push_word(self.pc)
        self._push_word(self.flags)
        self.pc = handler

    def _op_iret(self, _op: int, a: int, b: int, c: int) -> None:
        self.flags = self._pop_word()
        target = self._pop_word()
        if target & 3 or not (CODE_START <= target < CODE_END):
            raise PCFault(target)
        self.pc = target

    # ------------------------------------------------------------------
    # memory / I/O
    # ------------------------------------------------------------------
    def _op_load_imm(self, _op: int, a: int, b: int, c: int) -> None:
        self.set_r(a, b | (c << 8))

    def _op_load_reg(self, _op: int, a: int, b: int, c: int) -> None:
        address = self.r(b)
        self._check_data_read(address, 2)
        self.set_r(a, self.memory.read_word(address))

    def _op_load_mem(self, _op: int, a: int, b: int, c: int) -> None:
        address = self._addr(b, c)
        self._check_data_read(address, 2)
        self.set_r(a, self.memory.read_word(address))

    def _op_store_mem(self, _op: int, a: int, b: int, c: int) -> None:
        address = self._addr(a, b)
        self._check_data_read(address, 2)
        self.memory.write_word(address, self.r(c))

    def _op_push(self, _op: int, a: int, b: int, c: int) -> None:
        self._push_word(self._reg_or_imm(a))

    def _op_pop(self, _op: int, a: int, b: int, c: int) -> None:
        self.set_r(a, self._pop_word())

    def _op_lea(self, _op: int, a: int, b: int, c: int) -> None:
        self.set_r(a, self._addr(b, c))

    def _check_data_range(self, address: int, size: int) -> None:
        """Validate a contiguous guest data access.

        Every accessed byte must live at or above 0x4000 (i.e. outside the
        IVT and the read-only code segment) and the range may not wrap past
        the top of the 16-bit address space.
        """
        if size < 0:
            raise MemoryFault(address, "negative access size", self.pc)
        if address < CODE_END:
            raise MemoryFault(
                address, "data access inside code/IVT segment", self.pc
            )
        end = address + size
        if end > 0x10000:
            raise MemoryFault(
                0xFFFF, "data access wraps past address space", self.pc
            )

    def _check_data_read(self, address: int, size: int) -> None:
        # Backwards-compatible wrapper for word-sized data accesses.
        self._check_data_range(address, size)

    def _op_syscall(self, _op: int, a: int, b: int, c: int) -> None:
        number = a
        dispatch = {
            SYS_PRINT: self._sys_print,
            SYS_READ: self._sys_read,
            SYS_MMAP: self._sys_mmap,
            SYS_WRITE: self._sys_write,
            SYS_HALT: self._sys_halt,
        }
        handler = dispatch.get(number)
        if handler is None:
            raise Trap(f"unknown syscall {number}", self.pc)
        handler()

    # -- syscalls -------------------------------------------------------
    def _sys_print(self) -> None:
        kind = self.r(1)
        if kind == 0:  # signed integer in R2
            value = s16(self.r(2))
            self.stdout.write(str(value).encode("ascii"))
        elif kind == 1:  # single character code in R2
            self.stdout.write(bytes([self.r(2) & 0xFF]))
        elif kind == 2:  # NUL-terminated string at R2
            self.stdout.write(self._read_cstring(self.r(2)))
        else:
            raise Trap(f"print: unknown format {kind}", self.pc)
        self.stdout.flush()

    def _sys_read(self) -> None:
        kind = self.r(1)
        if kind == 0:  # read one signed integer, result in R2
            token = self._read_token()
            try:
                value = int(token)
            except ValueError:
                raise Trap(f"read: not an integer: {token!r}", self.pc)
            self.set_r(2, value)
        elif kind == 1:  # read one byte, result in R2; -1 on EOF
            chunk = self.stdin.read(1)
            self.set_r(2, chunk[0] if chunk else 0xFFFF)
        else:
            raise Trap(f"read: unknown format {kind}", self.pc)

    def _sys_mmap(self) -> None:
        operation = self.r(1)
        if operation == 0:  # malloc, size in R2; pointer in R1, status in R2
            size = self.r(2)
            pointer = self.heap.malloc(size)
            self.set_r(1, pointer)
            self.set_r(2, 0 if pointer else 1)  # 1 == ENOMEM
        elif operation == 1:  # free, pointer in R2
            self.heap.free(self.r(2))
        else:
            raise Trap(f"mmap: unknown operation {operation}", self.pc)

    def _sys_write(self) -> None:
        address = self.r(1)
        length = self.r(2)
        self._check_data_read(address, length)
        chunk = bytes(self.memory.data[address:address + length])
        self.stdout.write(chunk)
        self.stdout.flush()
        self.set_r(3, len(chunk))

    def _sys_halt(self) -> None:
        self.halted = True

    def _read_token(self) -> str:
        chars: List[str] = []
        while True:
            byte = self.stdin.read(1)
            if not byte:
                break
            char = byte.decode("latin-1")
            if char.isspace():
                if chars:
                    break
                continue
            chars.append(char)
        return "".join(chars)

    def _read_cstring(self, address: int) -> bytes:
        if address < CODE_END:
            raise MemoryFault(
                address, "cstring access inside code/IVT segment", self.pc
            )
        out = bytearray()
        cursor = address
        for _ in range(0x10000):
            byte = self.memory.read_byte(cursor)
            if byte == 0:
                break
            out.append(byte)
            cursor += 1
            if cursor > 0xFFFF:
                raise MemoryFault(
                    0xFFFF, "unterminated string reached end of memory", self.pc
                )
        return bytes(out)

    # ------------------------------------------------------------------
    # tracing
    # ------------------------------------------------------------------
    def _emit_trace(self, pc: int, opcode: int, a: int, b: int, c: int) -> None:
        assert self.trace_sink is not None
        mnemonic = MNEMONICS.get(opcode, f"0x{opcode:02x}")
        regs = " ".join(f"R{i}={self.r(i):04x}" for i in range(NUM_GP_REGISTERS))
        self.trace_sink.write(
            f"cyc={self.cycles:>8} pc=0x{pc:04x} "
            f"{mnemonic:<9} [{a:02x} {b:02x} {c:02x}] "
            f"sp=0x{self.sp:04x} fp=0x{self.fp:04x} "
            f"flags={self._flag_text()} {regs}\n"
        )
        self.trace_sink.flush()

    def _flag_text(self) -> str:
        return (
            f"{'Z' if self.flags & FLAG_ZERO else '-'}"
            f"{'S' if self.flags & FLAG_SIGN else '-'}"
            f"{'C' if self.flags & FLAG_CARRY else '-'}"
            f"{'O' if self.flags & FLAG_OVERFLOW else '-'}"
        )
