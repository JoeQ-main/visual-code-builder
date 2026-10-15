from pathlib import Path

import pytest

from vcb import js, layout, model  # noqa: F401 - js registers the language

pytest.importorskip("esprima")

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample.js"
JS = "javascript"


def roundtrip(src: str) -> str:
    """Generated text must parse, and must be a fixed point of parse/generate."""
    blocks = model.parse_source(src, JS)
    out = model.generate_source(blocks, lang=JS)
    assert model.syntax_error(out, JS) is None, out
    again = model.generate_source(model.parse_source(out, JS), lang=JS)
    assert again == out
    return out


def test_sample_roundtrips_and_has_expected_kinds():
    out = roundtrip(SAMPLE.read_text())
    blocks = model.parse_source(SAMPLE.read_text(), JS)
    kinds = [b.kind for b in blocks]
    assert kinds == ["import", "decl", "decl", "class", "function", "function",
                     "function", "function", "function", "function", "call"]
    item = blocks[3]
    assert [m.kind for m in item.slots["body"]] == ["method"] * 3
    assert item.slots["body"][2].fields["accessor"] == "get"
    report = blocks[8]
    loop = report.slots["body"][1]
    assert loop.kind == "for" and loop.fields["update"] == "i++"
    chain = loop.slots["body"][2]
    assert chain.kind == "if" and chain.slots["orelse"][0].kind == "elif"
    assert chain.slots["orelse"][0].slots["orelse"][0].kind == "call"
    main = blocks[9]
    assert main.fields["async"] == "async"
    while_block = main.slots["body"][4]
    try_block = while_block.slots["body"][1]
    assert try_block.slots["handlers"][0].kind == "catch"
    assert try_block.slots["handlers"][0].fields["param"] == "err"
    assert try_block.slots["finalbody"]
    assert main.slots["body"][5].kind == "dowhile"
    assert "} else if (subtotal > 100) {" in out
    assert "} while (attempts > 0);" in out


@pytest.mark.parametrize("src", [
    "x = 1;",
    "let a, b = 2;",
    "export const c = 3;",
    "export default class A extends B {}",
    "export { a as b };",
    "export * from './m.js';",
    "import def, { x } from 'm';",
    "if (a) b(); else if (c) d(); else { e(); }",
    "for (k in obj) {}",
    "for (;;) { break; }",
    "while (x) continue;",
    "try { f(); } catch (e) { g(); }",
    "try { f(); } finally { g(); }",
    "switch (v) { case 1: case 2: f(); break; default: g(); }",
    "class C { static m() {} get v() { return 1; } set v(x) {} *gen() {} async run() {} }",
    "function* gen(a = 1, ...rest) { yield a; }",
    "label: for (;;) { break label; }",
    "{ let scoped = 1; }",
    "throw new Error('x');",
    "const f = (a, b) => a + b;",
    "debugger;",
])
def test_roundtrip_each_statement_kind(src):
    roundtrip(src)


def test_syntax_error_reports_line():
    err = model.syntax_error("let x = 1;\nlet y = ;", JS)
    assert err is not None and err.lineno == 2
    assert model.syntax_error("let x = 1;", JS) is None
    with pytest.raises(SyntaxError):
        model.parse_source("let y = ;", JS)


def test_palette_and_functions_generate_valid_js():
    for entry in js.PALETTE + js.PALETTE_EXTRA:
        block = model.block_from_entry(entry)
        assert block.lang == JS
        src = model.generate_source([block])
        if entry.word in ("await", "yield", "this", "super", "static", "get", "set",
                          "method"):
            # only valid inside a function, generator, or class body
            src = f"class K extends B {{ *m() {{\n{src}}} }}\n" if entry.word in (
                "yield", "super") else f"async function w() {{\n{src}}}\n"
            if entry.word in ("static", "get", "set", "method"):
                src = f"class K {{\n{model.generate_source([block])}}}\n"
        elif entry.word in ("break", "continue"):
            src = f"while (x) {{\n{src}}}\n"
        elif entry.word == "return":
            src = f"function w() {{\n{src}}}\n"
        assert model.syntax_error(src, JS) is None, (entry.word, src)
    assert len(js.FUNCTIONS) == 30
    for fn in js.FUNCTIONS:
        block = model.block_from_function(fn)
        assert block.lang == JS and block.desc_in and block.desc_out
        src = model.generate_source([block])
        if "await" in fn.template:
            src = f"async function w() {{\n{src}}}\n"
        assert model.syntax_error(src, JS) is None, fn.word


def test_every_js_kind_and_field_documented():
    for kind, spec in js.SPECS.items():
        assert model.block_doc(kind, JS), kind
        for name, _ in spec.fields:
            assert model.field_doc(kind, name, JS), f"{kind}.{name}"


def test_call_edges_and_layout_use_js_kinds():
    blocks = model.parse_source(SAMPLE.read_text(), JS)
    edges = model.call_edges(blocks)
    names = {dst.fields["name"] for _, dst in edges}
    assert {"Item", "area", "parseLine", "loadItems", "classify", "report", "main",
            "total"} <= names
    layout.auto_arrange(blocks, show_desc=True)
    assert all(layout.column(b) == "defs" for b in blocks if b.kind in ("class", "function"))
    assert layout.column(blocks[-1]) == "main"
    main = blocks[9]
    loop = main.slots["body"][4]
    brk = loop.slots["body"][1].slots["body"][1]
    assert brk.kind == "break" and layout.enclosing_loop(brk) is loop
    assert layout.terminates(brk) and layout.is_loop(loop)
    assert layout.title_parts(main)[0] == "async function"
    assert layout.title_parts(blocks[3].slots["body"][2])[0] == "get"
    ins, outs = model.describe_io(blocks[4])
    assert ins == "radius" and "Math.PI" in outs
    assert model.describe_io(blocks[1])[1] == "TAX_RATE"
    spans = {}
    out = model.generate_source(blocks, spans, lang=JS)
    first, last = spans[blocks[4].id]
    lines = out.split("\n")
    assert lines[first - 1].startswith("function area(") and lines[last - 1] == "}"


def test_slot_labels_say_catch_for_js():
    blocks = model.parse_source("try { f(); } catch (e) { g(); }", JS)
    assert model.slot_label(blocks[0], "handlers") == "catch"
    py = model.parse_source("try:\n    pass\nexcept E:\n    pass")
    assert model.slot_label(py[0], "handlers") == "except"
