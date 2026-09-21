"""Arithmetic and logic instruction tests, including FLAGS behavior."""

from tinyvm.isa import FLAG_CARRY, FLAG_OVERFLOW, FLAG_SIGN, FLAG_ZERO

from conftest import run_asm


def test_add():
    vm = run_asm("""
        LOAD_IMM R0, 40
        LOAD_IMM R1, 2
        ADD R2, R0, R1
        SYSCALL 4
    """)
    assert vm.regs[2] == 42


def test_add_carry_and_zero():
    vm = run_asm("""
        LOAD_IMM R0, 0xFFFF
        LOAD_IMM R1, 1
        ADD R2, R0, R1
        SYSCALL 4
    """)
    assert vm.regs[2] == 0
    assert vm.flags & FLAG_CARRY
    assert vm.flags & FLAG_ZERO


def test_add_signed_overflow():
    vm = run_asm("""
        LOAD_IMM R0, 0x7FFF
        LOAD_IMM R1, 1
        ADD R2, R0, R1
        SYSCALL 4
    """)
    assert vm.regs[2] == 0x8000
    assert vm.flags & FLAG_OVERFLOW
    assert vm.flags & FLAG_SIGN
    assert not vm.flags & FLAG_CARRY


def test_sub():
    vm = run_asm("""
        LOAD_IMM R0, 10
        LOAD_IMM R1, 3
        SUB R2, R0, R1
        SYSCALL 4
    """)
    assert vm.regs[2] == 7
    assert not vm.flags & FLAG_CARRY


def test_sub_borrow_sets_carry():
    vm = run_asm("""
        LOAD_IMM R0, 3
        LOAD_IMM R1, 10
        SUB R2, R0, R1
        SYSCALL 4
    """)
    assert vm.regs[2] == (-7) & 0xFFFF
    assert vm.flags & FLAG_CARRY
    assert vm.flags & FLAG_SIGN


def test_mul():
    vm = run_asm("""
        LOAD_IMM R0, 6
        LOAD_IMM R1, 7
        MUL R2, R0, R1
        SYSCALL 4
    """)
    assert vm.regs[2] == 42


def test_mul_carry_on_overflow():
    vm = run_asm("""
        LOAD_IMM R0, 0x0100
        LOAD_IMM R1, 0x0100
        MUL R2, R0, R1
        SYSCALL 4
    """)
    assert vm.regs[2] == 0
    assert vm.flags & FLAG_CARRY


def test_div():
    vm = run_asm("""
        LOAD_IMM R0, 42
        LOAD_IMM R1, 6
        DIV R2, R0, R1
        SYSCALL 4
    """)
    assert vm.regs[2] == 7


def test_div_signed_truncates_toward_zero():
    vm = run_asm("""
        LOAD_IMM R0, -7
        LOAD_IMM R1, 2
        DIV R2, R0, R1
        SYSCALL 4
    """)
    assert vm.regs[2] == (-3) & 0xFFFF


def test_mod():
    vm = run_asm("""
        LOAD_IMM R0, 43
        LOAD_IMM R1, 6
        MOD R2, R0, R1
        SYSCALL 4
    """)
    assert vm.regs[2] == 1


def test_mod_signed():
    vm = run_asm("""
        LOAD_IMM R0, -7
        LOAD_IMM R1, 2
        MOD R2, R0, R1
        SYSCALL 4
    """)
    assert vm.regs[2] == (-1) & 0xFFFF


def test_and_or_xor():
    vm = run_asm("""
        LOAD_IMM R0, 0xF0F0
        LOAD_IMM R1, 0x0FF0
        AND R2, R0, R1
        OR  R3, R0, R1
        XOR R4, R0, R1
        SYSCALL 4
    """)
    assert vm.regs[2] == 0x00F0
    assert vm.regs[3] == 0xFFF0
    assert vm.regs[4] == 0xFF00


def test_logic_clears_carry():
    vm = run_asm("""
        LOAD_IMM R0, 0xFFFF
        LOAD_IMM R1, 1
        ADD R2, R0, R1
        AND R3, R0, R1
        SYSCALL 4
    """)
    assert not vm.flags & FLAG_CARRY
    assert not vm.flags & FLAG_OVERFLOW


def test_shl():
    vm = run_asm("""
        LOAD_IMM R0, 1
        LOAD_IMM R1, 4
        SHL R2, R0, R1
        SYSCALL 4
    """)
    assert vm.regs[2] == 16


def test_shl_carry():
    vm = run_asm("""
        LOAD_IMM R0, 0x8000
        LOAD_IMM R1, 1
        SHL R2, R0, R1
        SYSCALL 4
    """)
    assert vm.regs[2] == 0
    assert vm.flags & FLAG_CARRY
    assert vm.flags & FLAG_ZERO


def test_shr():
    vm = run_asm("""
        LOAD_IMM R0, 0xFF00
        LOAD_IMM R1, 4
        SHR R2, R0, R1
        SYSCALL 4
    """)
    assert vm.regs[2] == 0x0FF0


def test_shr_carry():
    vm = run_asm("""
        LOAD_IMM R0, 0xFFFF
        LOAD_IMM R1, 1
        SHR R2, R0, R1
        SYSCALL 4
    """)
    assert vm.regs[2] == 0x7FFF
    assert vm.flags & FLAG_CARRY


def test_neg():
    vm = run_asm("""
        LOAD_IMM R0, 5
        NEG R1, R0
        SYSCALL 4
    """)
    assert vm.regs[1] == (-5) & 0xFFFF


def test_not():
    vm = run_asm("""
        LOAD_IMM R0, 0x00FF
        NOT R1, R0
        SYSCALL 4
    """)
    assert vm.regs[1] == 0xFF00


def test_inc_dec():
    vm = run_asm("""
        LOAD_IMM R0, 5
        INC R0
        INC R0
        DEC R0
        SYSCALL 4
    """)
    assert vm.regs[0] == 6


def test_inc_preserves_carry():
    vm = run_asm("""
        LOAD_IMM R0, 0xFFFF
        LOAD_IMM R1, 0xFFFF
        ADD R2, R0, R1
        LOAD_IMM R3, 5
        INC R3
        SYSCALL 4
    """)
    assert vm.regs[3] == 6
    assert vm.flags & FLAG_CARRY


def test_dec_wraps_to_negative():
    vm = run_asm("""
        LOAD_IMM R0, 0
        DEC R0
        SYSCALL 4
    """)
    assert vm.regs[0] == 0xFFFF
    assert vm.flags & FLAG_SIGN


def test_cmp_sets_flags_without_storing():
    vm = run_asm("""
        LOAD_IMM R0, 7
        LOAD_IMM R1, 9
        CMP R0, R1
        SYSCALL 4
    """)
    assert vm.regs[0] == 7
    assert vm.regs[1] == 9
    assert vm.flags & FLAG_SIGN
    assert not vm.flags & FLAG_ZERO


def test_cmp_equal_sets_zero():
    vm = run_asm("""
        LOAD_IMM R0, 5
        LOAD_IMM R1, 5
        CMP R0, R1
        SYSCALL 4
    """)
    assert vm.flags & FLAG_ZERO
