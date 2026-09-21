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

import re
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
    COLON = auto()
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
        src = self.source
        pos, line, line_start, n = 0, 1, 0, len(src)
        out: list[Token] = []
        while pos < n:
            ch = src[pos]
            col = pos - line_start + 1
            if ch == "\n":
                out.append(Token(TokenType.EOL, None, line, col))
                pos += 1
                line += 1
                line_start = pos
                continue
            if ch in " \t\r":
                pos += 1
                continue
            if ch in ";#":
                while pos < n and src[pos] != "\n":
                    pos += 1
                continue
            if ch == ",":
                out.append(Token(TokenType.COMMA, None, line, col)); pos += 1
            elif ch == "[":
                out.append(Token(TokenType.LBRACKET, None, line, col)); pos += 1
            elif ch == "]":
                out.append(Token(TokenType.RBRACKET, None, line, col)); pos += 1
            elif ch == ":":
                out.append(Token(TokenType.COLON, None, line, col)); pos += 1
            elif ch == '"':
                tok, pos = self._read_string(pos, line, col, src)
                out.append(tok)
            elif ch in "+-0123456789":
                tok, pos = self._read_number(pos, line, col, src)
                out.append(tok)
            else:
                tok, pos = self._read_name(pos, line, col, src)
                out.append(tok)
        out.append(Token(TokenType.EOL, None, line, pos - line_start + 1))
        out.append(Token(TokenType.EOF, None, line, pos - line_start + 1))
        return out

    def _read_number(
        self, pos: int, line: int, col: int, src: str
    ) -> tuple[Token, int]:
        """Read a signed decimal or 0x-hex integer literal."""
        start = pos
        if src[pos] in "+-":
            pos += 1
        m = re.match(r"0[xX][0-9A-Fa-f]+|[0-9]+", src[pos:])
        if not m:
            raise AssemblerError("invalid number", line, col)
        text = src[start] + m.group(0) if src[start] in "+-" else m.group(0)
        pos += m.end()
        value = int(text, 16 if m.group(0).lower().startswith("0x") else 10)
        if pos < len(src) and (src[pos].isalnum() or src[pos] == "_"):
            raise AssemblerError("malformed number", line, col)
        return Token(TokenType.NUMBER, value, line, col), pos

    def _read_name(
        self, pos: int, line: int, col: int, src: str
    ) -> tuple[Token, int]:
        """Read an identifier, register, or directive name."""
        m = re.match(r"[A-Za-z_.][A-Za-z0-9_.]*", src[pos:])
        if not m:
            raise AssemblerError("invalid character", line, col)
        text = m.group(0)
        end = pos + len(text)
        if text.startswith("."):
            return Token(TokenType.DIRECTIVE, text.lower(), line, col), end
        reg = re.fullmatch(r"[rR](1[0-5]|[0-9])", text)
        if reg:
            return Token(TokenType.REGISTER, int(text[1:]), line, col), end
        if re.fullmatch(r"[rR][0-9]+", text):
            raise AssemblerError("register out of range (R0-R15)", line, col)
        return Token(TokenType.NAME, text, line, col), end

    def _read_string(
        self, pos: int, line: int, col: int, src: str
    ) -> tuple[Token, int]:
        """Read a double-quoted string, decoding standard escapes."""
        pos += 1  # opening quote
        chars: list[str] = []
        escapes = {"n": "\n", "t": "\t", "r": "\r", "0": "\0",
                   '"': '"', "\\": "\\"}
        while pos < len(src) and src[pos] != '"':
            ch = src[pos]
            if ch == "\n":
                raise AssemblerError("unterminated string", line, col)
            if ch == "\\":
                pos += 1
                if pos >= len(src):
                    raise AssemblerError("unterminated escape", line, col)
                esc = src[pos]
                if esc not in escapes:
                    raise AssemblerError(f"unknown escape \\{esc}", line, col)
                chars.append(escapes[esc])
            else:
                chars.append(ch)
            pos += 1
        if pos >= len(src):
            raise AssemblerError("unterminated string", line, col)
        return Token(TokenType.STRING, "".join(chars), line, col), pos + 1


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

# Data-segment pseudo-directives that emit bytes.
_DATA_DIRECTIVES = frozenset({".word", ".byte", ".string", ".space"})


def _value_count(args: list[Token]) -> int:
    """Count value tokens in a directive argument list (commas excluded)."""
    return sum(1 for tok in args if tok.type is not TokenType.COMMA)


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
        tokens = Tokenizer(self.source).tokenize()
        self.raw_lines = self._parse(tokens)
        self._collect_labels()
        self._emit()
        return self._build_program()

    # -- parsing -----------------------------------------------------------

    def _parse(self, tokens: list[Token]) -> list[_RawLine]:
        """Group tokens into logical lines, decoding operands."""
        lines: list[_RawLine] = []
        i, n = 0, len(tokens)
        while i < n and tokens[i].type is not TokenType.EOF:
            # gather tokens up to EOL
            chunk: list[Token] = []
            line_no = tokens[i].line
            while i < n and tokens[i].type not in (TokenType.EOL, TokenType.EOF):
                chunk.append(tokens[i])
                i += 1
            if i < n and tokens[i].type is TokenType.EOL:
                i += 1
            if not chunk:
                continue
            raw = _RawLine(line=line_no)
            j = 0
            # optional label definition: NAME ':'
            if (
                len(chunk) >= 2
                and chunk[0].type is TokenType.NAME
                and chunk[1].type is TokenType.COLON
            ):
                raw.label = str(chunk[0].value)
                raw.label_col = chunk[0].col
                j = 2
            body = chunk[j:]
            if body:
                head = body[0]
                if head.type is TokenType.DIRECTIVE:
                    raw.directive = str(head.value)
                    raw.args = body[1:]
                elif head.type is TokenType.NAME:
                    raw.mnemonic = str(head.value).upper()
                    self._parse_operands(raw, body[1:])
                else:
                    self._error("expected instruction or directive",
                                head.line, head.col)
            lines.append(raw)
        return lines

    def _parse_operands(self, line: _RawLine, tokens: list[Token]) -> None:
        """Parse the operand list of one instruction into line.operands."""
        groups: list[list[Token]] = [[]]
        for tok in tokens:
            if tok.type is TokenType.COMMA:
                groups.append([])
            else:
                groups[-1].append(tok)
        for group in groups:
            if not group:
                continue
            first = group[0]
            if first.type is TokenType.LBRACKET:
                if (
                    len(group) != 3
                    or group[1].type is not TokenType.REGISTER
                    or group[2].type is not TokenType.RBRACKET
                ):
                    self._error("indirect operand must be [Rn]",
                                first.line, first.col)
                line.operands.append(_Operand("mem", group[1].value, first.col))
            elif first.type is TokenType.REGISTER:
                if len(group) != 1:
                    self._error("unexpected tokens after register",
                                first.line, first.col)
                line.operands.append(_Operand("reg", first.value, first.col))
            elif first.type is TokenType.NAME:
                if len(group) != 1:
                    self._error("unexpected tokens after label",
                                first.line, first.col)
                line.operands.append(_Operand("label", str(first.value), first.col))
            elif first.type is TokenType.NUMBER:
                if len(group) != 1:
                    self._error("unexpected tokens after number",
                                first.line, first.col)
                line.operands.append(_Operand("imm", first.value, first.col))
            else:
                self._error("invalid operand", first.line, first.col)
        form = INSTRUCTIONS.get(line.mnemonic or "")
        if form is None:
            self._error(f"unknown instruction {line.mnemonic!r}", line.line,
                        tokens[0].col if tokens else 1)
        expected = OPERAND_COUNT[form[1]]
        if expected == 0 and any(g for g in groups):
            self._error(f"{line.mnemonic} expects no operands",
                        line.line, tokens[0].col)
        if expected > 0 and any(not g for g in groups):
            self._error("empty operand", line.line,
                        tokens[0].col if tokens else 1)
        if len(line.operands) != expected:
            self._error(
                f"{line.mnemonic} expects {expected} operand(s), got "
                f"{len(line.operands)}",
                line.line,
                tokens[0].col if tokens else 1,
            )

    # -- pass 1: symbol table ---------------------------------------------

    def _collect_labels(self) -> None:
        """Walk raw lines assigning addresses and recording labels/IVT."""
        from .isa import CODE_END, DATA_END, INSTR_SIZE

        segment = Segment.CODE
        code_off = 0
        data_off = 0
        for raw in self.raw_lines:
            directive = raw.directive
            if directive in (".code", ".data", ".ivt"):
                if directive == ".code":
                    segment = Segment.CODE
                elif directive == ".data":
                    segment = Segment.DATA
                continue
            if directive is not None and directive not in _DATA_DIRECTIVES:
                self._error(f"unknown directive {directive!r}", raw.line, 1)
            if raw.label is not None:
                if raw.label in self.labels:
                    self._error(f"duplicate label {raw.label!r}",
                                raw.line, raw.label_col)
                if segment is Segment.CODE:
                    self.labels[raw.label] = CODE_BASE + code_off
                else:
                    self.labels[raw.label] = DATA_BASE + data_off
            if raw.mnemonic is not None:
                if segment is not Segment.CODE:
                    self._error(
                        f"instruction {raw.mnemonic} not allowed in .data segment",
                        raw.line, 1,
                    )
                code_off += INSTR_SIZE
                if CODE_BASE + code_off > CODE_END + 1:
                    self._error("code segment overflow", raw.line, 1)
            else:
                if directive is None:
                    continue  # label-only line, occupies no bytes
                size = self._data_directive_size(raw)
                if segment is Segment.CODE:
                    self._error(
                        f"data directive {directive} not allowed in .code segment",
                        raw.line, 1,
                    )
                if DATA_BASE + data_off + size > DATA_END + 1:
                    self._error("data segment overflow", raw.line, 1)
                data_off += size

    def _data_directive_size(self, line: _RawLine) -> int:
        """Return the number of data bytes a directive emits."""
        d = line.directive
        if d == ".space":
            if len(line.args) != 1 or line.args[0].type is not TokenType.NUMBER:
                self._error(".space expects one non-negative number", line.line, 1)
            value = line.args[0].value
            if value < 0:
                self._error(".space size must be non-negative", line.line, 1)
            return int(value)
        if d == ".byte":
            return _value_count(line.args)
        if d == ".word":
            return 2 * _value_count(line.args)
        if d == ".string":
            if len(line.args) != 1 or line.args[0].type is not TokenType.STRING:
                self._error('.string expects one "..." argument', line.line, 1)
            return len(line.args[0].value.encode("utf-8")) + 1
        self._error(f"unknown data directive {d!r}", line.line, 1)

    # -- pass 2: codegen ---------------------------------------------------

    def _emit(self) -> None:
        """Encode instructions/directives, resolving label references."""
        segment = Segment.CODE
        for raw in self.raw_lines:
            directive = raw.directive
            if directive in (".code", ".data"):
                segment = Segment.CODE if directive == ".code" else Segment.DATA
                continue
            if directive == ".ivt":
                self._emit_ivt(raw)
                continue
            if directive in _DATA_DIRECTIVES:
                self._emit_data(raw)
                continue
            if raw.mnemonic is not None:
                self._emit_instruction(raw)

    def _emit_ivt(self, line: _RawLine) -> None:
        """Encode `.ivt n, handler` into the IVT table."""
        args = [a for a in line.args if a.type is not TokenType.COMMA]
        if (
            len(args) != 2
            or args[0].type is not TokenType.NUMBER
            or args[1].type not in (TokenType.NUMBER, TokenType.NAME)
        ):
            self._error(".ivt expects: .ivt <number>, <label>", line.line, 1)
        num = args[0].value
        if not 0 <= num < IVT_MAX_ENTRIES:
            self._error(f"interrupt number out of range (0-{IVT_MAX_ENTRIES - 1})",
                        line.line, args[0].col)
        if args[1].type is TokenType.NAME:
            handler = self._resolve(str(args[1].value), line.line, args[1].col)
        else:
            handler = self._check_u16(args[1].value, line.line, args[1].col)
        self.ivt[int(num)] = handler

    def _emit_instruction(self, line: _RawLine) -> None:
        """Encode one code-segment instruction into code_buf."""
        op, form = INSTRUCTIONS[line.mnemonic]  # type: ignore[index]
        ops = line.operands
        b = bytearray([int(op), 0, 0, 0])

        def reg(index: int) -> int:
            return self._as_register(ops[index], line)

        def value16(index: int) -> tuple[int, int]:
            operand = ops[index]
            if operand.kind == "label":
                v = self._resolve(str(operand.value), line.line, operand.col)
            else:
                v = self._check_u16(operand.value, line.line, operand.col)
            return (v >> 8) & 0xFF, v & 0xFF

        if form is OperandForm.NONE:
            pass
        elif form is OperandForm.R:
            b[1] = reg(0)
        elif form is OperandForm.RR:
            b[1], b[2] = reg(0), reg(1)
        elif form is OperandForm.RRR:
            b[1], b[2], b[3] = reg(0), reg(1), reg(2)
        elif form is OperandForm.N8:
            b[1] = self._as_u8(ops[0], line)
        elif form is OperandForm.A16:
            b[2], b[3] = value16(0)
        elif form is OperandForm.RA16:
            b[1] = reg(0)
            b[2], b[3] = value16(1)
        elif form is OperandForm.RI16:
            b[1] = reg(0)
            b[2], b[3] = value16(1)
        elif form is OperandForm.R_IND:
            if ops[0].kind != "reg" or ops[1].kind != "mem":
                self._error("expected Rd, [Rs]", line.line, ops[0].col)
            b[1] = self._as_register(ops[0], line)
            b[2] = self._as_register(ops[1], line)
        elif form is OperandForm.IND_R:
            if ops[0].kind != "mem" or ops[1].kind != "reg":
                self._error("expected [Rd], Rs", line.line, ops[0].col)
            b[1] = self._as_register(ops[0], line)
            b[2] = self._as_register(ops[1], line)
        else:  # pragma: no cover - exhaustive forms
            self._error(f"unsupported operand form {form}", line.line, 1)
        self.code_buf.extend(b)

    def _as_register(self, operand: _Operand, line: _RawLine) -> int:
        if operand.kind not in ("reg", "mem"):
            self._error("register operand expected", line.line, operand.col)
        value = int(operand.value)
        if not 0 <= value <= 15:
            self._error("register out of range (R0-R15)", line.line, operand.col)
        return value

    def _as_u8(self, operand: _Operand, line: _RawLine) -> int:
        if operand.kind == "label":
            self._error("label not allowed here", line.line, operand.col)
        value = int(operand.value)
        if not 0 <= value <= 0xFF:
            self._error("operand out of range (0-255)", line.line, operand.col)
        return value

    def _check_u16(self, value: object, line: int, col: int) -> int:
        ivalue = int(value)  # type: ignore[arg-type]
        if not 0 <= ivalue <= U16_MAX:
            raise AssemblerError("operand out of range (0-65535)", line, col)
        return ivalue

    def _emit_data(self, line: _RawLine) -> None:
        """Encode one data directive into data_buf."""
        d = line.directive
        args = line.args
        if d == ".space":
            self.data_buf.extend(b"\x00" * int(args[0].value))
            return
        if d == ".string":
            self.data_buf.extend(args[0].value.encode("utf-8"))
            self.data_buf.append(0)
            return
        values = [a for a in args if a.type is not TokenType.COMMA]
        if d == ".byte":
            for tok in values:
                if tok.type is TokenType.NAME:
                    self._error("label not allowed in .byte", tok.line, tok.col)
                if tok.type is not TokenType.NUMBER or not 0 <= tok.value <= 0xFF:
                    self._error(".byte value out of range (0-255)", tok.line, tok.col)
                self.data_buf.append(tok.value & 0xFF)
            return
        if d == ".word":
            for tok in values:
                if tok.type is TokenType.NAME:
                    word = self._resolve(str(tok.value), tok.line, tok.col)
                elif tok.type is TokenType.NUMBER:
                    word = self._check_u16(tok.value, tok.line, tok.col)
                else:
                    self._error(".word expects numbers or labels", tok.line, tok.col)
                self.data_buf.extend([(word >> 8) & 0xFF, word & 0xFF])
            return
        self._error(f"unknown data directive {d!r}", line.line, 1)

    # -- helpers -----------------------------------------------------------

    def _resolve(self, name: str, line: int, col: int) -> int:
        """Look up a label, raising AssemblerError if undefined."""
        if name not in self.labels:
            raise AssemblerError(f"undefined label {name!r}", line, col)
        return self.labels[name]

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
