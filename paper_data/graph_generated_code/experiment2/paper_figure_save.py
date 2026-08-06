"""Helpers for paper exports: tight content bbox, then pad to a target width:height."""

from __future__ import annotations

from matplotlib.figure import Figure
from matplotlib.transforms import Bbox


def savefig_tight_target_aspect(
    fig: Figure,
    path,
    target_wh: float,
    *,
    pad_inches: float = 0.03,
    dpi: int | None = None,
) -> None:
    """
    Save *fig* so the page has aspect width/height == *target_wh*.

    Starts from the usual tight bounding box (plus *pad_inches*), then expands
    the saved bbox with whitespace on one axis (never crops artists).
    """
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    bb = fig.get_tightbbox(renderer)
    if bb is None or bb.width <= 0 or bb.height <= 0:
        fig.savefig(path, bbox_inches="tight", pad_inches=pad_inches, dpi=dpi)
        return

    bb = bb.padded(pad_inches)
    w, h = float(bb.width), float(bb.height)
    cx = float(bb.x0) + 0.5 * w
    cy = float(bb.y0) + 0.5 * h
    cur = w / h
    if cur > target_wh:
        new_w, new_h = w, w / target_wh
    else:
        new_w, new_h = h * target_wh, h
    out = Bbox.from_bounds(cx - 0.5 * new_w, cy - 0.5 * new_h, new_w, new_h)
    kw: dict = {"bbox_inches": out, "pad_inches": 0}
    if dpi is not None:
        kw["dpi"] = dpi
    fig.savefig(path, **kw)
