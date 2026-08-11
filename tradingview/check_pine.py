#!/usr/bin/env python3
"""Static checks for the TFBS Pine Script, for use where Pine cannot compile.

TradingView owns the only real Pine compiler, so edits made outside it are
unverified until someone pastes the file in. This catches the structural
mistakes that are easy to make and produce opaque errors — most of all the
line-continuation rule, which cost us a CE10013:

    Pine treats a wrapped line indented by a MULTIPLE OF FOUR spaces as a new
    block, not a continuation. Block bodies are the exact inverse: they must be
    at a multiple of four.

Checks performed
    1. continuation lines are NOT at a multiple-of-4 indent
    2. block bodies ARE at a multiple-of-4 indent
    3. brackets balance across the file
    4. `str.format` placeholders are contiguous and match the argument count
    5. no `ta.*` call sits inside a conditional branch (Pine keeps internal
       state for those; calling them conditionally corrupts it silently)
    6. every `x :=` targets something previously declared with `=`

    python tradingview/check_pine.py                 # checks the bundled file
    python tradingview/check_pine.py path/to.pine    # or any other

Exit code is 0 when clean, 1 when something is flagged. This is a linter, not
a compiler: passing means the known traps are avoided, not that it builds.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List

DEFAULT_TARGET = Path(__file__).with_name("titan_tfbs_strategy.pine")

#: A line ending in one of these is continued on the next line.
_TAIL = re.compile(r"(\bor|\band|[?:+,=*/\-([])\s*$")
#: A line that opens an indented block.
_OPENS = re.compile(r"(=>\s*$)|(^(if|else|for|while|switch|type)\b)")


def strip_comment(line: str) -> str:
    """Remove a trailing // comment without touching // inside strings."""
    out: List[str] = []
    in_string = False
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if in_string:
            out.append(ch)
            if ch == quote:
                in_string = False
        elif ch in "\"'":
            in_string, quote = True, ch
            out.append(ch)
        elif ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def scan(text: str) -> List[Dict]:
    """Annotate each line with its indent and whether it continues the previous."""
    rows: List[Dict] = []
    depth = 0
    continued = False
    for number, raw in enumerate(text.split("\n"), 1):
        code = strip_comment(raw)
        stripped = code.strip()
        rows.append(
            {
                "n": number,
                "indent": len(raw) - len(raw.lstrip(" ")),
                "code": stripped,
                "raw": raw,
                "continuation": depth > 0 or continued,
            }
        )
        depth += (
            code.count("(") - code.count(")") + code.count("[") - code.count("]")
        )
        continued = depth > 0 or (
            bool(_TAIL.search(stripped)) and not stripped.startswith("//")
        )
    return rows


def check(text: str) -> List[str]:
    rows = scan(text)
    problems: List[str] = []

    # 1. Continuations must not look like block bodies.
    for r in rows:
        if r["continuation"] and r["code"] and r["indent"] > 0 and r["indent"] % 4 == 0:
            problems.append(
                f"L{r['n']}: continuation indented {r['indent']} (a multiple of 4) — "
                f"Pine will read it as a new block. Shift it by one space."
            )

    # 2. Block bodies must not look like continuations. Skip the opening
    #    statement's own wrapped condition lines, which are legitimately at a
    #    non-multiple-of-4 indent.
    for idx, r in enumerate(rows):
        if (
            not r["code"]
            or r["continuation"]
            or r["code"].startswith("//")
            or not _OPENS.search(r["code"])
        ):
            continue
        for nxt in rows[idx + 1 :]:
            if not nxt["code"] or nxt["code"].startswith("//") or nxt["continuation"]:
                continue
            if nxt["indent"] <= r["indent"]:
                break
            if nxt["indent"] % 4 != 0:
                problems.append(
                    f"L{nxt['n']}: block body indented {nxt['indent']} (not a multiple "
                    f"of 4), opened at L{r['n']} — Pine will read it as a continuation."
                )
            break

    # 3. Brackets balance.
    for opener, closer in (("(", ")"), ("[", "]")):
        net = sum(
            strip_comment(r["raw"]).count(opener) - strip_comment(r["raw"]).count(closer)
            for r in rows
        )
        if net:
            problems.append(f"unbalanced {opener}{closer}: net {net:+d}")

    # 4. str.format placeholders contiguous from {0}.
    text_lines = [r["raw"] for r in rows]
    for idx, raw in enumerate(text_lines):
        if "str.format(" not in raw:
            continue
        block, j = raw, idx + 1
        while block.count("(") > block.count(")") and j < len(text_lines):
            block += text_lines[j]
            j += 1
        found = sorted({int(m) for m in re.findall(r"(?<!\{)\{(\d+)\}", block)})
        if found and found != list(range(len(found))):
            problems.append(
                f"L{idx + 1}: str.format placeholders are not contiguous from 0: {found}"
            )

    # 5. ta.* must be evaluated on every bar. A call indented inside a branch
    #    is only reached sometimes, which silently corrupts its series state.
    in_security_helper = False
    for r in rows:
        if r["code"].startswith("f_biasScore()"):
            in_security_helper = True          # wrapped for request.security
            continue
        if in_security_helper and r["code"] and r["indent"] == 0:
            in_security_helper = False
        if in_security_helper:
            continue
        if re.search(r"\bta\.\w+\(", r["code"]) and r["indent"] > 0:
            problems.append(
                f"L{r['n']}: ta.* called at indent {r['indent']} — hoist it to global "
                f"scope so it is evaluated on every bar."
            )

    # 6. `:=` needs a prior `=` declaration.
    body = "\n".join(text_lines)
    declared = set(re.findall(r"^\s*(?:var\s+)?[\w<>]*\s*([a-zA-Z_]\w*)\s*=[^=]", body, re.M))
    for r in rows:
        for name in re.findall(r"\b([a-zA-Z_]\w*)\s*:=", r["code"]):
            if name not in declared:
                problems.append(f"L{r['n']}: `{name} :=` but {name} is never declared")

    return problems


def main(argv: List[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    target = Path(args[0]) if args else DEFAULT_TARGET
    if not target.exists():
        print(f"not found: {target}", file=sys.stderr)
        return 2

    problems = check(target.read_text())
    if not problems:
        print(f"{target.name}: clean — no known Pine traps found")
        print("  (a linter, not a compiler: TradingView is still the real test)")
        return 0

    print(f"{target.name}: {len(problems)} problem(s)\n")
    for p in problems:
        print(f"  {p}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
