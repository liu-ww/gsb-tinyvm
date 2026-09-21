; Bubble sort over an in-memory word array.
; Demonstrates LEA/LOAD_MEM/STORE_MEM/CMP/Jxx/CALL/RET and SYSCALLs.
;
; Register allocation:
;   R3 = 1 (constant)      R13 = n - 1
;   R8 = array base        R9  = n
;   R10 = i (outer)        R11 = j (inner)   R12 = n - i - 1
;   R2/R4 scratch addr math, R5=arr[j], R6=addr j+1, R7=arr[j+1]
;   R14 = print index, R15 = 2 (constant)

        LEA R8, arr
        LOAD_IMM R9, 8
        LOAD_IMM R3, 1
        LOAD_IMM R15, 2
        SUB R13, R9, R3

        LEA R0, before
        LOAD_IMM R1, 8
        SYSCALL 3
        CALL print_array

        LOAD_IMM R10, 0          ; i = 0
outer_loop:
        CMP R10, R13
        JGE sort_done            ; i >= n-1 -> finished
        SUB R12, R9, R10
        DEC R12                  ; bound = n - i - 1
        LOAD_IMM R11, 0          ; j = 0
inner_loop:
        CMP R11, R12
        JGE end_inner            ; j >= bound -> next pass
        SHL R2, R11, R3          ; 2*j (word index)
        ADD R4, R8, R2           ; &arr[j]
        LOAD_MEM R5, [R4]        ; arr[j]
        ADD R6, R4, R15          ; &arr[j+1]
        LOAD_MEM R7, [R6]        ; arr[j+1]
        CMP R5, R7
        JLE no_swap              ; already ordered -> skip
        STORE_MEM [R4], R7
        STORE_MEM [R6], R5
no_swap:
        INC R11
        JMP inner_loop
end_inner:
        INC R10
        JMP outer_loop
sort_done:
        LEA R0, after
        LOAD_IMM R1, 7
        SYSCALL 3
        CALL print_array
        SYSCALL 4

; print_array: print R9 signed numbers starting at R8, one per line.
; Preserves R8/R9/R3/R15 by using R14 as the loop index.
print_array:
        LOAD_IMM R14, 0
print_loop:
        CMP R14, R9
        JGE print_done
        SHL R2, R14, R3          ; 2 * index
        ADD R4, R8, R2
        LOAD_MEM R0, [R4]
        SYSCALL 0                ; print signed decimal + newline
        INC R14
        JMP print_loop
print_done:
        RET

.data
before: .string "before:\n"
after:  .string "after:\n"
arr:    .word 42, 7, 99, 3, 56, 18, 27, 64
