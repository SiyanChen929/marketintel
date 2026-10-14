# Research overview

## Signal validity

The 2 September 2026 factor study used 105 US filers, 176 month-ends from January 2012 to August 2026, and 13,228 ticker-month observations. SEC facts were admitted after filing dates; consensus fields were excluded from the historical study because they could not be reconstructed. The current universe remains subject to survivorship and membership limitations.

Thirty-six candidate definitions were evaluated with fixed signs and rationales. Checks included rank IC over multiple horizons, HAC uncertainty, split-period stability, exclusion of large historical winners, and size neutralization. Feature selection on the first half followed by evaluation on the second half distinguished full-sample exploration from nested evidence. Current-vintage inputs and reused development observations retain their limitations.

## Portfolio decisions

Construction comparisons use shared holdings inputs, trailing covariance estimates, explicit constraints, and cost assumptions. The current score uses fields that cannot all be reconstructed historically; construction walk-forward comparisons use the separately documented historical selection rule. Their curves should not be read as a historical test of today's composite score.

Black–Litterman experiments also inspect whether the chosen optimizer actually reads expected returns. An expected-return view cannot affect an optimizer that uses only covariance. This is a system-level interpretation check, separate from the known mathematics of the posterior.

## Events and governance

Rules form the deterministic event layer. Optional LLM classification adds structured interpretation and lineage; budget enforcement and review states control its use. Event impact is kept separate from portfolio alpha, with `w_event=0` by default.

## Decision

This is a research prototype with NOT VALIDATED investment status. Software checks and synthetic demos test logic. Historical factor and portfolio diagnostics guide further investigation. Deployment would need a complete historical data contract, executable market evidence, and prospective validation.
