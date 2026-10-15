"""Block model: parse Python source into blocks and generate source back.

This module has no GUI dependency. A ``Block`` is one statement. Nested
statements live in named *slots* (``body``, ``orelse``, ``handlers``,
``finalbody``, ``cases``). Expression-level content is kept as editable text
in ``fields``.
"""
from __future__ import annotations

import ast
import itertools
from dataclasses import dataclass, field
from typing import Any, Callable

_ids = itertools.count(1)
INDENT = "    "


# --------------------------------------------------------------------------
# Block specifications
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class BlockSpec:
    kind: str
    category: str
    fields: tuple[tuple[str, str], ...]   # (field name, default value)
    slots: tuple[str, ...] = ()
    help: str = ""


def _spec(kind, category, fields=(), slots=(), help=""):
    return BlockSpec(kind, category, tuple(fields), tuple(slots), help)


SPECS: dict[str, BlockSpec] = {s.kind: s for s in [
    _spec("def", "definition",
          [("async", ""), ("decorators", ""), ("name", "function"), ("typeparams", ""),
           ("args", ""), ("returns", "")], ["body"],
          "Function definition. 'async' may be empty or the word async."),
    _spec("class", "definition",
          [("decorators", ""), ("name", "ClassName"), ("typeparams", ""), ("bases", "")],
          ["body"], "Class definition. 'bases' is a comma separated list."),
    _spec("if", "flow", [("test", "condition")], ["body", "orelse"]),
    _spec("elif", "flow", [("test", "condition")], ["body", "orelse"],
          "Only valid as the single block in the else slot of an if/elif."),
    _spec("for", "loop", [("async", ""), ("target", "item"), ("iter", "items")],
          ["body", "orelse"]),
    _spec("while", "loop", [("test", "condition")], ["body", "orelse"]),
    _spec("try", "error", [("star", "")], ["body", "handlers", "orelse", "finalbody"],
          "'star' may be empty or * for except* groups."),
    _spec("except", "error", [("type", "Exception"), ("name", "")], ["body"],
          "Only valid inside the handlers slot of a try."),
    _spec("with", "context", [("async", ""), ("items", 'open("file") as f')], ["body"]),
    _spec("match", "flow", [("subject", "value")], ["cases"]),
    _spec("case", "flow", [("pattern", "_"), ("guard", "")], ["body"],
          "Only valid inside the cases slot of a match."),
    _spec("return", "simple", [("value", "")]),
    _spec("assign", "simple", [("targets", "name"), ("value", "value")],
          help="Chained targets are written 'a = b'."),
    _spec("augassign", "simple", [("target", "name"), ("op", "+"), ("value", "1")]),
    _spec("annassign", "simple", [("target", "name"), ("annotation", "int"), ("value", "")]),
    _spec("call", "simple", [("value", "print()")]),
    _spec("expr", "simple", [("value", "...")]),
    _spec("pass", "simple"),
    _spec("break", "simple"),
    _spec("continue", "simple"),
    _spec("raise", "error", [("exc", ""), ("cause", "")]),
    _spec("assert", "error", [("test", "condition"), ("msg", "")]),
    _spec("import", "import", [("names", "module")]),
    _spec("from", "import", [("module", "module"), ("names", "name")],
          help="Leading dots on 'module' make a relative import."),
    _spec("global", "scope", [("names", "name")]),
    _spec("nonlocal", "scope", [("names", "name")]),
    _spec("del", "simple", [("targets", "name")]),
    _spec("type", "definition", [("name", "Alias"), ("typeparams", ""), ("value", "int")]),
]}

@dataclass
class Language:
    """Everything the editor needs to know about one programming language."""
    name: str
    label: str
    extensions: tuple[str, ...]
    specs: dict[str, BlockSpec]
    docs: dict[str, tuple[str, dict[str, str]]]
    palette: list
    palette_extra: list
    functions: list
    slot_hosts: dict[str, str]        # slot -> container kind that owns it
    slot_fillers: dict[str, str]      # slot -> block kind that belongs in it
    placeholder_kind: str             # a do-nothing statement kind
    def_kinds: frozenset
    loop_kinds: frozenset
    terminators: frozenset
    parse: Callable[[str], list]
    generate: Callable[[list, dict], str]
    syntax_error: Callable[[str], SyntaxError | None]
    title: Callable[[Any], str]
    keyword: Callable[[Any], str]
    describe_io: Callable[[Any], tuple[str, str]]
    called_names: Callable[[Any], list[str]]
    function_block: Callable[[Any], Any]
    note: str = ""
    slot_labels: dict = field(default_factory=dict)   # overrides of SLOT_LABELS


def slot_label(block: "Block", slot: str) -> str:
    return LANGUAGES[block.lang].slot_labels.get(slot, SLOT_LABELS[slot])


LANGUAGES: dict[str, Language] = {}


def language_for_path(path: str) -> str | None:
    ext = ("." + path.rsplit(".", 1)[-1].lower()) if "." in path else ""
    for lang in LANGUAGES.values():
        if ext in lang.extensions:
            return lang.name
    return None


SLOT_LABELS = {
    "body": "body", "orelse": "else", "handlers": "except",
    "finalbody": "finally", "cases": "cases",
}

# Slots that must contain at least one statement when emitted.
_REQUIRED_SLOTS = {"body", "cases"}


# --------------------------------------------------------------------------
# Block
# --------------------------------------------------------------------------
@dataclass(eq=False)
class Block:
    kind: str
    fields: dict[str, str] = field(default_factory=dict)
    slots: dict[str, list["Block"]] = field(default_factory=dict)
    desc_in: str = ""
    desc_out: str = ""
    lang: str = "python"
    expanded: bool = False      # show the full text of a long block
    x: float = 0.0
    y: float = 0.0
    parent: "Block | None" = None
    slot_name: str | None = None
    id: int = field(default_factory=lambda: next(_ids))
    # layout cache, filled by vcb.layout
    w: float = 0.0
    h: float = 0.0
    slot_rects: dict = field(default_factory=dict, repr=False)
    desc_lines: list = field(default_factory=list, repr=False)

    @property
    def spec(self) -> BlockSpec:
        return LANGUAGES[self.lang].specs[self.kind]

    def children(self):
        for name in self.spec.slots:
            yield from self.slots.get(name, [])

    def title(self) -> str:
        """Short one-line header text for the block."""
        return LANGUAGES[self.lang].title(self)


def _py_title(self: Block) -> str:  # noqa: C901 - one branch per kind
    f = self.fields
    k = self.kind
    pre = (f.get("async", "") + " ").lstrip() if f.get("async") else ""
    if k == "def":
        ret = f" -> {f['returns']}" if f.get("returns") else ""
        return f"{pre}def {f['name']}({f['args']}){ret}"
    if k == "class":
        bases = f"({f['bases']})" if f.get("bases") else ""
        return f"class {f['name']}{bases}"
    if k in ("if", "elif", "while"):
        return f"{k} {f['test']}"
    if k == "for":
        return f"{pre}for {f['target']} in {f['iter']}"
    if k == "try":
        return "try" + ("*" if f.get("star") else "")
    if k == "except":
        name = f" as {f['name']}" if f.get("name") else ""
        return f"except {f['type']}{name}".rstrip()
    if k == "with":
        return f"{pre}with {f['items']}"
    if k == "match":
        return f"match {f['subject']}"
    if k == "case":
        guard = f" if {f['guard']}" if f.get("guard") else ""
        return f"case {f['pattern']}{guard}"
    if k == "return":
        return f"return {f['value']}".rstrip()
    if k == "raise":
        cause = f" from {f['cause']}" if f.get("cause") else ""
        return f"raise {f['exc']}{cause}".rstrip()
    if k == "assert":
        msg = f", {f['msg']}" if f.get("msg") else ""
        return f"assert {f['test']}{msg}"
    if k == "import":
        return f"import {f['names']}"
    if k == "from":
        return f"from {f['module']} import {f['names']}"
    if k in ("global", "nonlocal"):
        return f"{k} {f['names']}"
    if k == "del":
        return f"del {f['targets']}"
    if k == "type":
        tp = f"[{f['typeparams']}]" if f.get("typeparams") else ""
        return f"type {f['name']}{tp} = {f['value']}"
    if k == "assign":
        return f"{f['targets']} = {f['value']}"
    if k == "augassign":
        return f"{f['target']} {f['op']}= {f['value']}"
    if k == "annassign":
        val = f" = {f['value']}" if f.get("value") else ""
        return f"{f['target']}: {f['annotation']}{val}"
    if k in ("call", "expr"):
        return f["value"]
    return k


def new_block(kind: str, lang: str = "python", **overrides: str) -> Block:
    spec = LANGUAGES[lang].specs[kind]
    fields = {name: default for name, default in spec.fields}
    fields.update(overrides)
    return Block(kind, fields=fields, slots={s: [] for s in spec.slots}, lang=lang)


def walk(blocks):
    """Depth-first iteration over blocks and all nested blocks."""
    for b in blocks:
        yield b
        yield from walk(b.children())


def insert_block(parent: Block, slot: str, child: Block, index: int | None = None):
    if child.parent is not None:
        detach_block(child)
    lst = parent.slots.setdefault(slot, [])
    if index is None or index > len(lst):
        index = len(lst)
    lst.insert(index, child)
    child.parent = parent
    child.slot_name = slot


def detach_block(child: Block):
    if child.parent is None:
        return
    lst = child.parent.slots.get(child.slot_name, [])
    if child in lst:
        lst.remove(child)
    child.parent = None
    child.slot_name = None


def is_ancestor(maybe_ancestor: Block, block: Block) -> bool:
    cur = block.parent
    while cur is not None:
        if cur is maybe_ancestor:
            return True
        cur = cur.parent
    return False


def ordered_top_level(blocks) -> list[Block]:
    """Module order is top-to-bottom, then left-to-right, on the canvas."""
    return sorted(blocks, key=lambda b: (b.y, b.x, b.id))


def clone_block(block: Block) -> Block:
    """Deep copy of a block and its children with fresh ids and no parent."""
    copy = Block(block.kind, fields=dict(block.fields),
                 desc_in=block.desc_in, desc_out=block.desc_out,
                 lang=block.lang, expanded=block.expanded, x=block.x, y=block.y)
    copy.slots = {s: [] for s in block.spec.slots}
    for name in block.spec.slots:
        for child in block.slots.get(name, []):
            insert_block(copy, name, clone_block(child))
    return copy


# --------------------------------------------------------------------------
# Parsing: source -> blocks
# --------------------------------------------------------------------------
_AUG_OPS = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.MatMult: "@", ast.Div: "/",
    ast.Mod: "%", ast.Pow: "**", ast.LShift: "<<", ast.RShift: ">>",
    ast.BitOr: "|", ast.BitXor: "^", ast.BitAnd: "&", ast.FloorDiv: "//",
}


def parse_source(src: str, lang: str = "python") -> list[Block]:
    return LANGUAGES[lang].parse(src)


def _py_parse_source(src: str) -> list[Block]:
    tree = ast.parse(src)
    return [_stmt_to_block(node) for node in tree.body]


def _join(nodes) -> str:
    return ", ".join(ast.unparse(n) for n in nodes)


def _typeparams(node) -> str:
    return _join(getattr(node, "type_params", []) or [])


def _fill(block: Block, slot: str, nodes):
    for n in nodes:
        insert_block(block, slot, _stmt_to_block(n))


def _if_block(node: ast.If, kind: str) -> Block:
    b = new_block(kind, test=ast.unparse(node.test))
    _fill(b, "body", node.body)
    orelse = node.orelse
    if (len(orelse) == 1 and isinstance(orelse[0], ast.If)
            and orelse[0].col_offset == node.col_offset):
        insert_block(b, "orelse", _if_block(orelse[0], "elif"))
    else:
        _fill(b, "orelse", orelse)
    return b


def _stmt_to_block(node: ast.stmt) -> Block:  # noqa: C901 - one branch per kind
    u = ast.unparse
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        b = new_block(
            "def",
            **{"async": "async" if isinstance(node, ast.AsyncFunctionDef) else ""},
            decorators=_join(node.decorator_list), name=node.name,
            typeparams=_typeparams(node), args=u(node.args),
            returns=u(node.returns) if node.returns else "")
        _fill(b, "body", node.body)
        return b
    if isinstance(node, ast.ClassDef):
        bases = list(node.bases) + list(node.keywords)
        b = new_block("class", decorators=_join(node.decorator_list), name=node.name,
                      typeparams=_typeparams(node), bases=_join(bases))
        _fill(b, "body", node.body)
        return b
    if isinstance(node, ast.If):
        return _if_block(node, "if")
    if isinstance(node, (ast.For, ast.AsyncFor)):
        b = new_block("for", **{"async": "async" if isinstance(node, ast.AsyncFor) else ""},
                      target=u(node.target), iter=u(node.iter))
        _fill(b, "body", node.body)
        _fill(b, "orelse", node.orelse)
        return b
    if isinstance(node, ast.While):
        b = new_block("while", test=u(node.test))
        _fill(b, "body", node.body)
        _fill(b, "orelse", node.orelse)
        return b
    if isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
        star = "*" if type(node).__name__ == "TryStar" else ""
        b = new_block("try", star=star)
        _fill(b, "body", node.body)
        for h in node.handlers:
            hb = new_block("except", type=u(h.type) if h.type else "", name=h.name or "")
            _fill(hb, "body", h.body)
            insert_block(b, "handlers", hb)
        _fill(b, "orelse", node.orelse)
        _fill(b, "finalbody", node.finalbody)
        return b
    if isinstance(node, (ast.With, ast.AsyncWith)):
        b = new_block("with", **{"async": "async" if isinstance(node, ast.AsyncWith) else ""},
                      items=_join(node.items))
        _fill(b, "body", node.body)
        return b
    if isinstance(node, ast.Match):
        b = new_block("match", subject=u(node.subject))
        for c in node.cases:
            cb = new_block("case", pattern=u(c.pattern), guard=u(c.guard) if c.guard else "")
            _fill(cb, "body", c.body)
            insert_block(b, "cases", cb)
        return b
    if isinstance(node, ast.Return):
        return new_block("return", value=u(node.value) if node.value else "")
    if isinstance(node, ast.Assign):
        return new_block("assign", targets=" = ".join(u(t) for t in node.targets),
                         value=u(node.value))
    if isinstance(node, ast.AugAssign):
        return new_block("augassign", target=u(node.target), op=_AUG_OPS[type(node.op)],
                         value=u(node.value))
    if isinstance(node, ast.AnnAssign):
        return new_block("annassign", target=u(node.target), annotation=u(node.annotation),
                         value=u(node.value) if node.value else "")
    if isinstance(node, ast.Expr):
        kind = "call" if isinstance(node.value, ast.Call) else "expr"
        return new_block(kind, value=u(node.value))
    if isinstance(node, ast.Pass):
        return new_block("pass")
    if isinstance(node, ast.Break):
        return new_block("break")
    if isinstance(node, ast.Continue):
        return new_block("continue")
    if isinstance(node, ast.Raise):
        return new_block("raise", exc=u(node.exc) if node.exc else "",
                         cause=u(node.cause) if node.cause else "")
    if isinstance(node, ast.Assert):
        return new_block("assert", test=u(node.test), msg=u(node.msg) if node.msg else "")
    if isinstance(node, ast.Import):
        return new_block("import", names=_join(node.names))
    if isinstance(node, ast.ImportFrom):
        return new_block("from", module="." * node.level + (node.module or ""),
                         names=_join(node.names))
    if isinstance(node, ast.Global):
        return new_block("global", names=", ".join(node.names))
    if isinstance(node, ast.Nonlocal):
        return new_block("nonlocal", names=", ".join(node.names))
    if isinstance(node, ast.Delete):
        return new_block("del", targets=_join(node.targets))
    if hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias):
        return new_block("type", name=u(node.name), typeparams=_typeparams(node),
                         value=u(node.value))
    # Anything unknown is kept verbatim as an expression statement.
    return new_block("expr", value=u(node))


# --------------------------------------------------------------------------
# Generation: blocks -> source
# --------------------------------------------------------------------------
def generate_source(blocks, spans: dict[int, tuple[int, int]] | None = None,
                    lang: str | None = None) -> str:
    """Source text for the blocks, in their language.

    When ``spans`` is given it is filled with ``block.id -> (first, last)``
    line numbers, 1-based and inclusive, for every emitted block.
    """
    blocks = list(blocks)
    if lang is None:
        lang = blocks[0].lang if blocks else "python"
    return LANGUAGES[lang].generate(blocks, spans if spans is not None else {})


def _py_generate_source(blocks, spans: dict[int, tuple[int, int]] | None = None) -> str:
    lines: list[str] = []
    spans = spans if spans is not None else {}
    prev: Block | None = None
    for b in blocks:
        if prev is not None:
            big = {"def", "class"}
            if prev.kind in big or b.kind in big:
                lines.extend(["", ""])
            elif prev.spec.slots or b.spec.slots or prev.spec.category != b.spec.category:
                lines.append("")
        _emit(b, 0, lines, spans)
        prev = b
    return "\n".join(lines) + "\n"


def syntax_error(src: str, lang: str = "python") -> SyntaxError | None:
    return LANGUAGES[lang].syntax_error(src)


def _py_syntax_error(src: str) -> SyntaxError | None:
    try:
        ast.parse(src)
    except SyntaxError as e:
        return e
    return None


def _emit_slot(block: Block, slot: str, indent: int, lines: list[str], spans, required=True):
    kids = block.slots.get(slot, [])
    if kids:
        for k in kids:
            _emit(k, indent + 1, lines, spans)
    elif required:
        lines.append(INDENT * (indent + 1) + "pass")


def _emit_decorators(block: Block, ind: str, lines: list[str]):
    for d in split_top_level(block.fields.get("decorators", "")):
        lines.append(f"{ind}@{d}")


def _emit_if(block: Block, indent: int, lines: list[str], keyword: str, spans):
    ind = INDENT * indent
    start = len(lines) + 1
    lines.append(f"{ind}{keyword} {block.fields['test']}:")
    _emit_slot(block, "body", indent, lines, spans)
    orelse = block.slots.get("orelse", [])
    if len(orelse) == 1 and orelse[0].kind == "elif":
        _emit_if(orelse[0], indent, lines, "elif", spans)
    elif orelse:
        lines.append(f"{ind}else:")
        _emit_slot(block, "orelse", indent, lines, spans)
    spans[block.id] = (start, len(lines))


def _emit_except(block: Block, indent: int, lines: list[str], star: str, spans):
    ind = INDENT * indent
    start = len(lines) + 1
    f = block.fields
    head = f"{ind}except{star}"
    if f.get("type"):
        head += f" {f['type']}"
        if f.get("name"):
            head += f" as {f['name']}"
    lines.append(head + ":")
    _emit_slot(block, "body", indent, lines, spans)
    spans[block.id] = (start, len(lines))


def _emit_case(block: Block, indent: int, lines: list[str], spans):
    ind = INDENT * indent
    start = len(lines) + 1
    f = block.fields
    guard = f" if {f['guard']}" if f.get("guard") else ""
    lines.append(f"{ind}case {f['pattern']}{guard}:")
    _emit_slot(block, "body", indent, lines, spans)
    spans[block.id] = (start, len(lines))


def _emit(block: Block, indent: int, lines: list[str], spans):  # noqa: C901
    ind = INDENT * indent
    start = len(lines) + 1
    f = block.fields
    k = block.kind
    pre = f"{f['async']} " if f.get("async") else ""
    if k == "def":
        _emit_decorators(block, ind, lines)
        tp = f"[{f['typeparams']}]" if f.get("typeparams") else ""
        ret = f" -> {f['returns']}" if f.get("returns") else ""
        lines.append(f"{ind}{pre}def {f['name']}{tp}({f['args']}){ret}:")
        _emit_slot(block, "body", indent, lines, spans)
    elif k == "class":
        _emit_decorators(block, ind, lines)
        tp = f"[{f['typeparams']}]" if f.get("typeparams") else ""
        bases = f"({f['bases']})" if f.get("bases") else ""
        lines.append(f"{ind}class {f['name']}{tp}{bases}:")
        _emit_slot(block, "body", indent, lines, spans)
    elif k in ("if", "elif"):
        _emit_if(block, indent, lines, "if", spans)
    elif k == "for":
        lines.append(f"{ind}{pre}for {f['target']} in {f['iter']}:")
        _emit_slot(block, "body", indent, lines, spans)
        if block.slots.get("orelse"):
            lines.append(f"{ind}else:")
            _emit_slot(block, "orelse", indent, lines, spans)
    elif k == "while":
        lines.append(f"{ind}while {f['test']}:")
        _emit_slot(block, "body", indent, lines, spans)
        if block.slots.get("orelse"):
            lines.append(f"{ind}else:")
            _emit_slot(block, "orelse", indent, lines, spans)
    elif k == "try":
        star = "*" if f.get("star") else ""
        lines.append(f"{ind}try:")
        _emit_slot(block, "body", indent, lines, spans)
        handlers = block.slots.get("handlers", [])
        for h in handlers:
            if h.kind == "except":
                _emit_except(h, indent, lines, star, spans)
            else:  # a foreign block dropped in the handlers slot: wrap it
                lines.append(f"{ind}except Exception:")
                _emit(h, indent + 1, lines, spans)
        if block.slots.get("orelse"):
            if not handlers:
                lines.append(f"{ind}except Exception:")
                lines.append(f"{ind}{INDENT}pass")
            lines.append(f"{ind}else:")
            _emit_slot(block, "orelse", indent, lines, spans)
        if block.slots.get("finalbody") or not handlers:
            lines.append(f"{ind}finally:")
            _emit_slot(block, "finalbody", indent, lines, spans)
    elif k == "except":
        # Standalone handler: wrap so the output stays valid.
        lines.append(f"{ind}try:")
        lines.append(f"{ind}{INDENT}pass")
        _emit_except(block, indent, lines, "", spans)
    elif k == "with":
        lines.append(f"{ind}{pre}with {f['items']}:")
        _emit_slot(block, "body", indent, lines, spans)
    elif k == "match":
        lines.append(f"{ind}match {f['subject']}:")
        cases = block.slots.get("cases", [])
        if not cases:
            lines.append(f"{ind}{INDENT}case _:")
            lines.append(f"{ind}{INDENT}{INDENT}pass")
        for c in cases:
            if c.kind == "case":
                _emit_case(c, indent + 1, lines, spans)
            else:
                lines.append(f"{ind}{INDENT}case _:")
                _emit(c, indent + 2, lines, spans)
    elif k == "case":
        lines.append(f"{ind}match _:")
        _emit_case(block, indent + 1, lines, spans)
    elif k == "return":
        lines.append(f"{ind}return {f['value']}".rstrip())
    elif k == "assign":
        lines.append(f"{ind}{f['targets']} = {f['value']}")
    elif k == "augassign":
        lines.append(f"{ind}{f['target']} {f['op']}= {f['value']}")
    elif k == "annassign":
        val = f" = {f['value']}" if f.get("value") else ""
        lines.append(f"{ind}{f['target']}: {f['annotation']}{val}")
    elif k in ("call", "expr"):
        lines.append(f"{ind}{f['value']}")
    elif k in ("pass", "break", "continue"):
        lines.append(f"{ind}{k}")
    elif k == "raise":
        cause = f" from {f['cause']}" if f.get("cause") else ""
        lines.append(f"{ind}raise {f['exc']}{cause}".rstrip())
    elif k == "assert":
        msg = f", {f['msg']}" if f.get("msg") else ""
        lines.append(f"{ind}assert {f['test']}{msg}")
    elif k == "import":
        lines.append(f"{ind}import {f['names']}")
    elif k == "from":
        lines.append(f"{ind}from {f['module']} import {f['names']}")
    elif k in ("global", "nonlocal"):
        lines.append(f"{ind}{k} {f['names']}")
    elif k == "del":
        lines.append(f"{ind}del {f['targets']}")
    elif k == "type":
        tp = f"[{f['typeparams']}]" if f.get("typeparams") else ""
        lines.append(f"{ind}type {f['name']}{tp} = {f['value']}")
    else:
        lines.append(f"{ind}pass")
    spans[block.id] = (start, len(lines))


def split_top_level(text: str, sep: str = ",") -> list[str]:
    """Split on ``sep`` ignoring separators inside brackets or strings."""
    parts, depth, quote, cur, i = [], 0, None, [], 0
    while i < len(text):
        ch = text[i]
        if quote:
            cur.append(ch)
            if ch == "\\" and i + 1 < len(text):
                cur.append(text[i + 1])
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch in "([{":
            depth += 1
            cur.append(ch)
        elif ch in ")]}":
            depth -= 1
            cur.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


# --------------------------------------------------------------------------
# Analysis helpers used by the canvas and editor
# --------------------------------------------------------------------------
def shallow_source(block: Block) -> str:
    """Source for this block alone, with every slot emptied."""
    saved = block.slots
    block.slots = {s: [] for s in block.spec.slots}
    try:
        return generate_source([block], lang=block.lang)
    finally:
        block.slots = saved


def _shallow_tree(block: Block) -> ast.AST | None:
    try:
        return ast.parse(shallow_source(block))
    except SyntaxError:
        return None


def call_edges(blocks) -> list[tuple[Block, Block]]:
    """(caller block, definition block) pairs for every call of a defined name."""
    defs: dict[str, Block] = {}
    for b in walk(blocks):
        if b.kind in LANGUAGES[b.lang].def_kinds and b.fields.get("name"):
            defs.setdefault(b.fields["name"], b)
    edges: list[tuple[Block, Block]] = []
    seen = set()
    for b in walk(blocks):
        for name in LANGUAGES[b.lang].called_names(b):
            target = defs.get(name)
            if target is not None and (b.id, target.id) not in seen:
                seen.add((b.id, target.id))
                edges.append((b, target))
    return edges


def _py_called_names(block: Block) -> list[str]:
    tree = _shallow_tree(block)
    if tree is None:
        return []
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None)
            if name:
                names.append(name)
    return names


def _names_in(text: str) -> list[str]:
    for mode in ("eval", "exec"):
        try:
            tree = ast.parse(text, mode=mode)
            break
        except SyntaxError:
            tree = None
    if tree is None:
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in out:
            out.append(node.id)
    return out


def _arg_names(args_text: str) -> list[str]:
    try:
        fn = ast.parse(f"def _f({args_text}): pass").body[0]
    except SyntaxError:
        return []
    a = fn.args
    names = [x.arg for x in a.posonlyargs + a.args]
    if a.vararg:
        names.append(a.vararg.arg)
    names += [x.arg for x in a.kwonlyargs]
    if a.kwarg:
        names.append(a.kwarg.arg)
    return names


def _bound_names_in_pattern(text: str) -> list[str]:
    try:
        tree = ast.parse(f"match _:\n    case {text}:\n        pass")
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            out.append(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            out.append(node.rest)
    return out


def describe_io(block: Block) -> tuple[str, str]:
    """Automatic (inputs, outputs) descriptions for a block."""
    return LANGUAGES[block.lang].describe_io(block)


def _py_describe_io(block: Block) -> tuple[str, str]:  # noqa: C901
    f = block.fields
    k = block.kind
    j = ", ".join
    if k == "def":
        ins = j(_arg_names(f["args"]))
        if f.get("returns"):
            outs = f["returns"]
        else:
            rets = [b.fields["value"] for b in walk(block.children())
                    if b.kind == "return" and b.fields["value"]]
            outs = j(dict.fromkeys(rets)) if rets else "None"
        return ins, outs
    if k == "class":
        return f.get("bases", ""), f["name"]
    if k == "assign":
        targets = [t.strip() for t in f["targets"].split("=") if t.strip()]
        return j(_names_in(f["value"])), j(targets)
    if k == "augassign":
        return j(dict.fromkeys([f["target"]] + _names_in(f["value"]))), f["target"]
    if k == "annassign":
        return j(_names_in(f["value"])), f["target"]
    if k == "for":
        return j(_names_in(f["iter"])), f["target"]
    if k in ("if", "elif", "while", "assert"):
        return j(_names_in(f["test"])), ""
    if k == "with":
        bound = []
        for item in split_top_level(f["items"]):
            if " as " in item:
                bound.append(item.split(" as ", 1)[1].strip())
        return f["items"], j(bound)
    if k == "return":
        return j(_names_in(f["value"])), f["value"]
    if k in ("call", "expr"):
        return j(_names_in(f["value"])), ""
    if k == "raise":
        return j(_names_in(f["exc"])), ""
    if k in ("import", "from"):
        bound = []
        for alias in split_top_level(f["names"]):
            if " as " in alias:
                bound.append(alias.split(" as ", 1)[1].strip())
            else:
                bound.append(alias.split(".")[0])
        return "", j(bound)
    if k in ("global", "nonlocal"):
        return f["names"], ""
    if k == "del":
        return f["targets"], ""
    if k == "except":
        return f["type"], f.get("name", "")
    if k == "match":
        return j(_names_in(f["subject"])), ""
    if k == "case":
        return "", j(_bound_names_in_pattern(f["pattern"]))
    if k == "type":
        return "", f["name"]
    return "", ""


def effective_io(block: Block) -> tuple[str, str]:
    """User descriptions when given, otherwise the automatic ones."""
    auto_in, auto_out = describe_io(block)
    return block.desc_in or auto_in, block.desc_out or auto_out


# --------------------------------------------------------------------------
# Palette: one entry per reserved word
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class PaletteEntry:
    word: str
    kind: str
    fields: tuple[tuple[str, str], ...] = ()
    slot: str | None = None      # adds to this slot of the selected container
    note: str = ""
    lang: str = "python"


def _pe(word, kind, fields=(), slot=None, note=""):
    return PaletteEntry(word, kind, tuple(fields), slot, note)


PALETTE: list[PaletteEntry] = [
    _pe("False", "assign", [("targets", "flag"), ("value", "False")]),
    _pe("None", "assign", [("targets", "name"), ("value", "None")]),
    _pe("True", "assign", [("targets", "flag"), ("value", "True")]),
    _pe("and", "assign", [("targets", "result"), ("value", "a and b")]),
    _pe("as", "with", note="Also used by import and except; edit those blocks."),
    _pe("assert", "assert"),
    _pe("async", "def", [("async", "async"), ("name", "coroutine")]),
    _pe("await", "expr", [("value", "await coroutine()")]),
    _pe("break", "break"),
    _pe("class", "class"),
    _pe("continue", "continue"),
    _pe("def", "def"),
    _pe("del", "del"),
    _pe("elif", "elif", slot="orelse", note="Adds to the else slot of the selected if."),
    _pe("else", "pass", slot="orelse", note="Adds an else slot to the selected block."),
    _pe("except", "except", slot="handlers", note="Adds a handler to the selected try."),
    _pe("finally", "pass", slot="finalbody", note="Adds a finally slot to the selected try."),
    _pe("for", "for"),
    _pe("from", "from"),
    _pe("global", "global"),
    _pe("if", "if"),
    _pe("import", "import"),
    _pe("in", "assign", [("targets", "found"), ("value", "item in items")]),
    _pe("is", "assign", [("targets", "same"), ("value", "value is None")]),
    _pe("lambda", "assign", [("targets", "func"), ("value", "lambda x: x")]),
    _pe("nonlocal", "nonlocal"),
    _pe("not", "assign", [("targets", "result"), ("value", "not value")]),
    _pe("or", "assign", [("targets", "result"), ("value", "a or b")]),
    _pe("pass", "pass"),
    _pe("raise", "raise", [("exc", "Exception()")]),
    _pe("return", "return"),
    _pe("try", "try"),
    _pe("while", "while"),
    _pe("with", "with"),
    _pe("yield", "expr", [("value", "yield value")]),
    _pe("match", "match", note="Soft keyword."),
    _pe("case", "case", slot="cases", note="Adds a case to the selected match."),
    _pe("type", "type", note="Soft keyword: type alias."),
]

PALETTE_EXTRA: list[PaletteEntry] = [
    _pe("call", "call", note="Call expression statement."),
    _pe("assign", "assign"),
    _pe("x += 1", "augassign"),
    _pe("x: int", "annassign"),
    _pe("expression", "expr"),
]


def block_from_entry(entry: PaletteEntry) -> Block:
    return new_block(entry.kind, entry.lang, **dict(entry.fields))


# --------------------------------------------------------------------------
# Plain-language documentation for every block kind and field
# --------------------------------------------------------------------------
DOCS: dict[str, tuple[str, dict[str, str]]] = {
    "def": ("Defines a function: a named, reusable piece of code that runs when "
            "it is called.",
            {"async": "Make it a coroutine that callers must await.",
             "decorators": "Decorators applied to the function, comma separated "
                           "(e.g. staticmethod, lru_cache(maxsize=8)).",
             "name": "The name used to call the function.",
             "typeparams": "Generic type parameters, e.g. T or K, V.",
             "args": "The parameter list exactly as written inside the parentheses.",
             "returns": "Return type annotation, e.g. int or list[str]."}),
    "class": ("Defines a class: a blueprint for objects that bundles data and "
              "methods.",
              {"decorators": "Decorators applied to the class, comma separated.",
               "name": "The class name, used to create instances.",
               "typeparams": "Generic type parameters, e.g. T.",
               "bases": "Parent classes and class keywords, e.g. Base, metaclass=Meta."}),
    "if": ("Runs the body only when the condition is true; otherwise runs the "
           "else slot, if any.",
           {"test": "The condition, evaluated for truth."}),
    "elif": ("Another condition tried only when the preceding if/elif was false.",
             {"test": "The condition, evaluated for truth."}),
    "for": ("Loops over every item of an iterable. The else slot runs when the "
            "loop finishes without a break.",
            {"async": "Iterate an asynchronous iterator (inside async def).",
             "target": "Variable, or tuple of variables, that receives each item.",
             "iter": "The iterable being looped over, e.g. range(10) or items."}),
    "while": ("Repeats the body for as long as the condition stays true. The else "
              "slot runs when it ends without a break.",
              {"test": "The condition checked before every iteration."}),
    "try": ("Runs the body and lets the except handlers catch errors. The else "
            "slot runs if nothing was raised; finally always runs last.",
            {"star": "Use except* to handle exception groups."}),
    "except": ("Catches a matching exception raised in the try body and runs its "
               "body instead of crashing.",
               {"type": "Exception class or tuple to catch; empty catches everything.",
                "name": "Variable bound to the caught exception object."}),
    "with": ("Enters a context manager, runs the body, then exits it even if an "
             "error occurs (e.g. a file that is closed afterwards).",
             {"async": "Use an asynchronous context manager.",
              "items": "Context expressions, comma separated, each optionally 'as name'."}),
    "match": ("Compares a value against the case patterns and runs the first one "
              "that fits.",
              {"subject": "The value being matched."}),
    "case": ("One pattern of a match. Its body runs when the pattern fits the "
             "subject.",
             {"pattern": "Structural pattern, e.g. [x, y], Point(x=0), 1 | 2, or _ for anything.",
              "guard": "Extra condition that must also be true for this case."}),
    "return": ("Ends the current function and hands a value back to the caller.",
               {"value": "Expression to return; empty returns None."}),
    "assign": ("Stores the result of an expression in one or more variables.",
               {"targets": "Variable, attribute, item or unpacking target; chain "
                           "several with 'a = b'.",
                "value": "Expression whose result is stored."}),
    "augassign": ("Updates a variable in place by combining it with a value, "
                  "e.g. total += price.",
                  {"target": "The variable being updated.",
                   "op": "Operator: + - * / // % ** @ & | ^ << >>",
                   "value": "Value combined with the current contents."}),
    "annassign": ("Declares a variable with a type hint and optionally assigns it.",
                  {"target": "The variable being declared.",
                   "annotation": "The type hint, e.g. int or list[str].",
                   "value": "Optional initial value."}),
    "call": ("Calls a function or method for its effect; the result is discarded.",
             {"value": "The call expression, e.g. print(x) or items.append(y)."}),
    "expr": ("Evaluates an expression as a statement: a docstring, yield, await "
             "or any other expression whose value is not stored.",
             {"value": "The expression."}),
    "pass": ("Does nothing. A placeholder where Python requires a statement.", {}),
    "break": ("Leaves the innermost enclosing loop immediately.", {}),
    "continue": ("Skips the rest of this iteration and starts the next one.", {}),
    "raise": ("Throws an exception, stopping normal flow until an except catches it.",
              {"exc": "Exception instance or class; empty re-raises the current one.",
               "cause": "Exception to chain as the cause (raise ... from ...)."}),
    "assert": ("Checks that a condition holds and raises AssertionError if not. "
               "Used for sanity checks during development.",
               {"test": "The condition expected to be true.",
                "msg": "Optional message attached to the error."}),
    "import": ("Loads modules and binds them to names in this file.",
               {"names": "Modules, comma separated, each optionally 'as alias'."}),
    "from": ("Imports specific names out of a module.",
             {"module": "Module path; leading dots make it relative to this package.",
              "names": "Names to import, each optionally 'as alias', or * for all."}),
    "global": ("Declares that these names refer to module-level variables, so "
               "assigning them inside a function updates the global.",
               {"names": "Variable names, comma separated."}),
    "nonlocal": ("Declares that these names belong to an enclosing function's "
                 "scope rather than creating new locals.",
                 {"names": "Variable names, comma separated."}),
    "del": ("Removes variables, items or attributes.",
            {"targets": "What to delete, comma separated, e.g. x, items[0], obj.attr."}),
    "type": ("Creates a type alias (Python 3.12+), a new name for a type expression.",
             {"name": "The alias name.",
              "typeparams": "Generic type parameters, e.g. T.",
              "value": "The type expression, e.g. list[int] | None."}),
}


def block_doc(kind: str, lang: str = "python") -> str:
    return LANGUAGES[lang].docs.get(kind, ("", {}))[0]


def field_doc(kind: str, name: str, lang: str = "python") -> str:
    return LANGUAGES[lang].docs.get(kind, ("", {}))[1].get(name, "")


# --------------------------------------------------------------------------
# The most common built-in functions, offered as ready-made blocks
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class FunctionEntry:
    word: str        # the function name shown on the button
    params: str      # signature shown in hints
    template: str    # expression placed in the block
    result: str      # variable that receives the result; "" for no result
    doc: str
    inputs: str
    outputs: str
    lang: str = "python"


def _fn(word, params, template, result, doc, inputs, outputs):
    return FunctionEntry(word, params, template, result, doc, inputs, outputs)


FUNCTIONS: list[FunctionEntry] = [
    _fn("print", "*objects, sep=' ', end='\n'", "print(value)", "",
        "Writes values to the screen (standard output).",
        "values to show", "nothing; text appears on screen"),
    _fn("input", "prompt=''", "input('Enter a value: ')", "text",
        "Pauses and reads one line typed by the user.",
        "prompt shown to the user", "the typed line, as str"),
    _fn("len", "obj", "len(items)", "count",
        "Number of items in a sequence, string or collection.",
        "a sequence or collection", "item count, as int"),
    _fn("range", "start, stop, step", "range(10)", "numbers",
        "Sequence of integers from start up to, but not including, stop.",
        "stop (and optional start, step)", "lazy sequence of ints"),
    _fn("str", "value", "str(value)", "text",
        "Converts a value to its text form.", "any value", "str"),
    _fn("int", "value", "int(value)", "number",
        "Converts text or a decimal number to a whole number.",
        "str or float", "int"),
    _fn("float", "value", "float(value)", "number",
        "Converts text or an int to a decimal number.", "str or int", "float"),
    _fn("bool", "value", "bool(value)", "flag",
        "The truth value of anything: empty and zero are False.",
        "any value", "True or False"),
    _fn("list", "iterable", "list(items)", "items",
        "Builds a list from an iterable, or an empty list.",
        "iterable (optional)", "new list"),
    _fn("dict", "**kwargs", "dict(key='value')", "mapping",
        "Builds a dictionary of key/value pairs.",
        "pairs or keywords (optional)", "new dict"),
    _fn("set", "iterable", "set(items)", "unique",
        "Builds a set holding each distinct item once.",
        "iterable (optional)", "new set"),
    _fn("tuple", "iterable", "tuple(items)", "frozen",
        "Builds an immutable tuple from an iterable.",
        "iterable (optional)", "new tuple"),
    _fn("type", "value", "type(value)", "kind",
        "The class of a value.", "any value", "its class"),
    _fn("isinstance", "value, classinfo", "isinstance(value, int)", "matches",
        "Whether a value is an instance of a class (or tuple of classes).",
        "value, class or tuple of classes", "True or False"),
    _fn("open", "file, mode='r'", "open('file.txt')", "file",
        "Opens a file and returns a file object; best used in a with block.",
        "path and mode ('r', 'w', 'a', 'b')", "file object"),
    _fn("sorted", "iterable, key=None, reverse=False", "sorted(items)", "ordered",
        "A new list of the items in ascending order.",
        "iterable (optional key, reverse)", "new sorted list"),
    _fn("reversed", "sequence", "reversed(items)", "backwards",
        "Iterates the items from last to first.", "sequence", "reverse iterator"),
    _fn("enumerate", "iterable, start=0", "enumerate(items)", "pairs",
        "Pairs every item with its position: (0, a), (1, b), ...",
        "iterable (optional start)", "iterator of (index, item)"),
    _fn("zip", "*iterables", "zip(first, second)", "pairs",
        "Pairs up items from several iterables position by position.",
        "two or more iterables", "iterator of tuples"),
    _fn("map", "function, iterable", "map(func, items)", "mapped",
        "Applies a function to every item.",
        "function, iterable", "iterator of results"),
    _fn("filter", "function, iterable", "filter(func, items)", "kept",
        "Keeps only the items for which the function returns true.",
        "function, iterable", "iterator of kept items"),
    _fn("sum", "iterable, start=0", "sum(numbers)", "total",
        "Adds up all the numbers.", "iterable of numbers", "the total"),
    _fn("min", "iterable", "min(values)", "smallest",
        "The smallest item.", "iterable or several values", "smallest item"),
    _fn("max", "iterable", "max(values)", "largest",
        "The largest item.", "iterable or several values", "largest item"),
    _fn("abs", "number", "abs(number)", "magnitude",
        "Absolute value: the distance from zero.", "number", "non-negative number"),
    _fn("round", "number, ndigits=None", "round(number, 2)", "rounded",
        "Rounds to a number of decimal places (none gives an int).",
        "number, decimal places", "rounded number"),
    _fn("any", "iterable", "any(flags)", "some",
        "True if at least one item is true.", "iterable", "True or False"),
    _fn("all", "iterable", "all(flags)", "every",
        "True if every item is true (and for an empty iterable).",
        "iterable", "True or False"),
    _fn("iter", "iterable", "iter(items)", "iterator",
        "An iterator that walks the items one at a time.",
        "iterable", "iterator"),
    _fn("next", "iterator, default", "next(iterator)", "item",
        "The next item from an iterator, or default when exhausted.",
        "iterator (optional default)", "next item"),
]


def block_from_function(entry: FunctionEntry) -> Block:
    """A block calling the function, in the entry's language, with the
    input/output descriptions filled in."""
    return LANGUAGES[entry.lang].function_block(entry)


def _py_function_block(entry: FunctionEntry) -> Block:
    """A call block for a function used for its effect, otherwise an
    assignment that keeps the result."""
    if entry.result:
        block = new_block("assign", targets=entry.result, value=entry.template)
    else:
        block = new_block("call", value=entry.template)
    block.desc_in = entry.inputs
    block.desc_out = entry.outputs
    return block


_PY_KEYWORD_KINDS = {
    "def", "class", "if", "elif", "for", "while", "try", "except", "with", "match",
    "case", "return", "raise", "assert", "import", "from", "global", "nonlocal",
    "del", "type", "pass", "break", "continue",
}


def _py_keyword(block: Block) -> str:
    """The leading keyword of a block's header, or '' when it has none."""
    if block.kind not in _PY_KEYWORD_KINDS:
        return ""
    kw = block.kind
    if block.kind == "try" and block.fields.get("star"):
        kw = "try*"
    if block.fields.get("async"):
        kw = "async " + kw
    return kw


PYTHON = Language(
    name="python", label="Python", extensions=(".py", ".pyw", ".pyi"),
    specs=SPECS, docs=DOCS, palette=PALETTE, palette_extra=PALETTE_EXTRA,
    functions=FUNCTIONS,
    slot_hosts={"orelse": "if", "handlers": "try", "finalbody": "try", "cases": "match"},
    slot_fillers={"handlers": "except", "cases": "case"},
    placeholder_kind="pass",
    def_kinds=frozenset({"def", "class"}),
    loop_kinds=frozenset({"for", "while"}),
    terminators=frozenset({"return", "raise", "break", "continue"}),
    parse=_py_parse_source, generate=_py_generate_source, syntax_error=_py_syntax_error,
    title=_py_title, keyword=_py_keyword, describe_io=_py_describe_io,
    called_names=_py_called_names, function_block=_py_function_block,
    note="Comments and blank lines are not kept when a file is re-generated.",
)
LANGUAGES[PYTHON.name] = PYTHON
