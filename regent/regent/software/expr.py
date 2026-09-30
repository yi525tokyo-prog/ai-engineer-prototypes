"""A small, safe expression language for metrics over observed series.

A metric is written as an expression over *references* ``"source:field.path"``; each reference
denotes the time series of that field across a capability's observations. Only whitelisted
functions and arithmetic are allowed (parsed with ``ast``; no names, attributes or builtins),
so a metric proposed by a reasoning worker can be evaluated without executing its code.

Functions
---------
latest(ref)                   last observed value
increase(ref, window)         sum of increases within the window; a drop is a counter reset
                              (daily counters restart at 0), so the post-reset value counts
delta(ref, window)            last - first within the window
max_over(ref, window) / min_over(ref, window)
count_items(ref, key, window) items of list-valued fields whose timestamp ``key`` is within the
                              window, de-duplicated across observations
distinct_items(ref)           distinct list items ever observed
observed_for(ref)             seconds of history available
max(a, b...), min(a, b...), round(x, n), coalesce(a, b...)

Windows: "15m", "24h", "7d", "30d", "all".
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

Series = list[tuple[datetime, Any]]


class ExprError(ValueError):
    pass


def window_s(w: str) -> float | None:
    if w in ("all", "all_time", ""):
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhdw])", w.strip().lower())
    if not m:
        raise ExprError(f"bad window {w!r}")
    return float(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2)]


@dataclass
class EvalContext:
    series: Callable[[str], Series]          # ref -> [(t, value)], oldest first, ok observations only
    now: datetime
    notes: list[str] = field(default_factory=list)
    refs: set[str] = field(default_factory=set)
    partial: bool = False


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _within(ctx: EvalContext, s: Series, w: str) -> Series:
    ws = window_s(w)
    if ws is None:
        return s
    if s and (ctx.now - s[0][0]).total_seconds() < ws * 0.95:
        ctx.partial = True
        ctx.notes.append(f"history covers {(ctx.now - s[0][0]).total_seconds() / 3600:.1f}h of the {w} window")
    return [(t, v) for t, v in s if (ctx.now - t).total_seconds() <= ws]


def _ts(v: Any) -> datetime | None:
    if isinstance(v, (int, float)):
        x = float(v)
        if x > 1e12:
            x /= 1000.0
        if x > 1e8:
            return datetime.fromtimestamp(x, timezone.utc)
        return None
    if isinstance(v, str):
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _fns(ctx: EvalContext) -> dict[str, Callable[..., Any]]:
    def ser(ref: str) -> Series:
        if not isinstance(ref, str) or ":" not in ref:
            raise ExprError(f"reference must be 'source:field', got {ref!r}")
        ctx.refs.add(ref)
        return ctx.series(ref)

    def latest(ref):
        s = ser(ref)
        return s[-1][1] if s else None

    def increase(ref, w="all"):
        s = [(t, _num(v)) for t, v in _within(ctx, ser(ref), w)]
        s = [(t, v) for t, v in s if v is not None]
        if not s:
            return None
        total = 0.0
        for (_, a), (_, b) in zip(s, s[1:]):
            total += (b - a) if b >= a else b
        return total

    def delta(ref, w="all"):
        s = [(t, _num(v)) for t, v in _within(ctx, ser(ref), w)]
        s = [(t, v) for t, v in s if v is not None]
        return (s[-1][1] - s[0][1]) if len(s) >= 1 else None

    def max_over(ref, w="all"):
        vals = [_num(v) for _, v in _within(ctx, ser(ref), w)]
        vals = [v for v in vals if v is not None]
        return max(vals) if vals else None

    def min_over(ref, w="all"):
        vals = [_num(v) for _, v in _within(ctx, ser(ref), w)]
        vals = [v for v in vals if v is not None]
        return min(vals) if vals else None

    def _items(ref) -> list[Any]:
        seen: dict[str, Any] = {}
        for _, v in ser(ref):
            for it in v if isinstance(v, list) else []:
                seen.setdefault(json.dumps(it, sort_keys=True, default=str), it)
        return list(seen.values())

    def count_items(ref, key, w="all"):
        ws = window_s(w)
        n = 0
        for it in _items(ref):
            t = _ts(it.get(key)) if isinstance(it, dict) else None
            if t is None:
                continue
            if ws is None or 0 <= (ctx.now - t).total_seconds() <= ws:
                n += 1
        return n

    def distinct_items(ref):
        return len(_items(ref))

    def observed_for(ref):
        s = ser(ref)
        return (ctx.now - s[0][0]).total_seconds() if s else 0.0

    def coalesce(*xs):
        return next((x for x in xs if x is not None), None)

    def _max(*xs):
        xs2 = [x for x in xs if x is not None]
        return max(xs2) if xs2 else None

    def _min(*xs):
        xs2 = [x for x in xs if x is not None]
        return min(xs2) if xs2 else None

    def _round(x, n=0):
        return None if x is None else round(float(x), int(n))

    return {"latest": latest, "increase": increase, "delta": delta, "max_over": max_over, "min_over": min_over,
            "count_items": count_items, "distinct_items": distinct_items, "observed_for": observed_for,
            "coalesce": coalesce, "max": _max, "min": _min, "round": _round}


FUNCTIONS = ("latest", "increase", "delta", "max_over", "min_over", "count_items", "distinct_items", "observed_for",
             "coalesce", "max", "min", "round")


def parse(expr: str) -> ast.Expression:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ExprError(f"syntax: {e.msg}") from e
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Load, ast.Add, ast.Sub,
                             ast.Mult, ast.Div, ast.USub, ast.UAdd, ast.FloorDiv, ast.Mod)):
            continue
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS or node.keywords:
                raise ExprError(f"only these functions are allowed: {', '.join(FUNCTIONS)}")
            continue
        if isinstance(node, ast.Name) and node.id in FUNCTIONS:
            continue
        raise ExprError(f"not allowed in a metric: {type(node).__name__}")
    return tree


def references(expr: str) -> list[str]:
    tree = parse(expr)
    return sorted({n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and ":" in n.value})


def evaluate(expr: str, ctx: EvalContext) -> Any:
    tree = parse(expr)
    fns = _fns(ctx)

    def ev(n: ast.AST) -> Any:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            return None if v is None else (-v if isinstance(n.op, ast.USub) else v)
        if isinstance(n, ast.BinOp):
            a, b = ev(n.left), ev(n.right)
            a, b = _num(a) if a is not None else None, _num(b) if b is not None else None
            if a is None or b is None:
                return None
            op = n.op
            if isinstance(op, ast.Add):
                return a + b
            if isinstance(op, ast.Sub):
                return a - b
            if isinstance(op, ast.Mult):
                return a * b
            if isinstance(op, ast.Div):
                return None if b == 0 else a / b
            if isinstance(op, ast.FloorDiv):
                return None if b == 0 else a // b
            if isinstance(op, ast.Mod):
                return None if b == 0 else a % b
        if isinstance(n, ast.Call):
            return fns[n.func.id](*[ev(a) for a in n.args])
        raise ExprError(f"cannot evaluate {type(n).__name__}")

    return ev(tree)
