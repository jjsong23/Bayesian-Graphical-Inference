#!/usr/bin/env python3
"""Plot retained STRING combined scores and their converted Bayes factors."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = (
    PROJECT_ROOT
    / "results"
    / "backend_bayes_factor_catalogs"
    / "edge_factors_891"
    / "string_v12_bf_gt1.tsv.gz"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "results"
    / "sensitivity_analysis"
    / "string_score_bf_distributions_2026-09-23.png"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "arialbd.ttf" if bold else "arial.ttf"
    return ImageFont.truetype(name, size=size)


def nice_ceiling(value: float) -> int:
    magnitude = 10 ** math.floor(math.log10(max(value, 1)))
    return int(math.ceil(value / magnitude) * magnitude)


def draw_histogram(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    bounds: tuple[int, int, int, int],
    counts: np.ndarray,
    edges: np.ndarray,
    *,
    title: str,
    x_label: str,
    x_ticks: list[tuple[float, str]],
    color: str,
    reference_lines: list[tuple[float, str, str, int]],
) -> None:
    left, top, right, bottom = bounds
    plot_left, plot_top = left + 92, top + 52
    plot_right, plot_bottom = right - 24, bottom - 76
    plot_width = plot_right - plot_left
    plot_height = plot_bottom - plot_top
    x_min, x_max = float(edges[0]), float(edges[-1])
    y_max = nice_ceiling(float(counts.max()) * 1.02)

    draw.text((left, top), title, fill="#1f2933", font=font(25, bold=True))
    for index in range(6):
        value = y_max * index / 5
        y = plot_bottom - value / y_max * plot_height
        draw.line((plot_left, y, plot_right, y), fill="#d9dee3", width=1)
        label = f"{int(value):,}"
        label_box = draw.textbbox((0, 0), label, font=font(17))
        draw.text(
            (plot_left - 12 - (label_box[2] - label_box[0]), y - 9),
            label,
            fill="#4f5b66",
            font=font(17),
        )

    def x_pixel(value: float) -> float:
        return plot_left + (value - x_min) / (x_max - x_min) * plot_width

    bar_width = plot_width / len(counts)
    for index, count in enumerate(counts):
        x0 = plot_left + index * bar_width + 1
        x1 = plot_left + (index + 1) * bar_width - 1
        y0 = plot_bottom - float(count) / y_max * plot_height
        draw.rectangle((x0, y0, x1, plot_bottom), fill=color)

    draw.line((plot_left, plot_top, plot_left, plot_bottom), fill="#59636e", width=2)
    draw.line((plot_left, plot_bottom, plot_right, plot_bottom), fill="#59636e", width=2)
    for value, label in x_ticks:
        x = x_pixel(value)
        draw.line((x, plot_bottom, x, plot_bottom + 7), fill="#59636e", width=2)
        box = draw.textbbox((0, 0), label, font=font(17))
        draw.text(
            (x - (box[2] - box[0]) / 2, plot_bottom + 11),
            label,
            fill="#4f5b66",
            font=font(17),
        )

    for value, label, line_color, label_row in reference_lines:
        x = x_pixel(value)
        dash = 9
        y = plot_top
        while y < plot_bottom:
            draw.line((x, y, x, min(y + dash, plot_bottom)), fill=line_color, width=3)
            y += dash * 2
        label_y = plot_top + label_row * 30
        label_box = draw.textbbox((0, 0), label, font=font(17, bold=True))
        label_width = label_box[2] - label_box[0]
        label_x = min(max(x + 8, plot_left + 4), plot_right - label_width - 4)
        draw.text((label_x, label_y), label, fill=line_color, font=font(17, bold=True))

    x_box = draw.textbbox((0, 0), x_label, font=font(19))
    draw.text(
        ((plot_left + plot_right - (x_box[2] - x_box[0])) / 2, bottom - 27),
        x_label,
        fill="#26323d",
        font=font(19),
    )
    y_label = "Retained relationship count"
    label_box = draw.textbbox((0, 0), y_label, font=font(19))
    label_image = Image.new(
        "RGBA", (label_box[2] - label_box[0] + 6, label_box[3] - label_box[1] + 6), (255, 255, 255, 0)
    )
    ImageDraw.Draw(label_image).text((3, 3 - label_box[1]), y_label, fill="#26323d", font=font(19))
    rotated = label_image.rotate(90, expand=True)
    image.alpha_composite(
        rotated,
        (left + 5, int(plot_top + (plot_height - rotated.height) / 2)),
    )


def main() -> int:
    args = parse_args()
    table = pd.read_csv(args.input, sep="\t")
    scores = pd.to_numeric(table["combined_score"], errors="raise").to_numpy(float)
    factors = pd.to_numeric(table["bayes_factor"], errors="raise").to_numpy(float)
    score_counts, score_edges = np.histogram(scores, bins=np.linspace(0.0, 1.0, 51))
    log_factors = np.log10(factors)
    factor_counts, factor_edges = np.histogram(log_factors, bins=np.linspace(0.0, 4.5, 46))

    image = Image.new("RGBA", (1800, 760), "white")
    draw = ImageDraw.Draw(image)
    heading = f"STRING v12 score and Bayes-factor distributions (n = {len(table):,})"
    draw.text((60, 28), heading, fill="#17212b", font=font(32, bold=True))
    draw.text(
        (60, 75),
        "Retained relationships only; unreported pairs are assigned BF = 1 (neutral) and are not shown.",
        fill="#56616c",
        font=font(19),
    )

    draw_histogram(
        image,
        draw,
        (50, 125, 885, 720),
        score_counts,
        score_edges,
        title="A. STRING combined scores",
        x_label="Combined score",
        x_ticks=[(value, f"{value:.1f}") for value in np.linspace(0, 1, 6)],
        color="#2a9d78",
        reference_lines=[
            (0.041, "Reference = 0.041", "#b84a4f", 0),
            (float(np.median(scores)), f"Median = {np.median(scores):.3f}", "#333333", 1),
        ],
    )
    draw_histogram(
        image,
        draw,
        (915, 125, 1750, 720),
        factor_counts,
        factor_edges,
        title="B. Converted STRING Bayes factors",
        x_label="Bayes factor (logarithmic axis)",
        x_ticks=[
            (0.0, "1"),
            (1.0, "10"),
            (2.0, "100"),
            (3.0, "1,000"),
            (4.0, "10,000"),
        ],
        color="#4477b7",
        reference_lines=[
            (math.log10(float(factors.min())), f"Minimum = {factors.min():.2f}", "#b84a4f", 0),
            (math.log10(float(np.median(factors))), f"Median = {np.median(factors):.2f}", "#333333", 1),
        ],
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(args.output, quality=95)
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
