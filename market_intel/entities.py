"""Entity resolution: map text to universe tickers, themes and value-chain read-through.

Two mechanisms:
* **Aliases** - curated company names/brands/executives per ticker. Bare ticker symbols are matched only
  when they are not English words (``ON``, ``AI``, ``NOW``, ``ARM`` ... are matched only as ``$ARM`` or
  ``(NASDAQ: ARM)``).
* **Themes** - value-chain topics (HBM pricing, CoWoS, export controls, hyperscaler capex ...) that map to
  segments/tickers with a default polarity and a read-through weight.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import config

# ----------------------------------------------------------------------------- aliases
ALIASES: dict[str, list[str]] = {
    "SNPS": ["Synopsys"], "CDNS": ["Cadence Design", "Cadence"], "ARM": ["Arm Holdings", "Arm Ltd", "chip designer Arm", "Arm's chip", "SoftBank-owned Arm", "Arm stock", "Arm shares"],
    "ASML": ["ASML"], "AMAT": ["Applied Materials"], "LRCX": ["Lam Research"], "KLAC": ["KLA Corp", "KLA Corporation", "KLA-Tencor", "KLA"],
    "TER": ["Teradyne"], "ONTO": ["Onto Innovation"], "ENTG": ["Entegris"], "MKSI": ["MKS Inc", "MKS Instruments", "MKS Inc."],
    "ACLS": ["Axcelis"], "AEIS": ["Advanced Energy Industries", "Advanced Energy"], "FORM": ["FormFactor"], "CAMT": ["Camtek"],
    "NVMI": ["Nova Ltd", "Nova Measuring", "Nova Ltd."], "KLIC": ["Kulicke and Soffa", "Kulicke & Soffa", "Kulicke"], "UCTT": ["Ultra Clean Holdings", "Ultra Clean"],
    "ICHR": ["Ichor Holdings", "Ichor"], "COHU": ["Cohu"], "VECO": ["Veeco"], "ACMR": ["ACM Research"], "PLAB": ["Photronics"],
    "TSM": ["TSMC", "Taiwan Semiconductor", "Taiwan Semi"], "INTC": ["Intel", "Lip-Bu Tan"], "GFS": ["GlobalFoundries"], "UMC": ["United Microelectronics", "UMC"],
    "AMKR": ["Amkor"], "NVDA": ["Nvidia", "NVIDIA", "Jensen Huang"], "AMD": ["AMD", "Advanced Micro Devices", "Lisa Su"], "AVGO": ["Broadcom", "Hock Tan"],
    "MRVL": ["Marvell"], "QCOM": ["Qualcomm", "QUALCOMM"], "MU": ["Micron"], "WDC": ["Western Digital"], "STX": ["Seagate"],
    "SNDK": ["Sandisk", "SanDisk"], "NTAP": ["NetApp"], "ANET": ["Arista Networks", "Arista"], "CSCO": ["Cisco"], "CRDO": ["Credo Technology", "Credo"],
    "ALAB": ["Astera Labs", "Astera"], "COHR": ["Coherent Corp", "Coherent Inc", "Coherent Corp.", "Coherent Inc."], "LITE": ["Lumentum"],
    "AAOI": ["Applied Optoelectronics", "Applied Opto"], "CIEN": ["Ciena"], "FN": ["Fabrinet"], "MTSI": ["MACOM"], "SITM": ["SiTime"],
    "RMBS": ["Rambus"], "MXL": ["MaxLinear"], "TXN": ["Texas Instruments"], "ADI": ["Analog Devices"], "MCHP": ["Microchip Technology", "Microchip Tech"],
    "NXPI": ["NXP Semiconductors", "NXP"], "ON": ["onsemi", "Onsemi", "ON Semiconductor"], "MPWR": ["Monolithic Power"], "SWKS": ["Skyworks"], "QRVO": ["Qorvo"],
    "LSCC": ["Lattice Semiconductor", "Lattice Semi"], "SLAB": ["Silicon Labs", "Silicon Laboratories"], "POWI": ["Power Integrations"],
    "DIOD": ["Diodes Inc", "Diodes Incorporated", "Diodes Inc."], "VSH": ["Vishay"], "SMTC": ["Semtech"], "NVTS": ["Navitas"],
    "ALGM": ["Allegro MicroSystems", "Allegro Micro"], "AOSL": ["Alpha and Omega Semiconductor", "Alpha & Omega Semiconductor"], "VICR": ["Vicor"],
    "SMCI": ["Super Micro Computer", "Super Micro", "Supermicro"], "DELL": ["Dell Technologies", "Dell", "Michael Dell"], "HPE": ["Hewlett Packard Enterprise", "HPE"],
    "VRT": ["Vertiv"], "ETN": ["Eaton"], "NVT": ["nVent"], "PWR": ["Quanta Services"], "MOD": ["Modine"], "CLS": ["Celestica"], "JBL": ["Jabil"],
    "FLEX": ["Flex Ltd", "Flex Ltd."], "SANM": ["Sanmina"], "MSFT": ["Microsoft", "Azure", "Satya Nadella"], "GOOGL": ["Alphabet", "Google", "Google Cloud", "Sundar Pichai"],
    "AMZN": ["Amazon", "AWS", "Amazon Web Services", "Andy Jassy"], "META": ["Meta Platforms", "Zuckerberg", "Meta AI", "Meta's", "Meta"],
    "ORCL": ["Oracle", "Larry Ellison", "OCI"], "IBM": ["IBM"], "CRWV": ["CoreWeave"], "NBIS": ["Nebius"], "APLD": ["Applied Digital"], "IREN": ["IREN", "Iris Energy"],
    "DLR": ["Digital Realty"], "EQIX": ["Equinix"], "PLTR": ["Palantir"], "SNOW": ["Snowflake"], "DDOG": ["Datadog"], "MDB": ["MongoDB"], "NOW": ["ServiceNow"],
    "CRM": ["Salesforce", "Agentforce", "Benioff"], "ADBE": ["Adobe"], "AI": ["C3.ai", "C3 AI", "C3ai"], "SOUN": ["SoundHound"], "PATH": ["UiPath"],
    "ESTC": ["Elastic N.V.", "Elastic NV", "Elasticsearch"], "GTLB": ["GitLab"], "APP": ["AppLovin", "Applovin"], "TEM": ["Tempus AI"], "RBRK": ["Rubrik"],
    "VST": ["Vistra"], "CEG": ["Constellation Energy"], "NRG": ["NRG Energy", "NRG"], "TLN": ["Talen Energy", "Talen"], "GEV": ["GE Vernova"],
    "BE": ["Bloom Energy"], "OKLO": ["Oklo"], "SMR": ["NuScale"],
}
# symbols that are also words/acronyms: only matched as $SYM, (NASDAQ: SYM), "SYM stock" etc.
RISKY_SYMBOLS = {"ON", "AI", "BE", "NOW", "PATH", "ARM", "APP", "TEM", "MOD", "FN", "SMR", "FLEX", "FORM", "CRM", "SNOW", "SLAB",
                 "LITE", "ONTO", "META", "MU", "CLS", "ADI", "IBM", "TER", "VST", "STX", "GFS", "OCI", "PWR", "NVT", "ETN", "KLAC",
                 "MAX", "SITM", "TSM", "UMC", "HPE", "AMD", "DELL", "INTC"}

# ----------------------------------------------------------------------------- themes / read-through
SEG = {
    "eda": "EDA & Semiconductor IP", "equip": "Semiconductor Equipment", "foundry": "Foundry, IDM & Packaging",
    "compute": "AI Compute Silicon", "memory": "Memory & Storage", "network": "Interconnect, Networking & Optics",
    "analog": "Analog, Power & Mixed-Signal", "servers": "Servers & Data Center Infrastructure",
    "cloud": "Cloud, Hyperscale & Neocloud", "software": "AI Software & Applications", "power": "Power & Energy for AI",
}
SEMI_SEGMENTS = [SEG[k] for k in ("eda", "equip", "foundry", "compute", "memory", "network", "analog")]

THEMES: dict[str, dict] = {
    "hyperscaler_capex": {"patterns": [r"\bcapex\b", r"capital (expenditure|spending)", r"AI (infrastructure )?spend", r"data.?cent(er|re) (build|spend|invest|capacity)", r"\bStargate\b", r"gigawatt", r"\bGW\b"],
                          "segments": ["compute", "memory", "network", "servers", "foundry", "equip", "power"], "sign": 1, "weight": 0.5},
    "ai_demand": {"patterns": [r"\bAI demand\b", r"GPU demand", r"compute demand", r"inference demand", r"AI (chip|accelerator) (demand|orders?|sales)", r"\bOpenAI\b", r"\bAnthropic\b", r"\bxAI\b", r"Hugging Face"],
                  "segments": ["compute", "network", "servers", "cloud", "memory"], "sign": 1, "weight": 0.4},
    "hbm_dram": {"patterns": [r"\bHBM\d?E?\b", r"high.?bandwidth memory", r"\bDRAM\b", r"memory (prices?|pricing|contract)"], "tickers": ["MU"], "segments": [], "sign": 1, "weight": 0.6},
    "nand": {"patterns": [r"\bNAND\b", r"flash memory", r"\bSSD\b"], "tickers": ["SNDK", "MU", "WDC"], "sign": 1, "weight": 0.6},
    "hdd": {"patterns": [r"\bHDD\b", r"hard (disk|drive)", r"nearline", r"\bHAMR\b"], "tickers": ["STX", "WDC"], "sign": 1, "weight": 0.6},
    "advanced_packaging": {"patterns": [r"\bCoWoS\b", r"advanced packaging", r"chip.?on.?wafer", r"\bTCB\b", r"hybrid bonding", r"\bSoIC\b"], "tickers": ["TSM", "AMKR", "KLIC", "ONTO", "CAMT", "AMAT"], "sign": 1, "weight": 0.5},
    "export_controls": {"patterns": [r"export (control|restriction|curb|licen)", r"entity list", r"chip (ban|curb)s?", r"\bBIS\b", r"\bH20\b", r"China (chip|semiconductor) (sale|export|restriction)", r"Commerce Department"],
                        "tickers": ["NVDA", "AMD", "AMAT", "LRCX", "KLAC", "ACMR", "ASML", "MRVL", "SNPS", "CDNS", "INTC", "MU"], "sign": -1, "weight": 0.5},
    "tariffs": {"patterns": [r"\btariffs?\b", r"Section 232", r"Section 301", r"trade war", r"import dut(y|ies)"], "segments": ["equip", "foundry", "compute", "memory", "analog", "network", "servers"], "sign": -1, "weight": 0.35},
    "optics": {"patterns": [r"\b(800G|1\.6T|400G)\b", r"optical (transceiver|interconnect|module)", r"co-?packaged optics", r"\bCPO\b", r"silicon photonics", r"\bDCI\b"], "tickers": ["COHR", "LITE", "AAOI", "FN", "CRDO", "MRVL", "CIEN", "MTSI", "AVGO"], "sign": 1, "weight": 0.5},
    "networking": {"patterns": [r"\bEthernet\b", r"\bInfiniBand\b", r"\bNVLink\b", r"scale-?(up|out) network", r"switch(ing)? (silicon|ASIC)", r"\bUALink\b", r"\bSerDes\b", r"\bPCIe\b", r"\bCXL\b"], "tickers": ["ANET", "AVGO", "CSCO", "CRDO", "MRVL", "ALAB", "CLS"], "sign": 1, "weight": 0.5},
    "cooling_power": {"patterns": [r"liquid cool", r"thermal management", r"rack-?scale", r"power (delivery|module|management)", r"\b800V(DC)?\b"], "tickers": ["VRT", "NVT", "MOD", "ETN", "SMCI", "DELL", "AEIS", "VICR", "MPWR", "NVTS"], "sign": 1, "weight": 0.45},
    "energy_grid": {"patterns": [r"\bnuclear\b", r"\bPPA\b", r"power purchase agreement", r"\bgrid\b", r"gas turbine", r"\bSMRs?\b", r"small modular reactor", r"\bPJM\b", r"\bFERC\b", r"behind-the-meter", r"data.?cent(er|re) power"],
                    "tickers": ["VST", "CEG", "NRG", "TLN", "GEV", "BE", "OKLO", "SMR", "ETN", "PWR"], "sign": 1, "weight": 0.45},
    "neocloud": {"patterns": [r"\bneocloud\b", r"GPU (cloud|rental|pricing|hourly)", r"AI cloud", r"colocation", r"data.?cent(er|re) (lease|REIT)"], "tickers": ["CRWV", "NBIS", "APLD", "IREN", "DLR", "EQIX"], "sign": 1, "weight": 0.5},
    "custom_asic": {"patterns": [r"custom (AI )?(chip|silicon|ASIC|accelerator)s?", r"\bXPU\b", r"\bTPU\b", r"\bTrainium\b", r"\bMTIA\b", r"\bMaia\b"], "tickers": ["AVGO", "MRVL", "ALAB", "CRDO", "TSM"], "sign": 1, "weight": 0.5},
    "nvidia_supply_chain": {"patterns": [r"\bBlackwell\b", r"\bRubin\b", r"\bGB[23]00\b", r"\bNVL\d+\b", r"\bVera Rubin\b", r"Nvidia supply chain"], "tickers": ["NVDA", "TSM", "MU", "SMCI", "DELL", "VRT", "ANET", "COHR", "LITE", "ALAB", "CRDO", "AMKR", "CLS", "FN"], "sign": 1, "weight": 0.4},
    "eda_ip": {"patterns": [r"\bEDA\b", r"chip design (software|tools?)", r"semiconductor IP\b"], "tickers": ["SNPS", "CDNS", "ARM"], "sign": 1, "weight": 0.5},
    "wfe": {"patterns": [r"\bWFE\b", r"wafer fab equipment", r"semiconductor equipment", r"\bfab\b", r"\bEUV\b", r"lithography", r"\b2nm\b", r"\b3nm\b", r"\b18A\b", r"gate-all-around", r"\bGAA\b", r"capacity expansion"],
            "segments": ["equip"], "tickers": ["TSM", "INTC"], "sign": 1, "weight": 0.4},
    "analog_auto_industrial": {"patterns": [r"analog (chip|semi)", r"auto(motive)? (chip|semi|demand)", r"industrial (chip|demand|recovery)", r"\bMCU\b", r"silicon carbide", r"\bSiC\b", r"\bGaN\b"], "segments": ["analog"], "sign": 1, "weight": 0.5},
    "smartphone_pc": {"patterns": [r"\bsmartphone\b", r"\bhandset\b", r"\bPC (demand|market|shipments?)\b", r"\biPhone\b", r"AI PC"], "tickers": ["QCOM", "SWKS", "QRVO", "INTC", "AMD", "MU", "SNDK"], "sign": 1, "weight": 0.35},
    "ai_software": {"patterns": [r"\bagentic\b", r"AI agents?", r"\bcopilot\b", r"enterprise AI", r"\bLLM\b", r"large language model", r"\bSaaS\b", r"seat-based"], "segments": ["software"], "sign": 1, "weight": 0.35},
    "efficiency_shock": {"patterns": [r"\bDeepSeek\b", r"cheaper (AI )?model", r"compute efficiency", r"fewer (GPUs|chips)", r"AI bubble", r"overbuild", r"digestion"], "segments": ["compute", "servers", "cloud", "power", "memory", "network"], "sign": -1, "weight": 0.4},
    "taiwan_geopolitics": {"patterns": [r"Taiwan Strait", r"\bblockade\b", r"invasion of Taiwan", r"PLA (drills|exercise)", r"cross-strait"], "segments": ["foundry", "compute", "equip", "memory"], "sign": -1, "weight": 0.4},
    "china_ai_chips": {"patterns": [r"\bHuawei\b", r"\bAscend\b", r"Chinese (AI )?chip", r"\bSMIC\b", r"\bCambricon\b", r"domestic (chip|GPU)"], "tickers": ["NVDA", "AMD", "AMAT", "LRCX", "ACMR"], "sign": -1, "weight": 0.3},
    "rates_macro": {"patterns": [r"\bFOMC\b", r"\bFed\b", r"Federal Reserve", r"rate (cut|hike)s?", r"\bCPI\b", r"\binflation\b", r"\bpayrolls\b", r"jobs report", r"\bunemployment\b", r"\bGDP\b", r"\bPCE\b", r"Treasury yields?", r"bond selloff", r"\bPowell\b", r"\btariff\b"],
                    "segments": [], "sign": 0, "weight": 0.0},
}
THEME_RE = {k: [re.compile(p, re.I) for p in v["patterns"]] for k, v in THEMES.items()}
# causal edge and affected KPI behind every value-chain read-through (S7): a read-through without an edge is not written
THEME_EDGES: dict[str, tuple[str, str]] = {
    "hyperscaler_capex": ("hyperscaler capex plans -> orders for AI infrastructure suppliers", "revenue growth / backlog"),
    "ai_demand": ("AI compute demand -> accelerator, memory, network and server volumes", "revenue growth"),
    "hbm_dram": ("HBM/DRAM pricing and allocation -> memory ASPs", "gross margin / ASP"),
    "nand": ("NAND pricing -> flash ASPs", "gross margin / ASP"),
    "hdd": ("nearline HDD demand and pricing", "revenue / ASP"),
    "advanced_packaging": ("CoWoS / advanced-packaging capacity -> packaging and tool demand", "capacity utilisation / orders"),
    "export_controls": ("US export controls -> China revenue at risk", "China revenue share"),
    "tariffs": ("tariffs -> landed cost and demand elasticity", "gross margin / demand"),
    "optics": ("optical interconnect demand (800G / 1.6T / CPO)", "revenue growth"),
    "networking": ("AI networking attach (Ethernet / InfiniBand / NVLink)", "revenue growth"),
    "cooling_power": ("rack power density -> liquid cooling and power-delivery content", "content per rack"),
    "energy_grid": ("data-center power contracts (PPA, nuclear, grid)", "contracted capacity / pricing"),
    "neocloud": ("GPU cloud demand and pricing", "utilisation / pricing"),
    "custom_asic": ("custom accelerator programs (XPU / TPU / Trainium)", "design wins / revenue mix"),
    "nvidia_supply_chain": ("Nvidia platform ramp (Blackwell / Rubin) -> supplier volumes", "revenue growth"),
    "eda_ip": ("chip design activity -> EDA / IP licensing", "bookings"),
    "wfe": ("wafer-fab equipment spending -> tool orders", "orders / WFE share"),
    "analog_auto_industrial": ("auto / industrial demand cycle", "revenue growth / inventory"),
    "smartphone_pc": ("smartphone / PC unit demand", "unit shipments / revenue"),
    "ai_software": ("enterprise AI adoption -> seat / consumption growth", "net revenue retention"),
    "efficiency_shock": ("compute-efficiency gains -> lower hardware demand", "capex outlook / demand"),
    "taiwan_geopolitics": ("Taiwan supply-disruption risk -> foundry and supply chain", "supply continuity / risk premium"),
    "china_ai_chips": ("domestic Chinese accelerators -> share loss in China", "China revenue share"),
    "rates_macro": ("macro / rates -> valuation multiples", "discount rate / multiple"),
}


@dataclass
class Company:
    ticker: str
    name: str
    segment: str
    sub_segment: str = ""
    description: str = ""
    cik: int | None = None
    aliases: list[str] = field(default_factory=list)
    weight: float = 0.0        # executable target weight of the canonical run (0 if not in the target list)
    model_weight: float = 0.0  # fully-invested model weight before the vol target (display only)
    next_earnings: str | None = None
    market_cap: float | None = None

    @property
    def is_holding(self) -> bool:
        return self.weight > 0


def _alias_regex(alias: str) -> re.Pattern:
    # case-sensitive for proper nouns; brands written in lowercase (onsemi) are given explicitly
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9-])")


class EntityIndex:
    def __init__(self, companies: list[Company]):
        self.companies: dict[str, Company] = {c.ticker: c for c in companies}
        self.by_cik: dict[int, str] = {c.cik: c.ticker for c in companies if c.cik}
        self.by_segment: dict[str, list[str]] = {}
        for c in companies:
            self.by_segment.setdefault(c.segment, []).append(c.ticker)
        self._alias_re: list[tuple[str, re.Pattern]] = []
        self._symbol_re: list[tuple[str, re.Pattern]] = []
        for c in companies:
            for a in c.aliases:
                self._alias_re.append((c.ticker, _alias_regex(a)))
            t = c.ticker
            # $SYM, (NASDAQ: SYM), (NYSE:SYM), "SYM stock", "SYM shares"
            te = re.escape(t)
            self._symbol_re.append((t, re.compile(r"(?:\$" + te + r"\b|\((?:NASDAQ|NYSE|Nasdaq|NYSE American|NYSE Arca)\s*:\s*" + te + r"\)|\b" + te + r"(?= (?:stock|shares|ticker))\b)")))
            if t not in RISKY_SYMBOLS and len(t) >= 3 and t.isalpha():
                self._symbol_re.append((t, re.compile(r"(?<![A-Za-z$])" + te + r"(?![A-Za-z])")))

    # ---- basic accessors -------------------------------------------------------------------
    @property
    def tickers(self) -> list[str]:
        return list(self.companies)

    @property
    def holdings(self) -> list[str]:
        return [t for t, c in self.companies.items() if c.is_holding]

    def name(self, t: str) -> str:
        c = self.companies.get(t)
        return c.name if c else t

    def segment_tickers(self, key_or_name: str) -> list[str]:
        name = SEG.get(key_or_name, key_or_name)
        return self.by_segment.get(name, [])

    # ---- matching ----------------------------------------------------------------------------
    def match(self, text: str, hints: list[str] | None = None) -> dict[str, float]:
        """Return {ticker: confidence} for companies named in ``text`` (plus source-provided hints)."""
        found: dict[str, float] = {}
        if not text:
            text = ""
        for t, rx in self._alias_re:
            if rx.search(text):
                found[t] = max(found.get(t, 0), 0.9)
        for t, rx in self._symbol_re:
            if rx.search(text):
                found[t] = max(found.get(t, 0), 0.85)
        for h in hints or []:
            h = h.upper()
            if h in self.companies:
                found[h] = max(found.get(h, 0), 0.7)
        return found

    def themes(self, text: str) -> list[str]:
        out = []
        for k, rxs in THEME_RE.items():
            if any(rx.search(text or "") for rx in rxs):
                out.append(k)
        return out

    def readthrough_detail(self, themes: list[str], direct: dict[str, float], broad_factor: float = 1.0) -> dict[str, dict]:
        """{ticker: {weight, sign, theme, edge, kpi, via}} for value-chain names implied by the themes but not named directly.
        Segment-wide targets are scaled by ``broad_factor`` (used to damp generic theme articles). The strongest theme wins per ticker."""
        out: dict[str, dict] = {}
        for th in themes:
            spec = THEMES.get(th)
            if not spec or spec.get("weight", 0) <= 0:
                continue
            edge, kpi = THEME_EDGES.get(th, (th.replace("_", " "), "n/a"))
            targets = [(t, 1.0, "named") for t in spec.get("tickers", [])]
            for seg in spec.get("segments", []):
                targets.extend((t, broad_factor, f"segment:{seg}") for t in self.segment_tickers(seg))
            for t, f, via in targets:
                if t in direct or t not in self.companies:
                    continue
                w = spec["weight"] * f
                if t not in out or out[t]["weight"] < w:
                    out[t] = {"weight": w, "sign": spec["sign"], "theme": th, "edge": edge, "kpi": kpi, "via": via}
        return out

    def readthrough(self, themes: list[str], direct: dict[str, float], broad_factor: float = 1.0) -> dict[str, tuple[float, int]]:
        """{ticker: (weight, sign)} view of readthrough_detail (kept for the rule engine and tests)."""
        return {t: (d["weight"], d["sign"]) for t, d in self.readthrough_detail(themes, direct, broad_factor).items()}

    def describe_universe(self) -> str:
        """Compact universe listing for LLM prompts (stable text -> prompt-cacheable)."""
        lines = []
        for seg in sorted(self.by_segment, key=lambda s: list(SEG.values()).index(s) if s in SEG.values() else 99):
            for t in self.by_segment[seg]:
                c = self.companies[t]
                held = " [TARGET %.1f%% exec / %.1f%% model]" % (c.weight * 100, c.model_weight * 100) if c.is_holding else ""
                lines.append(f"{t} | {c.name} | {seg} / {c.sub_segment} | {c.description}{held}")
        return "\n".join(lines)


def build_index() -> EntityIndex:
    hold = config.holdings()
    model = config.holdings_model()
    extra = config.extra_watchlist()
    companies = []
    for r in config.universe_table():
        t = r["ticker"]
        aliases = list(ALIASES.get(t, []))
        aliases += list((extra.get(t) or {}).get("aliases", []))
        if not aliases and r.get("name"):
            nm = re.sub(r",? (Inc\.?|Corp\.?|Corporation|Ltd\.?|plc|PLC|N\.V\.|Holdings?|Co\.?|LLC|Incorporated|Company|Technologies?)$", "", r["name"]).strip()
            if len(nm) >= 4:
                aliases.append(nm)
        companies.append(Company(ticker=t, name=r.get("name") or t, segment=r.get("segment", ""), sub_segment=r.get("sub_segment", ""),
                                 description=r.get("description", ""), cik=r.get("cik"), aliases=aliases, weight=hold.get(t, 0.0), model_weight=model.get(t, 0.0),
                                 next_earnings=r.get("next_earnings"), market_cap=r.get("market_cap")))
    return EntityIndex(companies)
