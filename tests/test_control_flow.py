"""Control flow tests: jumps, conditional branches, CALL/RET nesting."""

from tinyvm.isa import STACK_TOP

from conftest import run_asm


def test_jmp_skips_instruction():
    vm = run_asm("""
        LOAD_IMM R0, 1
        JMP skip
        LOAD_IMM R0, 2
    skip:
        SYSCALL 4
    """)
    assert vm.regs[0] == 1


def test_jeq_taken():
    vm = run_asm("""
        LOAD_IMM R0, 5
        LOAD_IMM R1, 5
        CMP R0, R1
        JEQ eq
        LOAD_IMM R2, 0
        JMP end
    eq:
        LOAD_IMM R2, 1
    end:
        SYSCALL 4
    """)
    assert vm.regs[2] == 1


def test_jeq_not_taken():
    vm = run_asm("""
        LOAD_IMM R0, 5
        LOAD_IMM R1, 6
        CMP R0, R1
        JEQ eq
        LOAD_IMM R2, 0
        JMP end
    eq:
        LOAD_IMM R2, 1
    end:
        SYSCALL 4
    """)
    assert vm.regs[2] == 0


def test_jne_taken():
    vm = run_asm("""
        LOAD_IMM R0, 5
        LOAD_IMM R1, 6
        CMP R0, R1
        JNE ne
        LOAD_IMM R2, 0
        JMP end
    ne:
        LOAD_IMM R2, 1
    end:
        SYSCALL 4
    """)
    assert vm.regs[2] == 1


def test_jlt_signed():
    vm = run_asm("""
        LOAD_IMM R0, -1
        LOAD_IMM R1, 1
        CMP R0, R1
        JLT less
        LOAD_IMM R2, 0
        JMP end
    less:
        LOAD_IMM R2, 1
    end:
        SYSCALL 4
    """)
    assert vm.regs[2] == 1


def test_jgt_signed():
    vm = run_asm("""
        LOAD_IMM R0, 5
        LOAD_IMM R1, 3
        CMP R0, R1
        JGT greater
        LOAD_IMM R2, 0
        JMP end
    greater:
        LOAD_IMM R2, 1
    end:
        SYSCALL 4
    """)
    assert vm.regs[2] == 1


def test_jle_equal():
    vm = run_asm("""
        LOAD_IMM R0, 3
        LOAD_IMM R1, 3
        CMP R0, R1
        JLE le
        LOAD_IMM R2, 0
        JMP end
    le:
        LOAD_IMM R2, 1
    end:
        SYSCALL 4
    """)
    assert vm.regs[2] == 1


def test_jge_not_taken_for_less():
    vm = run_asm("""
        LOAD_IMM R0, 3
        LOAD_IMM R1, 5
        CMP R0, R1
        JGE ge
        LOAD_IMM R2, 0
        JMP end
    ge:
        LOAD_IMM R2, 1
    end:
        SYSCALL 4
    """)
    assert vm.regs[2] == 0


def test_loop_sum_1_to_10():
    vm = run_asm("""
        LOAD_IMM R0, 10
        LOAD_IMM R1, 0
        LOAD_IMM R2, 0
    loop:
        CMP R0, R2
        JEQ done
        ADD R1, R1, R0
        DEC R0
        JMP loop
    done:
        SYSCALL 4
    """)
    assert vm.regs[1] == 55


def test_call_ret_simple():
    vm = run_asm("""
        LOAD_IMM R0, 0
        CALL f
        SYSCALL 4
    f:
        LOAD_IMM R0, 42
        RET
    """)
    assert vm.regs[0] == 42


def test_call_ret_nested_three_levels():
    vm = run_asm("""
        LOAD_IMM R0, 0
        CALL f1
        SYSCALL 4
    f1:
        INC R0
        CALL f2
        RET
    f2:
        INC R0
        CALL f3
        RET
    f3:
        INC R0
        RET
    """)
    assert vm.regs[0] == 3


def test_call_ret_restores_stack_pointer():
    vm = run_asm("""
        CALL f1
        SYSCALL 4
    f1:
        CALL f2
        RET
    f2:
        RET
    """)
    assert vm.sp == STACK_TOP


def test_recursion_factorial_5():
    # R0 = n; returns R1 = n! via recursive calls.
    vm = run_asm("""
        LOAD_IMM R0, 5
        CALL fact
        SYSCALL 4
    fact:
        LOAD_IMM R2, 1
        CMP R0, R2
        JLE base
        PUSH R0
        DEC R0
        CALL fact
        POP R3
        MUL R1, R1, R3
        RET
    base:
        LOAD_IMM R1, 1
        RET
    """)
    assert vm.regs[1] == 120
