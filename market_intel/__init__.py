"""Market Intelligence AI: real-time event monitoring for the AI/semiconductor stock-selection process.

    python -m market_intel serve      # poller + local dashboard/API (http://127.0.0.1:8787)
    python -m market_intel poll       # one polling cycle
    python -m market_intel ask "..."  # question answering over the event store
    python -m market_intel brief      # pre-market brief
"""
__version__ = "0.1.0"
