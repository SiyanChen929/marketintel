# Analyst-authored scenarios for the name dossiers

`GET /api/research/dossier/<TICKER>` reads `data/dossiers/<TICKER>.json` when it exists. Scenarios are analyst
judgement, never model output: the model provides the fair-value bridge, consensus and the evidence-logged
strategic assessment; the analyst writes base / bull / bear with probabilities, a 12-month price target each,
the thesis and the kill criteria. Copy `TEMPLATE.json` to `<TICKER>.json`.

Fields: `horizon` (e.g. "12m"), `benchmark` (e.g. "SOXX"), `base|bull|bear` = `{pt, prob, thesis, kill_criteria?}`
(probabilities should sum to 1), `watch_kpis` (list), `author`, `asof` (YYYY-MM-DD). Everything else in the
dossier is assembled from the canonical run.
