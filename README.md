# Visual Code Builder

Open any Python file and edit it as a canvas of connected, draggable blocks.
Every statement is a block and nested statements are blocks inside blocks. The
left column is the program flow in execution order; functions and classes sit in
a right-hand column and are entered only through calls. Solid arrows show
control flow, including loop-backs and `continue`/`break` jumps, and dashed
lines show calls to functions and classes defined on the canvas.

Works with **Python** and **JavaScript** files. The language follows the file
extension when you open a file, and File > New lets you pick it for a new file.
The Blocks and Functions tabs switch to the chosen language.

Requires Python 3.12 or newer. Python editing needs nothing else (tkinter ships
with Python). JavaScript editing needs the pure-Python Esprima parser:

```bash
python -m pip install esprima
```

```bash
python main.py
```

```bash
python main.py examples/sample.py
```

```bash
python main.py examples/sample.js
```

## Using it

- **Blocks tab** (left): one button per reserved word. Click it to add the
  block. If a container block (def, if, for, ...) is selected, the new block
  goes inside it. `else`, `elif`, `except`, `finally`, `case` add the matching
  slot to the selected block, or create a host block when none fits.
- **Files tab** (left): browse drives, home, or any folder, and double-click a
  `.py` file to open it. File > Open also works.
- **Canvas** (centre): click to select, drag to move. Drop a block inside
  another block's slot to nest it; drop it on empty canvas to make it top level.
  Moving a container moves everything nested inside it. Drag empty canvas to
  pan. Right-click a block to delete, duplicate, add slots, or pop it out to top
  level. Moving a top-level block up or down reorders it within its own column,
  so rearranging functions never reorders the main flow.
- **Editor** (right): edit every property of the selected block and give it
  custom input/output descriptions. View > "Show input/output descriptions"
  draws them on the blocks; blocks without custom text show automatic ones.
- **Generated code** (bottom): live, with a syntax check. File > Save or
  Save As writes it to disk.

Keys: Ctrl+N new, Ctrl+O open, Ctrl+S save, Ctrl+Shift+S save as, Del delete,
Ctrl+D duplicate, Ctrl+L auto-arrange.

## Limits

Files are parsed (Python with the `ast` module, JavaScript with Esprima) and
regenerated from the blocks, so comments are dropped and formatting is
normalised on save. Expressions inside a statement are edited as text, not as
sub-blocks. The Esprima port does not understand a few newer JavaScript forms,
such as top-level `await`, `catch` without a parameter, and class fields;
statements it cannot model are kept verbatim in a "raw" block.

## Tests

```bash
python -m pytest tests -q
```
