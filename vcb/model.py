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
        return SPECS[self.kind]

    def children(self):
        for name in self.spec.slots:
            yield from self.slots.get(name, [])

    def title(self) -> str:
        """Short one-line header text for the block."""
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


def new_block(kind: str, **overrides: str) -> Block:
    spec = SPECS[kind]
    fields = {name: default for name, default in spec.fields}
    fields.update(overrides)
    return Block(kind, fields=fields, slots={s: [] for s in spec.slots})


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
                 x=block.x, y=block.y)
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


def parse_source(src: str) -> list[Block]:
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
def generate_source(blocks) -> str:
    lines: list[str] = []
    prev: Block | None = None
    for b in blocks:
        if prev is not None:
            big = {"def", "class"}
            if prev.kind in big or b.kind in big:
                lines.extend(["", ""])
            elif prev.spec.slots or b.spec.slots or prev.spec.category != b.spec.category:
                lines.append("")
        _emit(b, 0, lines)
        prev = b
    return "\n".join(lines) + "\n"


def syntax_error(src: str) -> SyntaxError | None:
    try:
        ast.parse(src)
    except SyntaxError as e:
        return e
    return None


def _emit_slot(block: Block, slot: str, indent: int, lines: list[str], required=True):
    kids = block.slots.get(slot, [])
    if kids:
        for k in kids:
            _emit(k, indent + 1, lines)
    elif required:
        lines.append(INDENT * (indent + 1) + "pass")


def _emit_decorators(block: Block, ind: str, lines: list[str]):
    for d in split_top_level(block.fields.get("decorators", "")):
        lines.append(f"{ind}@{d}")


def _emit_if(block: Block, indent: int, lines: list[str], keyword: str):
    ind = INDENT * indent
    lines.append(f"{ind}{keyword} {block.fields['test']}:")
    _emit_slot(block, "body", indent, lines)
    orelse = block.slots.get("orelse", [])
    if len(orelse) == 1 and orelse[0].kind == "elif":
        _emit_if(orelse[0], indent, lines, "elif")
    elif orelse:
        lines.append(f"{ind}else:")
        _emit_slot(block, "orelse", indent, lines)


def _emit_except(block: Block, indent: int, lines: list[str], star: str):
    ind = INDENT * indent
    f = block.fields
    head = f"{ind}except{star}"
    if f.get("type"):
        head += f" {f['type']}"
        if f.get("name"):
            head += f" as {f['name']}"
    lines.append(head + ":")
    _emit_slot(block, "body", indent, lines)


def _emit_case(block: Block, indent: int, lines: list[str]):
    ind = INDENT * indent
    f = block.fields
    guard = f" if {f['guard']}" if f.get("guard") else ""
    lines.append(f"{ind}case {f['pattern']}{guard}:")
    _emit_slot(block, "body", indent, lines)


def _emit(block: Block, indent: int, lines: list[str]):  # noqa: C901
    ind = INDENT * indent
    f = block.fields
    k = block.kind
    pre = f"{f['async']} " if f.get("async") else ""
    if k == "def":
        _emit_decorators(block, ind, lines)
        tp = f"[{f['typeparams']}]" if f.get("typeparams") else ""
        ret = f" -> {f['returns']}" if f.get("returns") else ""
        lines.append(f"{ind}{pre}def {f['name']}{tp}({f['args']}){ret}:")
        _emit_slot(block, "body", indent, lines)
    elif k == "class":
        _emit_decorators(block, ind, lines)
        tp = f"[{f['typeparams']}]" if f.get("typeparams") else ""
        bases = f"({f['bases']})" if f.get("bases") else ""
        lines.append(f"{ind}class {f['name']}{tp}{bases}:")
        _emit_slot(block, "body", indent, lines)
    elif k in ("if", "elif"):
        _emit_if(block, indent, lines, "if")
    elif k == "for":
        lines.append(f"{ind}{pre}for {f['target']} in {f['iter']}:")
        _emit_slot(block, "body", indent, lines)
        if block.slots.get("orelse"):
            lines.append(f"{ind}else:")
            _emit_slot(block, "orelse", indent, lines)
    elif k == "while":
        lines.append(f"{ind}while {f['test']}:")
        _emit_slot(block, "body", indent, lines)
        if block.slots.get("orelse"):
            lines.append(f"{ind}else:")
            _emit_slot(block, "orelse", indent, lines)
    elif k == "try":
        star = "*" if f.get("star") else ""
        lines.append(f"{ind}try:")
        _emit_slot(block, "body", indent, lines)
        handlers = block.slots.get("handlers", [])
        for h in handlers:
            if h.kind == "except":
                _emit_except(h, indent, lines, star)
            else:  # a foreign block dropped in the handlers slot: wrap it
                lines.append(f"{ind}except Exception:")
                _emit(h, indent + 1, lines)
        if block.slots.get("orelse"):
            if not handlers:
                lines.append(f"{ind}except Exception:")
                lines.append(f"{ind}{INDENT}pass")
            lines.append(f"{ind}else:")
            _emit_slot(block, "orelse", indent, lines)
        if block.slots.get("finalbody") or not handlers:
            lines.append(f"{ind}finally:")
            _emit_slot(block, "finalbody", indent, lines)
    elif k == "except":
        # Standalone handler: wrap so the output stays valid.
        lines.append(f"{ind}try:")
        lines.append(f"{ind}{INDENT}pass")
        _emit_except(block, indent, lines, "")
    elif k == "with":
        lines.append(f"{ind}{pre}with {f['items']}:")
        _emit_slot(block, "body", indent, lines)
    elif k == "match":
        lines.append(f"{ind}match {f['subject']}:")
        cases = block.slots.get("cases", [])
        if not cases:
            lines.append(f"{ind}{INDENT}case _:")
            lines.append(f"{ind}{INDENT}{INDENT}pass")
        for c in cases:
            if c.kind == "case":
                _emit_case(c, indent + 1, lines)
            else:
                lines.append(f"{ind}{INDENT}case _:")
                _emit(c, indent + 2, lines)
    elif k == "case":
        lines.append(f"{ind}match _:")
        _emit_case(block, indent + 1, lines)
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
        return generate_source([block])
    finally:
        block.slots = saved


def _shallow_tree(block: Block) -> ast.AST | None:
    try:
        return ast.parse(shallow_source(block))
    except SyntaxError:
        return None


def call_edges(blocks) -> list[tuple[Block, Block]]:
    """(caller block, def/class block) pairs for every call of a defined name."""
    defs: dict[str, Block] = {}
    for b in walk(blocks):
        if b.kind in ("def", "class"):
            defs.setdefault(b.fields["name"], b)
    edges: list[tuple[Block, Block]] = []
    seen = set()
    for b in walk(blocks):
        tree = _shallow_tree(b)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None)
            target = defs.get(name)
            if target is not None and (b.id, target.id) not in seen:
                seen.add((b.id, target.id))
                edges.append((b, target))
    return edges


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


def describe_io(block: Block) -> tuple[str, str]:  # noqa: C901
    """Automatic (inputs, outputs) descriptions for a block."""
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
    return new_block(entry.kind, **dict(entry.fields))
