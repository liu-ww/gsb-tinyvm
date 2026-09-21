"""Two-pass assembler for tinyvm.

Public API:
    assemble(source: str) -> Program

Pipeline (implemented in a later pass, see method docstrings):
    source text
      -> Tokenizer.tokenize() : list[Token]
      -> Assembler._parse()   : list[_RawLine]
      -> pass 1 _collect_labels(): label table, IVT entries, sizes
      -> pass 2 _emit()       : code/data bytes with label fixups

This module currently contains the skeleton: token/line data structures,
the encoding tables, and the Assembler state. Parsing and the two passes
are NotImplementedError placeholders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from .errors import AssemblerError
from .isa import (
    CODE_BASE,
    DATA_BASE,
    INSTRUCTIONS,
    IVT_MAX_ENTRIES,
    U16_MAX,
    OperandForm,
)
from .program import Program

__all__ = ["Token", "Tokenizer", "Assembler", "assemble"]


class TokenType(Enum):
    """Kinds of lexical tokens produced by the tokenizer."""

    MNEMONIC = auto()
    DIRECTIVE = auto()
    REGISTER = auto()
    NUMBER = auto()
    NAME = auto()
    STRING = auto()
    COMMA = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    EOL = auto()
    EOF = auto()


@dataclass
class Token:
    """One lexical token with its 1-based source position."""

    type: TokenType
    value: object
    line: int
    col: int

    def __repr__(self) -> str:
        return f"Token({self.type.name}, {self.value!r}, line={self.line})"


class Tokenizer:
    """Hand-written lexical analyzer for tinyvm assembly source.

    Planned lexical rules (implemented in a later pass):
      - comments start with ';' or '#' and run to end of line;
      - labels are identifiers immediately followed by ':'; the ':' is
        consumed and the token is emitted as NAME;
      - numbers accept decimal and 0x-prefixed hex, with an optional sign;
      - strings are double quoted and support n t r 0 quote backslash
        escape sequences;
      - one logical EOL token is emitted per physical source line so that
        downstream line numbers stay aligned with the source.
    """

    def __init__(self, source: str) -> None:
        self.source = source
        self.tokens: list[Token] = []

    def tokenize(self) -> list[Token]:
        """Scan the full source and return the token list ending in EOF."""
        raise NotImplementedError("tokenizer pass not implemented yet")

    def _read_number(self, pos: int, line: int, col: int) -> tuple[Token, int]:
        """Read a signed decimal or 0x-hex integer literal."""
        raise NotImplementedError

    def _read_name(self, pos: int, line: int, col: int) -> tuple[Token, int]:
        """Read an identifier / register / mnemonic / directive name."""
        raise NotImplementedError

    def _read_string(self, pos: int, line: int, col: int) -> tuple[Token, int]:
        """Read a double-quoted string, decoding standard escapes."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Parsed-line data structures
# ---------------------------------------------------------------------------


class Segment(Enum):
    """Which section a parsed line belongs to."""

    CODE = auto()
    DATA = auto()


@dataclass
class _Operand:
    """One decoded instruction operand (in source order).

    kind:
      'reg'   -> value is register number 0-15
      'imm'   -> value is an immediate int from a numeric literal
      'label' -> value is a label name, resolved in pass 2
      'mem'   -> value is a register number, for [Rn] indirect operands
    """

    kind: str
    value: object
    col: int


@dataclass
class _RawLine:
    """One logical source line after parsing, before encoding."""

    line: int
    label: str | None = None
    label_col: int = 0
    mnemonic: str | None = None
    operands: list[_Operand] = field(default_factory=list)
    directive: str | None = None
    args: list[Token] = field(default_factory=list)


# Operand forms carrying a 16-bit value resolved from labels or numbers.
ADDRESS_FORMS = frozenset({OperandForm.A16, OperandForm.RA16, OperandForm.RI16})

# Expected operand count for each operand form (brackets only affect syntax).
OPERAND_COUNT: dict[OperandForm, int] = {
    OperandForm.NONE: 0,
    OperandForm.R: 1,
    OperandForm.RR: 2,
    OperandForm.RRR: 3,
    OperandForm.N8: 1,
    OperandForm.A16: 1,
    OperandForm.RA16: 2,
    OperandForm.RI16: 2,
    OperandForm.R_IND: 2,
    OperandForm.IND_R: 2,
}


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------


class Assembler:
    """Two-pass tinyvm assembler.

    State populated across the pipeline:
      raw_lines : parsed logical lines (parser output)
      labels    : label name -> absolute address (pass 1)
      ivt       : interrupt number -> handler address (from .ivt)
      code_buf  : bytearray of the code section at CODE_BASE
      data_buf  : bytearray of the data section at DATA_BASE
    """

    def __init__(self, source: str) -> None:
        self.source = source
        self.raw_lines: list[_RawLine] = []
        self.labels: dict[str, int] = {}
        self.ivt: dict[int, int] = {}
        self.code_buf = bytearray()
        self.data_buf = bytearray()

    # -- public driver -----------------------------------------------------

    def assemble(self) -> Program:
        """Run parse + pass 1 + pass 2 and return a Program."""
        raise NotImplementedError("assembler passes not implemented yet")

    # -- parsing -----------------------------------------------------------

    def _parse(self, tokens: list[Token]) -> list[_RawLine]:
        """Group tokens into logical lines, decoding operands."""
        raise NotImplementedError

    def _parse_operands(self, line: _RawLine, tokens: list[Token]) -> None:
        """Parse the operand list of one instruction into line.operands."""
        raise NotImplementedError

    # -- pass 1: symbol table ---------------------------------------------

    def _collect_labels(self) -> None:
        """Walk raw lines assigning addresses and recording labels/IVT."""
        raise NotImplementedError

    def _data_directive_size(self, line: _RawLine) -> int:
        """Return the number of data bytes a directive emits."""
        raise NotImplementedError

    # -- pass 2: codegen ---------------------------------------------------

    def _emit(self) -> None:
        """Encode instructions/directives, resolving label references."""
        raise NotImplementedError

    def _emit_instruction(self, line: _RawLine) -> None:
        """Encode one code-segment instruction into code_buf."""
        raise NotImplementedError

    def _emit_data(self, line: _RawLine) -> None:
        """Encode one data directive into data_buf."""
        raise NotImplementedError

    # -- helpers -----------------------------------------------------------

    def _resolve(self, name: str, line: int, col: int) -> int:
        """Look up a label, raising AssemblerError if undefined."""
        raise NotImplementedError

    def _error(self, message: str, line: int, col: int = 1) -> None:
        """Raise an AssemblerError at the given source position."""
        raise AssemblerError(message, line, col)

    def _build_program(self) -> Program:
        """Assemble the final Program container from the emitted buffers."""
        return Program(
            code=bytes(self.code_buf),
            data=bytes(self.data_buf),
            entry=CODE_BASE,
            ivt=dict(self.ivt),
        )


def assemble(source: str) -> Program:
    """Assemble tinyvm assembly source text into a Program."""
    return Assembler(source).assemble()
