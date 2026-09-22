"""The Nova app's stylesheet may not grow a new font size.

The design proposal counted 22 distinct `font-size` values in `style.css`
on 2026-08-24. On 2026-09-18 there were 31: nine more in under four
weeks, and nothing counted them.

Many of them sit close together, which is what Material's type-scale
guidance warns against. Rendered at a 390px viewport in light mode on 2026-09-18,
the `/plan` page draws 17 distinct text sizes, seven of them between
11.52px and 13.12px (0.72, 0.74, 0.75, 0.76, 0.78, 0.8 and 0.82rem) --
seven steps inside 1.6 pixels. Material's own scale has 11 distinct sizes
across 15 styles, from 11px to 57px, and its page says: "Sizes on the
rendered type scale should aim to provide impactful contrast between
sizes by avoiding small differences." Source, fetched and read on
2026-09-18: `nova/resources/design-wiki/material-3-type-scale.md`.

On 2026-09-22 the design run collapsed those seven onto two: 0.72, 0.74
and 0.76rem onto 0.75rem (12px), 0.78 and 0.82rem onto 0.8rem (12.8px).
No declaration moved by more than half a pixel. It rendered /, /issues,
/plan, /journal and /projects at 390px in light mode against the old
and the new stylesheet: /plan went from 17 drawn sizes to 12. One plan
row (`.next-num`) sat on a flex-wrap edge and 0.32px pushed its id onto
its own line, so that label took 0.75rem, the size of the chips it
sits beside. 31 distinct sizes became 26.

The count can go down, and it cannot go up. Lower `MAX_DISTINCT` in the
same change that removes a size.
"""

from __future__ import annotations

import re
from pathlib import Path

STYLE = Path(__file__).resolve().parents[1] / "agora_runner" / "nova_public" / "style.css"

MAX_DISTINCT = 26


def distinct_font_sizes(css: str) -> set:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return {m.strip() for m in re.findall(r"font-size\s*:\s*([^;}]+)", css)}


def test_parser_reads_every_form_the_stylesheet_uses():
    css = "a{font-size:0.8rem}/* font-size: 9px; */ b { font-size : 1.05em; } c{font-size: var(--x)}"
    assert distinct_font_sizes(css) == {"0.8rem", "1.05em", "var(--x)"}


def test_stylesheet_does_not_grow_a_font_size():
    sizes = distinct_font_sizes(STYLE.read_text())
    assert len(sizes) > 20, "read too few sizes to be measuring the real stylesheet"
    assert len(sizes) <= MAX_DISTINCT, (
        f"style.css has {len(sizes)} distinct font sizes, over the {MAX_DISTINCT} ratchet. "
        "Reuse an existing size rather than adding one: "
        + ", ".join(sorted(sizes))
    )
