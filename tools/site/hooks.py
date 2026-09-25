"""The MkDocs hooks that turn `docs/` into the documentation site, with the Markdown files left as GitHub shows them.

`mkdocs build --strict` (`make site`) runs them. They do three things:

- highlight CAIRN code with a Pygments lexer written from the compiler's own vocabulary, the table
  `cairn.editor.grammar` writes the editor grammars from, so the site colours every word the editors do;
- label a block tagged `cairn rejects E-CODE` with the code the compiler refuses it with, and show a
  `cairn fragment` block as CAIRN;
- point a link to anything but a page of the site (`../README.md`, `../examples/hello`, `project/capabilities.json`)
  at the file on GitHub, since the site holds only the pages.
"""

from __future__ import annotations

import posixpath
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from pygments import lexers
from pygments.lexer import RegexLexer, bygroups, words
from pygments.token import Comment, Keyword, Name, Number, Operator, Punctuation, String, Text, Whitespace

from cairn.compiler.syntax.tree import PLACES
from cairn.editor.grammar import BOUNDS, BUILTINS, CONTEXT, TYPES, WORDS, library_variants

STYLE = Path(__file__).with_name("site.css")
FENCE = re.compile(r"^(\s*)```cairn (?:rejects (E-[A-Z0-9-]+)|fragment)\s*$", re.M)
LINK = re.compile(r"(!?)\[((?:[^\[\]]|\[[^\]]*\])*)\]\(([^()\s]+)((?:\s+\"[^\"]*\")?)\)")
CODE = re.compile(r"(`+)(?:.|\n)*?\1")
KINDS = {
    "declaration": Keyword.Declaration,
    "binding": Keyword.Declaration,
    "modifier": Keyword.Type,
    "constant": Keyword.Constant,
}


def cairn_lexer() -> type[RegexLexer]:
    """A lexer whose words are the grammar's: each class of reserved word, the contextual words with the lookahead
    that makes each one a keyword, the types, the builtins, the bounds, the placements and the library's variants."""
    root: list = [
        (r"\s+", Whitespace),
        (r"//.*?$", Comment.Single),
        (r'"(?:\\.|[^"\\])*"', String.Double),
        (r"'(?:\\.|[^'\\])*'", String.Char),
        (r"\b(fn)(\s+)([A-Za-z_]\w*)", bygroups(Keyword.Declaration, Whitespace, Name.Function)),
        (
            r"\b(struct|enum|trait|family|module)(\s+)([A-Za-z_][\w.]*)",
            bygroups(Keyword.Declaration, Whitespace, Name.Class),
        ),
    ]
    for kind, (_, _, names) in WORDS.items():
        root.append((words(sorted(names), prefix=r"\b", suffix=r"\b"), KINDS.get(kind, Keyword)))
    for word, (_, ahead) in sorted(CONTEXT.items()):
        root.append((rf"\b{word}\b{ahead}", Keyword.Reserved))
    root += [
        (words(TYPES, prefix=r"\b", suffix=r"\b"), Keyword.Type),
        (words(BOUNDS, prefix=r"\b", suffix=r"\b"), Name.Builtin.Pseudo),
        (words(BUILTINS, prefix=r"\b", suffix=r"\b"), Name.Builtin),
        (words(library_variants(), prefix=r"\b", suffix=r"\b"), Name.Constant),
        (rf"@(?:{'|'.join(sorted(PLACES, key=len, reverse=True))})\b", Name.Decorator),
        (r"\b0x[0-9A-Fa-f_]+\b|\b0b[01_]+\b", Number.Hex),
        (r"\b\d[\d_]*\.\d[\d_]*(?:[eE][+-]?\d+)?\b|\b\d[\d_]*[eE][+-]?\d+\b", Number.Float),
        (r"\b\d[\d_]*\b", Number.Integer),
        (r"[A-Za-z_]\w*", Name),
        (r"\.\.|->|=>|[-+*/%&|^!~<>=]=?|&&|\|\||<<|>>|::|\?", Operator),
        (r"[{}()\[\];,.:@]", Punctuation),
        (r".", Text),
    ]
    return type("CairnLexer", (RegexLexer,), {"name": "CAIRN", "aliases": ["cairn"], "filenames": ["*.cairn"],
                                              "tokens": {"root": root}})  # fmt: skip


def listed(nav):
    """The navigation with its `std/` entry spelled out: one page per module `make docs` writes under docs/std/."""
    if isinstance(nav, list):
        return [listed(item) for item in nav]
    if isinstance(nav, dict):
        return {title: listed(item) for title, item in nav.items()}
    if nav == "std/":
        return [f"std/{page.name}" for page in sorted((ROOT / "docs" / "std").glob("*.md"))]
    return nav


def on_config(config):
    """Register the lexer where `get_lexer_by_name("cairn")` looks, Pygments' table of lexers and its cache; list the
    library's module pages; and add the site's stylesheet."""
    config["nav"] = listed(config["nav"])
    lexer = cairn_lexer()
    lexers.LEXERS["CairnLexer"] = (__name__, lexer.name, tuple(lexer.aliases), tuple(lexer.filenames), ())
    lexers._lexer_cache[lexer.name] = lexer
    config["extra_css"] = [*config["extra_css"], "assets/cairn.css"]
    return config


def github(config, path: str, image: bool) -> str:
    """Where GitHub shows a file of the repository: the rendered page, a folder's listing, or an image's bytes."""
    repo = config.repo_url.rstrip("/")
    if image:
        return repo.replace("https://github.com/", "https://raw.githubusercontent.com/") + f"/main/{path}"
    return f"{repo}/{'tree' if (ROOT / path).is_dir() else 'blob'}/main/{path}"


def outside(text: str, page, config) -> str:
    """Prose with every relative link that leaves `docs/` pointed at GitHub; code spans are left as written."""
    here = posixpath.dirname(page.file.src_uri)

    def moved(found: re.Match) -> str:
        bang, label, target, title = found.groups()
        path, hashed, fragment = target.partition("#")
        if not path or re.match(r"[a-z][a-z0-9+.-]*:", path) or path.startswith("/"):
            return found.group(0)
        inside = posixpath.normpath(posixpath.join("docs", here, path))
        if inside.startswith("docs/") and (inside.endswith(".md") and not inside.startswith("docs/project/")):
            return found.group(0)
        return f"{bang}[{label}]({github(config, inside, bool(bang))}{hashed}{fragment}{title})"

    pieces, at = [], 0
    for span in CODE.finditer(text):
        pieces += [LINK.sub(moved, text[at : span.start()]), span.group(0)]
        at = span.end()
    return "".join([*pieces, LINK.sub(moved, text[at:])])


def on_page_markdown(markdown: str, page, config, files) -> str:
    out, fenced = [], False
    for block in re.split(r"(^\s*```.*$)", markdown, flags=re.M):
        if re.fullmatch(r"\s*```.*", block):
            fenced = not fenced
            if found := FENCE.fullmatch(block):
                indent, code = found.groups()
                block = (
                    f'{indent}``` {{ .cairn .refused title="Refused with {code}" }}' if code else f"{indent}```cairn"
                )
            out.append(block)
        else:
            out.append(block if fenced else outside(block, page, config))
    return "".join(out)


def on_post_build(config) -> None:
    target = Path(config.site_dir) / "assets" / "cairn.css"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(STYLE.read_text(encoding="utf-8"), encoding="utf-8")
