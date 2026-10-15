import textwrap

from vcb import layout, model

SRC = textwrap.dedent('''\
    def f(a):
        if a:
            return 1
        else:
            return 2

    x = f(3)
    ''')


def inside(inner, outer):
    """Nested blocks sit below the parent's header, indented, and never
    extend past its bottom. Their right end deliberately overhangs."""
    return (outer.x < inner.x and outer.y < inner.y
            and inner.y + inner.h <= outer.y + outer.h)


def test_children_nest_inside_parent():
    blocks = model.parse_source(SRC)
    layout.auto_arrange(blocks, show_desc=False)
    fdef = blocks[0]
    if_block = fdef.slots["body"][0]
    assert inside(if_block, fdef)
    for child in if_block.children():
        assert inside(child, if_block)
    assert "orelse" in if_block.slot_rects and "body" in if_block.slot_rects


def test_definitions_go_in_right_column_and_main_flow_stacks_left():
    src = "import os\ndef f():\n    pass\nx = 1\nclass A:\n    pass\ny = 2\n"
    blocks = model.parse_source(src)
    layout.auto_arrange(blocks, show_desc=True)
    imp, fdef, x, cls, y = blocks
    assert imp.x == x.x == y.x == layout.STACK_X
    assert fdef.x == cls.x == layout.defs_column_x(blocks)
    assert fdef.x > max(b.x + b.w for b in (imp, x, y))
    assert imp.y + imp.h < x.y < x.y + x.h < y.y
    assert fdef.y + fdef.h < cls.y
    # the block list (module order) is untouched by the layout
    assert [b.kind for b in blocks] == ["import", "def", "assign", "class", "assign"]


def test_flow_helpers():
    blocks = model.parse_source(
        "for i in x:\n    if i:\n        continue\n    break\n")
    loop = blocks[0]
    cont = loop.slots["body"][0].slots["body"][0]
    brk = loop.slots["body"][1]
    assert layout.enclosing_loop(cont) is loop
    assert layout.enclosing_loop(brk) is loop
    assert layout.enclosing_loop(loop) is None
    assert layout.terminates(brk) and not layout.terminates(loop)


def test_descriptions_change_height():
    blocks = model.parse_source(SRC)
    layout.auto_arrange(blocks, show_desc=False)
    h0 = blocks[0].h
    layout.auto_arrange(blocks, show_desc=True)
    assert blocks[0].h > h0
    assert blocks[0].desc_lines[0].startswith("in: a")


def test_block_at_returns_deepest():
    blocks = model.parse_source(SRC)
    layout.auto_arrange(blocks, show_desc=False)
    ret = blocks[0].slots["body"][0].slots["body"][0]
    hit = layout.block_at(blocks, ret.x + 5, ret.y + 5)
    assert hit is ret
    assert layout.block_at(blocks, -100, -100) is None


def test_drop_target_finds_slot_and_index():
    blocks = model.parse_source(SRC)
    layout.auto_arrange(blocks, show_desc=False)
    if_block = blocks[0].slots["body"][0]
    x1, y1, x2, y2 = if_block.slot_rects["orelse"]
    target = layout.drop_target(blocks, (x1 + x2) / 2, y2 - 2, moving=blocks[1])
    assert target is not None
    container, slot, index = target
    assert container is if_block and slot == "orelse" and index == 1
    # a block cannot be dropped into itself
    assert layout.drop_target(blocks, x1 + 1, y1 + 1, moving=blocks[0]) is None


def test_long_text_expands_into_wrapped_lines():
    long = "x = " + " + ".join(f"value_{i}" for i in range(30))
    blocks = model.parse_source(long)
    b = blocks[0]
    assert layout.is_truncatable(b)
    layout.auto_arrange(blocks, show_desc=False)
    h_collapsed = b.h
    assert layout.title_lines(b)[0].endswith("\u2026")
    b.expanded = True
    layout.auto_arrange(blocks, show_desc=False)
    lines = layout.title_lines(b)
    assert len(lines) > 1 and "".join(lines) == b.title()
    assert all(len(line) <= layout.MAX_TITLE_CHARS for line in lines)
    assert b.h > h_collapsed
    assert not layout.is_truncatable(model.parse_source("x = 1")[0])


def test_children_form_a_staircase_to_the_right():
    blocks = model.parse_source(SRC)
    layout.auto_arrange(blocks, show_desc=False)
    fdef = blocks[0]
    if_block = fdef.slots["body"][0]
    ret = if_block.slots["body"][0]
    assert if_block.x - fdef.x == layout.SLOT_INDENT
    assert (if_block.x + if_block.w) - (fdef.x + fdef.w) == layout.DEPTH_STEP
    assert (ret.x + ret.w) - (if_block.x + if_block.w) == layout.DEPTH_STEP
    # a very wide grandchild still pushes its ancestors wide enough
    wide = model.parse_source("def f():\n    if x:\n        y = " + "a" * 120)
    layout.auto_arrange(wide, show_desc=False)
    outer, inner = wide[0], wide[0].slots["body"][0]
    leaf = inner.slots["body"][0]
    assert leaf.x + leaf.w == inner.x + inner.w + layout.DEPTH_STEP
    assert inner.x + inner.w == outer.x + outer.w + layout.DEPTH_STEP


def test_title_parts_split_keyword():
    blocks = model.parse_source("async def f():\n    return 1\nx = 2\ntry:\n    pass\nexcept* E:\n    pass\n")
    assert layout.title_parts(blocks[0]) == ("async def", " f()")
    assert layout.title_parts(blocks[0].slots["body"][0]) == ("return", " 1")
    assert layout.title_parts(blocks[1]) == ("", "x = 2")
    assert layout.title_parts(blocks[2]) == ("try*", "")


def test_push_apart_resolves_overlaps_after_height_change():
    blocks = model.parse_source("def f():\n    pass\n\n\ndef g():\n    pass\n")
    layout.auto_arrange(blocks, show_desc=False)
    f, g = blocks
    f.desc_in = "lots of description text"
    f.desc_out = "and more"
    f.slots["body"][0].desc_in = "inner"
    layout.layout_all(blocks, show_desc=True)   # f grows, g stays put
    assert g.y < f.y + f.h
    layout.push_apart(blocks)
    assert g.y >= f.y + f.h
    # blocks in different columns are left alone
    blocks = model.parse_source("x = 1\ndef f():\n    pass\n")
    layout.auto_arrange(blocks, show_desc=False)
    ys = [b.y for b in blocks]
    layout.push_apart(blocks)
    assert [b.y for b in blocks] == ys


def test_assign_lanes_separates_overlapping_spans():
    spans = [(10, 100), (50, 200), (150, 300), (400, 500), (90, 95)]
    lanes = layout.assign_lanes(spans)
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            a, b = sorted(spans[i]), sorted(spans[j])
            overlap = not (a[1] + 4 < b[0] - 4 or b[1] + 4 < a[0] - 4)
            if overlap:
                assert lanes[i] != lanes[j], (spans[i], spans[j])
    # disjoint spans share lane 0 and the packing stays compact
    assert lanes[3] == 0 and max(lanes) <= 2
    assert layout.assign_lanes([]) == []
    assert layout.assign_lanes([(5, 1)]) == [0]
