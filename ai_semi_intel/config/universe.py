"""End-to-end AI & semiconductor value-chain universe.

Segments follow the physical/economic flow of the AI stack:
design tools -> equipment -> fabrication/packaging -> compute silicon ->
memory -> interconnect -> analog/power silicon -> systems/infrastructure ->
cloud capacity -> software/applications -> power for data centres.

Each entry: ticker -> (segment, sub-segment, one-line business description).
US-listed ADRs of foreign issuers are flagged so the analyst can exclude them.
"""
from __future__ import annotations

import pandas as pd

SEGMENT_ORDER = [
    "EDA & Semiconductor IP",
    "Semiconductor Equipment",
    "Foundry, IDM & Packaging",
    "AI Compute Silicon",
    "Memory & Storage",
    "Interconnect, Networking & Optics",
    "Analog, Power & Mixed-Signal",
    "Servers & Data Center Infrastructure",
    "Cloud, Hyperscale & Neocloud",
    "AI Software & Applications",
    "Power & Energy for AI",
]

UNIVERSE: dict[str, tuple[str, str, str]] = {
    # ---- EDA & IP -----------------------------------------------------------
    "SNPS": ("EDA & Semiconductor IP", "EDA", "Chip design software, IP and (post-Ansys) simulation"),
    "CDNS": ("EDA & Semiconductor IP", "EDA", "EDA software, verification hardware, system analysis"),
    "ARM":  ("EDA & Semiconductor IP", "CPU IP", "CPU architecture licensing and royalties (ADR)"),
    # ---- Equipment ------------------------------------------------------------
    "ASML": ("Semiconductor Equipment", "Lithography", "Monopoly in EUV lithography (ADR)"),
    "AMAT": ("Semiconductor Equipment", "Deposition/Etch", "Broadest WFE portfolio; deposition, etch, CMP, packaging"),
    "LRCX": ("Semiconductor Equipment", "Etch/Deposition", "Etch & deposition leader; HBM/NAND exposure"),
    "KLAC": ("Semiconductor Equipment", "Process Control", "Inspection & metrology leader; highest WFE margins"),
    "TER":  ("Semiconductor Equipment", "Test", "Semiconductor test; AI accelerator/HBM test share"),
    "ONTO": ("Semiconductor Equipment", "Metrology", "Advanced packaging metrology & inspection"),
    "ENTG": ("Semiconductor Equipment", "Materials", "Filtration, specialty chemicals, wafer handling"),
    "MKSI": ("Semiconductor Equipment", "Subsystems", "RF power, vacuum, lasers, PCB chemistry"),
    "ACLS": ("Semiconductor Equipment", "Ion Implant", "Ion implantation; SiC power exposure"),
    "AEIS": ("Semiconductor Equipment", "Power Subsystems", "Precision power for etch/deposition and data centre"),
    "FORM": ("Semiconductor Equipment", "Probe Cards", "Probe cards; HBM/DRAM test exposure"),
    "CAMT": ("Semiconductor Equipment", "Inspection", "Advanced packaging inspection (ADR)"),
    "NVMI": ("Semiconductor Equipment", "Metrology", "Optical metrology (ADR)"),
    "KLIC": ("Semiconductor Equipment", "Packaging", "Wire/TCB bonders for advanced packaging"),
    "UCTT": ("Semiconductor Equipment", "Subsystems", "Gas delivery subsystems for WFE OEMs"),
    "ICHR": ("Semiconductor Equipment", "Subsystems", "Fluid delivery subsystems for WFE OEMs"),
    "COHU": ("Semiconductor Equipment", "Test", "Test handlers and contactors"),
    "VECO": ("Semiconductor Equipment", "Deposition", "MOCVD, ion beam, laser annealing"),
    "ACMR": ("Semiconductor Equipment", "Cleaning", "Wafer cleaning; China-centric growth"),
    "PLAB": ("Semiconductor Equipment", "Photomasks", "Photomask manufacturing"),
    # ---- Foundry / IDM / Packaging ---------------------------------------------
    "TSM":  ("Foundry, IDM & Packaging", "Foundry", "Leading-edge foundry; CoWoS packaging (ADR)"),
    "INTC": ("Foundry, IDM & Packaging", "IDM/Foundry", "x86 CPUs, foundry turnaround (18A)"),
    "GFS":  ("Foundry, IDM & Packaging", "Foundry", "Specialty/mature-node foundry"),
    "UMC":  ("Foundry, IDM & Packaging", "Foundry", "Mature-node foundry (ADR)"),
    "AMKR": ("Foundry, IDM & Packaging", "OSAT", "Outsourced assembly & test; advanced packaging"),
    # ---- Compute -----------------------------------------------------------------
    "NVDA": ("AI Compute Silicon", "GPU/Accelerator", "Dominant AI accelerator, networking and CUDA platform"),
    "AMD":  ("AI Compute Silicon", "GPU/CPU", "MI-series accelerators, EPYC CPUs"),
    "AVGO": ("AI Compute Silicon", "Custom ASIC/Networking", "Custom AI ASICs (XPU), Ethernet switching, VMware"),
    "MRVL": ("AI Compute Silicon", "Custom ASIC/Optics", "Custom silicon, electro-optics, DCI"),
    "QCOM": ("AI Compute Silicon", "Edge AI SoC", "Mobile/auto/PC SoCs, edge AI"),
    # ---- Memory & Storage --------------------------------------------------------
    "MU":   ("Memory & Storage", "DRAM/HBM/NAND", "HBM3E/HBM4 supplier; DRAM & NAND"),
    "WDC":  ("Memory & Storage", "HDD", "Nearline HDD for hyperscale storage"),
    "STX":  ("Memory & Storage", "HDD", "Nearline HDD, HAMR technology"),
    "SNDK": ("Memory & Storage", "NAND", "NAND flash (spun from WDC)"),
    "NTAP": ("Memory & Storage", "Storage Systems", "Enterprise storage, AI data infrastructure"),
    # ---- Interconnect / Networking / Optics --------------------------------------
    "ANET": ("Interconnect, Networking & Optics", "Ethernet Switching", "AI back-end/front-end Ethernet switching"),
    "CSCO": ("Interconnect, Networking & Optics", "Networking", "Networking, Silicon One, AI infra orders"),
    "CRDO": ("Interconnect, Networking & Optics", "AEC/SerDes", "Active electrical cables, SerDes, optical DSPs"),
    "ALAB": ("Interconnect, Networking & Optics", "PCIe/CXL Connectivity", "PCIe retimers, CXL, Scorpio switches"),
    "COHR": ("Interconnect, Networking & Optics", "Optical Transceivers", "Datacom transceivers, lasers, SiC"),
    "LITE": ("Interconnect, Networking & Optics", "Optical Components", "Lasers, transceivers, optical circuit switches"),
    "AAOI": ("Interconnect, Networking & Optics", "Optical Transceivers", "800G transceivers, hyperscaler wins"),
    "CIEN": ("Interconnect, Networking & Optics", "Optical Systems", "DCI optical systems, pluggables"),
    "FN":   ("Interconnect, Networking & Optics", "Optical Manufacturing", "Precision optical/electronic manufacturing"),
    "MTSI": ("Interconnect, Networking & Optics", "RF/Optical Semis", "High-performance analog for data centre optics"),
    "SITM": ("Interconnect, Networking & Optics", "Timing", "MEMS timing for AI data centres"),
    "RMBS": ("Interconnect, Networking & Optics", "Memory Interface", "Memory interface chips, IP"),
    "MXL":  ("Interconnect, Networking & Optics", "Mixed-Signal", "Broadband/infrastructure mixed-signal"),
    # ---- Analog / Power / Mixed-signal -------------------------------------------
    "TXN":  ("Analog, Power & Mixed-Signal", "Analog", "Broadest analog catalogue; 300mm capacity"),
    "ADI":  ("Analog, Power & Mixed-Signal", "Analog", "High-performance analog, mixed-signal"),
    "MCHP": ("Analog, Power & Mixed-Signal", "MCU/Analog", "Microcontrollers, analog, FPGA"),
    "NXPI": ("Analog, Power & Mixed-Signal", "Auto/Industrial", "Auto & industrial processors (ADR)"),
    "ON":   ("Analog, Power & Mixed-Signal", "Power/SiC", "Power semis, SiC, sensing"),
    "MPWR": ("Analog, Power & Mixed-Signal", "Power Management", "Power management for AI accelerators"),
    "SWKS": ("Analog, Power & Mixed-Signal", "RF", "RF front-end"),
    "QRVO": ("Analog, Power & Mixed-Signal", "RF", "RF front-end, defence"),
    "LSCC": ("Analog, Power & Mixed-Signal", "FPGA", "Low-power FPGAs for servers"),
    "SLAB": ("Analog, Power & Mixed-Signal", "IoT Wireless", "IoT wireless SoCs"),
    "POWI": ("Analog, Power & Mixed-Signal", "Power ICs", "High-voltage power conversion ICs"),
    "DIOD": ("Analog, Power & Mixed-Signal", "Discrete", "Discrete & analog semis"),
    "VSH":  ("Analog, Power & Mixed-Signal", "Discrete/Passives", "Discretes and passives"),
    "SMTC": ("Analog, Power & Mixed-Signal", "Mixed-Signal", "LoRa, data-centre signal integrity"),
    "NVTS": ("Analog, Power & Mixed-Signal", "GaN/SiC", "GaN & SiC power for AI data centre"),
    "ALGM": ("Analog, Power & Mixed-Signal", "Sensors", "Magnetic sensors & power ICs"),
    "AOSL": ("Analog, Power & Mixed-Signal", "Power Discretes", "Power discretes & ICs"),
    "VICR": ("Analog, Power & Mixed-Signal", "Power Modules", "Power modules for AI/HPC"),
    # ---- Servers & DC infrastructure --------------------------------------------
    "SMCI": ("Servers & Data Center Infrastructure", "AI Servers", "AI server & rack-scale systems"),
    "DELL": ("Servers & Data Center Infrastructure", "AI Servers", "AI-optimised servers, storage, PCs"),
    "HPE":  ("Servers & Data Center Infrastructure", "AI Servers/Networking", "AI systems, Juniper networking"),
    "VRT":  ("Servers & Data Center Infrastructure", "Power & Cooling", "Data-centre power, thermal, liquid cooling"),
    "ETN":  ("Servers & Data Center Infrastructure", "Electrical", "Electrical power distribution for data centres"),
    "NVT":  ("Servers & Data Center Infrastructure", "Liquid Cooling", "Enclosures, liquid cooling"),
    "PWR":  ("Servers & Data Center Infrastructure", "Grid Contractor", "Electric grid & data-centre construction"),
    "MOD":  ("Servers & Data Center Infrastructure", "Thermal", "Data-centre cooling systems"),
    "CLS":  ("Servers & Data Center Infrastructure", "EMS/ODM", "Hyperscale networking & server manufacturing"),
    "JBL":  ("Servers & Data Center Infrastructure", "EMS", "Electronics manufacturing; AI data-centre exposure"),
    "FLEX": ("Servers & Data Center Infrastructure", "EMS", "Manufacturing; data-centre power & rack"),
    "SANM": ("Servers & Data Center Infrastructure", "EMS", "Electronics manufacturing services"),
    # ---- Cloud / Hyperscale / Neocloud ------------------------------------------
    "MSFT": ("Cloud, Hyperscale & Neocloud", "Hyperscaler", "Azure, Copilot, OpenAI partnership"),
    "GOOGL":("Cloud, Hyperscale & Neocloud", "Hyperscaler", "Google Cloud, TPU, Gemini"),
    "AMZN": ("Cloud, Hyperscale & Neocloud", "Hyperscaler", "AWS, Trainium, Bedrock"),
    "META": ("Cloud, Hyperscale & Neocloud", "AI Platform", "Llama, AI-driven ads, largest capex"),
    "ORCL": ("Cloud, Hyperscale & Neocloud", "Cloud Infrastructure", "OCI, Stargate, RPO backlog"),
    "IBM":  ("Cloud, Hyperscale & Neocloud", "Hybrid Cloud/AI", "watsonx, Red Hat, consulting"),
    "CRWV": ("Cloud, Hyperscale & Neocloud", "Neocloud", "GPU cloud; Microsoft/OpenAI contracts"),
    "NBIS": ("Cloud, Hyperscale & Neocloud", "Neocloud", "AI cloud infrastructure (ADR)"),
    "APLD": ("Cloud, Hyperscale & Neocloud", "Neocloud/DC", "HPC data-centre developer"),
    "IREN": ("Cloud, Hyperscale & Neocloud", "Neocloud/DC", "AI cloud & data-centre capacity (ADR)"),
    "DLR":  ("Cloud, Hyperscale & Neocloud", "Data Center REIT", "Data-centre REIT"),
    "EQIX": ("Cloud, Hyperscale & Neocloud", "Data Center REIT", "Interconnection & colocation REIT"),
    # ---- AI Software ----------------------------------------------------------------
    "PLTR": ("AI Software & Applications", "AI Platform", "AIP, Foundry, Gotham"),
    "SNOW": ("AI Software & Applications", "Data Cloud", "Data cloud, Cortex AI"),
    "DDOG": ("AI Software & Applications", "Observability", "Observability, LLM monitoring"),
    "MDB":  ("AI Software & Applications", "Database", "Document database, vector search"),
    "NOW":  ("AI Software & Applications", "Workflow/Agents", "Enterprise workflow, Now Assist"),
    "CRM":  ("AI Software & Applications", "CRM/Agents", "Agentforce, Data Cloud"),
    "ADBE": ("AI Software & Applications", "Creative AI", "Firefly, creative & document cloud"),
    "AI":   ("AI Software & Applications", "Enterprise AI", "Enterprise AI applications"),
    "SOUN": ("AI Software & Applications", "Voice AI", "Conversational voice AI"),
    "PATH": ("AI Software & Applications", "Automation", "Agentic automation"),
    "ESTC": ("AI Software & Applications", "Search/Vector DB", "Search, vector database, observability"),
    "GTLB": ("AI Software & Applications", "DevSecOps", "AI-native DevSecOps"),
    "APP":  ("AI Software & Applications", "AI Advertising", "AI-driven ad engine (Axon)"),
    "TEM":  ("AI Software & Applications", "Healthcare AI", "AI diagnostics & data"),
    "RBRK": ("AI Software & Applications", "Data Security", "Cyber resilience, data security for AI"),
    # ---- Power & Energy ----------------------------------------------------------------
    "VST":  ("Power & Energy for AI", "IPP", "Nuclear/gas generation; data-centre PPAs"),
    "CEG":  ("Power & Energy for AI", "Nuclear IPP", "Largest US nuclear fleet; hyperscaler PPAs"),
    "NRG":  ("Power & Energy for AI", "IPP", "Generation & retail; data-centre deals"),
    "TLN":  ("Power & Energy for AI", "Nuclear IPP", "Susquehanna nuclear; AWS campus"),
    "GEV":  ("Power & Energy for AI", "Turbines/Grid", "Gas turbines, grid equipment, nuclear"),
    "BE":   ("Power & Energy for AI", "Fuel Cells", "On-site fuel-cell power for data centres"),
    "OKLO": ("Power & Energy for AI", "SMR", "Advanced fission developer"),
    "SMR":  ("Power & Energy for AI", "SMR", "Small modular reactor developer"),
}

ADR_TICKERS = {"ARM", "ASML", "CAMT", "NVMI", "TSM", "UMC", "NXPI", "NBIS", "IREN"}

BENCHMARKS = {"SPY": "S&P 500", "QQQ": "Nasdaq-100", "SOXX": "iShares Semiconductor ETF"}


def universe_frame() -> pd.DataFrame:
    rows = []
    for t, (seg, sub, desc) in UNIVERSE.items():
        rows.append({"ticker": t, "segment": seg, "sub_segment": sub, "description": desc,
                     "is_adr": t in ADR_TICKERS, "segment_rank": SEGMENT_ORDER.index(seg)})
    return pd.DataFrame(rows).set_index("ticker")


def tickers() -> list[str]:
    return list(UNIVERSE.keys())
