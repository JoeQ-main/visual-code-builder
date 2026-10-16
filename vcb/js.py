"""JavaScript support: parse with esprima, generate source, palette and docs.

Importing this module registers the ``javascript`` language in
``model.LANGUAGES``. The esprima package is imported lazily, so Python-only
use of the program does not need it.
"""
from __future__ import annotations

import re

from . import model
from .model import Block, BlockSpec, FunctionEntry, PaletteEntry, insert_block

LANG = "javascript"
IND = "  "


def _spec(kind, category, fields=(), slots=(), help=""):
    return BlockSpec(kind, category, tuple(fields), tuple(slots), help)


SPECS: dict[str, BlockSpec] = {s.kind: s for s in [
    _spec("function", "definition",
          [("export", ""), ("async", ""), ("generator", ""), ("name", "doSomething"),
           ("params", "")], ["body"],
          "'export' may be empty, export, or export default."),
    _spec("class", "definition",
          [("export", ""), ("name", "ClassName"), ("superclass", "")], ["body"]),
    _spec("method", "definition",
          [("static", ""), ("async", ""), ("generator", ""), ("accessor", ""),
           ("name", "method"), ("params", "")], ["body"],
          "Only valid inside a class body. 'accessor' may be get or set."),
    _spec("if", "flow", [("test", "condition")], ["body", "orelse"]),
    _spec("elif", "flow", [("test", "condition")], ["body", "orelse"],
          "else if: only valid as the single block in the else slot of an if."),
    _spec("for", "loop", [("init", "let i = 0"), ("test", "i < 10"), ("update", "i++")],
          ["body"]),
    _spec("foreach", "loop", [("left", "const item"), ("op", "of"), ("right", "items")],
          ["body"], "'op' is of (values) or in (keys)."),
    _spec("while", "loop", [("test", "condition")], ["body"]),
    _spec("dowhile", "loop", [("test", "condition")], ["body"]),
    _spec("try", "error", [], ["body", "handlers", "finalbody"]),
    _spec("catch", "error", [("param", "error")], ["body"],
          "Only valid inside the catch slot of a try."),
    _spec("switch", "flow", [("subject", "value")], ["cases"]),
    _spec("case", "flow", [("test", "1")], ["body"],
          "Empty 'test' makes it the default case. Only valid inside a switch."),
    _spec("return", "simple", [("value", "")]),
    _spec("throw", "error", [("value", 'new Error("message")')]),
    _spec("break", "simple"),
    _spec("continue", "simple"),
    _spec("decl", "simple", [("export", ""), ("keyword", "const"),
                             ("declarators", "name = value")],
          "Several declarators are separated by commas."),
    _spec("call", "simple", [("value", "console.log()")]),
    _spec("expr", "simple", [("value", "value")]),
    _spec("import", "import", [("spec", "{ name } from './module.js'")]),
    _spec("export", "import", [("spec", "{ name }")]),
    _spec("block", "context", [], ["body"]),
    _spec("raw", "simple", [("code", "// todo")],
          "Verbatim source for statements the editor does not model."),
]}

SLOT_LABELS = {"handlers": "catch"}


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
MISSING_MESSAGE = ("JavaScript support needs the esprima package: "
                   "run  python -m pip install esprima")


def _esprima():
    try:
        import esprima  # noqa: WPS433 - optional dependency
    except ImportError as e:  # pragma: no cover
        raise ImportError(MISSING_MESSAGE) from e
    return esprima


def available() -> bool:
    """True when the esprima parser can be imported."""
    try:
        _esprima()
    except ImportError:
        return False
    return True


def _to_syntax_error(e) -> SyntaxError:
    text = str(e)
    m = re.match(r"Line (\d+): (.*)", text)
    err = SyntaxError(m.group(2) if m else text)
    err.lineno = int(m.group(1)) if m else getattr(e, "lineNumber", 1)
    err.msg = m.group(2) if m else text
    return err


def _parse_tree(src: str):
    """Parse as a module, falling back to script mode (for 'with', etc.).

    When both fail, the error that got furthest is the meaningful one.
    """
    esprima = _esprima()
    options = {"range": True}
    try:
        return esprima.parseModule(src, options)
    except esprima.Error as module_err:
        try:
            return esprima.parseScript(src, options)
        except esprima.Error as script_err:
            a, b = _to_syntax_error(module_err), _to_syntax_error(script_err)
            raise (a if a.lineno >= b.lineno else b) from None


def syntax_error(src: str) -> SyntaxError | None:
    try:
        _parse_tree(src)
    except SyntaxError as e:
        return e
    return None


def parse(src: str) -> list[Block]:
    """Blocks for a JavaScript module. Raises SyntaxError on bad input."""
    tree = _parse_tree(src)
    return [_stmt(n, src) for n in tree.body if n.type != "EmptyStatement"]


def _text(node, src: str) -> str:
    return src[node.range[0]:node.range[1]].strip()


def _list(node):
    if node is None:
        return []
    return list(node.body) if node.type == "BlockStatement" else [node]


def _fill(block: Block, slot: str, nodes, src: str):
    for n in nodes:
        if n.type != "EmptyStatement":
            insert_block(block, slot, _stmt(n, src))


def _params(fn, src: str) -> str:
    if not fn.params:
        return ""
    return src[fn.params[0].range[0]:fn.params[-1].range[1]].strip()


def _nb(kind, **fields):
    return model.new_block(kind, LANG, **fields)


def _if(n, kind: str, src: str) -> Block:
    b = _nb(kind, test=_text(n.test, src))
    _fill(b, "body", _list(n.consequent), src)
    alt = n.alternate
    if alt is not None and alt.type == "IfStatement":
        insert_block(b, "orelse", _if(alt, "elif", src))
    elif alt is not None:
        _fill(b, "orelse", _list(alt), src)
    return b


def _function(n, src: str, export: str = "") -> Block:
    b = _nb("function", export=export,
            **{"async": "async" if getattr(n, "isAsync", False) else ""},
            generator="*" if getattr(n, "generator", False) else "",
            name=n.id.name if n.id is not None else "", params=_params(n, src))
    _fill(b, "body", _list(n.body), src)
    return b


def _class(n, src: str, export: str = "") -> Block:
    b = _nb("class", export=export, name=n.id.name if n.id is not None else "",
            superclass=_text(n.superClass, src) if n.superClass is not None else "")
    for m in n.body.body:
        if m.type == "MethodDefinition":
            fn = m.value
            key = _text(m.key, src)
            if getattr(m, "computed", False):
                key = f"[{key}]"
            mb = _nb("method", static="static" if m.static else "",
                     **{"async": "async" if getattr(fn, "isAsync", False) else ""},
                     generator="*" if getattr(fn, "generator", False) else "",
                     accessor=m.kind if m.kind in ("get", "set") else "",
                     name=key, params=_params(fn, src))
            _fill(mb, "body", _list(fn.body), src)
            insert_block(b, "body", mb)
        else:
            insert_block(b, "body", _nb("raw", code=_text(m, src)))
    return b


def _decl(n, src: str, export: str = "") -> Block:
    first, last = n.declarations[0], n.declarations[-1]
    return _nb("decl", export=export, keyword=n.kind,
               declarators=src[first.range[0]:last.range[1]].strip())


def _stmt(n, src: str) -> Block:  # noqa: C901 - one branch per node type
    t = n.type
    if t == "FunctionDeclaration":
        return _function(n, src)
    if t == "ClassDeclaration":
        return _class(n, src)
    if t == "IfStatement":
        return _if(n, "if", src)
    if t == "ForStatement":
        # a declaration's range includes its ';', which the for header supplies
        b = _nb("for", init=_text(n.init, src).rstrip(";").strip() if n.init is not None else "",
                test=_text(n.test, src) if n.test is not None else "",
                update=_text(n.update, src) if n.update is not None else "")
        _fill(b, "body", _list(n.body), src)
        return b
    if t in ("ForOfStatement", "ForInStatement"):
        b = _nb("foreach", left=_text(n.left, src), op="of" if t == "ForOfStatement" else "in",
                right=_text(n.right, src))
        _fill(b, "body", _list(n.body), src)
        return b
    if t == "WhileStatement":
        b = _nb("while", test=_text(n.test, src))
        _fill(b, "body", _list(n.body), src)
        return b
    if t == "DoWhileStatement":
        b = _nb("dowhile", test=_text(n.test, src))
        _fill(b, "body", _list(n.body), src)
        return b
    if t == "TryStatement":
        b = _nb("try")
        _fill(b, "body", n.block.body, src)
        if n.handler is not None:
            h = n.handler
            cb = _nb("catch", param=_text(h.param, src) if h.param is not None else "")
            _fill(cb, "body", h.body.body, src)
            insert_block(b, "handlers", cb)
        if n.finalizer is not None:
            _fill(b, "finalbody", n.finalizer.body, src)
        return b
    if t == "SwitchStatement":
        b = _nb("switch", subject=_text(n.discriminant, src))
        for c in n.cases:
            cb = _nb("case", test=_text(c.test, src) if c.test is not None else "")
            _fill(cb, "body", c.consequent, src)
            insert_block(b, "cases", cb)
        return b
    if t == "ReturnStatement":
        return _nb("return", value=_text(n.argument, src) if n.argument is not None else "")
    if t == "ThrowStatement":
        return _nb("throw", value=_text(n.argument, src))
    if t == "BreakStatement":
        return _nb("break")
    if t == "ContinueStatement":
        return _nb("continue")
    if t == "VariableDeclaration":
        return _decl(n, src)
    if t == "ExpressionStatement":
        kind = "call" if n.expression.type == "CallExpression" else "expr"
        return _nb(kind, value=_text(n.expression, src))
    if t == "ImportDeclaration":
        return _nb("import", spec=_strip_stmt(_text(n, src), "import"))
    if t == "ExportNamedDeclaration" and n.declaration is not None:
        return _exported(n.declaration, src, "export")
    if t == "ExportDefaultDeclaration":
        d = n.declaration
        if d.type in ("FunctionDeclaration", "ClassDeclaration"):
            return _exported(d, src, "export default")
        return _nb("export", spec="default " + _text(d, src))
    if t in ("ExportNamedDeclaration", "ExportAllDeclaration"):
        return _nb("export", spec=_strip_stmt(_text(n, src), "export"))
    if t == "BlockStatement":
        b = _nb("block")
        _fill(b, "body", n.body, src)
        return b
    return _nb("raw", code=_text(n, src))


def _exported(decl, src: str, export: str) -> Block:
    if decl.type == "FunctionDeclaration":
        return _function(decl, src, export)
    if decl.type == "ClassDeclaration":
        return _class(decl, src, export)
    if decl.type == "VariableDeclaration":
        return _decl(decl, src, export)
    return _nb("export", spec=_text(decl, src))


def _strip_stmt(text: str, keyword: str) -> str:
    text = text.strip()
    if text.startswith(keyword):
        text = text[len(keyword):]
    return text.strip().rstrip(";").strip()


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------
def generate(blocks, spans: dict) -> str:
    lines: list[str] = []
    prev: Block | None = None
    for b in blocks:
        if prev is not None and (prev.kind in ("function", "class")
                                 or b.kind in ("function", "class")):
            lines.append("")
        _emit(b, 0, lines, spans)
        prev = b
    return "\n".join(lines) + "\n"


def _slot(block: Block, slot: str, indent: int, lines: list[str], spans: dict):
    for k in block.slots.get(slot, []):
        _emit(k, indent + 1, lines, spans)


def _head(parts) -> str:
    return " ".join(p for p in parts if p)


def _fn_head(f: dict) -> str:
    return _head([f.get("export", ""), f.get("async", ""),
                  "function" + f.get("generator", ""), f["name"]]) + f"({f['params']})"


def _method_head(f: dict) -> str:
    name = f.get("generator", "") + f["name"]
    return _head([f.get("static", ""), f.get("async", ""), f.get("accessor", ""),
                  name]) + f"({f['params']})"


def _emit_if(block: Block, indent: int, lines: list[str], spans: dict, first: bool):
    ind = IND * indent
    start = len(lines) + 1
    f = block.fields
    opener = "if" if first else "} else if"
    lines.append(f"{ind}{opener} ({f['test']}) {{")
    _slot(block, "body", indent, lines, spans)
    orelse = block.slots.get("orelse", [])
    if len(orelse) == 1 and orelse[0].kind == "elif":
        _emit_if(orelse[0], indent, lines, spans, False)
    else:
        if orelse:
            lines.append(f"{ind}}} else {{")
            _slot(block, "orelse", indent, lines, spans)
        lines.append(f"{ind}}}")
    spans[block.id] = (start, len(lines))


def _emit_catch(block: Block, indent: int, lines: list[str], spans: dict):
    ind = IND * indent
    start = len(lines) + 1
    # esprima cannot read a bare "catch {", so an empty parameter gets a name.
    param = block.fields.get("param") or "error"
    lines.append(f"{ind}}} catch ({param}) {{")
    _slot(block, "body", indent, lines, spans)
    spans[block.id] = (start, len(lines))


def _emit_case(block: Block, indent: int, lines: list[str], spans: dict):
    ind = IND * indent
    start = len(lines) + 1
    test = block.fields.get("test", "")
    lines.append(f"{ind}case {test}:" if test else f"{ind}default:")
    _slot(block, "body", indent, lines, spans)
    spans[block.id] = (start, len(lines))


def _emit(block: Block, indent: int, lines: list[str], spans: dict):  # noqa: C901
    ind = IND * indent
    start = len(lines) + 1
    f = block.fields
    k = block.kind
    if k == "function":
        lines.append(f"{ind}{_fn_head(f)} {{")
        _slot(block, "body", indent, lines, spans)
        lines.append(f"{ind}}}")
    elif k == "class":
        ext = f" extends {f['superclass']}" if f.get("superclass") else ""
        lines.append(f"{ind}{_head([f.get('export', ''), 'class', f['name']])}{ext} {{")
        _slot(block, "body", indent, lines, spans)
        lines.append(f"{ind}}}")
    elif k == "method":
        lines.append(f"{ind}{_method_head(f)} {{")
        _slot(block, "body", indent, lines, spans)
        lines.append(f"{ind}}}")
    elif k in ("if", "elif"):
        _emit_if(block, indent, lines, spans, True)
        return
    elif k == "for":
        lines.append(f"{ind}for ({f['init']}; {f['test']}; {f['update']}) {{")
        _slot(block, "body", indent, lines, spans)
        lines.append(f"{ind}}}")
    elif k == "foreach":
        lines.append(f"{ind}for ({f['left']} {f['op']} {f['right']}) {{")
        _slot(block, "body", indent, lines, spans)
        lines.append(f"{ind}}}")
    elif k == "while":
        lines.append(f"{ind}while ({f['test']}) {{")
        _slot(block, "body", indent, lines, spans)
        lines.append(f"{ind}}}")
    elif k == "dowhile":
        lines.append(f"{ind}do {{")
        _slot(block, "body", indent, lines, spans)
        lines.append(f"{ind}}} while ({f['test']});")
    elif k == "try":
        lines.append(f"{ind}try {{")
        _slot(block, "body", indent, lines, spans)
        handlers = block.slots.get("handlers", [])
        for h in handlers:
            if h.kind == "catch":
                _emit_catch(h, indent, lines, spans)
            else:
                lines.append(f"{ind}}} catch (error) {{")
                _emit(h, indent + 1, lines, spans)
        if block.slots.get("finalbody") or not handlers:
            lines.append(f"{ind}}} finally {{")
            _slot(block, "finalbody", indent, lines, spans)
        lines.append(f"{ind}}}")
    elif k == "catch":
        lines.append(f"{ind}try {{")
        _emit_catch(block, indent, lines, spans)
        lines.append(f"{ind}}}")
    elif k == "switch":
        lines.append(f"{ind}switch ({f['subject']}) {{")
        for c in block.slots.get("cases", []):
            if c.kind == "case":
                _emit_case(c, indent + 1, lines, spans)
            else:
                lines.append(f"{ind}{IND}default:")
                _emit(c, indent + 2, lines, spans)
        lines.append(f"{ind}}}")
    elif k == "case":
        lines.append(f"{ind}switch (true) {{")
        _emit_case(block, indent + 1, lines, spans)
        lines.append(f"{ind}}}")
    elif k == "return":
        lines.append(f"{ind}return {f['value']}".rstrip() + ";")
    elif k == "throw":
        lines.append(f"{ind}throw {f['value']};")
    elif k in ("break", "continue"):
        lines.append(f"{ind}{k};")
    elif k == "decl":
        lines.append(f"{ind}{_head([f.get('export', ''), f['keyword']])} {f['declarators']};")
    elif k in ("call", "expr"):
        lines.append(f"{ind}{f['value']};")
    elif k == "import":
        lines.append(f"{ind}import {f['spec']};")
    elif k == "export":
        lines.append(f"{ind}export {f['spec']};")
    elif k == "block":
        lines.append(f"{ind}{{")
        _slot(block, "body", indent, lines, spans)
        lines.append(f"{ind}}}")
    elif k == "raw":
        for line in f["code"].splitlines() or [""]:
            lines.append(f"{ind}{line}")
    else:
        lines.append(f"{ind};")
    spans[block.id] = (start, len(lines))


# --------------------------------------------------------------------------
# Titles, keywords, descriptions, call detection
# --------------------------------------------------------------------------
def keyword(block: Block) -> str:
    f = block.fields
    k = block.kind
    if k == "function":
        return _head([f.get("export", ""), f.get("async", ""),
                      "function" + f.get("generator", "")])
    if k == "class":
        return _head([f.get("export", ""), "class"])
    if k == "method":
        return _head([f.get("static", ""), f.get("async", ""), f.get("accessor", "")])
    if k == "elif":
        return "else if"
    if k in ("for", "foreach"):
        return "for"
    if k == "dowhile":
        return "do"
    if k == "case":
        return "case" if f.get("test") else "default"
    if k == "decl":
        return _head([f.get("export", ""), f["keyword"]])
    if k in ("if", "while", "try", "catch", "switch", "return", "throw", "break",
             "continue", "import", "export"):
        return k
    return ""


def title(block: Block) -> str:  # noqa: C901
    f = block.fields
    k = block.kind
    if k == "function":
        return _fn_head(f)
    if k == "class":
        ext = f" extends {f['superclass']}" if f.get("superclass") else ""
        return _head([f.get("export", ""), "class", f["name"]]) + ext
    if k == "method":
        return _method_head(f)
    if k == "if":
        return f"if ({f['test']})"
    if k == "elif":
        return f"else if ({f['test']})"
    if k == "for":
        return f"for ({f['init']}; {f['test']}; {f['update']})"
    if k == "foreach":
        return f"for ({f['left']} {f['op']} {f['right']})"
    if k == "while":
        return f"while ({f['test']})"
    if k == "dowhile":
        return f"do … while ({f['test']})"
    if k == "try":
        return "try"
    if k == "catch":
        return f"catch ({f['param']})" if f.get("param") else "catch"
    if k == "switch":
        return f"switch ({f['subject']})"
    if k == "case":
        return f"case {f['test']}" if f.get("test") else "default"
    if k == "return":
        return f"return {f['value']}".rstrip()
    if k == "throw":
        return f"throw {f['value']}"
    if k == "decl":
        return f"{_head([f.get('export', ''), f['keyword']])} {f['declarators']}"
    if k in ("call", "expr"):
        return f["value"]
    if k == "import":
        return f"import {f['spec']}"
    if k == "export":
        return f"export {f['spec']}"
    if k == "block":
        return "{ … }"
    if k == "raw":
        return f["code"].splitlines()[0] if f["code"] else ""
    return k


_ID = re.compile(r"[A-Za-z_$][\w$]*")
_CALL = re.compile(r"([A-Za-z_$][\w$]*)\s*\(")
_RESERVED = {
    "await", "break", "case", "catch", "class", "const", "continue", "debugger",
    "default", "delete", "do", "else", "export", "extends", "finally", "for",
    "function", "if", "import", "in", "instanceof", "let", "new", "return", "static",
    "super", "switch", "this", "throw", "try", "typeof", "var", "void", "while",
    "with", "yield", "async", "of", "get", "set", "null", "true", "false",
    "undefined",
}


def _names(text: str) -> list[str]:
    out: list[str] = []
    for m in _ID.findall(text or ""):
        if m not in _RESERVED and m not in out:
            out.append(m)
    return out


def _declared(declarators: str) -> list[str]:
    out = []
    for part in model.split_top_level(declarators):
        out += _names(part.split("=", 1)[0])
    return out


def describe_io(block: Block) -> tuple[str, str]:  # noqa: C901
    f = block.fields
    k = block.kind
    j = ", ".join
    if k in ("function", "method"):
        rets = [b.fields["value"] for b in model.walk(block.children())
                if b.kind == "return" and b.fields["value"]]
        return j(_names(f["params"])), j(dict.fromkeys(rets)) if rets else "undefined"
    if k == "class":
        return f.get("superclass", ""), f["name"]
    if k == "decl":
        values = [p.split("=", 1)[1] for p in model.split_top_level(f["declarators"])
                  if "=" in p]
        return j(_names(" ".join(values))), j(_declared(f["declarators"]))
    if k == "foreach":
        return j(_names(f["right"])), j(_names(f["left"]))
    if k == "for":
        return j(_names(f["test"])), j(_declared(f["init"]))
    if k in ("if", "elif", "while", "dowhile"):
        return j(_names(f["test"])), ""
    if k == "switch":
        return j(_names(f["subject"])), ""
    if k == "case":
        return j(_names(f["test"])), ""
    if k == "catch":
        return "", f.get("param", "")
    if k == "return":
        return j(_names(f["value"])), f["value"]
    if k == "throw":
        return j(_names(f["value"])), ""
    if k in ("call", "expr"):
        return j(_names(f["value"])), ""
    if k == "import":
        return "", j(_names(f["spec"].split(" from ")[0]))
    if k == "export":
        return j(_names(f["spec"])), ""
    return "", ""


def called_names(block: Block) -> list[str]:
    """Names called in this block's own text (not its children).

    A definition's header, e.g. ``function area(radius)``, looks like a call
    of its own name to the regex, so that name is excluded for definitions.
    """
    names = [m for m in _CALL.findall(model.shallow_source(block)) if m not in _RESERVED]
    if block.kind in JAVASCRIPT.def_kinds:
        own = block.fields.get("name")
        names = [n for n in names if n != own]
    return names


def function_block(entry: FunctionEntry) -> Block:
    if entry.result:
        block = _nb("decl", keyword="const", declarators=f"{entry.result} = {entry.template}")
    else:
        block = _nb("call", value=entry.template)
    block.desc_in = entry.inputs
    block.desc_out = entry.outputs
    return block


# --------------------------------------------------------------------------
# Palette, functions and docs
# --------------------------------------------------------------------------
def _pe(word, kind, fields=(), slot=None, note=""):
    return PaletteEntry(word, kind, tuple(fields), slot, note, LANG)


PALETTE: list[PaletteEntry] = [
    _pe("async", "function", [("async", "async"), ("name", "load")]),
    _pe("await", "expr", [("value", "await promise")]),
    _pe("break", "break"),
    _pe("case", "case", slot="cases", note="Adds a case to the selected switch."),
    _pe("catch", "catch", slot="handlers", note="Adds a catch to the selected try."),
    _pe("class", "class"),
    _pe("const", "decl", [("keyword", "const")]),
    _pe("continue", "continue"),
    _pe("debugger", "raw", [("code", "debugger;")]),
    _pe("default", "case", [("test", "")], slot="cases",
        note="Adds the default case to the selected switch."),
    _pe("delete", "expr", [("value", "delete obj.key")]),
    _pe("do", "dowhile"),
    _pe("else", "raw", [("code", "// todo")], slot="orelse",
        note="Adds an else slot to the selected if."),
    _pe("export", "decl", [("export", "export"), ("keyword", "const")]),
    _pe("extends", "class", [("superclass", "Base")]),
    _pe("finally", "raw", [("code", "// todo")], slot="finalbody",
        note="Adds a finally slot to the selected try."),
    _pe("for", "for"),
    _pe("function", "function"),
    _pe("if", "if"),
    _pe("import", "import"),
    _pe("in", "foreach", [("left", "const key"), ("op", "in"), ("right", "obj")]),
    _pe("instanceof", "decl", [("declarators", "ok = value instanceof Type")]),
    _pe("let", "decl", [("keyword", "let")]),
    _pe("new", "decl", [("declarators", "obj = new Type()")]),
    _pe("of", "foreach"),
    _pe("return", "return"),
    _pe("static", "method", [("static", "static")], note="Add inside a class."),
    _pe("super", "call", [("value", "super()")]),
    _pe("switch", "switch"),
    _pe("this", "expr", [("value", "this.value = value")]),
    _pe("throw", "throw"),
    _pe("try", "try"),
    _pe("typeof", "decl", [("declarators", "kind = typeof value")]),
    _pe("var", "decl", [("keyword", "var")]),
    _pe("void", "expr", [("value", "void 0")]),
    _pe("while", "while"),
    _pe("yield", "expr", [("value", "yield value")]),
    _pe("get", "method", [("accessor", "get")], note="Add inside a class."),
    _pe("set", "method", [("accessor", "set"), ("params", "value")],
        note="Add inside a class."),
    _pe("null", "decl", [("keyword", "let"), ("declarators", "name = null")]),
    _pe("true", "decl", [("declarators", "flag = true")]),
    _pe("false", "decl", [("declarators", "flag = false")]),
]

PALETTE_EXTRA: list[PaletteEntry] = [
    _pe("call", "call"),
    _pe("expression", "expr"),
    _pe("else if", "elif", slot="orelse", note="Adds an else-if to the selected if."),
    _pe("method", "method", note="Add inside a class."),
    _pe("{ block }", "block"),
    _pe("raw code", "raw"),
]


def _fn(word, params, template, result, doc, inputs, outputs):
    return FunctionEntry(word, params, template, result, doc, inputs, outputs, LANG)


FUNCTIONS: list[FunctionEntry] = [
    _fn("console.log", "...values", "console.log(value)", "",
        "Prints values to the console.", "values to show", "nothing"),
    _fn("console.error", "...values", "console.error(error)", "",
        "Prints an error message to the console.", "values to show", "nothing"),
    _fn("parseInt", "text, radix", "parseInt(text, 10)", "number",
        "Converts text to a whole number.", "string (and base)", "integer or NaN"),
    _fn("parseFloat", "text", "parseFloat(text)", "number",
        "Converts text to a decimal number.", "string", "number or NaN"),
    _fn("String", "value", "String(value)", "text",
        "Converts a value to a string.", "any value", "string"),
    _fn("Number", "value", "Number(value)", "number",
        "Converts a value to a number.", "any value", "number or NaN"),
    _fn("Boolean", "value", "Boolean(value)", "flag",
        "The truth value of anything.", "any value", "true or false"),
    _fn("Array.isArray", "value", "Array.isArray(value)", "isList",
        "Whether a value is an array.", "any value", "true or false"),
    _fn("Array.from", "iterable", "Array.from(items)", "list",
        "Builds an array from an iterable or array-like.", "iterable", "new array"),
    _fn("Object.keys", "obj", "Object.keys(obj)", "keys",
        "The object's own property names.", "object", "array of strings"),
    _fn("Object.values", "obj", "Object.values(obj)", "values",
        "The object's own property values.", "object", "array"),
    _fn("Object.entries", "obj", "Object.entries(obj)", "entries",
        "The object's own [key, value] pairs.", "object", "array of pairs"),
    _fn("Object.assign", "target, ...sources", "Object.assign({}, source)", "merged",
        "Copies properties from sources into the target.",
        "target, sources", "the target"),
    _fn("JSON.stringify", "value", "JSON.stringify(value)", "json",
        "Turns a value into JSON text.", "any value", "string"),
    _fn("JSON.parse", "text", "JSON.parse(text)", "data",
        "Turns JSON text back into a value.", "string", "value"),
    _fn("Math.floor", "number", "Math.floor(number)", "whole",
        "Rounds down to an integer.", "number", "integer"),
    _fn("Math.round", "number", "Math.round(number)", "rounded",
        "Rounds to the nearest integer.", "number", "integer"),
    _fn("Math.max", "...numbers", "Math.max(a, b)", "largest",
        "The largest of the numbers.", "numbers", "number"),
    _fn("Math.min", "...numbers", "Math.min(a, b)", "smallest",
        "The smallest of the numbers.", "numbers", "number"),
    _fn("Math.random", "", "Math.random()", "chance",
        "A random number from 0 up to (not including) 1.", "nothing", "number"),
    _fn("Math.abs", "number", "Math.abs(number)", "magnitude",
        "Absolute value.", "number", "non-negative number"),
    _fn("setTimeout", "callback, ms", "setTimeout(() => {}, 1000)", "timer",
        "Runs a function once after a delay.", "function, milliseconds", "timer id"),
    _fn("setInterval", "callback, ms", "setInterval(() => {}, 1000)", "timer",
        "Runs a function repeatedly with a delay.", "function, milliseconds", "timer id"),
    _fn("clearTimeout", "id", "clearTimeout(timer)", "",
        "Cancels a timer from setTimeout or setInterval.", "timer id", "nothing"),
    _fn("fetch", "url, options", "await fetch(url)", "response",
        "Requests a URL over the network (returns a promise).",
        "url, options", "Response"),
    _fn("isNaN", "value", "isNaN(value)", "notNumber",
        "Whether a value is not a number.", "any value", "true or false"),
    _fn("alert", "message", "alert(message)", "",
        "Shows a message box in the browser.", "message", "nothing"),
    _fn("prompt", "message", "prompt(message)", "answer",
        "Asks the user for text in the browser.", "message", "string or null"),
    _fn("Date.now", "", "Date.now()", "now",
        "The current time in milliseconds since 1970.", "nothing", "number"),
    _fn("Promise.all", "promises", "await Promise.all(promises)", "results",
        "Waits for every promise in a list.", "array of promises", "array of results"),
]

DOCS: dict[str, tuple[str, dict[str, str]]] = {
    "function": ("Defines a function: a named, reusable piece of code that runs "
                 "when it is called.",
                 {"export": "Empty, export, or export default to share it from the module.",
                  "async": "Make it return a promise and allow await inside.",
                  "generator": "* makes a generator that can yield values.",
                  "name": "The name used to call the function.",
                  "params": "Parameter list as written inside the parentheses."}),
    "class": ("Defines a class: a blueprint for objects with methods.",
              {"export": "Empty, export, or export default.",
               "name": "The class name, used with new.",
               "superclass": "Parent class to extend, if any."}),
    "method": ("A function that belongs to a class and is called on its instances "
               "(or the class itself when static).",
               {"static": "Belongs to the class rather than to instances.",
                "async": "Returns a promise and allows await.",
                "generator": "* makes a generator method.",
                "accessor": "get or set to define a property accessor.",
                "name": "The method name; constructor runs on new.",
                "params": "Parameter list inside the parentheses."}),
    "if": ("Runs the body only when the condition is true; otherwise runs the else "
           "slot, if any.", {"test": "The condition, evaluated for truth."}),
    "elif": ("else if: another condition tried only when the preceding one was false.",
             {"test": "The condition, evaluated for truth."}),
    "for": ("Classic counting loop: runs init once, repeats while test is true, "
            "running update after each pass.",
            {"init": "Runs once before the loop, e.g. let i = 0.",
             "test": "Checked before every pass; the loop ends when false.",
             "update": "Runs after every pass, e.g. i++."}),
    "foreach": ("Loops over the values (of) or keys (in) of something.",
                {"left": "Variable that receives each value, e.g. const item.",
                 "op": "of for values of an iterable, in for property names.",
                 "right": "The iterable or object being looped over."}),
    "while": ("Repeats the body for as long as the condition stays true.",
              {"test": "Checked before every pass."}),
    "dowhile": ("Runs the body once, then repeats while the condition is true.",
                {"test": "Checked after every pass."}),
    "try": ("Runs the body and lets catch handle any error thrown; finally always "
            "runs last.", {}),
    "catch": ("Handles an error thrown in the try body.",
              {"param": "Variable bound to the thrown error (may be empty)."}),
    "switch": ("Compares a value against each case and runs the matching one "
               "(falling through until a break).",
               {"subject": "The value being compared."}),
    "case": ("One branch of a switch. Its body runs when the value matches, until "
             "a break.", {"test": "Value to compare with; empty means default."}),
    "return": ("Ends the current function and hands a value back to the caller.",
               {"value": "Expression to return; empty returns undefined."}),
    "throw": ("Throws an error, stopping normal flow until a catch handles it.",
              {"value": "The error to throw, usually new Error('message')."}),
    "break": ("Leaves the innermost loop or switch immediately.", {}),
    "continue": ("Skips the rest of this pass and starts the next one.", {}),
    "decl": ("Declares one or more variables, optionally with initial values.",
             {"export": "export to share the variables from the module.",
              "keyword": "const (fixed), let (changeable) or var (old style).",
              "declarators": "name = value pairs, separated by commas."}),
    "call": ("Calls a function or method for its effect; the result is discarded.",
             {"value": "The call expression, e.g. console.log(x)."}),
    "expr": ("Evaluates an expression as a statement, e.g. an assignment or await.",
             {"value": "The expression."}),
    "import": ("Loads names from another module.",
               {"spec": "What to import and from where, e.g. { a } from './m.js'."}),
    "export": ("Shares names from this module.",
               {"spec": "What to export, e.g. { a, b } or default value."}),
    "block": ("A bare block that groups statements and scopes let/const.", {}),
    "raw": ("Source kept exactly as written, for statements the editor does not "
            "model.", {"code": "The verbatim code."}),
}


JAVASCRIPT = model.Language(
    name=LANG, label="JavaScript", extensions=(".js", ".mjs", ".cjs", ".jsx"),
    specs=SPECS, docs=DOCS, palette=PALETTE, palette_extra=PALETTE_EXTRA,
    functions=FUNCTIONS,
    slot_hosts={"orelse": "if", "handlers": "try", "finalbody": "try", "cases": "switch"},
    slot_fillers={"handlers": "catch", "cases": "case"},
    placeholder_kind="raw",
    def_kinds=frozenset({"function", "class", "method"}),
    loop_kinds=frozenset({"for", "foreach", "while", "dowhile"}),
    terminators=frozenset({"return", "throw", "break", "continue"}),
    parse=parse, generate=generate, syntax_error=syntax_error,
    title=title, keyword=keyword, describe_io=describe_io,
    called_names=called_names, function_block=function_block,
    note="Comments are not kept when a file is re-generated. Needs the esprima package.",
    slot_labels=SLOT_LABELS,
)
model.LANGUAGES[LANG] = JAVASCRIPT
