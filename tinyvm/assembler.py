"""Two-pass assembler for the tinyvm ISA.

Grammar (one statement per physical line)::

    [label [:]]   [mnemonic | directive operands]   [; comment]

Labels, mnemonics and register names are case-insensitive.  Immediates for
``ri`` slots (ADD family, CMP, PUSH) are written with a ``#`` prefix and
range from -64..63; bigger constants use ``LOAD_IMM rd, imm16``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .coding import encode_instruction
from .errors import AssemblerError
from .isabits import (
    CODE_END,
    CODE_START,
    DATA_END,
    DATA_START,
    DEFAULT_ENTRY,
    ENTRY_LABEL,
    OPCODES,
    OP_STORE_MEM,
    SIGNATURES,
)
from .program import Program

SECTION_CODE = "code"
SECTION_DATA = "data"

__all__ = [
    "assemble",
    "Assembler",
    "Tokenizer",
    "Token",
    "TOKEN_LABEL",
    "TOKEN_OP",
    "TOKEN_OPERAND",
    "TOKEN_DIRECTIVE",
    "AssemblerError",
    "SECTION_CODE",
    "SECTION_DATA",
]

_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class _Line:
    number: int
    text: str
    label: Optional[str] = None
    label_column: int = 0
    op: Optional[str] = None
    op_column: int = 0
    operands: List[str] = field(default_factory=list)
    operand_columns: List[int] = field(default_factory=list)


@dataclass
class _InstructionRecord:
    line: _Line
    opcode: int
    address: int


@dataclass
class _DataRecord:
    line: _Line
    address: int
    kind: str  # 'bytes' / 'space'
    content: bytes = b""
    size: int = 0


def assemble(source: str) -> Program:
    """Assemble source text into a serializable :class:`Program`."""
    lines = _tokenize(source)
    state = _AssemblerState(lines)
    state.pass_one()
    return state.pass_two()


class _AssemblerState:
    def __init__(self, lines: List[_Line]) -> None:
        self.lines = lines
        self.labels: Dict[str, int] = {}
        self.entry: Optional[int] = None
        self.entry_expr: Optional[str] = None
        self.entry_line: Optional[_Line] = None
        self.vectors: Dict[int, object] = {}
        self.code_base = CODE_START
        self.data_base = DATA_START
        self.code_end = self.code_base
        self.data_end = self.data_base
        self.instructions: List[_InstructionRecord] = []
        self.data_records: List[_DataRecord] = []

    # ------------------------------------------------------------------
    # pass 1: build symbol table + layout
    # ------------------------------------------------------------------
    def pass_one(self) -> None:
        section = SECTION_CODE
        code_pc = self.code_base
        data_pc = self.data_base

        for line in self.lines:
            if line.label is not None:
                name = line.label.upper()
                address = code_pc if section == SECTION_CODE else data_pc
                if name in self.labels:
                    raise AssemblerError(
                        f"duplicate label '{line.label}'", line.number,
                        line.label_column,
                    )
                self.labels[name] = address

            if line.op is None:
                continue
            op = line.op.upper()

            if op == ".CODE":
                section = SECTION_CODE
                continue
            if op == ".DATA":
                section = SECTION_DATA
                continue
            if op == ".ORG":
                target = self._absolute(line, self._single_operand(line))
                if CODE_START <= target < CODE_END:
                    section = SECTION_CODE
                    code_pc = target
                elif DATA_START <= target < DATA_END:
                    section = SECTION_DATA
                    data_pc = target
                else:
                    raise AssemblerError(
                        ".org address outside code/data segments",
                        line.number, line.operand_columns[0],
                    )
                continue
            if op == ".ENTRY":
                self.entry_expr = self._single_operand(line)
                self.entry_line = line
                continue
            if op == ".VECTOR":
                vector = self._int_operand(line, 0, 0, 255)
                handler_col = line.operand_columns[1]
                self.vectors[vector] = (line.operands[1], line, handler_col)
                continue

            if section == SECTION_CODE:
                if op not in OPCODES:
                    raise AssemblerError(
                        f"unknown mnemonic '{line.op}'", line.number, line.op_column
                    )
                self._check_operand_count(line)
                self.instructions.append(
                    _InstructionRecord(line, OPCODES[op], code_pc)
                )
                code_pc += 4
                if code_pc > CODE_END:
                    raise AssemblerError(
                        "code segment overflow (past 0x3FFF)", line.number, 1
                    )
            else:
                record, data_pc = self._layout_data(line, data_pc)
                self.data_records.append(record)
                if data_pc >= DATA_END:
                    raise AssemblerError(
                        "data segment overflow (past 0x7FFF)", line.number, 1
                    )

        self.code_end = code_pc
        self.data_end = data_pc
        if self.entry_expr is not None:
            self.entry = self._absolute(self.entry_line, self.entry_expr)
        if self.entry is None:
            if ENTRY_LABEL.upper() in self.labels:
                self.entry = self.labels[ENTRY_LABEL.upper()]
            elif self.instructions:
                self.entry = self.instructions[0].address
            else:
                self.entry = self.code_base

    def _layout_data(self, line: _Line, pc: int) -> Tuple[_DataRecord, int]:
        op = line.op.upper()  # type: ignore[union-attr]
        if op == ".STRING":
            if not line.operands:
                raise AssemblerError(".string needs a literal", line.number, 1)
            raw = b""
            for token in line.operands:
                raw += self._parse_string(line, token) + b"\x00"
            return _DataRecord(line, pc, "bytes", raw), pc + len(raw)
        if op == ".BYTE":
            if not line.operands:
                raise AssemblerError(".byte needs at least one value", line.number, 1)
            raw = bytes(self._int_operand(line, i, 0, 255)
                        for i in range(len(line.operands)))
            return _DataRecord(line, pc, "bytes", raw), pc + len(raw)
        if op == ".WORD":
            if not line.operands:
                raise AssemblerError(".word needs at least one value", line.number, 1)
            raw = b""
            for i, token in enumerate(line.operands):
                value = self._word_operand(line, i, token)
                raw += bytes([value & 0xFF, (value >> 8) & 0xFF])
            return _DataRecord(line, pc, "bytes", raw), pc + len(raw)
        if op == ".SPACE":
            size = self._int_operand(line, 0, 0, DATA_END - DATA_START)
            return _DataRecord(line, pc, "space", b"", size), pc + size
        raise AssemblerError(
            f"unknown directive '{line.op}' in .data section",
            line.number, line.op_column,
        )

    def _word_operand(self, line: "_Line", index: int, token: str) -> int:
        """Resolve a ``.word`` value: signed/unsigned int or a label."""
        text = token.strip()
        try:
            value = _parse_number(text)
        except ValueError:
            try:
                return self._absolute(
                    line, token, line.operand_columns[index]
                )
            except AssemblerError as exc:
                if "undefined label" in str(exc) or "out of 16-bit" in str(exc):
                    raise
                raise AssemblerError(
                    f".word expects a number or label, got '{token}'",
                    line.number, line.operand_columns[index],
                )
        if not (-0x8000 <= value <= 0xFFFF):
            raise AssemblerError(
                f".word value {value} out of range (-32768..65535)",
                line.number, line.operand_columns[index],
            )
        return value & 0xFFFF

    # ------------------------------------------------------------------
    # pass 2: emit bytes
    # ------------------------------------------------------------------
    def pass_two(self) -> Program:
        code = bytearray(self.code_end - self.code_base)

        for record in self.instructions:
            operands = self._encode_operands(record)
            encoded = encode_instruction(record.opcode, operands)
            offset = record.address - self.code_base
            code[offset:offset + 4] = encoded

        data = bytearray(self.data_end - self.data_base)
        for record in self.data_records:
            offset = record.address - self.data_base
            if record.kind == "space":
                data[offset:offset + record.size] = b"\x00" * record.size
            else:
                data[offset:offset + len(record.content)] = record.content

        # Resolve (possibly forward) vector labels and validate handlers.
        resolved_vectors: Dict[int, int] = {}
        for vector, value in self.vectors.items():
            expr, line, handler_col = value  # type: ignore[misc]
            handler = self._absolute(line, expr, handler_col)
            if not (CODE_START <= handler < CODE_END):
                raise AssemblerError(
                    f"vector {vector} handler 0x{handler:04x} outside code segment"
                )
            resolved_vectors[vector] = handler
        self.vectors = resolved_vectors

        return Program(
            entry=self.entry if self.entry is not None else DEFAULT_ENTRY,
            code_base=self.code_base,
            code=bytes(code),
            data_base=self.data_base,
            data=bytes(data),
            vectors=dict(resolved_vectors),
        )

    def _encode_operands(self, record: _InstructionRecord) -> list:
        line = record.line
        # STORE_MEM: "STORE_MEM Raddr, Rsrc" (register-indirect) vs
        # "STORE_MEM abs_addr, Rsrc" (absolute).
        if record.opcode == OP_STORE_MEM and line.operands[0].upper().startswith("R"):
            return [
                ("indirect",
                 self._parse_register(line, 0, line.operands[0]),
                 self._parse_register(line, 1, line.operands[1])),
            ]
        return [
            self._encode_operand(line, record.opcode, i, token)
            for i, token in enumerate(line.operands)
        ]

    def _encode_operand(self, line: _Line, opcode: int, index: int,
                        token: str):
        kinds = [k for k in SIGNATURES[opcode] if k]
        kind = kinds[index]
        if kind == "r":
            return self._parse_register(line, index, token)
        if kind == "u8":
            return self._int_operand(line, index, 0, 255)
        if kind == "ri":
            if token.startswith("#"):
                return ("imm", self._int_token(line, index, token[1:], -64, 63))
            return ("reg", self._parse_register(line, index, token))
        if kind == "addr16":
            return self._absolute(
                line, token, line.operand_columns[index]
            )
        raise AssemblerError(
            f"internal error: cannot encode {kind}", line.number,
            line.operand_columns[index],
        )  # pragma: no cover

    def _check_operand_count(self, line: _Line) -> None:
        expected = sum(1 for kind in SIGNATURES[OPCODES[line.op.upper()]] if kind)
        if len(line.operands) != expected:
            raise AssemblerError(
                f"{line.op} expects {expected} operand(s), got {len(line.operands)}",
                line.number, line.op_column,
            )

    # ------------------------------------------------------------------
    # operand parsing helpers
    # ------------------------------------------------------------------
    def _parse_register(self, line: _Line, index: int, token: str) -> int:
        if token.startswith("#"):
            raise AssemblerError(
                f"expected register, got immediate '{token}'",
                line.number, line.operand_columns[index],
            )
        name = token.upper()
        match = re.fullmatch(r"R(\d{1,2})", name)
        if match is None or not (0 <= int(match.group(1)) <= 15):
            raise AssemblerError(
                f"expected register R0..R15, got '{token}'",
                line.number, line.operand_columns[index],
            )
        return int(match.group(1))

    def _single_operand(self, line: _Line) -> str:
        if len(line.operands) != 1:
            raise AssemblerError(
                f"{line.op} expects exactly one operand", line.number, line.op_column
            )
        return line.operands[0]

    def _int_operand(self, line: _Line, index: int,
                     low: int, high: int) -> int:
        return self._int_token(
            line, index, line.operands[index], low, high
        )

    def _int_token(self, line: _Line, index: int, token: str,
                   low: int, high: int) -> int:
        token = token.strip()
        try:
            value = _parse_number(token)
        except ValueError:
            raise AssemblerError(
                f"invalid integer '{token}'",
                line.number, line.operand_columns[index],
            )
        if not (low <= value <= high):
            raise AssemblerError(
                f"operand {value} out of range ({low}..{high})",
                line.number, line.operand_columns[index],
            )
        return value

    def _absolute(self, line: _Line, token: str,
                  column: Optional[int] = None) -> int:
        """Resolve a label, number or ``label + const`` to a 16-bit address."""
        expr = token.replace(" ", "")
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)([+-]\d+)?", expr)
        if column is None:
            column = line.operand_columns[0] if line.operand_columns else 1
        col = column
        if match:
            name, offset_text = match.group(1), match.group(2)
            offset = int(offset_text) if offset_text else 0
            key = name.upper()
            if key not in self.labels:
                raise AssemblerError(f"undefined label '{name}'", line.number, col)
            value = self.labels[key] + offset
        else:
            try:
                value = _parse_number(expr)
            except ValueError:
                raise AssemblerError(
                    f"undefined label or invalid expression '{token}'",
                    line.number, col,
                )
        if not (0 <= value <= 0xFFFF):
            raise AssemblerError(
                f"address 0x{value:x} out of 16-bit range", line.number, col
            )
        return value

    def _parse_string(self, line: _Line, token: str) -> bytes:
        col = 1
        if line.operands:
            col = line.operand_columns[line.operands.index(token)]
        if len(token) < 2 or token[0] not in "\"'" or token[-1] != token[0]:
            raise AssemblerError(f"invalid string literal '{token}'", line.number, col)
        body = token[1:-1]
        try:
            return _unescape_string(body)
        except ValueError as exc:
            raise AssemblerError(str(exc), line.number, col)


def _parse_number(token: str) -> int:
    token = token.strip()
    sign = 1
    if token[:1] in "+-":
        if token[0] == "-":
            sign = -1
        token = token[1:]
    if not token:
        raise ValueError(token)
    if token.lower().startswith("0x"):
        return sign * int(token, 16)
    if token.lower().startswith("0b"):
        return sign * int(token, 2)
    return sign * int(token, 10)


_ESCAPES = {"n": b"\n", "t": b"\t", "r": b"\r", "0": b"\x00",
            "\\": b"\\", "'": b"'", '"': b'"'}


def _unescape_string(body: str) -> bytes:
    out = bytearray()
    i = 0
    while i < len(body):
        char = body[i]
        if char != "\\":
            out.extend(char.encode("latin-1"))
            i += 1
            continue
        i += 1
        if i >= len(body):
            raise ValueError("dangling backslash escape in string")
        esc = body[i]
        if esc in _ESCAPES:
            out.extend(_ESCAPES[esc])
            i += 1
        elif esc == "x" and i + 2 < len(body):
            hexd = body[i + 1:i + 3]
            try:
                out.append(int(hexd, 16))
            except ValueError:
                raise ValueError(f"invalid \\x escape '{hexd}'")
            i += 3
        else:
            raise ValueError(f"invalid escape '\\{esc}'")
    return bytes(out)

    # ------------------------------------------------------------------
    # tokenizer
    # ------------------------------------------------------------------
def _tokenize(source: str) -> List[_Line]:
    lines: List[_Line] = []
    token_re = re.compile(r"[A-Za-z_.][A-Za-z0-9_]*")
    for lineno, raw_line in enumerate(source.splitlines(), start=1):
        line = raw_line.split(";", 1)[0]
        if not line.strip():
            continue
        parsed = _Line(number=lineno, text=raw_line.rstrip("\n"))
        cursor = 0

        def skip_windows() -> None:
            nonlocal cursor
            while cursor < len(line) and line[cursor].isspace():
                cursor += 1

        skip_windows()
        token_match = token_re.match(line, cursor)
        if token_match:
            name = token_match.group(0)
            end = token_match.end()
            if not name.startswith(".") and line[end:end + 1] == ":":
                parsed.label = name
                parsed.label_column = cursor + 1
                cursor = end + 1
                skip_windows()
                token_match = token_re.match(line, cursor)
        if token_match is not None:
            parsed.op = token_match.group(0)
            parsed.op_column = cursor + 1
            cursor = token_match.end()
        elif cursor < len(line):
            raise AssemblerError(
                f"unexpected character '{line[cursor]}'", lineno, cursor + 1
            )
        tokens, columns = _split_operands(line, cursor, lineno)
        parsed.operands = tokens
        parsed.operand_columns = columns
        lines.append(parsed)
    return lines


def _split_operands(line: str, start: int, lineno: int) -> Tuple[List[str], List[int]]:
    tokens: List[str] = []
    columns: List[int] = []
    cursor = start
    length = len(line)
    while True:
        while cursor < length and (line[cursor].isspace() or line[cursor] == ","):
            cursor += 1
        if cursor >= length:
            break
        token_start = cursor
        if line[cursor] in "\"'":
            quote = line[cursor]
            cursor += 1
            while cursor < length and line[cursor] != quote:
                if line[cursor] == "\\":
                    cursor += 1
                cursor += 1
            if cursor >= length:
                raise AssemblerError(
                    "unterminated string literal", lineno, token_start + 1
                )
            cursor += 1
        else:
            while cursor < length and not line[cursor].isspace() \
                    and line[cursor] != ",":
                cursor += 1
        token = line[token_start:cursor].strip()
        if token:
            tokens.append(token)
            columns.append(token_start + 1)
    return tokens, columns


# ======================================================================
# Public tokenizer / assembler facade
#
# The implementation above uses the internal ``_Line`` representation.
# The classes below are the stable, importable public surface:
#   * ``Token``       - one lexical token with 1-based source position
#   * ``Tokenizer``   - line/comment aware lexer producing ``Token`` lists
#   * ``Assembler``   - stateful two-pass driver
#   * ``assemble()``  - one-shot convenience wrapper (already defined above)
# ======================================================================

TOKEN_LABEL = "label"
TOKEN_OP = "op"
TOKEN_OPERAND = "operand"
TOKEN_DIRECTIVE = "directive"


@dataclass
class Token:
    """A single lexical token.

    Attributes:
        kind: one of :data:`TOKEN_LABEL`, :data:`TOKEN_OP`,
            :data:`TOKEN_DIRECTIVE` or :data:`TOKEN_OPERAND`.
        value: the verbatim token text (without a trailing ``:`` for labels).
        line: 1-based source line number.
        column: 1-based source column number.
    """

    kind: str
    value: str
    line: int = 1
    column: int = 1

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Token({self.kind!r}, {self.value!r}, {self.line}:{self.column})"


class Tokenizer:
    """Locate-accurate tokenizer for assembly source.

    Comments start with ``;`` and run to end of line.  A line has the
    form::

        [label:] [mnemonic|.directive] [operand, ...]

    Both ``tokenize`` (flat token list) and ``tokenize_lines`` (one list
    per physical line) are provided; all positions are 1-based.
    """

    _IDENT = re.compile(r"[A-Za-z_.][A-Za-z0-9_]*")

    def __init__(self, source: str) -> None:
        self.source = source

    def tokenize(self) -> List[Token]:
        """Return every token in source order."""
        tokens: List[Token] = []
        for line_tokens in self.tokenize_lines():
            tokens.extend(line_tokens)
        return tokens

    def tokenize_lines(self) -> List[List[Token]]:
        """Return tokens grouped by physical source line."""
        result: List[List[Token]] = []
        for lineno, raw in enumerate(self.source.splitlines(), start=1):
            stripped = raw.split(";", 1)[0]
            if not stripped.strip():
                continue
            result.append(self._tokenize_one_line(stripped, lineno))
        return result

    # ------------------------------------------------------------------
    def _tokenize_one_line(self, line: str, lineno: int) -> List[Token]:
        tokens: List[Token] = []
        cursor = self._skip_space(line, 0)

        # Optional leading label: identifier immediately followed by ':'.
        match = self._IDENT.match(line, cursor)
        if match and not match.group(0).startswith("."):
            end = match.end()
            if line[end:end + 1] == ":":
                tokens.append(Token(TOKEN_LABEL, match.group(0),
                                    lineno, cursor + 1))
                cursor = self._skip_space(line, end + 1)
                match = self._IDENT.match(line, cursor)

        # Mnemonic or directive.
        if match is not None:
            name = match.group(0)
            kind = TOKEN_DIRECTIVE if name.startswith(".") else TOKEN_OP
            tokens.append(Token(kind, name, lineno, cursor + 1))
            cursor = match.end()
        elif cursor < len(line):
            raise AssemblerError(
                f"unexpected character '{line[cursor]}'", lineno, cursor + 1
            )

        # Comma/whitespace separated operands, strings allowed.
        for value, column in self._split_operands(line, cursor, lineno):
            tokens.append(Token(TOKEN_OPERAND, value, lineno, column))
        return tokens

    @staticmethod
    def _skip_space(line: str, cursor: int) -> int:
        while cursor < len(line) and line[cursor].isspace():
            cursor += 1
        return cursor

    @staticmethod
    def _split_operands(line: str, start: int,
                        lineno: int) -> List[Tuple[str, int]]:
        out: List[Tuple[str, int]] = []
        cursor = start
        length = len(line)
        while True:
            while cursor < length and (line[cursor].isspace()
                                       or line[cursor] == ","):
                cursor += 1
            if cursor >= length:
                break
            begin = cursor
            if line[cursor] in "\"'":
                quote = line[cursor]
                cursor += 1
                while cursor < length and line[cursor] != quote:
                    if line[cursor] == "\\":
                        cursor += 1
                    cursor += 1
                if cursor >= length:
                    raise AssemblerError(
                        "unterminated string literal", lineno, begin + 1
                    )
                cursor += 1
            else:
                while cursor < length and not line[cursor].isspace() \
                        and line[cursor] != ",":
                    cursor += 1
            value = line[begin:cursor].strip()
            if value:
                out.append((value, begin + 1))
        return out


class Assembler:
    """Stateful two-pass assembler facade.

    Typical use::

        asm = Assembler()
        program = asm.assemble(source)
        blob = program.serialize()

    After :meth:`assemble` (or after :meth:`pass_one`) the attributes
    ``labels``, ``entry``, ``code_size`` and ``data_size`` are populated,
    which is convenient for tooling and tests.
    """

    def __init__(self) -> None:
        self.labels: Dict[str, int] = {}
        self.entry: int = DEFAULT_ENTRY
        self.code_size: int = 0
        self.data_size: int = 0
        self.program: Optional[Program] = None
        self._state: Optional[_AssemblerState] = None

    # ------------------------------------------------------------------
    def tokenize(self, source: str) -> List[Token]:
        """Lex ``source`` into a flat list of :class:`Token`."""
        return Tokenizer(source).tokenize()

    def pass_one(self, source: str) -> Dict[str, int]:
        """Run the first pass only; return the resolved symbol table."""
        self._state = _AssemblerState(_tokenize(source))
        self._state.pass_one()
        self.labels = dict(self._state.labels)
        self.entry = self._state.entry if self._state.entry is not None \
            else DEFAULT_ENTRY
        self.code_size = self._state.code_end - self._state.code_base
        self.data_size = self._state.data_end - self._state.data_base
        return dict(self.labels)

    def pass_two(self) -> Program:
        """Emit machine code; :meth:`pass_one` must have been called."""
        if self._state is None:
            raise RuntimeError("pass_one must run before pass_two")
        program = self._state.pass_two()
        self.program = program
        self.entry = program.entry
        return program

    def assemble(self, source: str) -> Program:
        """Run both passes and return the resulting :class:`Program`."""
        self.pass_one(source)
        return self.pass_two()
