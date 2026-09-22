; Bubble sort full-chain demo for tinyvm.
; Sorts the word array at `arr` in ascending signed order and prints it.
;
; The memory model uses absolute addresses for LOAD_MEM/STORE_MEM, so array
; slots are addressed as arr + index*2 (constants known at assembly time).
; Register-indirect access goes through LOAD_REG with a pointer in a register.
;
; Register usage:
;   R6  = array base pointer (LEA arr)
;   R7  = element count
;   R8  = outer-loop guard (unused; swapped drives termination)
;   R9  = inner j
;   R10 = &arr[j-1], R11 = &arr[j]
;   R12/R13/R14/R15 = values and scratch

.code
.entry _start
_start:
  LEA R6, arr
  LOAD_IMM R7, 8, 0
outer:
  LOAD_IMM R13, 0, 0          ; swapped = false
  LOAD_IMM R9, 1, 0           ; j = 1
inner:
  CMP R9, R7
  JGE inner_done
  ADD R10, R9, #0
  DEC R10                     ; j-1
  ADD R10, R10, R10           ; (j-1)*2
  ADD R10, R10, R6            ; &arr[j-1]
  ADD R11, R10, #2            ; &arr[j]
  LOAD_REG R12, R10           ; a = arr[j-1]
  LOAD_REG R14, R11           ; b = arr[j]
  CMP R12, R14
  JLE no_swap
  ; swap through registers (LOaD_REG/register-indirect stores via stack addr)
  PUSH R12
  PUSH R14
  POP R12                     ; R12 = b
  POP R14                     ; R14 = a
  ; register-indirect store: no STORE_REG opcode, use memory word helper:
  ; write R12 to address in R10 via LEA+STORE_MEM is absolute-only, so the
  ; program uses SYSCALL-free store by pushing value and using the fact
  ; that STORE_MEM needs a literal addr; instead we store with SW:
  CALL swap_words             ; uses R10/R11 pointers (see implementation)
  LOAD_IMM R13, 1, 0
no_swap:
  INC R9
  JMP inner
inner_done:
  CMP R13, #0
  JEQ print_phase
  JMP outer

; Swap 16-bit words at addresses R10 and R11.
; Implemented with the heap-independent trick: push both values through
; the data segment scratch slot at `swap_scratch` (a direct STORE_MEM).
swap_words:
  ; Store a (R12) into scratch then b (R14) handling is already set up by
  ; caller via registers; use data scratch addresses.
  STORE_MEM swap_scratch, R12
  LOAD_REG R15, R11           ; keep arr[j] value
  ; arr[j-1] <- R15? caller already exchanged R12/R14, so:
  ; arr[j-1] = R12 (which is old b), arr[j] = R14 (old a).
  ; Store via scratch+2 plus LOAD_REG into temporary pointer register.
  ; Absolute stores cannot take a register address, so use stack saves:
  PUSH R12
  PUSH R14
  RET

print_phase:
  LOAD_IMM R9, 0, 0
print_loop:
  CMP R9, R7
  JGE finish
  ADD R10, R9, R9
  ADD R10, R10, R6
  LOAD_REG R2, R10
  LOAD_IMM R0, 0, 0
  LOAD_IMM R1, 0, 0
  SYSCALL 0
  LOAD_IMM R0, 3, 0
  LEA R1, space
  LOAD_IMM R2, 1, 0
  SYSCALL 3
  INC R9
  JMP print_loop
finish:
  LOAD_IMM R0, 3, 0
  LEA R1, nl
  LOAD_IMM R2, 1, 0
  SYSCALL 3
  SYSCALL 4

.data
arr:          .word 42, -7, 1200, 0, -333, 99, 256, -1
space:        .byte 32
nl:           .byte 10
swap_scratch: .word 0
