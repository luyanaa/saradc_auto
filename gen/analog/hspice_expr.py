#!/usr/bin/env python3
"""Resolve HSpice parameter expressions to numbers.

The ICsprout 55 ``.mdl`` files come in two shapes:

* binned files (``nhvt.mdl``, ``phvt.mdl``) hold 20 ``.model`` cards whose
  parameters are literal numbers; nothing here is needed.
* single-card files (``nsvt.mdl``, ``nlvt.mdl``, ``psvt.mdl``, ...) hold one
  card whose parameters are *interpolated* over a W/L grid with the vendor's
  ``select6``/``selectp7``/``evalbin`` macros and the C ternary operator.

HSpice evaluates those; Xyce has no such macros and aborts on the first card
parameter that still contains identifiers.  This module evaluates the same
expressions for one concrete (W, L) and hands back a card of literal numbers.

The evaluator is deliberately small: numbers, ``+ - * /``, comparisons, the
ternary operator, parentheses, and calls to the ``.param`` function
definitions in the same file.  That is exactly the language the cards use.
"""

from __future__ import annotations

import math
import re

_TOKEN = re.compile(r"""\s*(?:
    (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eEdD][+-]?\d+)?)
  | (?P<id>[A-Za-z_]\w*)
  | (?P<op><=|>=|==|!=|[-+*/?:()<>,])
)""", re.X)
_NAME = re.compile(r"[A-Za-z_]\w*")

_BUILTIN = {
    "pwr": lambda a, b: a ** b,
    "cosh": math.cosh,
    "sinh": math.sinh,
    "sqrt": math.sqrt,
    "abs": abs,
    "exp": math.exp,
    "log": math.log,
    "ln": math.log,
    "min": min,
    "max": max,
}


class Expression:
    """One HSpice arithmetic expression, evaluated against env + funcs."""

    def __init__(self, text, funcs, env):
        self.tokens = self._lex(text)
        self.pos = 0
        self.funcs = funcs
        self.env = env

    @staticmethod
    def _lex(text):
        tokens, pos, end = [], 0, len(text)
        while pos < end:
            match = _TOKEN.match(text, pos)
            if not match:
                raise ValueError(f"cannot lex {text[pos:pos + 24]!r}")
            pos = match.end()
            tokens.append((match.lastgroup, match.group(match.lastgroup)))
        tokens.append(("end", ""))
        return tokens

    def _peek(self):
        return self.tokens[self.pos]

    def _take(self, want=None):
        kind, value = self.tokens[self.pos]
        if want is not None and value != want:
            raise ValueError(f"expected {want!r}, got {value!r}")
        self.pos += 1
        return kind, value

    def evaluate(self):
        value = self._ternary()
        if self._peek()[0] != "end":
            raise ValueError(f"trailing token {self._peek()[1]!r}")
        return value

    def _ternary(self):
        cond = self._compare()
        if self._peek()[1] == "?":
            self._take("?")
            yes = self._ternary()
            self._take(":")
            no = self._ternary()
            return yes if cond else no
        return cond

    def _compare(self):
        ops = {
            "<": lambda a, b: a < b,
            ">": lambda a, b: a > b,
            "<=": lambda a, b: a <= b,
            ">=": lambda a, b: a >= b,
            "==": lambda a, b: a == b,
            "!=": lambda a, b: a != b,
        }
        left = self._additive()
        while self._peek()[1] in ops:
            op = self._take()[1]
            left = ops[op](left, self._additive())
        return left

    def _additive(self):
        left = self._multiplicative()
        while self._peek()[1] in ("+", "-"):
            op = self._take()[1]
            right = self._multiplicative()
            left = left + right if op == "+" else left - right
        return left

    def _multiplicative(self):
        left = self._unary()
        while self._peek()[1] in ("*", "/"):
            op = self._take()[1]
            right = self._unary()
            left = left * right if op == "*" else left / right
        return left

    def _unary(self):
        if self._peek()[1] in ("+", "-"):
            op = self._take()[1]
            value = self._unary()
            return -value if op == "-" else value
        return self._primary()

    def _primary(self):
        kind, value = self._take()
        if kind == "num":
            return float(value.replace("d", "e").replace("D", "E"))
        if kind == "op" and value == "(":
            inner = self._ternary()
            self._take(")")
            return inner
        if kind == "id":
            name = value.lower()
            if self._peek()[1] == "(":
                self._take("(")
                args = []
                if self._peek()[1] != ")":
                    args.append(self._ternary())
                    while self._peek()[1] == ",":
                        self._take(",")
                        args.append(self._ternary())
                self._take(")")
                return self._call(name, args)
            if name not in self.env:
                raise KeyError(name)
            return self.env[name]
        raise ValueError(f"unexpected token {value!r}")

    def _call(self, name, args):
        if name in self.funcs:
            params, body = self.funcs[name]
            if len(params) != len(args):
                raise ValueError(f"{name}: {len(args)} args, "
                                 f"wants {len(params)}")
            local = dict(self.env)
            local.update({p.lower(): a for p, a in zip(params, args)})
            return Expression(body, self.funcs, local).evaluate()
        if name in _BUILTIN:
            return _BUILTIN[name](*args)
        raise KeyError(name)


def statements(text):
    """Join HSpice continuation lines; comments and blanks do not end one."""
    out, current = [], []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("*"):
            continue
        if line.startswith("+"):
            current.append(line[1:].strip())
            continue
        if current:
            out.append(" ".join(current))
        current = [line]
    if current:
        out.append(" ".join(current))
    return out


def assignments(text):
    """Split ``name = value`` and ``func(args) = 'expr'`` pairs out of a line."""
    out, i, end = [], 0, len(text)
    while i < end:
        while i < end and text[i].isspace():
            i += 1
        if i >= end:
            break
        match = _NAME.match(text, i)
        if not match:
            raise ValueError(f"expected a name at {text[i:i + 24]!r}")
        name, i = match.group(0), match.end()
        params = None
        while i < end and text[i].isspace():
            i += 1
        if i < end and text[i] == "(":
            depth, j = 0, i
            while j < end:
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            params = [p.strip() for p in text[i + 1:j].split(",") if p.strip()]
            i = j + 1
            while i < end and text[i].isspace():
                i += 1
        if i >= end or text[i] != "=":
            raise ValueError(f"{name}: expected '=' at {text[i:i + 24]!r}")
        i += 1
        while i < end and text[i].isspace():
            i += 1
        if i < end and text[i] == "'":
            j = text.index("'", i + 1)
            value, i = text[i + 1:j], j + 1
        else:
            # unquoted values include function calls such as
            # select6(wum,0.12,...) where the commas are not separators
            j, depth = i, 0
            while j < end:
                char = text[j]
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                if depth == 0 and (char.isspace() or char == "'"):
                    break
                j += 1
            value, i = text[i:j], j
        out.append((name, params, value))
    return out


def resolve_card(path, device, kind, l_m, w_m, sigma=None):
    """Read ``path`` and return a flat ``.model`` card at (W, L).

    ``sigma`` sets the mismatch knobs (default: 0, i.e. a nominal device).
    Returns the list of text lines, first line the ``.model`` header.
    """
    env = {
        "l": l_m, "w": w_m, "nf": 1.0, "mr": 1.0, "temper": 27.0,
        "prelayout": 0.0, "mismod": 0.0,
    }
    for key, value in (sigma or {}).items():
        env[key.lower()] = value

    funcs, pending, card = {}, [], None
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    # The tt wrapper defines every corner delta (<d*>_<device>) as 0; the
    # files also reference *_retgt hooks that the wrapper only creates in the
    # statistical sections.  Seed any device-named symbol as 0 so those
    # nominal-case terms resolve exactly as HSpice would.
    if device:
        pattern = re.compile(r"\b[A-Za-z0-9_]*" + re.escape(device)
                             + r"[A-Za-z0-9_]*\b")
        for ident in set(pattern.findall(text)):
            env.setdefault(ident.lower(), 0.0)
    for stmt in statements(text):
        head = stmt.lstrip().lower()
        if head.startswith(".model"):
            header = re.match(r"\.model\s+(\S+)\s+(\S+)\s*(.*)$", stmt, re.I | re.S)
            if not header:
                raise SystemExit(f"{path}: cannot parse .model line")
            pairs = assignments(header.group(3))
            card = [name for name, params, _ in pairs if params is None]
            pending.extend((name, value) for name, params, value in pairs
                           if params is None)
            kind = header.group(2)
            continue
        if head.startswith(".param"):
            pairs = assignments(stmt[len(".param"):])
        elif head.startswith("."):
            continue
        else:
            pairs = assignments(stmt)
        for name, params, value in pairs:
            if params is None:
                pending.append((name, value))
            else:
                funcs[name.lower()] = (params, value)
    if card is None:
        raise SystemExit(f"{path}: no .model card")

    unresolved = []
    for _ in range(len(pending) + 2):
        if not pending:
            break
        stuck = []
        for name, value in pending:
            try:
                env[name.lower()] = Expression(value, funcs, env).evaluate()
            except KeyError:
                stuck.append((name, value))
        if len(stuck) == len(pending):
            unresolved = sorted({name for name, _ in stuck})
            break
        pending = stuck
    if unresolved:
        raise SystemExit(f"{path}: unresolved HSpice parameters "
                         f"{', '.join(unresolved[:6])}")
    return [f".model {device} {kind}"] + [
        f"+{name} = {env[name.lower()]!r}" for name in card]
