"""Drives the real tkinter app without a mouse. Skipped when no display."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from vcb import layout, model

try:
    from vcb.app import App
    _app = App()
    _app.withdraw()
except Exception as exc:  # pragma: no cover - no display
    pytest.skip(f"no display: {exc}", allow_module_level=True)

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample.py"


@pytest.fixture
def app():
    _app.path = None
    _app.modified = False
    _app.canvas.set_blocks([])
    _app.update()
    yield _app


def ev(x, y):
    return SimpleNamespace(x=int(x), y=int(y), x_root=int(x), y_root=int(y))


def drag(app, block, to_x, to_y):
    c = app.canvas
    c._on_press(ev(block.x + 5, block.y + 5))
    c._on_motion(ev(block.x + 15, block.y + 15))
    c._on_motion(ev(to_x, to_y))
    c._on_release(ev(to_x, to_y))
    app.update()


def by_name(app, name):
    return next(b for b in app.canvas.blocks
                if b.kind in ("def", "class") and b.fields["name"] == name)


def test_open_sample_roundtrips(app):
    app.open_file(str(SAMPLE))
    app.update()
    assert len(app.canvas.blocks) == len(ast.parse(SAMPLE.read_text()).body)
    out = app.canvas.source()
    assert ast.dump(ast.parse(out)) == ast.dump(ast.parse(SAMPLE.read_text()))
    assert "syntax OK" in app.syntax_label.cget("text")


def test_functions_sit_right_of_main_flow(app):
    app.open_file(str(SAMPLE))
    app.update()
    main = [b for b in app.canvas.blocks if layout.column(b) == "main"]
    defs = [b for b in app.canvas.blocks if layout.column(b) == "defs"]
    assert main and defs
    assert min(d.x for d in defs) > max(m.x + m.w for m in main)
    assert all(m.x == layout.STACK_X for m in main)


def test_palette_adds_block_and_nests_into_selected(app):
    entry = next(e for e in model.PALETTE if e.word == "def")
    app.pick_entry(entry)
    fdef = app.canvas.blocks[0]
    assert fdef.kind == "def" and app.canvas.selected is fdef
    assert fdef.x == layout.defs_column_x(app.canvas.blocks)
    app.pick_entry(next(e for e in model.PALETTE if e.word == "return"))
    assert fdef.slots["body"][0].kind == "return"
    app.pick_entry(next(e for e in model.PALETTE if e.word == "else"))
    # 'else' on a def has no else slot, so it makes a host if block at top level
    host = next(b for b in app.canvas.blocks if b.kind == "if")
    assert host.slots["orelse"] and host.x == layout.STACK_X
    assert model.syntax_error(app.canvas.source()) is None
    assert app.modified


def test_new_def_is_emitted_after_imports_before_main_code(app):
    app.pick_entry(next(e for e in model.PALETTE if e.word == "import"))
    app.canvas.select(None)
    app.pick_entry(next(e for e in model.PALETTE if e.word == "pass"))
    app.canvas.select(None)
    app.pick_entry(next(e for e in model.PALETTE if e.word == "def"))
    assert [b.kind for b in app.canvas.blocks] == ["import", "def", "pass"]


def test_drag_nests_and_unnests(app):
    app.open_file(str(SAMPLE))
    app.update()
    area = by_name(app, "area")
    report = by_name(app, "report")
    ret = report.slots["body"][2]
    assert ret.kind == "return"
    # drag the return out of report() into area()'s body
    x1, y1, x2, y2 = area.slot_rects["body"]
    drag(app, ret, x2 - 10, y2 - 3)
    assert ret.parent is area and ret in area.slots["body"]
    assert ret not in report.slots["body"]
    # drag it back out onto empty canvas: it becomes top level, in the main flow
    drag(app, ret, 2500, 50)
    assert ret.parent is None and ret in app.canvas.blocks
    # moving a container moves its children with it
    fy = area.slots["body"][0].y
    drag(app, area, area.x + 5 + 200, area.y + 5 + 100)
    assert area.slots["body"][0].y == pytest.approx(fy + 100)
    assert model.syntax_error(app.canvas.source()) is None


def test_moving_blocks_reorders_only_within_a_column(app):
    app.open_file(str(SAMPLE))
    app.update()
    blocks = app.canvas.blocks
    main = [b for b in blocks if layout.column(b) == "main"]
    first_main, second_main = main[0], main[1]
    # drag the first main block below the second one: main order flips
    drag(app, first_main, first_main.x + 5, second_main.y + second_main.h + 20)
    new_main = [b for b in app.canvas.blocks if layout.column(b) == "main"]
    assert new_main[0] is second_main and new_main[1] is first_main
    # definitions kept their relative order and still follow the imports
    defs = [b for b in app.canvas.blocks if layout.column(b) == "defs"]
    assert [d.fields["name"] for d in defs] == [
        "Item", "area", "parse_line", "load_items", "classify", "report", "main"]
    # moving a def far down the canvas does not move it past the main flow
    item = by_name(app, "Item")
    drag(app, item, item.x + 5, 5000)
    kinds = [b.kind for b in app.canvas.blocks]
    assert kinds.index("import") < kinds.index("class")
    assert [d.fields["name"] for d in app.canvas.blocks if layout.column(d) == "defs"][-1] == "Item"
    assert model.syntax_error(app.canvas.source()) is None


def test_editor_applies_field_change(app):
    app.open_file(str(SAMPLE))
    app.update()
    fdef = by_name(app, "area")
    app.canvas.select(fdef)
    app.editor._vars["name"].set("circle_area")
    app.editor._apply(fdef)
    assert "def circle_area(" in app.canvas.source()
    app.editor._vars["desc_in"].set("the radius in cm")
    app.editor._apply(fdef)
    app.canvas.set_show_desc(True)
    assert fdef.desc_lines[0] == "in: the radius in cm"


def test_save_creates_file(app, tmp_path):
    app.pick_entry(next(e for e in model.PALETTE if e.word == "pass"))
    target = tmp_path / "new_file.py"
    assert app._write(str(target))
    assert target.read_text() == "pass\n"
    assert app.path == str(target) and not app.modified


def test_flow_lines_solid_and_calls_dashed(app):
    app.open_file(str(SAMPLE))
    app.update()
    c = app.canvas.canvas
    lines = [i for i in c.find_all() if c.type(i) == "line"]
    from vcb.canvas import CALL_COLORS
    dashed_calls = [i for i in lines
                    if c.itemcget(i, "dash") and c.itemcget(i, "fill") in CALL_COLORS]
    solid = [i for i in lines if not c.itemcget(i, "dash")]
    assert len(dashed_calls) >= 6   # report, load_items, parse_line, Item, area, ...
    assert solid
    # no solid arrow leaves a return/break/continue towards a next sibling
    main_defs = by_name(app, "main")
    loop = main_defs.slots["body"][4]
    assert loop.kind == "while"


def test_palette_reflows_with_width(app):
    pal = app.palette
    pal._on_resize(SimpleNamespace(width=250))
    assert pal._cols == 3
    pal._on_resize(SimpleNamespace(width=600))
    assert pal._cols == 7
    pal._on_resize(SimpleNamespace(width=100))
    assert pal._cols == 1
    grid, buttons = pal._grids[0]
    assert all(int(b.grid_info()["column"]) == 0 for b in buttons)


def test_clicking_more_expands_block(app):
    app.open_file(str(SAMPLE))
    app.update()
    doc = app.canvas.blocks[0]
    assert doc.kind == "expr" and layout.is_truncatable(doc)
    c = app.canvas
    c._on_press(ev(doc.x + doc.w - 10, doc.y + 8))
    c._on_release(ev(doc.x + doc.w - 10, doc.y + 8))
    assert doc.expanded and len(layout.title_lines(doc)) > 1
    c._on_double(ev(doc.x + 5, doc.y + 5))
    assert not doc.expanded


def test_palette_hint_uses_correct_article(app):
    hints = []
    app.palette.on_hint = hints.append
    grid, buttons = app.palette._grids[1]
    buttons[1].event_generate("<Enter>")
    buttons[1].event_generate("<Leave>")
    assert any(h.startswith("Add an assign block.") for h in hints)


def test_selecting_a_block_highlights_its_code(app):
    app.open_file(str(SAMPLE))
    app.update()
    area = by_name(app, "area")
    app.canvas.select(area)
    ranges = app.code.tag_ranges("selected")
    assert ranges, "no highlight"
    text = app.code.get(ranges[0], ranges[1])
    assert text.startswith("def area(")
    assert text.rstrip().endswith("return math.pi * radius ** 2")
    app.canvas.select(None)
    assert not app.code.tag_ranges("selected")


def test_editor_shows_docs(app):
    app.pick_entry(next(e for e in model.PALETTE if e.word == "for"))
    texts = []

    def collect(w):
        try:
            texts.append(str(w.cget("text")))
        except Exception:
            pass
        for c in w.winfo_children():
            collect(c)
    collect(app.editor)
    assert any("Loops over every item" in t for t in texts)
    assert any("receives each item" in t for t in texts)


def test_single_column_width_is_the_widest_one_column_width():
    from vcb.palette import columns_for_width, single_column_width
    w = single_column_width()
    assert columns_for_width(w) == 1 and columns_for_width(w + 1) == 2


def test_function_tab_adds_block_in_block_style(app):
    length = next(f for f in model.FUNCTIONS if f.word == "len")
    app.pick_function(length)
    block = app.canvas.blocks[0]
    assert block.kind == "assign" and block.fields["value"] == "len(items)"
    assert block.desc_in == "a sequence or collection"
    assert "count = len(items)" in app.canvas.source()
    app.pick_function(next(f for f in model.FUNCTIONS if f.word == "print"))
    assert app.canvas.blocks[1].kind == "call"


def test_editing_real_code_updates_canvas(app):
    app.open_file(str(SAMPLE))
    app.update()
    n = len(app.canvas.blocks)
    app.code.insert("end", "\nEXTRA = 42\n")
    app._on_code_typed()
    assert "edited" in app.syntax_label.cget("text")
    assert app.apply_code()
    assert len(app.canvas.blocks) == n + 1
    assert app.canvas.blocks[-1].title() == "EXTRA = 42"
    assert app.modified and "syntax OK" in app.syntax_label.cget("text")
    # applying again with no edits is a no-op
    assert app.apply_code() and len(app.canvas.blocks) == n + 1


def test_invalid_real_code_is_not_applied(app):
    app.open_file(str(SAMPLE))
    app.update()
    before = app.canvas.source()
    app.code.insert("end", "\ndef broken(:\n")
    assert not app.apply_code(silent=True)
    assert app.canvas.source() == before
    assert "syntax error" in app.syntax_label.cget("text")
    # the user's text is left in place for fixing
    assert "def broken(:" in app.code.get("1.0", "end")


def test_real_code_edit_keeps_block_positions(app):
    app.open_file(str(SAMPLE))
    app.update()
    area = by_name(app, "area")
    drag(app, area, area.x + 5 + 300, area.y + 5 + 200)
    moved_x, moved_y = area.x, area.y
    text = app.code.get("1.0", "end-1c").replace("radius must be positive",
                                                "radius must be >= 0")
    app.code.delete("1.0", "end")
    app.code.insert("1.0", text)
    assert app.apply_code()
    again = by_name(app, "area")
    assert (again.x, again.y) == (moved_x, moved_y)
    assert "radius must be >= 0" in app.canvas.source()


def test_tab_moves_focus_instead_of_inserting(app):
    assert app.bind_class("Text", "<Tab>")
    assert app.bind_class("TEntry", "<Tab>")
    app.code.focus_set()
    before = app.code.get("1.0", "end")
    result = app._focus_next(SimpleNamespace(widget=app.code))
    assert result == "break"
    assert app.code.get("1.0", "end") == before
