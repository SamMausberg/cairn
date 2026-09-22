"""The editor grammars, generated from the compiler's own vocabulary so that no word goes unhighlighted.

Every reserved word, every word the parser treats specially in one position, every builtin, scalar,
intrinsic type, placement, effect and library variant comes from the module that owns it. `WORDS` sorts
them into classes; the TextMate grammar (VS Code, Cursor, GitHub's highlighter) and the Vim syntax file are
both written from that one table. `python -m cairn.editor.grammar` rewrites them, and `--check` fails when
a committed file differs from a fresh run. Scope names are the standard TextMate ones, so any theme colors
them; the language server's semantic tokens then refine what only the checker knows.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..compiler.builtins import TABLE
from ..compiler.effects import EFFECT_FAMILIES, EFFECTS
from ..compiler.lexing import RESERVED
from ..compiler.modules import STD
from ..compiler.syntax import Parser
from ..compiler.traits import CLASSES, KINDS
from ..compiler.tree import INTRINSIC_TYPES, PLACES, SCALAR, STORAGE

ROOT = Path(__file__).resolve().parents[3]

# Words the parser reads in one position only; anywhere else they are ordinary names (syntax.py says where).
CONTEXTUAL = {"after", "align", "fold", "grain", "into", "lanes", "packed", "plan", "recipe", "require"}

# class -> (TextMate scope, Vim group, words). Every reserved word is in exactly one class.
WORDS: dict[str, tuple[str, str, set[str]]] = {
    "conditional": ("keyword.control.conditional.cairn", "Conditional", {"if", "else", "match"}),
    "repeat": ("keyword.control.loop.cairn", "Repeat", {"for", "each", "in", "while"}),
    "flow": ("keyword.control.flow.cairn", "Statement", {"return", "break", "continue", "defer", "try", "yield"}),
    "concurrency": ("keyword.control.concurrency.cairn", "Statement", {"spawn", "parallel", "reduce", "compact"}),
    "declaration": (
        "keyword.declaration.cairn",
        "Keyword",
        {"fn", "struct", "enum", "trait", "impl", "module", "import", "const", "extern", "family", "derive"}
        | {"kernel", "type", "nat", "where"},
    ),
    "binding": ("storage.type.binding.cairn", "StorageClass", {"let", "reg", "buffer", "stack"}),
    "modifier": (
        "storage.modifier.cairn",
        "StorageClass",
        {"mut", "pub", "linear", "ro", "rw", "dyn", "effects", "pure", "unsafe", "as"},
    ),
    "constant": ("constant.language.cairn", "Boolean", {"true", "false", "zeroed"}),
}
# Contextual words, each with the lookahead that makes it a keyword: `plan f {`, `spawn f() into g;`, ...
CONTEXT: dict[str, tuple[str, str]] = {
    "after": ("keyword.control.concurrency.cairn", r"(?=\s+[A-Za-z_])"),
    "into": ("keyword.control.concurrency.cairn", r"(?=\s+[A-Za-z_]\w*\s*;)"),
    "fold": ("keyword.control.flow.cairn", r"(?=\s*(?:[-+*&|^]|[A-Za-z_][\w.]*\s+each\b))"),
    "grain": ("keyword.other.plan.cairn", r"(?=\s+[0-9])"),
    "lanes": ("keyword.other.plan.cairn", r"(?=\s+[0-9])"),
    "plan": ("keyword.declaration.cairn", r"(?=\s+[A-Za-z_][\w.]*\s*\{)"),
    "recipe": ("keyword.declaration.cairn", r"(?=\s+[A-Za-z_]\w*\s*[\[({]|\s+[A-Za-z_]\w*\s+for\b)"),
    "require": ("keyword.control.flow.cairn", r"(?=\s+\S[^;]*,\s*\")"),
    "packed": ("storage.modifier.layout.cairn", r"(?=\s*\{)"),
    "align": ("storage.modifier.layout.cairn", r"(?=\s*\(\s*[0-9]+\s*\)\s*\{)"),
}
FAMILIES = (*(f.rstrip(":") for f in EFFECT_FAMILIES), "read", "write", "lane")  # as effects.py names them
TYPES = sorted(SCALAR | STORAGE.keys() | {"void"} | {n for n in INTRINSIC_TYPES if n[0].isupper()})
BUILTINS = sorted(n for n in TABLE if n not in TYPES)
BOUNDS = sorted(set(KINDS) | set(CLASSES))


def unclassified() -> set[str]:
    """Reserved words that no class claims: the generator refuses to write a grammar that misses one."""
    return RESERVED - {w for _, _, words in WORDS.values() for w in words}


def library_variants() -> list[str]:
    """The variants of every sum the packaged library declares: `Some`, `None`, `Ok`, `Err`, ..."""
    names: set[str] = set()
    for path in sorted(STD.glob("*.cairn")):
        program = Parser(path.read_text(encoding="utf-8")).parse()
        names |= {v for variants in program.sums.values() for v, _ in variants}
        names |= {v for variants in program.enums.values() for v in variants}
    return sorted(names)


def alternation(words) -> str:
    return "|".join(sorted(words, key=lambda w: (-len(w), w)))


def keyword(words, scope: str) -> dict:
    return {"name": scope, "match": rf"\b(?:{alternation(words)})\b"}


def captured(match: str, *scopes: str) -> dict:
    return {"match": match, "captures": {str(i): {"name": s} for i, s in enumerate(scopes, 1) if s}}


IDENT = r"[A-Za-z_]\w*"
GENERIC = r"(?:\[(?:[^\[\]]|\[[^\[\]]*\])*\])?"  # `f[u64](`, `new[Vec[u8]](`: one level of nesting


def textmate() -> dict:
    """The TextMate grammar. Earlier patterns win at the same position, so the specific precede the general."""
    variants = library_variants()
    r: dict[str, dict] = {
        "comments": {
            "patterns": [
                {
                    "name": "comment.line.double-slash.cairn",
                    "begin": "//",
                    "end": "$",
                    "patterns": [
                        {"name": "constant.other.diagnostic-code.cairn", "match": r"\bE-[A-Z0-9]+(?:-[A-Z0-9]+)*\b"}
                    ],
                }
            ]
        },
        "strings": {
            "patterns": [
                {
                    "name": f"string.quoted.{kind}.cairn",
                    "begin": quote,
                    "end": quote,
                    "patterns": [
                        {"name": "constant.character.escape.cairn", "match": r"\\(?:x[0-9A-Fa-f]{2}|[ntr0\\\"'])"},
                        {"name": "invalid.illegal.escape.cairn", "match": r"\\."},
                    ],
                }
                for kind, quote in (("double", '"'), ("single", "'"))
            ]
        },
        "unsafe": {
            "patterns": [
                {
                    "name": "meta.block.unsafe.cairn",
                    "begin": r"\b(unsafe)\s*(\{)",
                    "beginCaptures": {
                        "1": {"name": "keyword.other.unsafe.cairn"},
                        "2": {"name": "punctuation.section.block.begin.cairn"},
                    },
                    "end": r"\}",
                    "endCaptures": {"0": {"name": "punctuation.section.block.end.cairn"}},
                    "patterns": [{"include": "#braces"}, {"include": "$self"}],
                }
            ]
        },
        "braces": {
            "patterns": [{"begin": r"\{", "end": r"\}", "patterns": [{"include": "#braces"}, {"include": "$self"}]}]
        },
        "placements": {
            "patterns": [
                captured(
                    rf"(@)({alternation(PLACES)})\b",
                    "punctuation.definition.placement.cairn",
                    "storage.modifier.placement.cairn",
                )
            ]
        },
        "effects": {
            "patterns": [
                {
                    "begin": r"\b(effects)\s*(\()",
                    "beginCaptures": {
                        "1": {"name": "storage.modifier.cairn"},
                        "2": {"name": "punctuation.section.parens.begin.cairn"},
                    },
                    "end": r"\)",
                    "endCaptures": {"0": {"name": "punctuation.section.parens.end.cairn"}},
                    "patterns": [
                        captured(
                            rf"\b({alternation(FAMILIES)})(:)({IDENT})",
                            "support.constant.effect.cairn",
                            "punctuation.separator.effect.cairn",
                            "variable.other.effect.cairn",
                        ),
                        keyword(EFFECTS, "support.constant.effect.cairn"),
                        {"name": "punctuation.separator.comma.cairn", "match": ","},
                    ],
                }
            ]
        },
        "declarations": {
            "patterns": [
                captured(
                    rf"\b(fn|kernel)\s+({IDENT})", "keyword.declaration.function.cairn", "entity.name.function.cairn"
                ),
                captured(
                    rf"\b(struct|enum|trait)\s+({IDENT})", "keyword.declaration.type.cairn", "entity.name.type.cairn"
                ),
                captured(
                    rf"\b(module|import)\s+({IDENT}(?:\.{IDENT})*)",
                    "keyword.declaration.module.cairn",
                    "entity.name.namespace.cairn",
                ),
                captured(rf"\b(as)\s+({IDENT})(?=\s*;)", "storage.modifier.cairn", "entity.name.namespace.cairn"),
                captured(
                    rf"\b(const)\s+({IDENT})", "keyword.declaration.constant.cairn", "variable.other.constant.cairn"
                ),
                captured(
                    rf"\b(family)\s+({IDENT})", "keyword.declaration.function.cairn", "entity.name.function.cairn"
                ),
                captured(rf"\b(recipe)\s+({IDENT})", "keyword.declaration.cairn", "entity.name.function.recipe.cairn"),
                captured(
                    rf"\b(plan)\s+({IDENT}(?:\.{IDENT})*)(?=\s*\{{)",
                    "keyword.declaration.cairn",
                    "entity.name.function.cairn",
                ),
                captured(
                    rf"\b(derive)\s+({IDENT}(?:\.{IDENT})*)",
                    "keyword.declaration.cairn",
                    "entity.name.function.recipe.cairn",
                ),
                captured(
                    rf"\b(let|reg|buffer|stack)\s+(?:(mut)\s+)?({IDENT})",
                    "storage.type.binding.cairn",
                    "storage.modifier.cairn",
                    "variable.other.declaration.cairn",
                ),
                captured(
                    rf"\b(for|each)\s+({IDENT})(?=\s+in\b)",
                    "keyword.control.loop.cairn",
                    "variable.other.declaration.cairn",
                ),
                captured(
                    rf"\b(parallel)\s+({IDENT})(?=\s+in\b)",
                    "keyword.control.concurrency.cairn",
                    "variable.other.declaration.cairn",
                ),
            ]
        },
        "bindings": {
            "patterns": [
                captured(
                    r"(?<=[\[(,|])\s*([A-Z]\w*)\s*(:)",
                    "entity.name.type.parameter.cairn",
                    "punctuation.separator.type.cairn",
                ),
                captured(
                    r"(?<=[(,|])\s*([a-z_]\w*)\s*(:)", "variable.parameter.cairn", "punctuation.separator.type.cairn"
                ),
                captured(
                    r"(?<=[{;])\s*([a-z_]\w*)\s*(:)",
                    "variable.other.property.cairn",
                    "punctuation.separator.type.cairn",
                ),
                {
                    "name": "support.type.bound.cairn",
                    "match": rf"(?<=[:+])\s*(?:{alternation([*BOUNDS, 'nat', 'type'])})\b",
                },
            ]
        },
        "borrows": {
            "patterns": [
                {
                    "begin": r"\b(ro|rw)\s*(<)",
                    "beginCaptures": {
                        "1": {"name": "storage.modifier.borrow.cairn"},
                        "2": {"name": "punctuation.definition.typeparameters.begin.cairn"},
                    },
                    "end": ">",
                    "endCaptures": {"0": {"name": "punctuation.definition.typeparameters.end.cairn"}},
                    "patterns": [{"include": "$self"}],
                }
            ]
        },
        "context": {
            "patterns": [
                {"name": scope, "match": rf"\b{word}\b{ahead}"} for word, (scope, ahead) in sorted(CONTEXT.items())
            ]
        },
        "keywords": {"patterns": [keyword(words, scope) for scope, _, words in WORDS.values()]},
        "numbers": {
            "patterns": [
                {"name": "constant.numeric.integer.hexadecimal.cairn", "match": r"\b0x[0-9A-Fa-f]+\b"},
                {
                    "name": "constant.numeric.float.cairn",
                    "match": r"\b[0-9]+(?:\.[0-9]+)?[eE][+-]?[0-9]+\b|\b[0-9]+\.[0-9]+\b",
                },
                {"name": "constant.numeric.integer.decimal.cairn", "match": r"\b[0-9]+\b"},
            ]
        },
        "variants": {
            "patterns": [
                captured(
                    r"\b([A-Z]\w*)(\.)([A-Z]\w*)\b",
                    "entity.name.type.cairn",
                    "punctuation.accessor.cairn",
                    "variable.other.enummember.cairn",
                ),
                {"name": "variable.other.enummember.cairn", "match": rf"(?<![.\w$])(?:{alternation(variants)})\b"},
            ]
        },
        "types": {
            "patterns": [
                {"name": "variable.language.self.cairn", "match": r"\b(?:self|Self)\b"},
                {"name": "support.type.cairn", "match": rf"\b(?:{alternation(TYPES)})\b"},
                {"name": "variable.other.constant.cairn", "match": r"\b[A-Z][A-Z0-9_]*[A-Z0-9]\b(?![\[(.])"},
                {"name": "entity.name.type.cairn", "match": r"\b[A-Z]\w*\b"},
            ]
        },
        "calls": {
            "patterns": [
                {
                    "name": "support.function.builtin.cairn",
                    "match": rf"\b(?:{alternation(BUILTINS)})\b(?={GENERIC}\s*\()",
                },
                {"name": "entity.name.function.member.cairn", "match": rf"(?<=\.)[a-z_]\w*(?={GENERIC}\s*\()"},
                {"name": "entity.name.function.call.cairn", "match": rf"\b[a-z_]\w*(?={GENERIC}\s*\()"},
                {"name": "variable.other.metavariable.cairn", "match": r"\$[A-Za-z_]\w*"},
            ]
        },
        "operators": {
            "patterns": [
                {"name": "keyword.operator.comparison.cairn", "match": r"==|!=|<=|>="},
                {"name": "keyword.operator.arrow.cairn", "match": r"=>|->"},
                {"name": "keyword.operator.assignment.compound.cairn", "match": r"[-+*/%&|^]="},
                {"name": "keyword.operator.logical.cairn", "match": r"&&|\|\||!"},
                {"name": "keyword.operator.range.cairn", "match": r"\.\."},
                {"name": "keyword.operator.assignment.cairn", "match": r"="},
                {"name": "keyword.operator.comparison.cairn", "match": r"[<>]"},
                {"name": "keyword.operator.arithmetic.cairn", "match": r"[-+*/%]"},
                {"name": "keyword.operator.bitwise.cairn", "match": r"[&|^~]"},
                {"name": "punctuation.terminator.statement.cairn", "match": ";"},
                {"name": "punctuation.separator.comma.cairn", "match": ","},
                {"name": "punctuation.accessor.cairn", "match": r"\."},
                {"name": "punctuation.separator.type.cairn", "match": ":"},
            ]
        },
    }
    order = ["comments", "strings", "unsafe", "placements", "effects", "declarations", "bindings", "borrows"]
    order += ["context", "keywords", "numbers", "variants", "types", "calls", "operators"]
    return {
        "$schema": "https://raw.githubusercontent.com/martinring/tmlanguage/master/tmlanguage.json",
        "name": "CAIRN",
        "scopeName": "source.cairn",
        "comment": "Generated by `python -m cairn.editor.grammar` from the compiler's vocabulary; do not edit.",
        "patterns": [{"include": "#" + name} for name in order],
        "repository": r,
    }


def vim() -> str:
    """The Vim syntax file: the same classes as Vim's standard highlight groups."""
    lines = [
        '" Vim syntax file for CAIRN. Generated by `python -m cairn.editor.grammar`; do not edit.',
        'if exists("b:current_syntax") | finish | endif',
        "syntax case match",
        "syntax iskeyword @,48-57,_,$",
        "",
    ]
    groups: dict[str, str] = {}
    for name, (_, group, words) in WORDS.items():  # `effects(` opens a region below, which a keyword would hide
        cls = "cairn" + name.capitalize()
        lines.append(f"syntax keyword {cls} {' '.join(sorted(words - {'effects'}))}")
        groups[cls] = group
    lines += [
        f"syntax keyword cairnType {' '.join(TYPES)}",
        f"syntax keyword cairnVariant {' '.join(library_variants())}",
        f"syntax match cairnBuiltin /\\v<({'|'.join(BUILTINS)})>\\ze\\s*[[(]/",
        "syntax match cairnCall /\\v<\\l\\w*\\ze\\s*\\(/",  # of two matches at one place, Vim takes the later
        "syntax match cairnFunction /\\v(<(fn|kernel)\\s+)@<=\\h\\w*/",
        "syntax match cairnUserType /\\v<\\u\\w*>/",
        "syntax match cairnConstant /\\v<\\u[A-Z0-9_]*[A-Z0-9]>/",
        "syntax match cairnEnumMember /\\v(<\\u\\w*\\.)@<=\\u\\w*>/",
        "syntax match cairnSelf /\\v<(self|Self)>/",
        f"syntax match cairnPlacement /\\v\\@({'|'.join(PLACES)})>/",
        f'syntax match cairnContext /\\v<({"|".join(sorted(CONTEXTUAL - {"packed", "align"}))})>\\ze\\s+[[:alnum:]_"$+*&|^-]/',
        "syntax match cairnContext /\\v<packed>\\ze\\s*\\{/",
        "syntax match cairnContext /\\v<align>\\ze\\s*\\(/",
        "syntax match cairnNumber /\\v<(0x\\x+|\\d+(\\.\\d+)?([eE][+-]?\\d+)?)>/",
        "syntax match cairnMeta /\\v\\$\\h\\w*/",
        "syntax match cairnOperator /\\v[-+*\\/%&|^]\\=|\\=\\>|-\\>|\\.\\./",
        "syntax match cairnEscape /\\v\\\\(x\\x{2}|[ntr0\\\\\"'])/ contained",
        'syntax region cairnString start=/"/ skip=/\\\\./ end=/"/ contains=cairnEscape',
        "syntax region cairnChar start=/'/ skip=/\\\\./ end=/'/ contains=cairnEscape",
        f"syntax keyword cairnEffect contained {' '.join(sorted(EFFECTS | set(FAMILIES)))}",
        "syntax region cairnEffects matchgroup=cairnModifier start=/\\v<effects\\s*\\(/ end=/)/ contains=cairnEffect",
        "syntax match cairnDiagnostic /\\v<E(-[A-Z0-9]+)+>/ contained",
        "syntax region cairnComment start=+//+ end=/$/ contains=cairnDiagnostic,@Spell",
        "syntax sync minlines=50",
        "",
    ]
    groups |= {
        "cairnType": "Type", "cairnUserType": "Type", "cairnVariant": "Constant", "cairnEnumMember": "Constant",
        "cairnBuiltin": "Function", "cairnFunction": "Function", "cairnCall": "Function", "cairnConstant": "Constant",
        "cairnSelf": "Identifier", "cairnPlacement": "PreProc", "cairnContext": "Keyword", "cairnNumber": "Number",
        "cairnMeta": "Special", "cairnOperator": "Operator", "cairnEscape": "SpecialChar", "cairnString": "String",
        "cairnChar": "Character", "cairnEffect": "Special", "cairnDiagnostic": "SpecialComment",
        "cairnComment": "Comment",
    }  # fmt: skip
    lines += [f"highlight default link {cls} {group}" for cls, group in groups.items()]
    return "\n".join([*lines, "", 'let b:current_syntax = "cairn"', ""])


FTDETECT = "autocmd BufRead,BufNewFile *.cairn setfiletype cairn\n"
FTPLUGIN = """\" CAIRN buffers: two-space indent, `//` comments. Generated by `python -m cairn.editor.grammar`.
if exists("b:did_ftplugin") | finish | endif
let b:did_ftplugin = 1
setlocal expandtab shiftwidth=2 softtabstop=2 commentstring=//\\ %s comments=://
let b:undo_ftplugin = "setlocal expandtab< shiftwidth< softtabstop< commentstring< comments<"
"""


def generated() -> dict[str, str]:
    """Every file this module owns, by path from the repository root."""
    return {
        "editors/vscode/syntaxes/cairn.tmLanguage.json": json.dumps(textmate(), indent=1) + "\n",
        "editors/vim/syntax/cairn.vim": vim(),
        "editors/vim/ftdetect/cairn.vim": FTDETECT,
        "editors/vim/ftplugin/cairn.vim": FTPLUGIN,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cairn.editor.grammar", description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail when a committed grammar differs from a fresh run")
    args = parser.parse_args(argv)
    if unclassified():
        print(f"Reserved words without a class in WORDS: {', '.join(sorted(unclassified()))}", file=sys.stderr)
        return 2
    differ = []
    for relative, text in generated().items():
        path = ROOT / relative
        if args.check:
            differ += [] if path.is_file() and path.read_text(encoding="utf-8") == text else [relative]
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    if differ:
        print(json.dumps({"in_sync": False, "differ": differ, "regenerate": "make editors"}, indent=2))
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
