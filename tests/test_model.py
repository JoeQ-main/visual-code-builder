import ast
import textwrap

import pytest

from vcb import model

SAMPLE = textwrap.dedent('''\
    import os
    from sys import argv as a, path
    from . import sibling

    GLOBAL_X: int = 3
    y = z = 4
    y += 1

    @decorator
    def helper(a, b=2, *args, c, **kw) -> int:
        """doc"""
        global GLOBAL_X
        if a > b:
            return a
        elif a == b:
            pass
        else:
            del a
        for i in range(3):
            if i:
                continue
            break
        else:
            print("done")
        while True:
            break
        try:
            x = 1 / 0
        except ZeroDivisionError as e:
            raise ValueError("bad") from e
        except (TypeError, KeyError):
            pass
        else:
            assert x, "msg"
        finally:
            x = None
        with open("f") as fh, open("g") as gh:
            fh.write(gh.read())
        return helper(a, b)

    async def runner():
        async with lock:
            await thing()
        async for item in gen():
            yield item

    class Thing(Base, metaclass=Meta):
        attr = 1

        def method(self):
            nonlocal_free = lambda q: q + 1
            return helper(1, 2)

    match command:
        case ["go", direction] if direction:
            print(direction)
        case _:
            pass

    type Alias = list[int]
    helper(1, 2)
    ''')


def roundtrip(src: str) -> str:
    blocks = model.parse_source(src)
    out = model.generate_source(blocks)
    assert ast.dump(ast.parse(out)) == ast.dump(ast.parse(src)), out
    return out


def test_roundtrip_sample():
    roundtrip(SAMPLE)


@pytest.mark.parametrize("src", [
    "pass",
    "x = 1",
    "x: int",
    "x -= 2",
    "print(1)",
    "1 + 1",
    "del a, b",
    "global a, b",
    "raise",
    "assert x",
    "return",
    "import a.b as c",
    "from ..pkg import *",
    "if x:\n    pass",
    "if x:\n    pass\nelse:\n    if y:\n        pass",
    "if x:\n    pass\nelif y:\n    pass\nelif z:\n    pass\nelse:\n    pass",
    "try:\n    pass\nfinally:\n    pass",
    "try:\n    pass\nexcept* ValueError:\n    pass",
    "while x:\n    pass\nelse:\n    pass",
    "class A:\n    pass",
    "def f[T](x: T) -> T:\n    return x",
    "match x:\n    case 1 | 2:\n        pass",
    "with a, b:\n    pass",
    "@d\n@e(1)\nclass A(B):\n    pass",
])
def test_roundtrip_each_kind(src):
    roundtrip(src)


def test_statement_kinds_and_nesting():
    blocks = model.parse_source(SAMPLE)
    kinds = [b.kind for b in blocks]
    assert kinds[:3] == ["import", "from", "from"]
    helper = next(b for b in blocks if b.kind == "def" and b.fields["name"] == "helper")
    assert helper.fields["args"] == "a, b=2, *args, c, **kw"
    assert helper.fields["returns"] == "int"
    assert helper.fields["decorators"] == "decorator"
    body_kinds = [b.kind for b in helper.slots["body"]]
    assert body_kinds == ["expr", "global", "if", "for", "while", "try", "with", "return"]
    if_block = helper.slots["body"][2]
    assert if_block.slots["orelse"][0].kind == "elif"
    assert if_block.slots["orelse"][0].slots["orelse"][0].kind == "del"
    for b in helper.slots["body"]:
        assert b.parent is helper and b.slot_name == "body"


def test_elif_vs_else_if_distinguished():
    blocks = model.parse_source("if x:\n    pass\nelse:\n    if y:\n        pass")
    assert blocks[0].slots["orelse"][0].kind == "if"


def test_try_slots():
    blocks = model.parse_source(SAMPLE)
    helper = blocks[6]
    try_block = helper.slots["body"][5]
    assert [h.kind for h in try_block.slots["handlers"]] == ["except", "except"]
    assert try_block.slots["handlers"][0].fields["type"] == "ZeroDivisionError"
    assert try_block.slots["handlers"][0].fields["name"] == "e"
    assert try_block.slots["finalbody"][0].kind == "assign"


def test_call_edges():
    blocks = model.parse_source(SAMPLE)
    edges = model.call_edges(blocks)
    helper = next(b for b in blocks if b.kind == "def" and b.fields["name"] == "helper")
    targets = {dst for _, dst in edges}
    assert helper in targets
    sources = [src for src, dst in edges if dst is helper]
    # recursive return, method return, and the module-level call
    assert len(sources) == 3
    assert all(s.kind in ("return", "call") for s in sources)


def test_new_block_from_palette_generates_valid_code():
    for entry in model.PALETTE:
        block = model.new_block(entry.kind)
        src = model.generate_source([block])
        ast.parse(src)


def test_generate_marks_invalid_but_still_returns_text():
    block = model.new_block("if")
    block.fields["test"] = "("
    src = model.generate_source([block])
    assert "if (" in src
    assert model.syntax_error(src) is not None


def test_insert_and_detach():
    root = model.new_block("def")
    child = model.new_block("pass")
    model.insert_block(root, "body", child, 0)
    assert child.parent is root and root.slots["body"] == [child]
    model.detach_block(child)
    assert child.parent is None and root.slots["body"] == []


def test_default_descriptions():
    blocks = model.parse_source(SAMPLE)
    helper = blocks[6]
    ins, outs = model.describe_io(helper)
    assert "a" in ins and "kw" in ins
    assert "int" in outs
    assign = blocks[4]
    ins, outs = model.describe_io(assign)
    assert outs == "y, z"


def test_walk_all_blocks_includes_nested():
    blocks = model.parse_source(SAMPLE)
    everything = list(model.walk(blocks))
    assert any(b.kind == "case" for b in everything)
    assert any(b.kind == "except" for b in everything)


def test_generate_records_line_spans():
    blocks = model.parse_source(SAMPLE)
    spans = {}
    src = model.generate_source(blocks, spans)
    lines = src.split("\n")
    for b in model.walk(blocks):
        assert b.id in spans, b.kind
    helper = blocks[6]
    first, last = spans[helper.id]
    assert lines[first - 1] == "@decorator"
    assert lines[first] .startswith("def helper(")
    assert lines[last - 1].strip() == "return helper(a, b)"
    if_block = helper.slots["body"][2]
    elif_block = if_block.slots["orelse"][0]
    f1, l1 = spans[if_block.id]
    f2, l2 = spans[elif_block.id]
    assert lines[f1 - 1].strip().startswith("if a > b")
    assert lines[f2 - 1].strip().startswith("elif a == b")
    assert f1 < f2 <= l2 <= l1
    try_block = helper.slots["body"][5]
    handler = try_block.slots["handlers"][0]
    hf, hl = spans[handler.id]
    assert lines[hf - 1].strip().startswith("except ZeroDivisionError")
    case = blocks[-3].slots["cases"][0]
    cf, cl = spans[case.id]
    assert lines[cf - 1].strip().startswith("case [")


def test_every_kind_and_field_is_documented():
    for kind, spec in model.SPECS.items():
        assert model.block_doc(kind), kind
        for name, _ in spec.fields:
            assert model.field_doc(kind, name), f"{kind}.{name}"


def test_thirty_common_functions_make_valid_blocks():
    assert len(model.FUNCTIONS) == 30
    assert len({f.word for f in model.FUNCTIONS}) == 30
    for entry in model.FUNCTIONS:
        block = model.block_from_function(entry)
        ast.parse(model.generate_source([block]))
        assert entry.word in block.fields["value"]
        assert block.desc_in and block.desc_out and entry.doc
    assert model.block_from_function(model.FUNCTIONS[0]).kind == "call"   # print
    assert model.block_from_function(model.FUNCTIONS[2]).kind == "assign"  # len
