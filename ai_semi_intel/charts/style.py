"""Chart theme: validated reference palette (dataviz skill), recessive axes, thin marks."""
from __future__ import annotations

import matplotlib as mpl
import matplotlib.pyplot as plt

# Categorical slots - fixed order, never cycled past 8 (adjacent-pair CVD validated).
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET, RED = CAT
SEQ_BLUE = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
            "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
DIV_NEG, DIV_MID, DIV_POS = "#e34948", "#f0efec", "#2a78d6"     # red <- gray -> blue
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
GRAY, GRAY_DARK, GRAY_LIGHT = "#b8b7b0", "#8a8983", "#dcdbd5"
TEXT, TEXT2, TEXT3 = "#0b0b0b", "#52514e", "#8a8983"
SURFACE, GRID = "#ffffff", "#ebeae5"
NAVY = "#12284b"                                  # deck primary (titles / dark slides)

# Macro-groups of the value chain (<= 4 categorical slots for portfolio colouring)
MACRO_GROUP = {
    "EDA & Semiconductor IP": "Design & Equipment", "Semiconductor Equipment": "Design & Equipment",
    "Foundry, IDM & Packaging": "Silicon", "AI Compute Silicon": "Silicon", "Memory & Storage": "Silicon",
    "Interconnect, Networking & Optics": "Silicon", "Analog, Power & Mixed-Signal": "Silicon",
    "Servers & Data Center Infrastructure": "Infrastructure & Power", "Power & Energy for AI": "Infrastructure & Power",
    "Cloud, Hyperscale & Neocloud": "Cloud & Software", "AI Software & Applications": "Cloud & Software",
}
MACRO_COLORS = {"Design & Equipment": BLUE, "Silicon": ORANGE, "Infrastructure & Power": AQUA, "Cloud & Software": YELLOW}


def apply_theme() -> None:
    mpl.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
        "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
        "axes.labelsize": 9.5, "axes.labelcolor": TEXT2, "axes.edgecolor": GRID, "axes.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
        "xtick.color": TEXT2, "ytick.color": TEXT2, "xtick.labelsize": 9, "ytick.labelsize": 9,
        "xtick.major.size": 0, "ytick.major.size": 0, "legend.frameon": False, "legend.fontsize": 9,
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "figure.dpi": 110, "savefig.dpi": 200, "savefig.bbox": "tight", "savefig.pad_inches": 0.15,
        "text.color": TEXT, "lines.linewidth": 2.0, "lines.markersize": 6,
    })


def pct(x, d=0):
    return f"{x*100:.{d}f}%"


def bn(x):
    if x is None or x != x:
        return "n/a"
    return f"${x/1e12:.2f}T" if abs(x) >= 1e12 else f"${x/1e9:.1f}B" if abs(x) >= 1e9 else f"${x/1e6:.0f}M"


def title(ax, text, sub=None):
    ax.set_title(text, pad=26 if sub else 10, color=TEXT)
    if sub:
        ax.text(0, 1.015, sub, transform=ax.transAxes, fontsize=9, color=TEXT2, va="bottom")


def source(fig, text="Source: SEC EDGAR, Yahoo Finance, FRED; analysis as of {asof}", asof=""):
    fig.text(0.01, -0.005, text.format(asof=asof), fontsize=7.5, color=TEXT3, ha="left", va="top")
