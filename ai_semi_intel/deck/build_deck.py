"""Investor pitch deck (PowerPoint) generated from the pipeline context.

Design: navy title/section slides, white content slides, one accent blue that
matches the charts, rounded stat cards as the recurring motif. Fonts: Cambria
(titles) + Calibri (body) - both ship with Office.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

from ..config.settings import DECK, CHARTS, CONFIG
from ..config.universe import SEGMENT_ORDER
from ..charts.style import MACRO_GROUP

NAVY = RGBColor(0x12, 0x28, 0x4B)
BLUE = RGBColor(0x2A, 0x78, 0xD6)
ICE = RGBColor(0xE8, 0xEF, 0xF9)
CARD = RGBColor(0xF3, 0xF5, 0xF9)
TEXT = RGBColor(0x0B, 0x0B, 0x0B)
TEXT2 = RGBColor(0x52, 0x51, 0x4E)
TEXT3 = RGBColor(0x8A, 0x89, 0x83)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GREEN = RGBColor(0x0C, 0xA3, 0x0C)
RED = RGBColor(0xD0, 0x3B, 0x3B)
ORANGE = RGBColor(0xEB, 0x68, 0x34)
W, H = 13.333, 7.5
TITLE_FONT, BODY_FONT = "Cambria", "Calibri"


# ------------------------------------------------------------------ primitives
def _rgb_from_hex(h: str) -> RGBColor:
    h = h.lstrip("#"); return RGBColor(int(h[:2], 16), int(h[2:4], 16), int(h[4:], 16))


def _tf(shape, size=14, color=TEXT, bold=False, font=BODY_FONT, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, margin=0.05):
    tf = shape.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Inches(margin); tf.margin_top = tf.margin_bottom = Inches(margin)
    return tf


def _run(par, text, size=14, color=TEXT, bold=False, font=BODY_FONT, italic=False):
    r = par.add_run(); r.text = text
    r.font.size = Pt(size); r.font.color.rgb = color; r.font.bold = bold; r.font.name = font; r.font.italic = italic
    return r


def text_box(slide, x, y, w, h, text, size=14, color=TEXT, bold=False, font=BODY_FONT, align=PP_ALIGN.LEFT,
             anchor=MSO_ANCHOR.TOP, line_spacing=1.1, margin=0.05):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = _tf(tb, margin=margin); tf.vertical_anchor = anchor
    lines = text if isinstance(text, list) else [text]
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align; p.line_spacing = line_spacing
        _run(p, line, size, color, bold, font)
    return tb


def bullets(slide, x, y, w, h, items, size=13, color=TEXT, space_after=6, bold_lead=True):
    """items: list of str or (lead, rest) tuples -> lead rendered bold."""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = _tf(tb)
    for i, it in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(space_after); p.line_spacing = 1.08
        _run(p, "•  ", size, BLUE, True)
        if isinstance(it, tuple):
            _run(p, it[0] + " ", size, color, bold_lead); _run(p, it[1], size, color)
        else:
            _run(p, it, size, color)
    return tb


def rect(slide, x, y, w, h, fill=CARD, line=None, rounded=True, radius=0.08):
    shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.fill.solid(); shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line; shp.line.width = Pt(0.75)
    if rounded:
        shp.adjustments[0] = radius
    shp.shadow.inherit = False
    return shp


def card(slide, x, y, w, h, value, label, sub=None, fill=CARD, value_color=NAVY, value_size=28):
    rect(slide, x, y, w, h, fill=fill)
    text_box(slide, x + 0.15, y + 0.12, w - 0.3, h * 0.5, value, size=value_size, color=value_color, bold=True, font=TITLE_FONT, anchor=MSO_ANCHOR.MIDDLE)
    text_box(slide, x + 0.15, y + h * 0.55, w - 0.3, h * 0.25, label, size=10.5, color=TEXT2, bold=True)
    if sub:
        text_box(slide, x + 0.15, y + h * 0.74, w - 0.3, h * 0.25, sub, size=9, color=TEXT3)


def circle_num(slide, x, y, n, d=0.42, fill=BLUE):
    c = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
    c.fill.solid(); c.fill.fore_color.rgb = fill; c.line.fill.background(); c.shadow.inherit = False
    tf = _tf(c, margin=0); tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER; _run(p, str(n), 12, WHITE, True)
    return c


def image(slide, path, x, y, w, h):
    """Place image inside (x,y,w,h) preserving aspect ratio, centred."""
    from PIL import Image as PILImage
    with PILImage.open(path) as im:
        iw, ih = im.size
    ar = iw / ih
    if w / h > ar:
        nh = h; nw = h * ar
    else:
        nw = w; nh = w / ar
    slide.shapes.add_picture(str(path), Inches(x + (w - nw) / 2), Inches(y + (h - nh) / 2), Inches(nw), Inches(nh))


def table(slide, df: pd.DataFrame, x, y, w, h, col_widths=None, font_size=9, header_fill=NAVY, index=True, zebra=True,
          align_right_from=1):
    data = df.reset_index() if index else df
    rows, cols = data.shape[0] + 1, data.shape[1]
    gt = slide.shapes.add_table(rows, cols, Inches(x), Inches(y), Inches(w), Inches(h)).table
    if col_widths:
        for i, cw in enumerate(col_widths):
            gt.columns[i].width = Inches(cw)
    for j, c in enumerate(data.columns):
        cell = gt.cell(0, j); cell.fill.solid(); cell.fill.fore_color.rgb = header_fill
        tf = cell.text_frame; tf.paragraphs[0].alignment = PP_ALIGN.LEFT if j < align_right_from else PP_ALIGN.RIGHT
        _run(tf.paragraphs[0], str(c), font_size, WHITE, True)
        cell.margin_left = cell.margin_right = Inches(0.05); cell.margin_top = cell.margin_bottom = Inches(0.02)
    for i in range(data.shape[0]):
        for j in range(cols):
            v = data.iat[i, j]
            cell = gt.cell(i + 1, j); cell.fill.solid()
            cell.fill.fore_color.rgb = CARD if (zebra and i % 2 == 1) else WHITE
            tf = cell.text_frame; tf.paragraphs[0].alignment = PP_ALIGN.LEFT if j < align_right_from else PP_ALIGN.RIGHT
            _run(tf.paragraphs[0], "" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v), font_size, TEXT, bold=(j == 0))
            cell.margin_left = cell.margin_right = Inches(0.05); cell.margin_top = cell.margin_bottom = Inches(0.02)
    return gt


# ------------------------------------------------------------------ slide scaffolds
class Deck:
    def __init__(self, asof: str):
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = Inches(W), Inches(H)
        self.blank = self.prs.slide_layouts[6]
        self.asof = asof
        self.n = 0

    def content(self, title_text: str, subtitle: str | None = None, section: str | None = None):
        s = self.prs.slides.add_slide(self.blank); self.n += 1
        tsize = 26 if len(title_text) <= 58 else 22 if len(title_text) <= 78 else 19
        text_box(s, 0.5, 0.32, 12.3, 0.7, title_text, size=tsize, color=NAVY, bold=True, font=TITLE_FONT, anchor=MSO_ANCHOR.MIDDLE)
        if subtitle:
            text_box(s, 0.5, 0.98, 12.3, 0.4, subtitle, size=12.5, color=TEXT2)
        if section:
            text_box(s, 9.8, 0.1, 3.0, 0.3, section.upper(), size=8.5, color=TEXT3, bold=True, align=PP_ALIGN.RIGHT)
        text_box(s, 0.5, 7.08, 9.0, 0.3, f"AI & Semiconductor Value Chain · Equity Research · Data as of {self.asof} · For discussion purposes only, not investment advice", size=8, color=TEXT3)
        text_box(s, 12.2, 7.08, 0.7, 0.3, str(self.n), size=8, color=TEXT3, align=PP_ALIGN.RIGHT)
        return s

    def dark(self, title_text: str, subtitle: str | None = None, kicker: str | None = None):
        s = self.prs.slides.add_slide(self.blank); self.n += 1
        rect(s, 0, 0, W, H, fill=NAVY, rounded=False)
        if kicker:
            text_box(s, 0.8, 2.2, 11.5, 0.5, kicker.upper(), size=12, color=_rgb_from_hex("9EC5F4"), bold=True)
        text_box(s, 0.8, 2.7, 11.5, 1.8, title_text, size=40 if len(title_text) <= 45 else 32, color=WHITE, bold=True, font=TITLE_FONT, anchor=MSO_ANCHOR.MIDDLE)
        if subtitle:
            text_box(s, 0.8, 4.6, 11.5, 1.2, subtitle, size=16, color=_rgb_from_hex("CADCFC"))
        return s

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.prs.save(str(path))
        return path


# ------------------------------------------------------------------ helpers for content
def pct(x, d=0, sign=False):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{x*100:+.{d}f}%" if sign else f"{x*100:.{d}f}%"


def mult(x, d=1):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{d}f}x"


def money(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"${x/1e12:.2f}T" if abs(x) >= 1e12 else f"${x/1e9:.1f}B" if abs(x) >= 1e9 else f"${x/1e6:.0f}M"


def stock_narrative(t: str, r: pd.Series, seg_row: pd.Series | None) -> dict:
    """Data-driven bullet points for the one-pager."""
    g = r.get("rev_growth_ntm"); e = r.get("eps_growth_ntm"); rev90 = r.get("eps_rev_90d")
    thesis = []
    thesis.append(("Business.", f"{r.get('description', '')}. {r.get('sub_segment', '')} within {r.get('segment', '')}; market cap {money(r.get('market_cap'))}, {r.get('style', '')} profile."))
    thesis.append(("Growth.", f"Consensus NTM revenue growth {pct(g)} (FY+1 {pct(r.get('rev_growth_fy1'))}) vs 3-yr CAGR {pct(r.get('rev_cagr_3y'))}; NTM EPS growth {pct(e)}. Latest quarter revenue {pct(r.get('rev_growth_q_yoy'), sign=True)} YoY."))
    pos = r.get("position", "")
    thesis.append(("Positioning.", f"{pos}: gross margin {pct(r.get('gross_margin'))} ({pct(r.get('gm_vs_segment'), sign=True)} vs segment median), growth {pct(r.get('growth_vs_segment'), sign=True)} vs segment; ~{pct(r.get('rev_share_in_segment'))} of segment revenue in our universe; R&D {pct(r.get('rnd_pct'))} of sales."))
    thesis.append(("Estimate momentum.", f"Consensus EPS revised {pct(rev90, sign=True)} over 90 days and {pct(r.get('eps_rev_30d'), sign=True)} over 30 days; price {pct(r.get('vs_200dma'), sign=True)} vs 200-day average, {pct(r.get('rel_6m_vs_soxx'), sign=True)} 6-month relative to SOXX."))
    val = [("Multiples.", f"EV/Sales NTM {mult(r.get('ev_sales_ntm'))} vs regression-fair {mult(r.get('mult_fair_ev_sales'))}; P/E NTM {mult(r.get('pe_ntm'))}; PEG {mult(r.get('peg'), 2)}; FCF yield {pct(r.get('fcf_yield'), 1)}."),
           ("Fair value.", f"Blended ${r.get('fair_value', np.nan):,.0f} vs price ${r.get('price', np.nan):,.0f} = {pct(r.get('upside'), sign=True)} (DCF {pct(r.get('dcf_upside'), sign=True)}, regression {pct(r.get('mult_upside'), sign=True)}, Street target {pct(r.get('target_upside'), sign=True)}). WACC {pct(r.get('wacc'), 1)}."),
           ("Market-implied.", f"Reverse DCF: the price discounts {pct(r.get('implied_growth'))} post-consensus growth (fading to 3%) at a {pct(r.get('dcf_margin_target'))} through-cycle FCF margin.")]
    risks = []
    if (r.get("vol_1y") or 0) > 0.6:
        b = r.get("beta")
        risks.append(f"High volatility ({pct(r.get('vol_1y'))} annualised)" + (f" and beta {b:.1f}." if b is not None and not np.isnan(b) else "."))
    if (r.get("fcf_margin") or 0) < 0:
        risks.append(f"Negative FCF margin ({pct(r.get('fcf_margin'))}): capex-heavy build-out; funding and utilisation risk.")
    if (r.get("max_dd_1y") or 0) < -0.35:
        risks.append(f"Drew down {pct(r.get('max_dd_1y'))} within the last year - sentiment-driven.")
    if (r.get("net_debt_to_ebitda") or 0) > 2:
        risks.append(f"Leverage {r.get('net_debt_to_ebitda'):.1f}x net debt/EBITDA.")
    if (r.get("short_pct_float") or 0) > 0.08:
        risks.append(f"Short interest {pct(r.get('short_pct_float'))} of float.")
    if (r.get("upside_dispersion") or 0) > 0.6:
        risks.append("Wide dispersion between valuation methods - fair value is input-sensitive.")
    seg = r.get("segment", "")
    generic = {"Memory & Storage": "Commodity pricing cycle: HBM/DRAM/NAND pricing can reverse quickly.",
               "AI Compute Silicon": "Customer concentration in hyperscalers; custom-silicon substitution; export controls.",
               "Interconnect, Networking & Optics": "Design-win concentration and rapid technology transitions (800G to 1.6T, co-packaged optics).",
               "Semiconductor Equipment": "WFE cyclicality and China export restrictions.",
               "Servers & Data Center Infrastructure": "Thin margins, component pass-through, GPU allocation dependence.",
               "Cloud, Hyperscale & Neocloud": "Capex intensity, financing, contract concentration, GPU depreciation.",
               "AI Software & Applications": "Monetisation of AI features vs seat-based pricing pressure.",
               "Analog, Power & Mixed-Signal": "Industrial/auto inventory cycle; pricing competition from China.",
               "Foundry, IDM & Packaging": "Geopolitics, capacity ramps and pricing.",
               "Power & Energy for AI": "Regulatory approval, PPA timing, commodity power prices.",
               "EDA & Semiconductor IP": "Customer R&D budgets and licensing renewals."}
    risks.append(generic.get(seg, "Sector cyclicality."))
    return {"thesis": thesis, "valuation": val, "risks": risks[:4]}


def strategic_block(t: str) -> list:
    """Bullets for the one-pager from the evidence-logged assessment."""
    from ..analysis import strategic as STR
    a = STR.load_assessment(t)
    if not a:
        return [("Not yet assessed.", "Queued for LLM deep research; the strategic pillar is neutral until an evidence-logged assessment exists.")]
    d = a["dimensions"]
    out = [("Thesis.", a.get("thesis", "")),
           ("Scores.", f"scarcity {STR._d(d, 'scarcity'):.2f}, lock-in {STR._d(d, 'lock_in'):.2f}, roadmap {STR._d(d, 'roadmap'):.2f}, AI exposure {STR._d(d, 'ai_exposure'):.2f}, pricing power {STR._d(d, 'pricing_power'):.2f}; "
                       f"risk (LLM) {STR.llm_risk(d):.2f}; 90-day news {a.get('news_direction', 0):+.2f}; confidence {a.get('confidence', 0):.2f} (v{a.get('version', 1)}, {a.get('asof')})."),
           ("Kill criteria.", " ".join(f"({i+1}) {k}" for i, k in enumerate(a.get("kill_criteria", [])[:3])))]
    ev = a.get("evidence", [])[:3]
    if ev:
        out.append(("Evidence.", " ".join(f"[{e.get('date', '')}, {e.get('source', '')}] {e.get('claim', '')}" for e in ev)))
    return out


# ------------------------------------------------------------------ the deck
def build(ctx: dict) -> Path:
    asof = ctx["asof"].strftime("%Y-%m-%d") if hasattr(ctx["asof"], "strftime") else str(ctx["asof"])
    top: pd.DataFrame = ctx["top"].sort_values("rank")
    uni: pd.DataFrame = ctx["universe"]
    res = ctx["res"]; opt = res["opt"]; ms = opt["recommended"]
    w = ms["weights"]; seg = ctx["seg"]; vmeta = ctx["vmeta"]; macro = ctx["macro_snapshot"]
    charts = ctx.get("charts", {}); bt = res["backtest"]["stats"]
    D = Deck(asof)

    # 1 title ------------------------------------------------------------------------
    s = D.dark("The AI & Semiconductor Value Chain: 15 Conviction Ideas and an Optimised Portfolio",
               f"Deep fundamental research across {len(uni)} US-listed companies from EDA to data-centre power  ·  {asof}",
               kicker="US Equity Research · Technology")
    text_box(s, 0.8, 6.5, 11.5, 0.5, "Sources: SEC EDGAR XBRL, Yahoo Finance consensus, FRED. For discussion purposes only; not investment advice.", size=10, color=_rgb_from_hex("9EC5F4"))

    # 2 executive summary ----------------------------------------------------------------
    s = D.content("Executive summary", "A systematic, fundamentals-first screen of the end-to-end AI stack, translated into a risk-optimised portfolio", "Summary")
    med_up = top["upside"].median(); med_g = top["rev_growth_ntm"].median()
    card(s, 0.5, 1.5, 2.95, 1.35, f"{len(uni)}", "Companies analysed", "11 value-chain segments, SEC + consensus data")
    card(s, 3.6, 1.5, 2.95, 1.35, pct(med_up, sign=True), "Median blended upside, top 15", "50% DCF · 30% regression · 20% Street")
    card(s, 6.7, 1.5, 2.95, 1.35, pct(med_g), "Median NTM revenue growth, top 15", f"vs {pct(uni['rev_growth_ntm'].median())} for the universe")
    card(s, 9.8, 1.5, 3.0, 1.35, f"{ms['sharpe']:.2f}", "Recommended portfolio Sharpe (ex ante)", f"E[r] {pct(ms['exp_return'])} · vol {pct(ms['vol'])} · cash {ms.get('cash', 0):.0%}")
    seg_counts = top.groupby("segment").size().sort_values(ascending=False)
    seg_txt = ", ".join(f"{k} ({v})" for k, v in seg_counts.items())
    bullets(s, 0.5, 3.15, 12.3, 3.8, [
        ("Where the value is.", f"The picks concentrate in {seg_txt}: the layers where consensus growth is highest and growth-adjusted multiples are lowest."),
        ("How we got there.", "Five direct pillars: growth 37.5%, momentum 26.25%, value 11.25%, evidence-weighted scarcity 15%, and market/financial/valuation risk resilience 10%. Alpha remains a separate 50/35/15 diagnostic; scarcity and risk are priors pending live validation."),
        ("What the portfolio looks like.", f"Equal-risk-contribution weights on a Ledoit-Wolf covariance, 2-15% per name, max 35% per segment, scaled to a 25% ex-ante volatility target (cash {ms.get('cash', 0):.0%}). Largest positions: {', '.join(f'{t} {w[t]:.0%}' for t in w.sort_values(ascending=False).index[:6])}."),
        ("Macro.", f"10-year yield {macro.loc['DGS10','value']:.2f}%, Fed funds {macro.loc['FEDFUNDS','value']:.2f}%, semiconductor industrial production {macro.loc['SEMI_IP_YOY','value']:+.1f}% YoY: a still-expanding capex cycle against a higher-for-longer discount rate."),
        ("Monitoring.", "A daily pipeline refreshes data, re-scores the universe, re-optimises the portfolio and raises KPI alerts (price, revisions, filings, valuation, earnings) on the dashboard."),
    ], size=12.5)

    # 3 investment thesis -------------------------------------------------------------------
    s = D.content("Investment thesis: the AI build-out is a multi-layer supply chain, not a single trade", section="Thesis")
    cols = [("Demand is broadening", ["Hyperscaler and neocloud capex still compounding; inference joins training as the growth engine",
                                       f"Semiconductor output {macro.loc['SEMI_IP_YOY','value']:+.1f}% YoY (FRED IPG3344S); semi PPI {macro.loc['SEMI_PPI_YOY','value']:+.1f}% YoY signals pricing power",
                                       "Sovereign AI, enterprise agents and edge devices extend the demand curve"]),
            ("Bottlenecks move along the chain", ["Compute (GPU/ASIC) → memory (HBM) → interconnect (optics, AEC, retimers) → packaging → power & cooling",
                                                   "Each bottleneck creates pricing power for a small set of suppliers for 4-8 quarters",
                                                   "Our segment map tracks where median growth and upside are migrating"]),
            ("Valuation is dispersed", [f"Universe EV/Sales NTM ranges {uni['ev_sales_ntm'].quantile(.1):.1f}x-{uni['ev_sales_ntm'].quantile(.9):.1f}x; growth-adjusted the spread is even wider",
                                        f"Regression of ln(EV/S) on growth and margins explains R² {vmeta['regression'].get('r2', 0):.2f}: the residual is our mispricing signal",
                                        "Reverse DCF shows what the market is discounting - we buy where implied growth is below consensus"])]
    for i, (hd, items) in enumerate(cols):
        x = 0.5 + i * 4.15
        rect(s, x, 1.5, 3.95, 5.3)
        circle_num(s, x + 0.2, 1.7, i + 1)
        text_box(s, x + 0.75, 1.65, 3.1, 0.5, hd, size=15, color=NAVY, bold=True, font=TITLE_FONT, anchor=MSO_ANCHOR.MIDDLE)
        bullets(s, x + 0.15, 2.35, 3.65, 4.3, items, size=12)

    # 4 macro ------------------------------------------------------------------------------------
    s = D.content("Macro backdrop: higher-for-longer rates, but the semiconductor cycle is expanding", section="Macro")
    if "macro" in charts:
        image(s, charts["macro"], 0.4, 1.4, 9.3, 5.5)
    ys = 1.5
    for sid, lab in (("DGS10", "10Y Treasury"), ("FEDFUNDS", "Fed funds"), ("CPI_YOY", "CPI YoY"), ("SEMI_IP_YOY", "Semi output YoY"), ("VIXCLS", "VIX")):
        if sid in macro.index:
            r = macro.loc[sid]
            card(s, 9.9, ys, 2.95, 1.0, f"{r['value']:.2f}" + ("%" if sid != "VIXCLS" else ""), lab, f"{r['chg_12m']:+.2f} vs 12m ago · {r['date']}", value_size=20)
            ys += 1.1

    # 5 value chain map --------------------------------------------------------------------------
    s = D.content("The end-to-end value chain and where our picks sit", section="Industry structure")
    if "value_chain" in charts:
        image(s, charts["value_chain"], 0.3, 1.25, 12.7, 5.7)

    # 6 industry structure ---------------------------------------------------------------------------
    s = D.content("Industry structure: growth, valuation and concentration by segment", section="Industry structure")
    if "segment_structure" in charts:
        image(s, charts["segment_structure"], 0.3, 1.35, 7.4, 5.6)
    st = seg.copy()
    st_disp = pd.DataFrame({"Segment": st.index, "N": st["n"].astype(int), "Mkt cap": st["market_cap_bn"].map(lambda v: f"${v/1000:.1f}T" if v >= 1000 else f"${v:.0f}B"),
                            "NTM gr.": st["med_rev_growth_ntm"].map(pct), "GM": st["med_gross_margin"].map(pct), "EV/S": st["med_ev_sales_ntm"].map(mult),
                            "Upside": st["med_upside"].map(lambda v: pct(v, sign=True)), "HHI": st["hhi_revenue"].map(lambda v: f"{v:,.0f}")}).set_index("Segment")
    table(s, st_disp, 7.85, 1.4, 5.1, 4.6, col_widths=[1.85, 0.35, 0.65, 0.6, 0.5, 0.5, 0.65], font_size=7.5)
    text_box(s, 7.85, 6.15, 5.1, 0.8, "HHI on TTM revenue within the segment (>2,500 = concentrated). Concentrated, fast-growing segments with reasonable multiples are where pricing power and earnings upside coincide.", size=9, color=TEXT2)

    # 7 methodology ---------------------------------------------------------------------------------
    s = D.content("Methodology: from 116 companies to 15 ideas and one portfolio", section="Methodology")
    steps = [("Universe", "11 segments of the AI/semi value chain, US-listed (incl. key ADRs), liquidity floor $2B"),
             ("Data", "SEC EDGAR XBRL (10-K/10-Q facts, filings), Yahoo Finance consensus & prices, FRED macro; PitchBook exports when available"),
             ("Features", "Growth, margins, ROIC, leverage, SBC, Rule of 40, revisions, momentum - NTM-aligned to each fiscal calendar, FX-normalised"),
             ("Valuation", "10-yr fading-growth DCF with through-cycle margins; ln(EV/S) regression on growth & margins; Street targets; reverse DCF"),
             ("Scoring", "Five pillars: growth 37.5% · momentum 26.25% · value 11.25% · scarcity 15% · risk resilience 10%; max 3 per segment"),
             ("Portfolio", "Ledoit-Wolf covariance; equal risk contribution with weight and segment caps, scaled to a 25% volatility target (cash remainder)")]
    for i, (hd, body) in enumerate(steps):
        x = 0.5 + (i % 3) * 4.15; y = 1.5 + (i // 3) * 2.75
        rect(s, x, y, 3.95, 2.45)
        circle_num(s, x + 0.2, y + 0.2, i + 1)
        text_box(s, x + 0.75, y + 0.15, 3.1, 0.5, hd, size=15, color=NAVY, bold=True, font=TITLE_FONT, anchor=MSO_ANCHOR.MIDDLE)
        text_box(s, x + 0.2, y + 0.8, 3.6, 1.6, body, size=11.5, color=TEXT)

    # 8 funnel ------------------------------------------------------------------------------------------
    s = D.content("Screening funnel and eligibility", section="Methodology")
    if "funnel" in charts:
        image(s, charts["funnel"], 0.3, 1.4, 7.6, 5.4)
    cfgs = CONFIG.screen
    bullets(s, 8.1, 1.5, 4.8, 5.3, [
        ("Liquidity.", f"Market cap ≥ ${cfgs.min_market_cap/1e9:.0f}B."),
        ("Data quality.", f"≥ {cfgs.min_feature_coverage:.0%} of scoring features available; valuation computable from consensus and statements."),
        ("Decision score.", "Ranked on alpha plus scarcity and risk resilience; the overlay is labelled unvalidated until the live archive supplies forward evidence."),
        ("Diversification.", f"Max {cfgs.max_per_segment} names per segment so the list spans the chain rather than one hot layer."),
        ("Style tag.", "Accelerating growth, Price momentum, or Undervalued (value tilt dominant with weak momentum)."),
        ("Refresh.", "Re-run daily; entries/exits and rank moves are logged and alerted."),
    ], size=12)

    # 9 top-15 table ------------------------------------------------------------------------------------
    s = D.content("The top 15: undervalued and high-growth names across the chain", section="Stock selection")
    tdisp = pd.DataFrame({
        "#": top["rank"].astype(int), "Ticker": top.index, "Company": top["name"].str.slice(0, 26), "Segment": top["segment"].str.replace(" & ", " & ").str.slice(0, 30),
        "Style": top["style"], "Score": top["composite"].map(lambda v: f"{v:.2f}"), "Price": top["price"].map(lambda v: f"${v:,.0f}"),
        "Fair value": top["fair_value"].map(lambda v: f"${v:,.0f}"), "Upside": top["upside"].map(lambda v: pct(v, sign=True)),
        "EV/S NTM": top["ev_sales_ntm"].map(mult), "P/E NTM": top["pe_ntm"].map(lambda v: mult(v, 0)), "NTM growth": top["rev_growth_ntm"].map(pct),
        "FCF mgn": top["fcf_margin"].map(pct), "Weight": [f"{w.get(t, 0):.1%}" for t in top.index]}).set_index("#")
    table(s, tdisp, 0.4, 1.3, 12.55, 5.5, col_widths=[0.3, 0.65, 2.0, 2.3, 0.95, 0.6, 0.75, 0.85, 0.7, 0.75, 0.7, 0.85, 0.7, 0.7], font_size=8, align_right_from=6)

    # 10-17 analytic charts --------------------------------------------------------------------------
    chart_slides = [("score_ranking", "Composite score decomposition: what drives each pick", "Stock selection"),
                    ("valuation_vs_growth", "Growth-adjusted valuation across the universe", "Valuation"),
                    ("upside", "Fair value upside by method", "Valuation"),
                    ("growth", "Growth profile: consensus acceleration vs history", "Fundamentals"),
                    ("margins", "Profitability profile", "Fundamentals"),
                    ("quality", "Quality map: returns on capital vs cash conversion", "Fundamentals"),
                    ("revisions", "Estimate momentum: consensus EPS revisions", "Fundamentals"),
                    ("relative_performance", "One-year relative performance vs SOXX", "Fundamentals")]
    for key, ttl, sec in chart_slides:
        if key in charts:
            s = D.content(ttl, section=sec)
            image(s, charts[key], 0.4, 1.25, 12.5, 5.75)

    # 18 section divider ---------------------------------------------------------------------------------
    D.dark("Company one-pagers", "Business, growth, positioning, valuation and risks for each of the 15 picks", kicker="Section")

    # 19-33 one-pagers ------------------------------------------------------------------------------------
    for t, r in top.iterrows():
        nar = stock_narrative(t, r, seg.loc[r["segment"]] if r["segment"] in seg.index else None)
        s = D.content(f"{t} · {r['name']}", f"{r['segment']} · {r['sub_segment']} · Rank #{int(r['rank'])} · {r['style']} · Portfolio weight {w.get(t, 0):.1%}", section="One-pager")
        if f"tearsheet_{t}" in charts:
            image(s, charts[f"tearsheet_{t}"], 0.3, 1.3, 12.75, 2.15)
        # stats cards row
        sc_ = r.get("scarcity_strength"); rk_ = r.get("risk_score")
        stats = [(f"${r['price']:,.0f}", "Price"), (f"${r['fair_value']:,.0f}", "Blended fair value"), (pct(r["upside"], sign=True), "Upside"),
                 (mult(r["ev_sales_ntm"]), "EV/Sales NTM"), (pct(r["rev_growth_ntm"]), "NTM rev growth"), (pct(r["fcf_margin"]), "FCF margin"),
                 ("n/a" if sc_ is None or np.isnan(sc_) else f"{sc_:.2f}", "Scarcity strength"), ("n/a" if rk_ is None or np.isnan(rk_) else f"{rk_:.2f}", "Risk score")]
        for i, (v, lab) in enumerate(stats):
            x = 0.4 + i * 1.575
            rect(s, x, 3.52, 1.5, 0.7)
            text_box(s, x + 0.05, 3.53, 1.4, 0.4, v, size=13, color=NAVY, bold=True, font=TITLE_FONT, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
            text_box(s, x + 0.05, 3.92, 1.4, 0.28, lab, size=8, color=TEXT2, align=PP_ALIGN.CENTER)
        strat = strategic_block(t)
        text_box(s, 0.4, 4.3, 3.3, 0.3, "Investment case", size=11.5, color=NAVY, bold=True, font=TITLE_FONT)
        bullets(s, 0.4, 4.55, 3.3, 2.5, nar["thesis"], size=8, space_after=2)
        text_box(s, 3.85, 4.3, 3.35, 0.3, "Strategic role: scarcity and risk (LLM research)", size=11.5, color=NAVY, bold=True, font=TITLE_FONT)
        bullets(s, 3.85, 4.55, 3.35, 2.5, strat, size=8, space_after=2)
        text_box(s, 7.35, 4.3, 2.8, 0.3, "Valuation", size=11.5, color=NAVY, bold=True, font=TITLE_FONT)
        bullets(s, 7.35, 4.55, 2.8, 2.5, nar["valuation"], size=8, space_after=2)
        text_box(s, 10.3, 4.3, 2.7, 0.3, "Key risks", size=11.5, color=NAVY, bold=True, font=TITLE_FONT)
        bullets(s, 10.3, 4.55, 2.7, 2.5, nar["risks"], size=8, space_after=2)

    # 34 portfolio section ---------------------------------------------------------------------------------
    D.dark("Portfolio construction", "Maximise expected return per unit of risk across the 15 names", kicker="Section")
    s = D.content("Portfolio methodology", section="Portfolio")
    pc = CONFIG.portfolio
    cards_ = [("Returns", f"{pc.lookback_years} years of daily total returns; recent IPOs back-filled with segment-peer averages for {', '.join(f'{k} ({v}d)' for k, v in res['returns_info']['backfilled_days'].items()) or 'none'}."),
              ("Risk model", "Ledoit-Wolf shrinkage covariance (annualised) - stable for 15 assets with 750 observations; correlations shown on the risk slide."),
              ("Expected returns", f"CAPM (beta vs S&P 500 × {CONFIG.valuation.equity_risk_premium:.1%} ERP over rf {pct(res['opt']['meta']['rf'], 2)}) plus a Grinold alpha = IC ({pc.grinold_ic:.2f}) × volatility × standardised score. Fair-value upside is reported, not used as a return forecast (it tested as non-predictive)."),
              ("Constraints", f"Long-only; {pc.min_weight:.0%}-{pc.max_weight:.0%} per name; ≤ {pc.max_segment_weight:.0%} per value-chain segment; fully invested."),
              ("Objectives", "Primary: equal risk contribution scaled to a 25% ex-ante volatility target (best risk-adjusted result in the 2012-2026 portfolio lab). References: recommended (ERC, vol-targeted) on Grinold alphas, minimum variance, equal weight."),
              ("Validation", f"Monthly-rebalanced backtest vs SPY/QQQ/SOXX over {res['backtest']['window'][0]} to {res['backtest']['window'][1]} (in-sample, for risk characteristics only).")]
    for i, (hd, body) in enumerate(cards_):
        x = 0.5 + (i % 3) * 4.15; y = 1.4 + (i // 3) * 2.8
        rect(s, x, y, 3.95, 2.55); circle_num(s, x + 0.2, y + 0.2, i + 1)
        text_box(s, x + 0.75, y + 0.15, 3.1, 0.5, hd, size=15, color=NAVY, bold=True, font=TITLE_FONT, anchor=MSO_ANCHOR.MIDDLE)
        text_box(s, x + 0.2, y + 0.8, 3.6, 1.7, body, size=11, color=TEXT)

    # 36 frontier -----------------------------------------------------------------------------------------
    s = D.content("Efficient frontier and candidate portfolios", section="Portfolio")
    if "efficient_frontier" in charts:
        image(s, charts["efficient_frontier"], 0.3, 1.25, 8.6, 5.8)
    ps = pd.DataFrame({k: {"E[return]": pct(opt[k]["exp_return"]), "Volatility": pct(opt[k]["vol"]), "Sharpe": f"{opt[k]['sharpe']:.2f}"} for k in ("max_sharpe", "min_vol", "risk_parity", "equal_weight")}).T
    ps.index = ["Max Sharpe", "Min variance", "Risk parity", "Equal weight"]
    table(s, ps, 9.1, 1.5, 3.85, 1.8, col_widths=[1.3, 0.85, 0.85, 0.85], font_size=9)
    bullets(s, 9.1, 3.6, 3.85, 3.3, [("Recommendation:", "equal risk contribution with a 25% volatility target - the construction with the best Sharpe and drawdown in the 2012-2026 lab."),
                                     ("Read-across:", "min-variance sacrifices little return for lower volatility; equal weight is dominated."),
                                     ("Note:", "expected returns are forward-looking estimates; volatility from a 3-year window that includes a semiconductor boom.")], size=10.5)

    # 37 weights --------------------------------------------------------------------------------------------
    run_ = ctx.get("run") or {}
    s = D.content("Executable target weights (equal risk contribution, 25% vol target) - model targets, not positions",
                  subtitle=f"{run_.get('validation_status', 'NOT VALIDATED · exploratory')} · run {run_.get('run_id', '-')}"
                           + ("" if run_.get("actionable", True) else " · NOT ACTIONABLE: " + "; ".join(run_.get("not_actionable_reasons", []))[:90]), section="Portfolio")
    if "weights" in charts:
        image(s, charts["weights"], 0.3, 1.25, 8.3, 5.8)
    wt = pd.DataFrame({"Ticker": w.sort_values(ascending=False).index, "Weight": w.sort_values(ascending=False).map(lambda v: f"{v:.1%}").values,
                       "Risk share": [f"{ms['risk_contrib'][t]:.1%}" for t in w.sort_values(ascending=False).index],
                       "E[r] BL": [pct(res["asset_stats"].loc[t, "exp_return_bl"]) for t in w.sort_values(ascending=False).index],
                       "Vol": [pct(res["asset_stats"].loc[t, "vol"]) for t in w.sort_values(ascending=False).index]}).set_index("Ticker")
    table(s, wt, 8.8, 1.4, 4.15, 5.4, col_widths=[0.9, 0.8, 0.85, 0.8, 0.8], font_size=8.5)

    # 38 risk -----------------------------------------------------------------------------------------------
    s = D.content("Risk: correlations and risk contributions", section="Portfolio")
    if "correlation" in charts:
        image(s, charts["correlation"], 0.3, 1.25, 6.3, 5.8)
    if "risk_contrib" in charts:
        image(s, charts["risk_contrib"], 6.7, 1.25, 6.35, 5.8)

    # 39 backtest ----------------------------------------------------------------------------------------------
    s = D.content("Hindsight illustration of today's target weights vs SPY, QQQ and SOXX - invalid as a track record", section="Portfolio")
    if "backtest" in charts:
        image(s, charts["backtest"], 0.3, 1.25, 8.6, 5.8)
    bts = bt[["cagr", "vol", "sharpe", "max_drawdown", "total_return"]].copy()
    bts.columns = ["CAGR", "Vol", "Sharpe", "Max DD", "Total"]
    for c in ("CAGR", "Vol", "Max DD", "Total"):
        bts[c] = bts[c].map(pct)
    bts["Sharpe"] = bts["Sharpe"].map(lambda v: f"{v:.2f}")
    table(s, bts, 9.1, 1.5, 3.85, 1.9, col_widths=[0.95, 0.7, 0.6, 0.6, 0.6, 0.6], font_size=8.5)
    bullets(s, 9.1, 3.6, 3.85, 3.3, [("Not a track record:", "today's names and weights (selection, covariance, vol scale) are replayed over the past; no costs, fills or delistings; cash accrues along the fed funds path. Sharpe = daily excess return / std, annualised."),
                                     ("Use it for:", f"risk profile - beta {bt.loc['Portfolio','beta_vs_spy']:.2f} vs SPY, tracking error {pct(bt.loc['Portfolio','tracking_error_vs_soxx'])} vs SOXX, drawdown comparable to SOXX."),
                                     ("Live forward evidence", "starts from the daily archive; the decision score is NOT VALIDATED until forward returns accumulate.")], size=10.5)

    # 40 risks ----------------------------------------------------------------------------------------------------
    s = D.content("Key risks to the thesis", section="Risks")
    risks = [("Capex digestion", "Hyperscaler capex growth decelerates or pauses; the whole chain de-rates simultaneously (correlations shown are already high)."),
             ("Memory cycle", "HBM/DRAM/NAND pricing reverses; memory names carry the largest weights and the highest consensus growth."),
             ("Concentration", "A handful of customers (hyperscalers, NVIDIA) drive demand for optics, servers and packaging suppliers."),
             ("Export controls & geopolitics", "China restrictions on equipment and accelerators; Taiwan concentration in leading-edge manufacturing."),
             ("Power & infrastructure limits", "Grid, turbine and cooling constraints delay data-centre commissioning and revenue recognition."),
             ("Valuation & rates", "A 10-year yield near 4.75% keeps the discount rate high; long-duration growth names are most sensitive.")]
    for i, (hd, body) in enumerate(risks):
        x = 0.5 + (i % 3) * 4.15; y = 1.4 + (i // 3) * 2.8
        rect(s, x, y, 3.95, 2.55); circle_num(s, x + 0.2, y + 0.2, i + 1, fill=ORANGE)
        text_box(s, x + 0.75, y + 0.15, 3.1, 0.5, hd, size=15, color=NAVY, bold=True, font=TITLE_FONT, anchor=MSO_ANCHOR.MIDDLE)
        text_box(s, x + 0.2, y + 0.8, 3.6, 1.7, body, size=11, color=TEXT)

    # 41 monitoring -----------------------------------------------------------------------------------------------
    s = D.content("Daily monitoring: KPI dashboard and alert rules", "The pipeline re-runs every trading day, re-ranks the universe, re-optimises the portfolio and logs changes", section="Monitoring")
    from ..dashboard.monitoring import ALERT_RULES
    rules = pd.DataFrame({"Rule": ["1-day price move", "30-day EPS revision", "Upside below floor", "Upside above ceiling", "Earnings within", "Weight change vs prior run", "1-year drawdown", "New 10-Q / 8-K filing", "Top-15 entry / exit"],
                          "Threshold": [pct(ALERT_RULES["price_move_1d"]), pct(ALERT_RULES["eps_rev_30d"]), pct(ALERT_RULES["upside_floor"]), pct(ALERT_RULES["upside_ceiling"]), f"{ALERT_RULES['earnings_days']} days", pct(ALERT_RULES["weight_change_pp"]), pct(ALERT_RULES["drawdown_1y"]), "7 days", "any"],
                          "Severity": ["warning / critical", "info / warning", "warning", "info", "info", "info", "warning", "info", "info / warning"]}).set_index("Rule")
    table(s, rules, 0.5, 1.6, 6.0, 4.3, col_widths=[2.6, 1.5, 1.9], font_size=9.5)
    kp = ["Per stock: price, 1D/1W/1M returns, blended fair value & upside, EV/Sales and P/E NTM, NTM growth, FCF margin, 30/90-day revisions, distance to 200-day average, next earnings date, recent SEC filings with links",
          "Portfolio: target weights (recommended (ERC, vol-targeted), min-variance, risk parity), expected return / volatility / Sharpe, risk contributions, turnover vs previous run, drawdown from run-history peak",
          "Universe: full ranking table with pillar scores, so tomorrow's candidates are visible today",
          "Macro: FRED panel (rates, inflation, semiconductor output and prices, capital-goods orders)",
          "Delivery: static HTML dashboard regenerated daily (no server needed) plus an interactive Streamlit app; history folder keeps every run for audit"]
    bullets(s, 6.8, 1.6, 6.2, 5.3, kp, size=11)
    n_alerts = len(ctx.get("alerts", []))
    text_box(s, 0.5, 6.1, 6.0, 0.8, f"Today's run: {n_alerts} alerts, {len(ctx.get('changes', {}).get('entries', []))} entries, {len(ctx.get('changes', {}).get('exits', []))} exits, turnover {ctx.get('changes', {}).get('turnover', 0):.0%}.", size=10.5, color=TEXT2)

    # 42 appendix: data & assumptions ------------------------------------------------------------------------------
    D.dark("Appendix", "Data sources, assumptions and the full universe", kicker="Section")
    s = D.content("Appendix: data sources and key assumptions", section="Appendix")
    vc = CONFIG.valuation
    left = [("SEC EDGAR.", "XBRL company facts (revenue, operating income, OCF, capex, SBC, buybacks, equity, cash, debt; 10-K/10-Q/20-F) and the filing index for alert generation. Foreign private issuers via 20-F where available."),
            ("Yahoo Finance (yfinance).", "Adjusted prices (4 years), statements, consensus revenue/EPS estimates and 7/30/60/90-day EPS trend, price targets, earnings calendar, FX for non-USD reporters."),
            ("FRED.", "DGS10 (risk-free), DGS2, FEDFUNDS, CPI, core PCE, unemployment, GDP, industrial production incl. semiconductors (IPG3344S), semiconductor PPI, VIX, HY spreads, capital-goods orders."),
            ("PitchBook.", ctx["pitchbook"]["status"])]
    right = [("Discount rate.", f"CAPM with Blume-adjusted beta (floor {vc.beta_floor}, cap {vc.beta_cap}), ERP {vc.equity_risk_premium:.1%}, rf = 10Y Treasury ({pct(res['opt']['meta']['rf'], 2)}); WACC bounded {vc.wacc_floor:.1%}-{vc.wacc_cap:.1%}; tax {vc.tax_rate:.0%}."),
             ("DCF.", f"Year 1 = NTM consensus revenue; year-2 growth = {vc.post_consensus_growth_share:.0%} of FY+1 consensus growth (cap {vc.max_growth_input:.0%}), linear fade to {vc.terminal_growth:.0%} terminal by year {vc.projection_years}; FCF margin converges over 5 years to {vc.margin_weight_current:.0%} current / {1-vc.margin_weight_current:.0%} five-year average (EDGAR)."),
             ("Regression.", f"ln(EV/S NTM) = {vmeta['regression']['coef'].get('intercept', 0):.2f} + {vmeta['regression']['coef'].get('growth', 0):.2f}·growth + {vmeta['regression']['coef'].get('fcf_margin', 0):.2f}·FCF margin + {vmeta['regression']['coef'].get('gross_margin', 0):.2f}·gross margin (n={vmeta['regression']['n']}, R²={vmeta['regression']['r2']:.2f})."),
             ("Blend.", f"Fair value = {vc.w_dcf:.0%} DCF + {vc.w_multiple:.0%} regression + {vc.w_target:.0%} Street target; methods outside 0.1x-5x of price are excluded and weights renormalised."),
             ("NTM alignment.", "FY0/FY+1 consensus blended by months remaining in the fiscal year; ADR financials converted at spot FX; share counts implied from USD market cap.")]
    bullets(s, 0.5, 1.4, 6.1, 5.5, left, size=10.5)
    bullets(s, 6.9, 1.4, 6.1, 5.5, right, size=10.5)

    # 43-44 universe list --------------------------------------------------------------------------------------------
    u = uni.sort_values(["segment_rank", "composite"], ascending=[True, False])
    ud = pd.DataFrame({"Ticker": u.index, "Segment": u["segment"].str.slice(0, 28), "Score": u["composite"].map(lambda v: f"{v:.2f}"), "Upside": u["upside"].map(lambda v: pct(v, sign=True)),
                       "EV/S": u["ev_sales_ntm"].map(mult), "NTM gr": u["rev_growth_ntm"].map(pct)})
    half = int(np.ceil(len(ud) / 2))
    for part, (a, b) in enumerate(((0, half), (half, len(ud)))):
        s = D.content(f"Appendix: full universe ranking ({part + 1}/2)", section="Appendix")
        chunk = ud.iloc[a:b]
        h1 = int(np.ceil(len(chunk) / 2))
        for k, sub in enumerate((chunk.iloc[:h1], chunk.iloc[h1:])):
            table(s, sub.set_index("Ticker"), 0.4 + k * 6.35, 1.3, 6.1, 5.6, col_widths=[0.7, 2.5, 0.7, 0.75, 0.7, 0.75], font_size=7.5, align_right_from=2)

    out = DECK / "AI_Semi_Value_Chain_Investor_Deck.pptx"
    return D.save(out)
