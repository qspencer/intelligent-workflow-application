"""Generate the application logo used on OAuth consent screens.

The logo is a diamond-shaped DAG (4 nodes, fork-and-join), which is the
canonical workflow shape in this codebase — the PDF classifier, PR triage,
and paper triage workflows all share this topology after their agentic
step splits into a routing branch and an evaluation branch.

Rendered at 4x for crisp downsampling, then resized to 120x120 — the size
Google's OAuth consent screen actually displays. Run:

    backend/.venv/bin/python docs/assets/generate_logo.py

Re-run after editing constants below; commit both the script and the
resulting PNG.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

# Render large, then downscale with Lanczos — anti-aliased edges look
# much better than drawing at the target size directly.
SUPERSAMPLE = 4
TARGET = 120
SIZE = TARGET * SUPERSAMPLE

# White background reads cleanly against Google's consent UI.
BG = (255, 255, 255, 255)
# Deep indigo — readable at small sizes, professional, not Google blue.
FG = (40, 60, 130, 255)


def draw_logo() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), BG)
    draw = ImageDraw.Draw(img)

    # Diamond layout. Padding is proportional so the design re-renders
    # cleanly at any SIZE.
    pad = int(SIZE * 0.18)
    node_r = int(SIZE * 0.10)
    edge_w = int(SIZE * 0.030)

    cx = SIZE // 2
    cy = SIZE // 2
    top = (cx, pad)
    left = (pad, cy)
    right = (SIZE - pad, cy)
    bottom = (cx, SIZE - pad)

    # Edges first so the node circles draw over their endpoints.
    for a, b in [(top, left), (top, right), (left, bottom), (right, bottom)]:
        draw.line([a, b], fill=FG, width=edge_w)

    for nx, ny in [top, left, right, bottom]:
        draw.ellipse(
            [nx - node_r, ny - node_r, nx + node_r, ny + node_r],
            fill=FG,
        )

    return img.resize((TARGET, TARGET), Image.LANCZOS)


def main() -> None:
    out_path = Path(__file__).parent / "logo.png"
    img = draw_logo()
    img.save(out_path, "PNG", optimize=True)
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes, {TARGET}x{TARGET})")


if __name__ == "__main__":
    main()
