"""Rule-based event classifier: type, direction, materiality, tickers (direct + read-through), pillars, action.

It runs on every item, is fully deterministic and needs no API. The LLM layer (``llm.py``) refines the
same fields for the most relevant items.
"""
from __future__ import annotations

import re

from ..entities import EntityIndex, THEMES
from ..sources.base import RawItem

RULES_VERSION = "rules-v3-2026-09-03"        # bump when patterns or scoring rules change
EVENT_TYPES = ["earnings", "guidance", "preannouncement", "m_and_a", "capital_raise", "buyback_dividend", "contract_customer", "product",
               "capacity_capex", "supply_chain_pricing", "management", "regulatory_export", "tariff_trade", "litigation_investigation",
               "analyst", "insider_holder", "index_change", "macro_data", "fed_policy", "geopolitical", "policy", "price_move", "filing",
               "commentary", "market_news", "other"]
HORIZONS = ["days", "weeks", "quarters"]
PILLARS = ["growth", "momentum", "value", "quality", "risk", "macro"]
ACTIONS = ["none", "monitor", "analyst review", "refresh model inputs"]
LEGACY_ACTIONS = {"thesis check": "analyst review", "re-run model": "refresh model inputs"}   # values stored before S3

# ordered by priority: (event_type, [patterns], base materiality)
TYPE_RULES: list[tuple[str, list[str], float]] = [
    ("guidance", [r"\b(cuts?|lowers?|slash(es|ed)?|trims?|reduc(es|ed)) (its )?(full[- ]year |annual |quarterly |fiscal |20\d\d )?(guidance|outlook|forecast)", r"\b(raises?|lifts?|boosts?|hikes?) (its )?(full[- ]year |annual |quarterly |fiscal |20\d\d )?(guidance|outlook|forecast)", r"\bguidance\b", r"\boutlook\b.*\b(above|below|beat|miss|disappoint|strong|weak)", r"\bwarns?\b", r"profit warning"], 0.9),
    ("preannouncement", [r"preliminary (results|revenue|quarter)", r"pre-?announc", r"business update"], 0.85),
    ("earnings", [r"\b(reports?|posts?|announces?|delivers?) (record |strong |weak |mixed |better|worse)?[- ]?(than[- ]expected )?(first|second|third|fourth|fiscal|Q[1-4]|quarterly)[- ](quarter|20\d\d|results|earnings)", r"\bQ[1-4] (results|earnings|revenue|EPS)", r"quarterly (results|earnings|revenue)", r"\b(beats?|tops?|misses?|falls? short of) (estimates|expectations|consensus|forecasts|Street)", r"earnings (beat|miss|call|report|preview|season)", r"\bEPS\b.*\b(beat|miss|estimate)", r"results of operations"], 0.9),
    ("m_and_a", [r"\b(to acquire|acquires?|acquisition of|agrees? to buy|to buy|buyout|takeover|merger|merge with|in talks to (buy|acquire)|to be acquired|definitive agreement to acquire|take[- ]private|sells? (its )?\w+ (unit|business|division))\b"], 0.8),
    ("capital_raise", [r"\b(public offering|convertible (senior )?notes?|secondary offering|at-the-market|registered direct|share sale|equity offering|prices? (its )?offering|debt offering|senior notes? offering|notes? due 20\d\d)\b"], 0.7),
    ("buyback_dividend", [r"\b(share repurchase|buyback|repurchase program|dividend (increase|hike|cut|declar)|declares? (a )?(quarterly )?dividend)\b"], 0.5),
    ("regulatory_export", [r"export (control|restriction|curb|licen|ban)", r"entity list", r"\bchip (ban|curbs?)\b", r"\bBIS\b", r"Commerce Department", r"\bH20\b", r"national security review", r"\bCFIUS\b"], 0.8),
    ("tariff_trade", [r"\btariffs?\b", r"Section 232", r"Section 301", r"trade (war|deal|talks|truce)", r"import duties"], 0.6),
    ("litigation_investigation", [r"\b(lawsuit|sues|sued|class action|subpoena|SEC (charges|probe|investigation)|investigation into|probe into|indict|fraud|whistleblower|non-reliance|restat(e|ement)|delist)\b", r"cyber ?(attack|security incident|breach)", r"antitrust (probe|investigation|ruling|approval|case)", r"\bFTC\b", r"\bDOJ\b", r"\bSAMR\b", r"\bEU\b.*\b(fine|probe|ruling)"], 0.7),
    ("management", [r"\b(appoints?|names?|hires?) (new )?(CEO|CFO|COO|CTO|chief|president|chairman)", r"\b(CEO|CFO|COO|CTO|chief executive|chief financial|founder|chairman) (steps? down|resigns?|to leave|departs?|exits?|ousted|fired|dies|retire)", r"\bsteps down as\b", r"\bresignation\b", r"succession plan", r"layoffs?", r"job cuts", r"restructuring"], 0.6),
    ("insider_holder", [r"\b(13D|13G|activist|stake in|takes? (a )?stake|insider (buying|selling|sells|buys)|Form 4|sold shares|bought shares)\b"], 0.5),
    ("index_change", [r"(joins?|added to|inclusion in|removed from|to join) (the )?(S&P 500|S&P 500 index|Nasdaq-?100|Dow Jones Industrial|Russell)", r"index (rebalance|inclusion|reconstitution)"], 0.6),
    ("analyst", [r"\b(upgrades?|downgrades?)\b", r"price target", r"\bPT\b", r"initiates? (coverage|at)", r"\b(overweight|underweight|outperform|underperform|top pick|conviction (buy|list))\b", r"\b(reiterates?|maintains?) (buy|sell|hold|neutral|outperform)", r"\b(Morgan Stanley|Goldman|JPMorgan|Bank of America|BofA|Jefferies|Bernstein|Citi|Barclays|Wells Fargo|Evercore|Needham|Stifel|KeyBanc|Mizuho|Raymond James|Piper Sandler|Wolfe|Melius|Rosenblatt|Cantor|TD Cowen|Loop Capital|Susquehanna|Oppenheimer|Baird|Truist|Deutsche Bank|HSBC|BNP|Macquarie|Daiwa|Nomura)\b.*\b(rating|target|upgrade|downgrade|buy|sell|neutral|overweight|initiat)"], 0.4),
    ("contract_customer", [r"\b(multi-?year (agreement|deal|contract)|design win|selected by|selects|wins? (a )?(contract|order|deal)|orders? (worth|valued)|\$\d[\d.,]*\s?(billion|million|bn|mn|B|M)\b.*\b(deal|contract|order|agreement|investment|commitment)|supply agreement|strategic partnership|partners? with|teams? up with|to supply|to deploy|deployment of|expands? (its )?partnership|purchase agreement|reservation)\b"], 0.6),
    ("capacity_capex", [r"\b(capex|capital (expenditure|spending)|new (fab|plant|factory|facility)|expand(s|ing)? (capacity|production|manufacturing)|capacity expansion|breaks ground|\d+ ?(GW|MW|gigawatt|megawatt)|data.?cent(er|re) (campus|build|project|investment|expansion))\b", r"\$\d[\d.,]*\s?(billion|bn|B)\b.*\b(invest|spend|build|expansion|capacity|data.?cent)", r"\b(equipment|tool) (purchases?|orders?|spending|buying)\b", r"doubles? down on (equipment|capacity|capex|investment|expansion)"], 0.55),
    ("supply_chain_pricing", [r"\b(shortage|supply constraint|constrained supply|lead times?|allocation|double[- ]ordering|inventory (correction|digestion|glut|build)|price (hike|increase|cut|war)|contract prices?|spot prices?|pricing (power|environment)|TrendForce|ASP|sold out|capacity is (full|booked)|utilization rate)\b"], 0.55),
    ("product", [r"\b(launch(es|ed)?|unveils?|introduces?|debuts?|announces? (its |the |a )?(new|next[- ]gen)|roadmap|next[- ]generation|ships?|begins? (volume )?(production|shipments)|tape[- ]?out|sampling|GTC|Computex|CES|keynote)\b"], 0.4),
    ("fed_policy", [r"\bFOMC\b", r"\b(Fed|Federal Reserve)\b", r"rate (cut|hike|decision|path)s?", r"\bPowell\b", r"basis points", r"dot plot", r"quantitative (easing|tightening)", r"\bECB\b", r"Bank of Japan", r"\bBOJ\b"], 0.6),
    ("macro_data", [r"\bCPI\b", r"\binflation\b", r"\bpayrolls\b", r"jobs report", r"nonfarm", r"\bunemployment\b", r"\bGDP\b", r"\bPCE\b", r"retail sales", r"\bISM\b", r"\bPMI\b", r"jobless claims", r"consumer (sentiment|confidence)", r"\bJOLTS\b", r"Treasury yields?", r"bond (selloff|rally|market)", r"\b10-year\b", r"housing starts", r"durable goods", r"trade deficit"], 0.55),
    ("geopolitical", [r"\bTaiwan\b", r"\bStrait\b", r"\bsanctions?\b", r"\bIran\b", r"\bRussia\b", r"\bUkraine\b", r"Middle East", r"\bblockade\b", r"\bmissile\b", r"\bwar\b", r"\bceasefire\b", r"\bPLA\b", r"\bXi Jinping\b", r"\bBeijing\b.*\b(retaliat|ban|probe|restrict)"], 0.5),
    ("policy", [r"executive order", r"\bWhite House\b", r"\bCongress\b", r"\bSenate\b", r"\bbill\b.*\b(pass|vote|sign)", r"CHIPS Act", r"\bsubsid(y|ies)\b", r"\bregulat(ion|ory)\b", r"AI (Action Plan|regulation|safety bill)", r"data.?cent(er|re) (moratorium|permit|zoning)"], 0.5),
]
TYPE_RE = [(t, [re.compile(p, re.I) for p in pats], m) for t, pats, m in TYPE_RULES]
# a place name alone (Taiwan, Beijing, Iran ...) is not a geopolitical *event*: when a universe company is named, one of these must also hit
GEO_STRONG_RE = [re.compile(p, re.I) for p in [r"\bStrait\b", r"\bblockade\b", r"\binvasion\b", r"\bmissiles?\b", r"\bwar\b", r"\bceasefire\b", r"\bPLA\b", r"\bsanctions?\b",
                                                r"\bBeijing\b.*\b(retaliat|ban|probe|restrict)", r"military (drills?|exercises?)", r"\b(air|drone|missile) strikes?\b", r"\bconflict\b", r"\bescalat"]]

POS_WORDS = {r"\bbeats?\b": 1.5, r"\btops?\b": 1.2, r"\brecord\b": 1.0, r"\bsurges?\b": 1.5, r"\bsoars?\b": 1.5, r"\bjumps?\b": 1.2, r"\brallies\b": 1.0,
             r"\bclimbs?\b": 0.8, r"\bup \d+(\.\d+)?%": 1.0, r"\bgrowth of \d+%": 0.8, r"\brises?\b": 0.7, r"\bgains?\b": 0.6, r"\bstrong(er)?\b": 0.8, r"\braises?\b": 1.2, r"\bboosts?\b": 1.0,
             r"\bupgrades?\b": 1.5, r"\bwins?\b": 1.0, r"\bexpands?\b": 0.5, r"\baccelerat": 0.8, r"\bbetter[- ]than[- ]expected\b": 1.5,
             r"\bahead of (estimates|expectations)\b": 1.2, r"\boutperform": 0.8, r"\bbullish\b": 0.8, r"\brobust\b": 0.6, r"\bdemand (is )?strong\b": 1.0,
             r"\bsold out\b": 1.0, r"\bpositive\b": 0.5, r"\bbreakout\b": 0.5, r"\bhits? (all-time|record) high": 1.2, r"\beases?\b": 0.6,
             r"\blifts?\b": 0.8, r"\bexceeds?\b": 1.0, r"\bapprov(es|ed|al)\b": 0.8, r"\bdividend (increase|hike)\b": 0.8, r"\bbuyback\b": 0.5,
             r"\bdesign win\b": 1.0, r"\bmulti-?year\b": 0.5, r"\bexpansion\b": 0.4, r"\bpartnership\b": 0.4, r"\bmomentum\b": 0.3,
             r"\bresum(es|ed)\b": 0.5, r"\bsettles?\b": 0.4, r"\bhigher\b": 0.4, r"\bstake\b": 0.3, r"\btop pick\b": 1.0}
NEG_WORDS = {r"\bmiss(es|ed)?\b": 1.5, r"\bfalls? short\b": 1.5, r"\bcuts?\b": 1.2, r"\blowers?\b": 1.2, r"\bslash(es|ed)?\b": 1.5, r"\bwarns?\b": 1.5,
             r"\bplunges?\b": 1.8, r"\bplummets?\b": 1.8, r"\btumbles?\b": 1.5, r"\bsinks?\b": 1.3, r"\bslumps?\b": 1.3, r"\bslides?\b": 1.0, r"\bslips?\b": 0.9, r"\bdown \d+(\.\d+)?%": 1.0, r"\bdives?\b": 1.3, r"\bcrater": 1.5,
             r"\bdrops?\b": 0.9, r"\bfalls?\b": 0.8, r"\bdeclines?\b": 0.7, r"\bweak(er|ness)?\b": 0.9, r"\bdisappoint": 1.3, r"\bdowngrades?\b": 1.5,
             r"\bdelays?\b": 1.0, r"\bhalts?\b": 1.0, r"\bsuspends?\b": 1.0, r"\brecall\b": 1.0, r"\bprobe\b": 0.9, r"\binvestigat": 0.9,
             r"\blawsuit\b": 0.9, r"\bsues?\b": 0.8, r"\blayoffs?\b": 0.7, r"\bshortfall\b": 1.2, r"\bworse[- ]than[- ]expected\b": 1.5,
             r"\bbelow (estimates|expectations|consensus)\b": 1.3, r"\bunderperform": 0.8, r"\bbearish\b": 0.8, r"\bsell-?off\b": 1.0, r"\brout\b": 1.2,
             r"\bban(s|ned)?\b": 1.0, r"\brestrict": 0.9, r"\bcurbs?\b": 1.0, r"\btariffs?\b": 0.6, r"\bimpairment\b": 1.2, r"\bwrite-?down\b": 1.0,
             r"\bbankrupt": 2.0, r"\bdefault\b": 1.2, r"\bfraud\b": 1.5, r"\brestat": 1.5, r"\bnon-reliance\b": 1.5, r"\bdelist": 1.5,
             r"\bresign": 0.7, r"\bsteps? down\b": 0.7, r"\bnegative\b": 0.5, r"\bconcerns?\b": 0.5, r"\brisks?\b": 0.3, r"\bglut\b": 1.0,
             r"\bcancel": 1.0, r"\bpressure\b": 0.4, r"\bdilut": 0.8, r"\boffering\b": 0.6, r"\bshortage\b": 0.3, r"\bloses?\b": 0.8, r"\blower\b": 0.4,
             r"\bcrash": 1.5, r"\bfine[sd]?\b": 0.6, r"\bbubble\b": 0.8, r"\boverbuild": 0.8, r"\bdigestion\b": 0.7}
POS_RE = [(re.compile(p, re.I), w) for p, w in POS_WORDS.items()]
NEG_RE = [(re.compile(p, re.I), w) for p, w in NEG_WORDS.items()]

COMMENTARY_RE = [re.compile(p, re.I) for p in [
    r"^is \w[\w.& ]* (stock )?(a )?(buy|sell|hold)", r"buy,? sell,? or hold", r"should you buy", r"\bno-brainer\b", r"stocks? to (buy|watch|own|avoid)",
    r"\bprediction\b", r"here'?s why", r"what to know", r"\d+ reasons", r"millionaire", r"\btop \d+ (stocks|picks)", r"\bvs\.? \b", r"better (AI |semiconductor )?stock",
    r"\bcould (soar|skyrocket|double|crash)\b", r"\bbest (AI|semiconductor|growth) stocks?\b", r"\bmagnificent seven\b", r"if you('d| had) invested", r"\bwhy .* stock (is|was) (up|down|falling|rising|soaring|sinking)\b",
    r"^why ", r"\bwall street('s)? (favorite|loves)\b", r"\bopinion\b", r"\bexplained\b", r"\bin \d+ charts\b", r"\bquiz\b", r"\bpodcast\b", r"\bvideo\b", r"\bwatch:", r"\banalysis:", r"should (investors|you|i) (buy|sell|hold|be)", r"chart pattern", r"bottom fishing", r"driving attention",
    r"\bwhat('s| is) driving\b", r"\bworth (buying|a look)\b", r"\btime to (buy|sell)\b", r"^prediction:", r"\bbigger opportunity\b", r"\bstock (is|could be) a (buy|sell|steal|bargain)\b",
    r"\bbuy the dip\b", r"\bbull case\b", r"\bbear case\b", r"\bshould you\b", r"\bmoves to watch\b", r"\b(tumbling|soaring|falling|rising) today\b", r"^(can|could|will|is|are|should|does|do|why|what|how|which|where) \S.*\?\s*$"]]
NOISE_RE = [re.compile(p, re.I) for p in [r"\bETFs?\b", r"\bYieldMax\b", r"Option Income", r"\bcovered.?call\b", r"\b(Direxion|ProShares|GraniteShares|Roundhill|Defiance|Kurv|Rex Shares)\b",
                                            r"\b(fund|trust)\b.*\bdeclares?\b", r"leveraged (and inverse )?(fund|product)", r"\bETN\b(?! Eaton)", r"\bmutual fund\b", r"\bclosed-end fund\b"]]
SEMI_MACRO_THEMES = {"taiwan_geopolitics", "export_controls", "tariffs", "china_ai_chips", "hyperscaler_capex", "ai_demand", "efficiency_shock", "wfe", "hbm_dram"}
RT_TYPES = {"earnings", "guidance", "preannouncement", "supply_chain_pricing", "capacity_capex", "contract_customer", "product", "m_and_a", "regulatory_export",
            "tariff_trade", "geopolitical", "policy", "macro_data", "fed_policy", "price_move", "management", "litigation_investigation", "filing"}
LOW_VALUE_PUBLISHERS = {"Motley Fool", "The Motley Fool", "24/7 Wall St.", "TipRanks", "Zacks", "Zacks Investment Research", "Benzinga", "InvestorPlace",
                        "Simply Wall St", "Insider Monkey", "MarketBeat", "GuruFocus", "Barchart", "Sharewise", "Yahoo Finance Video", "Trefis",
                        "Seeking Alpha", "Investing.com", "FXStreet", "Finbold", "Stocktwits", "TradingView", "Investopedia", "Kiplinger", "Nasdaq",
                        "AOL", "MSN", "Substack", "Medium", "Forbes"}
TIER1_PUBLISHERS = {"Reuters", "Bloomberg", "Wall Street Journal", "Financial Times", "CNBC", "Barron's", "The Information", "New York Times",
                    "Nikkei Asia", "DigiTimes", "SemiAnalysis", "TrendForce", "Investor's Business Daily", "SEC EDGAR", "Federal Reserve", "BLS", "White House",
                    "Business Wire", "GlobeNewswire", "PR Newswire", "MarketWatch", "Dow Jones", "Axios", "The Register", "Tom's Hardware", "TechCrunch"}
MACRO_TYPES = {"macro_data", "fed_policy", "geopolitical", "policy", "tariff_trade"}
TYPE_PILLARS = {"earnings": ["growth"], "guidance": ["growth"], "preannouncement": ["growth"], "supply_chain_pricing": ["growth"], "contract_customer": ["growth"],
                "capacity_capex": ["growth"], "product": ["growth"], "analyst": ["momentum"], "price_move": ["momentum"], "index_change": ["momentum"],
                "m_and_a": ["value", "quality"], "capital_raise": ["value", "quality"], "buyback_dividend": ["value"], "litigation_investigation": ["risk"],
                "management": ["risk", "quality"], "regulatory_export": ["risk", "growth"], "tariff_trade": ["risk", "macro"], "insider_holder": ["momentum"],
                "macro_data": ["macro"], "fed_policy": ["macro"], "geopolitical": ["macro", "risk"], "policy": ["macro"], "filing": ["quality"]}
TYPE_HORIZON = {"earnings": "quarters", "guidance": "quarters", "preannouncement": "quarters", "m_and_a": "quarters", "regulatory_export": "quarters",
                "tariff_trade": "quarters", "capacity_capex": "quarters", "contract_customer": "quarters", "management": "weeks", "capital_raise": "weeks",
                "analyst": "days", "price_move": "days", "index_change": "days", "macro_data": "weeks", "fed_policy": "weeks", "geopolitical": "weeks",
                "supply_chain_pricing": "weeks", "product": "weeks", "litigation_investigation": "weeks"}


def _sentiment(title: str, body: str) -> float:
    s = 0.0
    for rx, w in POS_RE:
        s += w * (2 * len(rx.findall(title)) + len(rx.findall(body)))
    for rx, w in NEG_RE:
        s -= w * (2 * len(rx.findall(title)) + len(rx.findall(body)))
    return s


def direction_from_score(s: float) -> int:
    if s >= 3.0:
        return 2
    if s >= 1.0:
        return 1
    if s <= -3.0:
        return -2
    if s <= -1.0:
        return -1
    return 0


def source_factor(item: RawItem) -> float:
    if item.source_type in ("sec", "price"):
        return 1.0
    if item.source_name in TIER1_PUBLISHERS:
        return max(item.weight, 0.85)
    if item.source_name in LOW_VALUE_PUBLISHERS:
        return min(item.weight, 0.4)
    return item.weight


def is_commentary(item: RawItem) -> bool:
    if item.source_type in ("sec", "price", "macro", "policy"):
        return False
    return any(rx.search(item.title) for rx in COMMENTARY_RE)


def classify(item: RawItem, index: EntityIndex) -> dict:
    title, body = item.title or "", (item.summary or "")[:500]
    text = f"{title}. {body}"
    direct = index.match(text, item.tickers_hint)
    themes = index.themes(text)
    commentary = is_commentary(item)
    sf = source_factor(item)
    # ---- event type ------------------------------------------------------------------------------
    etype, base_mat, direction_prior, strong = "other", 0.25, 0, False
    if item.source_type == "sec":
        etype = item.extra.get("rule_type", "filing"); base_mat = float(item.extra.get("rule_materiality", 0.4)); direction_prior = int(item.extra.get("rule_direction", 0)); strong = True
    elif item.source_type == "price":
        etype, strong = "price_move", True
        pct = abs(float(item.extra.get("pct", 0)))
        base_mat = min(1.0, 0.5 + pct * 4)             # 4% -> 0.66, 8% -> 0.82, 12.5%+ -> 1.0
        direction_prior = 2 if float(item.extra.get("pct", 0)) > 0.08 else 1 if float(item.extra.get("pct", 0)) > 0 else -2 if float(item.extra.get("pct", 0)) < -0.08 else -1
    else:
        for t, rxs, m in TYPE_RE:
            hits = sum(1 for rx in rxs if rx.search(title)) * 2 + sum(1 for rx in rxs if rx.search(body))
            if hits and t == "geopolitical" and direct and not any(rx.search(text) for rx in GEO_STRONG_RE):
                continue                                   # company news that merely mentions a country is not geopolitics
            if hits:
                etype, base_mat, strong = t, m, hits >= 2
                break
        if etype == "other" and not direct and not themes:
            etype = "market_news" if item.source_type in ("news", "press") else "other"
    if commentary:
        etype = "commentary"
    noise = item.source_type not in ("sec", "price") and any(rx.search(title) for rx in NOISE_RE)
    if noise:
        etype = "other"
    # ---- direction ----------------------------------------------------------------------------------
    sent = _sentiment(title, body)
    direction = direction_from_score(sent)
    if etype == "fed_policy":                          # rate cuts are positive for equities, hikes negative
        if re.search(r"rate cut|cuts? (interest )?rates?|lower(s|ed|ing)? (the )?(target range|rates?|fed funds)|easing", text, re.I):
            direction = 1
        elif re.search(r"rate hike|raises? (interest )?rates?|hikes? rates?|raise(s|d)? (the )?target range|tightening", text, re.I):
            direction = -1
        elif re.search(r"holds? (rates?|steady)|unchanged|leaves? rates?", text, re.I):
            direction = 0
    if direction == 0:
        direction = direction_prior
    if direction == 0 and etype in ("regulatory_export", "tariff_trade", "geopolitical"):
        direction = -1
    if direction == 0 and themes:
        signs = [THEMES[t]["sign"] for t in themes if THEMES[t]["sign"]]
        if signs and etype not in ("commentary", "market_news", "other"):
            direction = int(round(sum(signs) / len(signs))) or 0
    # ---- materiality / relevance --------------------------------------------------------------------
    materiality = base_mat * (0.6 + 0.4 * sf)
    if commentary:
        materiality = min(materiality, 0.12)
    if item.source_type in ("news", "press") and not direct and etype not in MACRO_TYPES and etype not in ("commentary",):
        materiality *= (0.8 if (themes and sf >= 0.75) else 0.6 if themes else 0.4)
    if etype in MACRO_TYPES and item.source_type == "news" and sf < 0.7:
        materiality *= 0.7
    if noise:
        materiality *= 0.2
    materiality = float(max(0.0, min(1.0, materiality)))
    # value-chain read-through only for material, event-like items; generic theme articles are damped
    rt: dict = {}
    rt_detail: dict = {}
    if not commentary and not noise and etype in RT_TYPES and materiality >= 0.4:
        rt_detail = index.readthrough_detail(themes, direct, broad_factor=1.0 if direct else 0.5)
        rt = {t: (d["weight"], d["sign"]) for t, d in rt_detail.items()}
    relevant = bool(direct) or bool(rt) or item.source_type in ("sec", "price") or (etype in MACRO_TYPES and materiality >= 0.35)
    if etype in MACRO_TYPES and not direct:            # macro/geopolitics: only official sources, tier-1 prints, or semis-linked themes
        relevant = item.source_type in ("macro", "policy") or (sf >= 0.75 and materiality >= 0.4 and etype != "geopolitical") or bool(set(themes) & SEMI_MACRO_THEMES)
        if not relevant:
            rt = {}
    if noise or (commentary and not any(index.companies[t].is_holding for t in direct)):
        relevant = False
    # ---- confidence ---------------------------------------------------------------------------------
    confidence = 0.45
    if direct and max(direct.values()) >= 0.85:
        confidence += 0.2
    if strong:
        confidence += 0.15
    if item.source_type in ("sec", "price"):
        confidence = 0.9
    if commentary:
        confidence = 0.3
    confidence = min(0.95, confidence)
    # ---- pillars / horizon / action -------------------------------------------------------------------
    pillars = list(TYPE_PILLARS.get(etype, []))
    horizon = TYPE_HORIZON.get(etype, "days")
    holding_hit = [t for t in direct if index.companies[t].is_holding]
    action = "none"
    if holding_hit:
        if etype in ("earnings", "guidance", "preannouncement") and materiality >= 0.6:
            action = "refresh model inputs"
        elif materiality >= 0.6 and abs(direction) >= 1:
            action = "analyst review"
        elif materiality >= 0.35:
            action = "monitor"
    elif direct and materiality >= 0.6 and abs(direction) >= 1:
        action = "monitor"
    return {"event_type": etype, "direction": int(direction), "materiality": round(materiality, 3), "confidence": round(confidence, 2),
            "tickers": direct, "tickers_rt": rt, "readthrough": rt_detail, "themes": themes, "pillars": pillars, "horizon": horizon, "action": action,
            "relevant": relevant, "sentiment": round(sent, 2), "source_factor": round(sf, 2), "commentary": commentary}


def impacts(cls: dict, index: EntityIndex, rt_conf: float = 0.6) -> list[tuple[str, str, float]]:
    """Signed impact per ticker in [-1, 1]: direction/2 x materiality x confidence (x read-through weight)."""
    out: list[tuple[str, str, float]] = []
    d, m, c = cls.get("direction", 0), cls.get("materiality", 0.0), cls.get("confidence", 0.5)
    direct = cls.get("tickers") or {}
    for t, conf in (direct.items() if isinstance(direct, dict) else ((t, 0.8) for t in direct)):
        if t in index.companies:
            out.append((t, "direct", round((d / 2.0) * m * c * (0.7 + 0.3 * conf), 4)))
    rt = cls.get("tickers_rt") or {}
    for t, spec in (rt.items() if isinstance(rt, dict) else ((t, (0.4, 0)) for t in rt)):
        if t in index.companies and t not in direct:
            w, sign = (spec if isinstance(spec, (list, tuple)) else (spec, 0))
            d_eff = d if d != 0 else (sign or 0)
            out.append((t, "readthrough", round((d_eff / 2.0) * m * rt_conf * float(w), 4)))
    return out
