// Read-only presentation rules. These helpers never calculate portfolio risk or fill missing metrics.
const RiskReview = (() => {
  const tr=(s,v={})=>typeof globalThis.t==='function'?globalThis.t(s,v):s.replace(/\{(\w+)\}/g,(_,k)=>v[k]??'{'+k+'}');
  function selected(data, view, name) {
    const block = data?.constructions?.[name];
    const layer = block?.layers?.[view.layer];
    const window = layer?.windows?.[view.win];
    const row = (data?.['comparison_' + view.layer] || []).find(r => r.construction === name);
    const reason = !block ? tr('Choose a construction.') : !layer?.available
      ? (layer?.reason || tr('This construction has no book in this layer.'))
      : !window?.available ? (window?.reason || tr('This exact window is unavailable.')) : '';
    return {block, layer, window, row, reason, available: !reason};
  }
  function mismatch(book, matrix) {
    if (!book || !matrix) return tr('Both risk snapshots are required.');
    const a = book.run?.run_id, b = matrix.run?.run_id;
    if (!a || !b || a !== b) return tr('Run IDs differ or are missing. Refresh the complete review.');
    const other = Object.values(matrix.constructions || {}).flatMap(c => Object.values(c.layers || {}))
      .find(l => l.provenance?.data_snapshot_hash)?.provenance;
    const own = book.var?.provenance;
    // Unavailable calculators may have no provenance; their metrics remain unavailable.
    if (own && other) {
      for (const key of ['data_snapshot_hash', 'calculator_code_hash', 'acquired_at', 'asof_last_confirmed_close']) {
        if (!own[key] || !other[key] || own[key] !== other[key]) return tr('Risk snapshot mismatch: {key}. Refresh the complete review.',{key});
      }
    }
    return '';
  }
  function checks(compliance) {
    const rows = compliance?.checks || [];
    return {total: rows.length, passed: rows.filter(c => c.ok === true).length,
      failed: rows.filter(c => c.ok === false).length, unknown: rows.filter(c => c.ok !== true && c.ok !== false).length};
  }
  function findings(book, matrix) {
    const out = [], add = (level, title, detail, target) => out.push({level, title:tr(title), detail, target});
    const health = book?.run?.data_health || {}, gaps = health.price_gaps || {};
    const names = Object.keys(gaps).sort();
    if (names.length) add('block', 'Repair price gaps', tr('{count} target names: {names}. Rebuild the snapshot before investment use.',{count:names.length,names:names.join(', ')}), 'evidence-review');
    else if (book?.run?.actionable !== true) add('block', 'Run is not actionable', (book?.run?.not_actionable_reasons || []).join('; ') || tr('Actionability has not been established.'), 'evidence-review');
    const version = book?.var?.schema_version;
    if (version !== 'risk.v4') add('block', 'Legacy risk service', tr('{version} is being served. Figures are diagnostic; the current risk.v4 controls are not active on this service.',{version:version||tr('Unknown version')}), 'evidence-review');
    const candidates = Object.values(matrix?.constructions || {}).map(c => c.layers?.model?.windows?.['3y']).filter(Boolean);
    const unavailable = candidates.filter(w => !w.available);
    if (unavailable.length) add('gap', '3Y coverage unavailable', tr('{unavailable} of {total} labels lack this window. ',{unavailable:unavailable.length,total:candidates.length}) + (unavailable[0].reason || tr('Insufficient price history.')), 'pc-panel');
    if (matrix?.benchmark?.is_formal_mandate !== true) add('gap', 'Define the mandate benchmark', tr('Current-universe and SOXX tracking error are research comparisons. No mandate active-risk limit can be assessed.'), 'var-panel');
    if (!book?.liquidity?.computable?.days_to_liquidate || !book?.liquidity?.computable?.cost_impact)
      add('gap', 'Execution capacity unassessed', tr('A verified NAV, position ledger and execution assumptions are required for liquidation time and market impact.'), 'liq-panel');
    const c = checks(book?.compliance);
    if (c.failed || c.unknown || !c.total) add('block', 'Resolve target constraint checks', tr('{failed} failed; {unknown} unknown. Unknown checks do not count as passes.',{failed:c.failed,unknown:c.unknown}), 'comp-panel');
    return out;
  }
  function order(rows, view, mode) {
    const list = rows.slice();
    if (mode === 'name') return list.sort((a, b) => a.construction.localeCompare(b.construction));
    const suffix = {var: '_var99_10d_overlap', es: '_es975_10d_overlap', te: '_' + view.bench}[mode];
    if (!suffix) return list;
    const value = r => r.layer_available && r[view.win + '_available'] && Number.isFinite(r[view.win + suffix]) ? r[view.win + suffix] : null;
    return list.sort((a, b) => {
      const x = value(a), y = value(b);
      return x === null ? (y === null ? 0 : 1) : y === null ? -1 : y - x;
    });
  }
  return {selected, mismatch, checks, findings, order};
})();
