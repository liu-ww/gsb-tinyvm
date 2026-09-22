; Bubble sort full-chain demo for tinyvm.
;
; Sorts the signed 16-bit word array `arr` in ascending order and prints
; the result to stdout, numbers separated by spaces.
;
; Register-indirect memory access is used for the swap:
;   LOAD_REG  rd, Rptr     ; rd = word at address in Rptr
;   STORE_MEM Rptr, rs     ; store rs into the address held in Rptr
;
; Register usage:
;   R6  = array base pointer
;   R7  = element count
;   R8  = outer-pass index i
;   R9  = inner index j (1-based)
;   R10 = &arr[j-1],  R11 = &arr[j]
;   R12 = arr[j-1],    R13 = arr[j]
;   R14 = swapped flag, R15 = print index

.code
.entry _start
_start:
  LEA R6, arr
  LOAD_IMM R7, 8, 0

outer:
  LOAD_IMM R14, 0, 0          ; swapped = false
  LOAD_IMM R9, 1, 0           ; j = 1

inner:
  CMP R9, R7
  JGE inner_done              ; while j < count
  ; R10 = base + (j-1)*2
  ADD R10, R9, #0
  DEC R10
  ADD R10, R10, R10
  ADD R10, R10, R6
  ADD R11, R10, #2            ; R11 = &arr[j]
  LOAD_REG R12, R10           ; a = arr[j-1]
  LOAD_REG R13, R11           ; b = arr[j]
  CMP R12, R13
  JLE no_swap                 ; if a <= b, in order
  STORE_MEM R10, R13          ; arr[j-1] = b
  STORE_MEM R11, R12          ; arr[j] = a
  LOAD_IMM R14, 1, 0          ; swapped = true
no_swap:
  INC R9
  JMP inner

inner_done:
  CMP R14, #0
  JEQ sorted                  ; no swaps this pass -> done
  INC R8
  JMP outer

; print every element: signed integer, then a space
sorted:
  LOAD_IMM R15, 0, 0
print_loop:
  CMP R15, R7
  JGE finish
  ADD R10, R15, R15
  ADD R10, R10, R6
  LOAD_REG R2, R10
  LOAD_IMM R0, 0, 0           ; syscall print
  LOAD_IMM R1, 0, 0           ; format = signed int
  SYSCALL 0
  LOAD_IMM R0, 3, 0           ; syscall write space
  LEA R1, space
  LOAD_IMM R2, 1, 0
  SYSCALL 3
  INC R15
  JMP print_loop

finish:
  LOAD_IMM R0, 3, 0           ; trailing newline
  LEA R1, nl
  LOAD_IMM R2, 1, 0
  SYSCALL 3
  SYSCALL 4

.data
arr:   .word 42, -7, 1200, 0, -333, 99, 256, -1
space: .byte 32
nl:    .byte 10
