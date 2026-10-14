/* Authored portfolio/risk UI vocabulary: [English, 简体中文, 日本語].
 * Translation is display-only. Source evidence, identifiers and formulas stay original.
 * Missing entries fall back to their original English in the shared runtime.
 */
window.MI_I18N.register({
  " · {count} daily returns": [
    " · {count} daily returns",
    " · {count} 个日度收益",
    " · 日次リターン {count}件"
  ],
  " · {count} findings": [
    " · {count} findings",
    " · {count} 项发现",
    " · 指摘 {count}件"
  ],
  " · {schema} legacy diagnostic": [
    " · {schema} legacy diagnostic",
    " · {schema} 旧版诊断",
    " · {schema} 旧形式の診断"
  ],
  " · no NAV": [
    " · no NAV",
    " · 缺少净值",
    " · NAVなし"
  ],
  " · not in universe/prices: {tickers}": [
    " · not in universe/prices: {tickers}",
    " · 股票池/价格中缺失：{tickers}",
    " · 投資対象/価格に存在しません：{tickers}"
  ],
  " · short history: {tickers}": [
    " · short history: {tickers}",
    " · 历史数据不足：{tickers}",
    " · 履歴不足：{tickers}"
  ],
  " (portfolio β {beta} on {invested} invested)": [
    " (portfolio β {beta} on {invested} invested)",
    "（已投资 {invested}，组合 β {beta}）",
    "（投資比率 {invested}、ポートフォリオβ {beta}）"
  ],
  " (remainder is cash)": [
    " (remainder is cash)",
    "（剩余部分为现金）",
    "（残りは現金）"
  ],
  " That is <b>not statistically distinguishable from the prior</b> on a risk-adjusted basis - the return gain is compensation for extra risk, not evidence of skill.": [
    " That is <b>not statistically distinguishable from the prior</b> on a risk-adjusted basis - the return gain is compensation for extra risk, not evidence of skill.",
    "风险调整后，<b>无法在统计上与先验区分</b>；收益提升补偿了额外风险，并非能力的证据。",
    "リスク調整後は<b>事前分布との統計的な差を識別できません</b>。リターン増加は追加リスクへの対価で、運用能力の証拠ではありません。"
  ],
  " The view layer clears the significance bar.": [
    " The view layer clears the significance bar.",
    "观点层达到显著性门槛。",
    "ビューは有意性の基準を満たしています。"
  ],
  " Views are not validated out of sample. Keep them as a small tilt, not a driver, until the live forward IC turns positive.": [
    " Views are not validated out of sample. Keep them as a small tilt, not a driver, until the live forward IC turns positive.",
    "观点尚未经样本外验证。在实时前瞻 IC 转正之前，仅宜作为小幅倾斜，而非主要驱动。",
    "ビューはサンプル外で未検証です。リアルタイムのフォワードICがプラスになるまでは、小さなティルトとして扱ってください。"
  ],
  "- confidence is the only dial, which is why the τ sweep above switches to a τ-independent Ω. δ is calibrated so the cap-weighted basket's equilibrium excess return equals β·ERP; left at the config's 2.5 it implies 13-28% excess returns for these 40-50% vol names and the prior swamps every view. Covariance is Ledoit-Wolf shrunk; the optimiser uses the": [
    "- confidence is the only dial, which is why the τ sweep above switches to a τ-independent Ω. δ is calibrated so the cap-weighted basket's equilibrium excess return equals β·ERP; left at the config's 2.5 it implies 13-28% excess returns for these 40-50% vol names and the prior swamps every view. Covariance is Ledoit-Wolf shrunk; the optimiser uses the",
    "— 置信度是唯一调节参数，因此上方 τ 扫描改用与 τ 无关的 Ω。δ 经校准，使市值加权篮子的均衡超额收益等于 β·ERP；若保持配置值 2.5，则对波动率 40-50% 的标的隐含 13-28% 超额收益，先验将压过所有观点。协方差使用 Ledoit-Wolf 收缩；优化器使用",
    "。調整パラメータは信頼度のみのため、上のτ感応度分析ではτに依存しないΩを使用します。δは時価総額加重バスケットの均衡超過リターンがβ·ERPとなるよう校正します。設定値2.5のままでは、ボラティリティ40-50%の銘柄に13-28%の超過リターンを想定し、事前分布がすべてのビューを圧倒します。共分散はLedoit-Wolfで縮小推定し、最適化には"
  ],
  ": read the spread between arms, never the level.": [
    ": read the spread between arms, never the level.",
    "：应比较方案间差异，不应解读绝对表现。",
    "。絶対水準ではなく、手法間の差を確認してください。"
  ],
  ". Each source below is a model or feed inside this system, so every tilt can be audited back to the number that caused it.": [
    ". Each source below is a model or feed inside this system, so every tilt can be audited back to the number that caused it.",
    "。下列各来源均为系统内模型或数据源，因此每个倾斜均可追溯至其触发数值。",
    "。以下の出所はすべてシステム内のモデルまたはフィードで、各ティルトを原因となった数値まで追跡できます。"
  ],
  ". Ω = diag(PτΣPᵗ)/confidence (Idzorek scaling), under which": [
    ". Ω = diag(PτΣPᵗ)/confidence (Idzorek scaling), under which",
    ". Ω = diag(PτΣPᵗ)/confidence（Idzorek 缩放），在此设定下",
    "で求めます。Ω = diag(PτΣPᵗ)/confidence（Idzorekスケーリング）では、"
  ],
  "· Research only.": [
    "· Research only.",
    "· 仅供研究。",
    "· 調査目的のみ。"
  ],
  "(CAGR − rf) / vol: the pre-remediation figure, not a Sharpe ratio": [
    "(CAGR − rf) / vol: the pre-remediation figure, not a Sharpe ratio",
    "(CAGR − rf) / vol：修正前的旧指标，并非夏普比率",
    "(CAGR − rf) / vol：修正前の旧指標であり、シャープレシオではありません"
  ],
  "(off)": [
    "(off)",
    "（未启用）",
    "（無効）"
  ],
  "{best} ranks first on Sharpe ({sharpe}); the production default, {production}, ranks {rank} of {count} ({productionSharpe}).": [
    "{best} ranks first on Sharpe ({sharpe}); the production default, {production}, ranks {rank} of {count} ({productionSharpe}).",
    "{best} 的夏普比率排名第一（{sharpe}）；默认生产方法 {production} 在 {count} 种方法中排名 {rank}（{productionSharpe}）。",
    "{best}がシャープレシオ1位（{sharpe}）。標準運用手法 {production}は{count}手法中{rank}位（{productionSharpe}）。"
  ],
  "{cash} cash · {status}": [
    "{cash} cash · {status}",
    "现金 {cash} · {status}",
    "現金 {cash} · {status}"
  ],
  "{construction} · {invested} invested · {cash} cash": [
    "{construction} · {invested} invested · {cash} cash",
    "{construction} · 已投资 {invested} · 现金 {cash}",
    "{construction} · 投資 {invested} · 現金 {cash}"
  ],
  "{construction} · {layer} · {window} · cash {cash} · TE: {benchmark}{legacy}": [
    "{construction} · {layer} · {window} · cash {cash} · TE: {benchmark}{legacy}",
    "{construction} · {layer} · {window} · 现金 {cash} · 跟踪误差：{benchmark}{legacy}",
    "{construction} · {layer} · {window} · 現金 {cash} · TE：{benchmark}{legacy}"
  ],
  "{construction} construction": [
    "{construction} construction",
    "{construction} 构建方法",
    "{construction}配分手法"
  ],
  "{construction} target book · {count} names · {blocks} blocking findings · {gaps} evidence gaps. {note}": [
    "{construction} target book · {count} names · {blocks} blocking findings · {gaps} evidence gaps. {note}",
    "{construction} 目标组合 · {count} 只标的 · {blocks} 个阻断项 · {gaps} 个证据缺口。{note}",
    "{construction}目標ポートフォリオ · {count}銘柄 · 利用を阻む問題 {blocks}件 · 根拠の不足 {gaps}件。{note}"
  ],
  "{count} methods": [
    "{count} methods",
    "{count} 种方法",
    "{count}手法"
  ],
  "{count} names": [
    "{count} names",
    "{count} 只标的",
    "{count}銘柄"
  ],
  "{count} names · vol-target scale {scale}": [
    "{count} names · vol-target scale {scale}",
    "{count} 只标的 · 波动率目标缩放系数 {scale}",
    "{count}銘柄 · ボラティリティ目標調整係数 {scale}"
  ],
  "{count} names · weights + cash = {total}": [
    "{count} names · weights + cash = {total}",
    "{count} 只标的 · 权重 + 现金 = {total}",
    "{count}銘柄 · ウェイト + 現金 = {total}"
  ],
  "{count} names have recent price gaps.": [
    "{count} names have recent price gaps.",
    "{count} 只标的近期存在价格缺口。",
    "{count}銘柄の直近価格に欠損があります。"
  ],
  "{count} rebalances": [
    "{count} rebalances",
    "{count} 次调仓",
    "{count}回リバランス"
  ],
  "{count} target names · cash {cash}": [
    "{count} target names · cash {cash}",
    "{count} 只目标标的 · 现金 {cash}",
    "目標{count}銘柄 · 現金 {cash}"
  ],
  "{count} views": [
    "{count} views",
    "{count} 个观点",
    "{count}ビュー"
  ],
  "{count} views · impact = total absolute posterior shift across all names": [
    "{count} views · impact = total absolute posterior shift across all names",
    "{count} 个观点 · 影响 = 所有标的后验变动绝对值之和",
    "{count}ビュー · 影響 = 全銘柄の事後リターン変化の絶対値合計"
  ],
  "{failed} failed · {unknown} unknown · no trade approval": [
    "{failed} failed · {unknown} unknown · no trade approval",
    "{failed} 未通过 · {unknown} 未知 · 不含交易审批",
    "不合格 {failed} · 不明 {unknown} · 売買承認なし"
  ],
  "{invested} invested": [
    "{invested} invested",
    "已投资 {invested}",
    "投資比率 {invested}"
  ],
  "{pass} / {total} pass": [
    "{pass} / {total} pass",
    "{pass} / {total} 通过",
    "{pass} / {total} 合格"
  ],
  "{period}: {start} → {end} · rebased to 100 at the window start · net of {cost} bps · click a legend entry to isolate": [
    "{period}: {start} → {end} · rebased to 100 at the window start · net of {cost} bps · click a legend entry to isolate",
    "{period}：{start} → {end} · 窗口起点归一至 100 · 扣除 {cost} 基点成本 · 点击图例切换序列",
    "{period}：{start} → {end} · 期間開始時点を100に換算 · {cost}bpsのコスト控除後 · 凡例で表示系列を切替"
  ],
  "{shown} of {total} shown": [
    "{shown} of {total} shown",
    "已显示 {shown} / {total}",
    "{total}系列中{shown}系列を表示"
  ],
  "{start} → {end} · {count} rebalances · BL turnover {turnover}/month": [
    "{start} → {end} · {count} rebalances · BL turnover {turnover}/month",
    "{start} → {end} · {count} 次调仓 · BL 月度换手率 {turnover}",
    "{start} → {end} · {count}回リバランス · BL月次回転率 {turnover}"
  ],
  "{ticker} contributes the most risk, {share} of portfolio variance ({method}).": [
    "{ticker} contributes the most risk, {share} of portfolio variance ({method}).",
    "{ticker} 的风险贡献最高，占组合方差的 {share}（{method}）。",
    "{ticker}のリスク寄与が最大で、ポートフォリオ分散の {share}（{method}）です。"
  ],
  "{ticker} has the largest {method} weight, {weight}; {count} names sum to {total}{cash}.": [
    "{ticker} has the largest {method} weight, {weight}; {count} names sum to {total}{cash}.",
    "{ticker} 的{method}权重最高，为 {weight}；{count} 只标的权重合计 {total}{cash}。",
    "{method}のウェイトが最大なのは {ticker}（{weight}）。{count}銘柄の合計 {total}{cash}。"
  ],
  "{ticker} removed": [
    "{ticker} removed",
    "已移除 {ticker}",
    "{ticker} を削除しました"
  ],
  "<span class=\"chip\">{asof}</span><span class=\"chip\">{names}</span><span class=\"chip\">{views}</span><span class=\"chip\">δ {delta}</span>": [
    "<span class=\"chip\">{asof}</span><span class=\"chip\">{names}</span><span class=\"chip\">{views}</span><span class=\"chip\">δ {delta}</span>",
    "<span class=\"chip\">{asof}</span><span class=\"chip\">{names}</span><span class=\"chip\">{views}</span><span class=\"chip\">δ {delta}</span>",
    "<span class=\"chip\">{asof}</span><span class=\"chip\">{names}</span><span class=\"chip\">{views}</span><span class=\"chip\">δ {delta}</span>"
  ],
  "<span class=\"chip\">{methods}</span><span class=\"chip\">{rebalances}</span>": [
    "<span class=\"chip\">{methods}</span><span class=\"chip\">{rebalances}</span>",
    "<span class=\"chip\">{methods}</span><span class=\"chip\">{rebalances}</span>",
    "<span class=\"chip\">{methods}</span><span class=\"chip\">{rebalances}</span>"
  ],
  "= fully-invested target weights;": [
    "= fully-invested target weights;",
    "= 全投资目标权重；",
    "= 全額投資の目標ウェイト。"
  ],
  "= the same book": [
    "= the same book",
    "= 同一组合",
    "= 同じポートフォリオを"
  ],
  "= the weights the run really published, read from run.json and available only for the run's own executable column. A window a book cannot support is reported unavailable with the limiting holding named — never shortened, never zero.": [
    "= the weights the run really published, read from run.json and available only for the run's own executable column. A window a book cannot support is reported unavailable with the limiting holding named — never shortened, never zero.",
    "= 从 run.json 读取本次运行实际发布的权重，仅对应运行本身的可执行列。组合无法覆盖的窗口标记为不可用，并列出限制标的；不缩短窗口，不以零填充。",
    "= run.jsonに記録された実際の公開ウェイトで、その実行結果の実行用列にのみ存在します。対象銘柄が期間をカバーできない場合は銘柄名を示して利用不可とし、期間を短縮したりゼロ補完したりしません。"
  ],
  "⟳ Re-run (full, ~2 min)": [
    "⟳ Re-run (full, ~2 min)",
    "⟳ 重新运行（完整，约 2 分钟）",
    "⟳ 再実行（全体、約2分）"
  ],
  "⟳ Re-run (no walk-forward)": [
    "⟳ Re-run (no walk-forward)",
    "⟳ 重新运行（不含滚动前测）",
    "⟳ 再実行（ウォークフォワードなし）"
  ],
  "⟳ Re-run (prior only)": [
    "⟳ Re-run (prior only)",
    "⟳ 重新运行（仅先验）",
    "⟳ 再実行（事前分布のみ）"
  ],
  "⟳ Re-run (views on)": [
    "⟳ Re-run (views on)",
    "⟳ 重新运行（启用观点）",
    "⟳ 再実行（ビューあり）"
  ],
  "1/N. The benchmark every other method has to beat.": [
    "1/N. The benchmark every other method has to beat.",
    "1/N。其他方法均需超越的比较基准。",
    "1/N。各手法が上回るべき比較基準。"
  ],
  "10-day route": [
    "10-day route",
    "10 日计算路径",
    "10日リスクの算出方法"
  ],
  "10D ES · highest first": [
    "10D ES · highest first",
    "10 日 ES · 降序",
    "10日ES · 高い順"
  ],
  "10d phase range": [
    "10d phase range",
    "10 日相位范围",
    "10日位相の範囲"
  ],
  "10D VaR · highest first": [
    "10D VaR · highest first",
    "10 日 VaR · 降序",
    "10日VaR · 高い順"
  ],
  "20-day, price cache": [
    "20-day, price cache",
    "20 日，价格缓存",
    "20日、価格キャッシュ"
  ],
  "a 13-year average hides which years produced the edge · production row highlighted": [
    "a 13-year average hides which years produced the edge · production row highlighted",
    "13 年平均值掩盖优势来自哪些年份 · 已突出显示生产方案行",
    "13年平均では優位性を生んだ年が見えません · 運用手法の行を強調表示"
  ],
  "a Black-Litterman posterior from momentum views": [
    "a Black-Litterman posterior from momentum views",
    "基于动量观点的 Black-Litterman 后验",
    "モメンタムビューによるBlack-Litterman事後分布"
  ],
  "Acquired at": [
    "Acquired at",
    "采集时间",
    "取得時刻"
  ],
  "Active share": [
    "Active share",
    "主动份额",
    "アクティブシェア"
  ],
  "Active weights vs benchmark": [
    "Active weights vs benchmark",
    "相对基准的主动权重",
    "ベンチマークに対するアクティブウェイト"
  ],
  "actual landed run book": [
    "actual landed run book",
    "实际发布组合",
    "実際に公開された実行結果"
  ],
  "Add": [
    "Add",
    "添加",
    "追加"
  ],
  "ADV {adv} · days-to-liquidate {days} · cost impact {cost}{nav}": [
    "ADV {adv} · days-to-liquidate {days} · cost impact {cost}{nav}",
    "平均成交额 {adv} · 清仓天数 {days} · 成本冲击 {cost}{nav}",
    "ADV {adv} · 清算日数 {days} · コスト影響 {cost}{nav}"
  ],
  "ADV 20d ($)": [
    "ADV 20d ($)",
    "20 日平均成交额（美元）",
    "20日平均売買代金（ドル）"
  ],
  "ADV 20d (sh)": [
    "ADV 20d (sh)",
    "20 日平均成交量（股）",
    "20日平均売買高（株）"
  ],
  "against its own ex-ante volatility (a hypothetical, not a book any run executed);": [
    "against its own ex-ante volatility (a hypothetical, not a book any run executed);",
    "按自身事前波动率调整（假设情景，并非任何运行实际执行的组合）；",
    "その事前ボラティリティに合わせて調整します（仮想シナリオであり、実行済み配分ではありません）。"
  ],
  "all arms scored on the same BL posterior; no return claim, risk and concentration only": [
    "all arms scored on the same BL posterior; no return claim, risk and concentration only",
    "各方案使用相同 BL 后验评估；仅考察风险与集中度，不主张收益表现",
    "全手法を同じBL事後分布で評価。リターンの実証ではなく、リスクと集中度の比較"
  ],
  "already running": [
    "already running",
    "已在运行",
    "実行中です"
  ],
  "Alternative": [
    "Alternative",
    "替代方案",
    "代替手法"
  ],
  "and reported in": [
    "and reported in",
    "并按以下",
    "以下の"
  ],
  "Arm": [
    "Arm",
    "方案",
    "手法"
  ],
  "as of {date}": [
    "as of {date}",
    "截至 {date}",
    "基準日 {date}"
  ],
  "average rank across calendar years, lower is better": [
    "average rank across calendar years, lower is better",
    "各自然年度平均排名，越低越好",
    "暦年ごとの平均順位。低いほど良好"
  ],
  "Avg weight": [
    "Avg weight",
    "平均权重",
    "平均ウェイト"
  ],
  "Basis": [
    "Basis",
    "依据",
    "算出根拠"
  ],
  "benchmark": [
    "benchmark",
    "基准",
    "ベンチマーク"
  ],
  "Benchmark": [
    "Benchmark",
    "基准",
    "ベンチマーク"
  ],
  "benchmark =": [
    "benchmark =",
    "基准 =",
    "ベンチマーク ="
  ],
  "Benchmark source": [
    "Benchmark source",
    "基准来源",
    "ベンチマークの出所"
  ],
  "Best diagnostic": [
    "Best diagnostic",
    "最佳诊断方案",
    "診断上の最良手法"
  ],
  "Best IR vs {benchmark}": [
    "Best IR vs {benchmark}",
    "相对 {benchmark} 的最高信息比率",
    "{benchmark}に対する最高情報レシオ"
  ],
  "best minus worst method": [
    "best minus worst method",
    "最佳方法减去最差方法",
    "最良手法と最低手法の差"
  ],
  "Best Sharpe": [
    "Best Sharpe",
    "最高夏普比率",
    "最高シャープレシオ"
  ],
  "Beta": [
    "Beta",
    "贝塔",
    "ベータ"
  ],
  "Beta vs SPY": [
    "Beta vs SPY",
    "相对 SPY 的贝塔",
    "SPYに対するベータ"
  ],
  "BL · max Sharpe": [
    "BL · max Sharpe",
    "BL · 最大夏普比率",
    "BL · 最大シャープレシオ"
  ],
  "BL · mean-variance": [
    "BL · mean-variance",
    "BL · 均值–方差",
    "BL · 平均分散"
  ],
  "BL · risk parity": [
    "BL · risk parity",
    "BL · 风险平价",
    "BL · リスクパリティ"
  ],
  "BL (CAPM prior) · risk parity": [
    "BL (CAPM prior) · risk parity",
    "BL（CAPM 先验）· 风险平价",
    "BL（CAPM事前分布）· リスクパリティ"
  ],
  "BL + views": [
    "BL + views",
    "BL + 观点",
    "BL + ビュー"
  ],
  "BL E[r] (mean-var)": [
    "BL E[r] (mean-var)",
    "BL 预期收益（均值–方差）",
    "BL期待リターン（平均分散）"
  ],
  "BL needs views; this is who produced each one": [
    "BL needs views; this is who produced each one",
    "BL 需要观点；此处列出各观点的提供方",
    "BLに必要な各ビューの提供元"
  ],
  "BL posterior returns and covariance in the main portfolio; mean-variance utility objective.": [
    "BL posterior returns and covariance in the main portfolio; mean-variance utility objective.",
    "主组合使用 BL 后验收益与协方差，以均值–方差效用为目标。",
    "主ポートフォリオでBL事後リターンと共分散を使用し、平均分散効用を最適化。"
  ],
  "BL views vs each alternative": [
    "BL views vs each alternative",
    "BL 观点与各替代方案对比",
    "BLビューと各代替手法の比較"
  ],
  "BL vol": [
    "BL vol",
    "BL 波动率",
    "BLボラティリティ"
  ],
  "Black-Litterman construction & evaluation": [
    "Black-Litterman construction & evaluation",
    "Black-Litterman 构建与评估",
    "Black-Littermanの構築と評価"
  ],
  "Black-Litterman does not generate opinions. It blends the market's implied returns (the prior, reverse-engineered from market caps) with": [
    "Black-Litterman does not generate opinions. It blends the market's implied returns (the prior, reverse-engineered from market caps) with",
    "Black-Litterman 不生成观点。它将市场隐含收益（根据市值反推的先验）与",
    "Black-Litterman自体はビューを作りません。時価総額から逆算した市場のインプライドリターン（事前分布）と"
  ],
  "Black-Litterman prior + Grinold alphas, Ledoit-Wolf covariance": [
    "Black-Litterman prior + Grinold alphas, Ledoit-Wolf covariance",
    "Black-Litterman 先验 + Grinold 超额收益，Ledoit-Wolf 协方差",
    "Black-Litterman事前分布 + Grinoldアルファ、Ledoit-Wolf共分散"
  ],
  "Black-Litterman runs on {count} holdings with {views} views ({sources}). The equilibrium prior is calibrated (δ {delta}) so the basket’s implied excess return equals its CAPM expectation {capm}; the posterior inflates variance by {inflation}.": [
    "Black-Litterman runs on {count} holdings with {views} views ({sources}). The equilibrium prior is calibrated (δ {delta}) so the basket’s implied excess return equals its CAPM expectation {capm}; the posterior inflates variance by {inflation}.",
    "Black-Litterman 对 {count} 只组合标的运行，使用 {views} 个观点（{sources}）。均衡先验经校准（δ {delta}），使篮子的隐含超额收益等于 CAPM 预期 {capm}；后验使方差增加 {inflation}。",
    "Black-Littermanは{count}銘柄、{views}ビュー（{sources}）で実行されています。均衡事前分布を校正（δ {delta}）し、バスケットのインプライド超過リターンをCAPM期待値 {capm}に合わせています。事後分布は分散を {inflation}増加させます。"
  ],
  "BLOCKING": [
    "BLOCKING",
    "阻断项",
    "利用を阻む問題"
  ],
  "bubble = average effective breadth": [
    "bubble = average effective breadth",
    "气泡大小 = 平均有效分散度",
    "バブルの大きさ = 平均実効銘柄数"
  ],
  "Calculator hash": [
    "Calculator hash",
    "计算器代码哈希",
    "計算コードのハッシュ"
  ],
  "Calendar": [
    "Calendar",
    "交易日历",
    "取引カレンダー"
  ],
  "Calendar-year returns": [
    "Calendar-year returns",
    "自然年度收益",
    "暦年リターン"
  ],
  "canonical": [
    "canonical",
    "标准方案",
    "標準"
  ],
  "Canonical": [
    "Canonical",
    "标准",
    "標準"
  ],
  "canonical executable target weight = model weight × (1 − cash); no live position ledger": [
    "canonical executable target weight = model weight × (1 − cash); no live position ledger",
    "标准可执行目标权重 = 模型权重 × (1 − 现金比例)；无实时持仓台账",
    "標準の実行用目標ウェイト = モデルウェイト × (1 − 現金比率)。実保有台帳なし"
  ],
  "canonical executable target weight = model weight × vol-target scale": [
    "canonical executable target weight = model weight × vol-target scale",
    "标准可执行目标权重 = 模型权重 × 波动率目标缩放系数",
    "標準の実行用目標ウェイト = モデルウェイト × ボラティリティ目標の調整係数"
  ],
  "Canonical target book diagnostics": [
    "Canonical target book diagnostics",
    "标准目标组合诊断",
    "標準目標ポートフォリオの診断"
  ],
  "Cap weight": [
    "Cap weight",
    "市值加权",
    "時価総額加重"
  ],
  "Cash": [
    "Cash",
    "现金",
    "現金"
  ],
  "Cash rate": [
    "Cash rate",
    "现金利率",
    "現金金利"
  ],
  "Caveat": [
    "Caveat",
    "注意事项",
    "留意点"
  ],
  "Cheapest turnover": [
    "Cheapest turnover",
    "最低换手率",
    "最低回転率"
  ],
  "Choueifaty: maximise (w'sigma)/sqrt(w'Sw).": [
    "Choueifaty: maximise (w'sigma)/sqrt(w'Sw).",
    "Choueifaty：最大化 (w'sigma)/sqrt(w'Sw)。",
    "Choueifaty：(w'sigma)/sqrt(w'Sw) を最大化。"
  ],
  "Clear": [
    "Clear",
    "清除",
    "選択解除"
  ],
  "common-vol-target scenario": [
    "common-vol-target scenario",
    "共同波动率目标情景",
    "共通ボラティリティ目標シナリオ"
  ],
  "Complete snapshot could not be loaded": [
    "Complete snapshot could not be loaded",
    "无法加载完整快照",
    "完全なスナップショットを読み込めませんでした"
  ],
  "Compliance · target constraints": [
    "Compliance · target constraints",
    "合规检查 · 目标约束",
    "制約チェック · 目標配分"
  ],
  "Composite": [
    "Composite",
    "综合评分",
    "総合スコア"
  ],
  "computed": [
    "computed",
    "已计算",
    "算出済み"
  ],
  "Confidence": [
    "Confidence",
    "置信度",
    "信頼度"
  ],
  "Confirmed close": [
    "Confirmed close",
    "已确认收盘日",
    "確定終値日"
  ],
  "Confirmed risk close": [
    "Confirmed risk close",
    "已确认的风险收盘日",
    "リスク算出の確定終値日"
  ],
  "Consistency": [
    "Consistency",
    "稳定性",
    "一貫性"
  ],
  "Construction": [
    "Construction",
    "构建方法",
    "配分手法"
  ],
  "Construction name": [
    "Construction name",
    "方法名称",
    "配分手法名"
  ],
  "Construction order": [
    "Construction order",
    "构建方法排序",
    "配分手法の並び順"
  ],
  "Contribution": [
    "Contribution",
    "贡献",
    "寄与"
  ],
  "Core columns": [
    "Core columns",
    "核心列",
    "主要列"
  ],
  "Cost impact": [
    "Cost impact",
    "成本冲击",
    "コスト影響"
  ],
  "cost of running each method": [
    "cost of running each method",
    "各方法的实施成本",
    "各手法の運用コスト"
  ],
  "Cost p.a.": [
    "Cost p.a.",
    "年化成本",
    "年率コスト"
  ],
  "Costs": [
    "Costs",
    "成本",
    "コスト"
  ],
  "Could not load portfolio data: {message}": [
    "Could not load portfolio data: {message}",
    "无法加载组合数据：{message}",
    "ポートフォリオを読み込めません：{message}"
  ],
  "country": [
    "country",
    "国家/地区",
    "国・地域"
  ],
  "covariance Σ̄. Views: fair-value upside (annualised over the view horizon, confidence from method count and dispersion), live event score (deliberately small and low-confidence - no validated forward IC yet), consensus targets, and relative within-segment ranking views. Research tooling, not investment advice.": [
    "covariance Σ̄. Views: fair-value upside (annualised over the view horizon, confidence from method count and dispersion), live event score (deliberately small and low-confidence - no validated forward IC yet), consensus targets, and relative within-segment ranking views. Research tooling, not investment advice.",
    "协方差 Σ̄。观点包括：公允价值上行空间（按观点期限年化，置信度取决于方法数量与分散度）、实时事件评分（刻意保持小幅、低置信度；前瞻 IC 尚未验证）、一致预期目标及行业组内相对排名。本工具仅供研究，不构成投资建议。",
    "共分散Σ̄を使用します。ビューは、適正価値の上昇余地（対象期間で年率換算、信頼度は手法数とばらつき）、リアルタイムのイベントスコア（フォワードIC未検証のため小幅・低信頼度）、コンセンサス目標、セグメント内の相対順位です。調査用ツールであり、投資助言ではありません。"
  ],
  "Coverage": [
    "Coverage",
    "覆盖率",
    "カバー率"
  ],
  "currency": [
    "currency",
    "货币",
    "通貨"
  ],
  "current diagnostic · not decision-usable": [
    "current diagnostic · not decision-usable",
    "当前诊断 · 不可用于决策",
    "現在の診断 · 意思決定には使用不可"
  ],
  "Current live w": [
    "Current live w",
    "当前实时权重",
    "現在の実保有ウェイト"
  ],
  "Current live weight (no ledger) versus executable and model target weights": [
    "Current live weight (no ledger) versus executable and model target weights",
    "当前实时权重（无台账）与可执行及模型目标权重对比",
    "現在の実保有ウェイト（台帳なし）と実行用・モデル目標ウェイトの比較"
  ],
  "Current vs target": [
    "Current vs target",
    "当前与目标对比",
    "現在配分と目標配分"
  ],
  "daily returns, common window": [
    "daily returns, common window",
    "日度收益，共同窗口",
    "共通期間の日次リターン"
  ],
  "Data hash": [
    "Data hash",
    "数据哈希",
    "データハッシュ"
  ],
  "Days": [
    "Days",
    "天数",
    "日数"
  ],
  "Days ahead": [
    "Days ahead",
    "距今天数",
    "発表までの日数"
  ],
  "Days to liquidate": [
    "Days to liquidate",
    "清仓天数",
    "清算所要日数"
  ],
  "Default 4": [
    "Default 4",
    "默认 4 条",
    "標準の4系列"
  ],
  "Defined": [
    "Defined",
    "已定义",
    "定義済み"
  ],
  "Depth": [
    "Depth",
    "幅度",
    "深さ"
  ],
  "Description": [
    "Description",
    "说明",
    "説明"
  ],
  "Detail": [
    "Detail",
    "详情",
    "詳細"
  ],
  "Diagnostics": [
    "Diagnostics",
    "诊断",
    "診断"
  ],
  "Dimension": [
    "Dimension",
    "维度",
    "分類軸"
  ],
  "does not reconcile": [
    "does not reconcile",
    "合计未核对一致",
    "合計が不整合"
  ],
  "done in {seconds}s": [
    "done in {seconds}s",
    "已完成，用时 {seconds} 秒",
    "完了（{seconds}秒）"
  ],
  "Drawdown": [
    "Drawdown",
    "回撤",
    "ドローダウン"
  ],
  "Drawdown episodes": [
    "Drawdown episodes",
    "回撤区间",
    "ドローダウン局面"
  ],
  "Dropped days": [
    "Dropped days",
    "剔除日期",
    "除外日"
  ],
  "E[r] lab": [
    "E[r] lab",
    "实验预期收益 E[r]",
    "ラボの期待リターン E[r]"
  ],
  "E[r] on BL": [
    "E[r] on BL",
    "BL 预期收益 E[r]",
    "BLによる期待リターン E[r]"
  ],
  "Each construction is computed": [
    "Each construction is computed",
    "各构建方法均",
    "各配分手法は"
  ],
  "Earnings": [
    "Earnings",
    "财报",
    "決算"
  ],
  "Eff. N": [
    "Eff. N",
    "有效标的数",
    "実効銘柄数"
  ],
  "Effective N": [
    "Effective N",
    "有效标的数",
    "実効銘柄数"
  ],
  "Efficient frontier": [
    "Efficient frontier",
    "有效前沿",
    "効率的フロンティア"
  ],
  "End": [
    "End",
    "结束",
    "終了"
  ],
  "EPS rev 30d": [
    "EPS rev 30d",
    "30 日 EPS 预期修正",
    "30日間のEPS予想修正"
  ],
  "Equal risk budget per value-chain segment, ERC inside each.": [
    "Equal risk budget per value-chain segment, ERC inside each.",
    "各价值链分组风险预算相等，组内使用 ERC。",
    "バリューチェーンの各セグメントに等リスク予算を配分し、内部はERC。"
  ],
  "Equal risk contribution. The current production default.": [
    "Equal risk contribution. The current production default.",
    "等风险贡献。当前默认生产方法。",
    "等リスク寄与。現在の標準運用手法。"
  ],
  "Equal weight": [
    "Equal weight",
    "等权重",
    "等ウェイト"
  ],
  "Equity curves": [
    "Equity curves",
    "净值曲线",
    "資産推移"
  ],
  "ERC / risk parity": [
    "ERC / risk parity",
    "等风险贡献 / 风险平价",
    "等リスク寄与 / リスクパリティ"
  ],
  "Estimate interval": [
    "Estimate interval",
    "估计值区间",
    "推定値の区間"
  ],
  "Event 7d": [
    "Event 7d",
    "7 日事件评分",
    "7日間のイベントスコア"
  ],
  "Event concentration": [
    "Event concentration",
    "事件集中度",
    "イベント集中度"
  ],
  "every arm is survivorship-biased alike": [
    "every arm is survivorship-biased alike",
    "各方案均存在相同幸存者偏差",
    "全手法に同じ生存者バイアスがあります"
  ],
  "Every method uses the same universe, covariance, expected returns, constraints and rebalance dates; only the construction differs, so the spread between curves reflects only the construction.": [
    "Every method uses the same universe, covariance, expected returns, constraints and rebalance dates; only the construction differs, so the spread between curves reflects only the construction.",
    "各方法使用相同股票池、协方差、预期收益、约束与调仓日；仅构建方法不同，因此曲线差异仅反映构建方法。",
    "全手法が同じ投資対象、共分散、期待リターン、制約、リバランス日を使用します。配分手法のみ異なるため、曲線間の差は配分手法だけを反映します。"
  ],
  "EVIDENCE GAP": [
    "EVIDENCE GAP",
    "证据缺口",
    "根拠の不足"
  ],
  "ex-ante": [
    "ex-ante",
    "事前估计",
    "事前推定"
  ],
  "Ex-ante comparison": [
    "Ex-ante comparison",
    "事前比较",
    "事前比較"
  ],
  "ex-ante: (expected annual return {return} − risk-free {rf}) / ex-ante annual volatility {vol}, for today's selected target portfolio": [
    "ex-ante: (expected annual return {return} − risk-free {rf}) / ex-ante annual volatility {vol}, for today's selected target portfolio",
    "事前：今日选定目标组合的（预期年化收益 {return} − 无风险利率 {rf}）/ 事前年化波动率 {vol}",
    "事前：現在の目標ポートフォリオの（期待年率リターン {return} − 無リスク金利 {rf}）/ 事前年率ボラティリティ {vol}"
  ],
  "Excess p.a.": [
    "Excess p.a.",
    "年化超额收益",
    "年率超過リターン"
  ],
  "Executable target": [
    "Executable target",
    "可执行目标",
    "実行用目標"
  ],
  "Executable target · ERC × vol target (canonical)": [
    "Executable target · ERC × vol target (canonical)",
    "可执行目标 · ERC × 波动率目标（标准方案）",
    "実行用目標 · ERC × ボラティリティ目標（標準）"
  ],
  "Executable target (ERC x volatility target)": [
    "Executable target (ERC x volatility target)",
    "可执行目标（ERC × 波动率目标）",
    "実行用目標（ERC × ボラティリティ目標）"
  ],
  "executable target weight": [
    "executable target weight",
    "可执行目标权重",
    "実行用目標ウェイト"
  ],
  "Executable target weights of the canonical run. Current positions, trade deltas, days-to-liquidate and cost impact cannot be computed. No trade instructions.": [
    "Executable target weights of the canonical run. Current positions, trade deltas, days-to-liquidate and cost impact cannot be computed. No trade instructions.",
    "标准运行的可执行目标权重。无法计算当前持仓、交易差额、清仓天数与成本冲击。此处不提供交易指令。",
    "標準実行結果の実行用目標ウェイトです。実保有、売買差分、清算所要日数、コスト影響は算出できません。売買指示は含みません。"
  ],
  "executable vs benchmark · model {model}": [
    "executable vs benchmark · model {model}",
    "可执行目标相对基准 · 模型 {model}",
    "実行用目標対ベンチマーク · モデル {model}"
  ],
  "expected excess return per name": [
    "expected excess return per name",
    "各标的预期超额收益",
    "銘柄別の期待超過リターン"
  ],
  "Expected return": [
    "Expected return",
    "预期收益",
    "期待リターン"
  ],
  "Export review snapshot": [
    "Export review snapshot",
    "导出风险审查快照",
    "リスク分析スナップショットを出力"
  ],
  "Exposure dimension": [
    "Exposure dimension",
    "敞口维度",
    "エクスポージャーの分類軸"
  ],
  "Exposures": [
    "Exposures",
    "风险敞口",
    "エクスポージャー"
  ],
  "failed": [
    "failed",
    "失败",
    "失敗"
  ],
  "failed to load: {message}": [
    "failed to load: {message}",
    "加载失败：{message}",
    "読み込み失敗：{message}"
  ],
  "Fair value": [
    "Fair value",
    "公允价值",
    "適正価値"
  ],
  "Family": [
    "Family",
    "类别",
    "分類"
  ],
  "Field": [
    "Field",
    "字段",
    "フィールド"
  ],
  "Finality": [
    "Finality",
    "收盘确认状态",
    "終値の確定状況"
  ],
  "from universe…": [
    "from universe…",
    "从股票池选择…",
    "投資対象から選択…"
  ],
  "frozen-position historical VaR / Expected Shortfall / tracking error per construction — separate from the performance matrix, which is a return record, not a risk one": [
    "frozen-position historical VaR / Expected Shortfall / tracking error per construction — separate from the performance matrix, which is a return record, not a risk one",
    "各构建方法按固定持仓计算历史 VaR、预期损失与跟踪误差 — 表现矩阵记录收益，风险在此单列",
    "配分手法別の固定ポジションによる過去VaR・ES・トラッキングエラー — リターン記録であるパフォーマンス一覧とは別に表示"
  ],
  "fully invested model": [
    "fully invested model",
    "全投资模型",
    "全額投資モデル"
  ],
  "fully-invested model weight of the primary construction": [
    "fully-invested model weight of the primary construction",
    "主构建方法的全投资模型权重",
    "基本配分手法の全額投資モデルウェイト"
  ],
  "fully-invested model weight of the primary construction, before the vol target": [
    "fully-invested model weight of the primary construction, before the vol target",
    "主构建方法在波动率目标调整前的全投资模型权重",
    "ボラティリティ目標適用前の、基本配分手法の全額投資モデルウェイト"
  ],
  "Gross": [
    "Gross",
    "总敞口",
    "グロス"
  ],
  "Gross / net / leverage": [
    "Gross / net / leverage",
    "总敞口 / 净敞口 / 杠杆",
    "グロス / ネット / レバレッジ"
  ],
  "Growth": [
    "Growth",
    "成长",
    "成長"
  ],
  "Growth-optimal: mean-variance utility at delta = 1.": [
    "Growth-optimal: mean-variance utility at delta = 1.",
    "增长最优：delta = 1 时的均值–方差效用。",
    "成長最適：delta = 1 の平均分散効用。"
  ],
  "Hashes": [
    "Hashes",
    "哈希值",
    "ハッシュ値"
  ],
  "He-Litterman closed form, solved with": [
    "He-Litterman closed form, solved with",
    "He-Litterman 闭式解，使用",
    "He-Littermanの閉形式を"
  ],
  "Hide diagnostics": [
    "Hide diagnostics",
    "隐藏诊断",
    "診断を隠す"
  ],
  "Hierarchical risk parity": [
    "Hierarchical risk parity",
    "层次风险平价",
    "階層型リスクパリティ"
  ],
  "Highest pairwise correlation: {a} × {b} at {corr}; average pairwise correlation {average} across {count} names.": [
    "Highest pairwise correlation: {a} × {b} at {corr}; average pairwise correlation {average} across {count} names.",
    "最高两两相关性：{a} × {b}，为 {corr}；{count} 只标的的平均两两相关性为 {average}。",
    "最大の銘柄間相関：{a} × {b}、{corr}。{count}銘柄の平均相関は {average}。"
  ],
  "Hindsight CAGR": [
    "Hindsight CAGR",
    "事后年化复合收益率",
    "事後シミュレーションのCAGR"
  ],
  "Hindsight illustration · today's target weights replayed": [
    "Hindsight illustration · today's target weights replayed",
    "事后示意 · 回放今日的目标权重",
    "事後シミュレーション · 現在の目標ウェイトを過去に適用"
  ],
  "HISTORICAL ARTIFACT · NOT DECISION-USABLE": [
    "HISTORICAL ARTIFACT · NOT DECISION-USABLE",
    "历史产物 · 不可用于决策",
    "履歴結果 · 意思決定には使用不可"
  ],
  "historical realised illustration: mean daily excess return over the cash rate / std, annualised, on the replayed book and sample - NOT the ex-ante Sharpe shown in the panel above": [
    "historical realised illustration: mean daily excess return over the cash rate / std, annualised, on the replayed book and sample - NOT the ex-ante Sharpe shown in the panel above",
    "历史回放示意：回放组合与样本的平均日度超额收益 / 标准差并年化；与上方事前夏普比率不同",
    "過去の再現例：再現配分と標本の日次超過リターン平均 / 標準偏差を年率換算。上の事前シャープレシオとは異なります"
  ],
  "HISTORICAL STALE COMPARISON": [
    "HISTORICAL STALE COMPARISON",
    "历史过期比较",
    "古い履歴比較"
  ],
  "Hit rate": [
    "Hit rate",
    "命中率",
    "的中率"
  ],
  "How q is set": [
    "How q is set",
    "q 的设定方式",
    "qの設定方法"
  ],
  "identifies the shared market data and is identical across constructions;": [
    "identifies the shared market data and is identical across constructions;",
    "标识共用市场数据，各构建方法的该值相同；",
    "は共通の市場データを識別し、配分手法間で同一です。"
  ],
  "Impact (bps)": [
    "Impact (bps)",
    "影响（基点）",
    "影響（bps）"
  ],
  "independently": [
    "independently",
    "独立计算",
    "個別に算出され"
  ],
  "information ratio": [
    "information ratio",
    "信息比率",
    "情報レシオ"
  ],
  "Information ratio vs {benchmark}": [
    "Information ratio vs {benchmark}",
    "相对 {benchmark} 的信息比率",
    "{benchmark}に対する情報レシオ"
  ],
  "Inspect evidence →": [
    "Inspect evidence →",
    "查看证据 →",
    "根拠を確認 →"
  ],
  "Invalid as track record": [
    "Invalid as track record",
    "不可视为业绩记录",
    "実績としては無効"
  ],
  "Inverse volatility": [
    "Inverse volatility",
    "波动率倒数加权",
    "逆ボラティリティ加重"
  ],
  "Invested": [
    "Invested",
    "已投资比例",
    "投資比率"
  ],
  "Invested (executable)": [
    "Invested (executable)",
    "已投资（可执行）",
    "投資比率（実行用）"
  ],
  "INVESTMENT RESEARCH": [
    "INVESTMENT RESEARCH",
    "投资研究",
    "投資調査"
  ],
  "is what differs. No route is published as preferred.": [
    "is what differs. No route is published as preferred.",
    "则随权重变化。未指定优先使用的计算路径。",
    "が手法によって異なります。推奨する算出方法は指定していません。"
  ],
  "Kelly (log-optimal)": [
    "Kelly (log-optimal)",
    "Kelly（对数最优）",
    "Kelly（対数最適）"
  ],
  "Lab weight": [
    "Lab weight",
    "实验权重",
    "ラボのウェイト"
  ],
  "Lab weights versus the run's target weights": [
    "Lab weights versus the run's target weights",
    "实验权重与本次运行目标权重对比",
    "ラボのウェイトと実行結果の目標ウェイトの比較"
  ],
  "Largest contributors": [
    "Largest contributors",
    "主要贡献标的",
    "主な寄与銘柄"
  ],
  "last run failed ({code})": [
    "last run failed ({code})",
    "上次运行失败（{code}）",
    "前回の実行は失敗（{code}）"
  ],
  "last run ok": [
    "last run ok",
    "上次运行成功",
    "前回の実行は成功"
  ],
  "Last source bar": [
    "Last source bar",
    "最新源数据 K 线",
    "ソースの最終価格バー"
  ],
  "Length": [
    "Length",
    "持续期",
    "期間"
  ],
  "Level": [
    "Level",
    "等级",
    "水準"
  ],
  "Leverage": [
    "Leverage",
    "杠杆",
    "レバレッジ"
  ],
  "Limiting holding": [
    "Limiting holding",
    "限制窗口的标的",
    "期間を制限する銘柄"
  ],
  "Liquidity": [
    "Liquidity",
    "流动性",
    "流動性"
  ],
  "Liquidity per target name: ADV where available; days-to-liquidate and cost impact not computable without a NAV": [
    "Liquidity per target name: ADV where available; days-to-liquidate and cost impact not computable without a NAV",
    "各目标标的流动性：可用时显示平均成交额；没有净值无法计算清仓天数与成本冲击",
    "銘柄別流動性：取得可能なADVを表示。NAVがない場合、清算日数とコスト影響は算出不可"
  ],
  "load failed: {message}": [
    "load failed: {message}",
    "加载失败：{message}",
    "読み込み失敗：{message}"
  ],
  "Loaded in this page": [
    "Loaded in this page",
    "本页加载时间",
    "このページの読込時刻"
  ],
  "Loading both snapshots. All risk sections refresh together.": [
    "Loading both snapshots. All risk sections refresh together.",
    "正在加载两个快照。全部风险区块一并刷新。",
    "両方のスナップショットを読み込み中。リスク項目をまとめて更新します。"
  ],
  "Loading portfolio": [
    "Loading portfolio",
    "正在加载组合",
    "ポートフォリオを読み込み中"
  ],
  "Loading risk workbench": [
    "Loading risk workbench",
    "正在加载风险工作台",
    "リスク分析を読み込み中"
  ],
  "Loading the canonical target book and construction evidence…": [
    "Loading the canonical target book and construction evidence…",
    "正在加载标准目标组合与构建证据…",
    "標準目標ポートフォリオと配分手法の根拠を読み込み中…"
  ],
  "Long": [
    "Long",
    "多头",
    "ロング"
  ],
  "long-only mandate": [
    "long-only mandate",
    "仅做多约束",
    "ロングのみの運用方針"
  ],
  "Lookback (y)": [
    "Lookback (y)",
    "回溯期（年）",
    "参照期間（年）"
  ],
  "Lopez de Prado: correlation clustering + recursive bisection, no matrix inversion.": [
    "Lopez de Prado: correlation clustering + recursive bisection, no matrix inversion.",
    "Lopez de Prado：相关性聚类 + 递归二分，不需要矩阵求逆。",
    "Lopez de Prado：相関クラスタリング + 再帰二分。行列の逆算は不要。"
  ],
  "Lowest ex-ante variance under the constraints.": [
    "Lowest ex-ante variance under the constraints.",
    "约束条件下最低事前方差。",
    "制約下で事前分散を最小化。"
  ],
  "Mandate benchmark": [
    "Mandate benchmark",
    "正式投资基准",
    "正式な運用ベンチマーク"
  ],
  "Market Intel · Constructions": [
    "Market Intel · Constructions",
    "Market Intel · 组合构建",
    "Market Intel · 配分手法"
  ],
  "Market Intel · Portfolio": [
    "Market Intel · Portfolio",
    "Market Intel · 投资组合",
    "Market Intel · ポートフォリオ"
  ],
  "Market Intel · Portfolio · 风险分析": [
    "Market Intel · Portfolio · Risk review",
    "Market Intel · 投资组合 · 风险分析",
    "Market Intel · ポートフォリオ · リスク分析"
  ],
  "Market-cap proportional within the selected names.": [
    "Market-cap proportional within the selected names.",
    "按所选标的市值比例分配。",
    "選択銘柄の時価総額に比例して配分。"
  ],
  "Max DD": [
    "Max DD",
    "最大回撤",
    "最大ドローダウン"
  ],
  "Max diversification": [
    "Max diversification",
    "最大分散化",
    "最大分散化"
  ],
  "Max drawdown": [
    "Max drawdown",
    "最大回撤",
    "最大ドローダウン"
  ],
  "Max segment": [
    "Max segment",
    "行业分组上限",
    "セグメント上限"
  ],
  "Max Sharpe": [
    "Max Sharpe",
    "最大夏普比率",
    "最大シャープレシオ"
  ],
  "Max w": [
    "Max w",
    "最高权重",
    "最大ウェイト"
  ],
  "Maximum diversification": [
    "Maximum diversification",
    "最大分散化",
    "最大分散化"
  ],
  "Mean-variance / Black-Litterman": [
    "Mean-variance / Black-Litterman",
    "均值–方差 / Black-Litterman",
    "平均分散 / Black-Litterman"
  ],
  "mean-variance arms consume E[r]; risk parity does not": [
    "mean-variance arms consume E[r]; risk parity does not",
    "均值–方差方案使用预期收益 E[r]；风险平价不使用",
    "平均分散は期待リターンE[r]を使用し、リスクパリティは使用しません"
  ],
  "measured on the mean-variance book": [
    "measured on the mean-variance book",
    "按均值–方差组合衡量",
    "平均分散ポートフォリオで測定"
  ],
  "MECHANISM DIAGNOSTIC ONLY · performance and alpha claims withheld": [
    "MECHANISM DIAGNOSTIC ONLY · performance and alpha claims withheld",
    "仅机制诊断 · 不作表现与超额收益主张",
    "仕組みの診断のみ · パフォーマンスとアルファの主張は保留"
  ],
  "Method": [
    "Method",
    "方法",
    "手法"
  ],
  "Method family": [
    "Method family",
    "方法类别",
    "手法の分類"
  ],
  "Method reference": [
    "Method reference",
    "方法参考",
    "手法の解説"
  ],
  "Methodology & lineage": [
    "Methodology & lineage",
    "方法与数据溯源",
    "算出方法とデータ来歴"
  ],
  "Metric": [
    "Metric",
    "指标",
    "指標"
  ],
  "Michaud: average max-Sharpe weights over draws of estimation error.": [
    "Michaud: average max-Sharpe weights over draws of estimation error.",
    "Michaud：对估计误差抽样所得最大夏普权重取平均。",
    "Michaud：推定誤差の標本ごとの最大シャープウェイトを平均。"
  ],
  "Min w": [
    "Min w",
    "最低权重",
    "最低ウェイト"
  ],
  "Minimum CVaR": [
    "Minimum CVaR",
    "最小条件风险价值（CVaR）",
    "最小CVaR"
  ],
  "Minimum CVaR (95%)": [
    "Minimum CVaR (95%)",
    "最小 CVaR（95%）",
    "最小CVaR（95%）"
  ],
  "Minimum variance": [
    "Minimum variance",
    "最小方差",
    "最小分散"
  ],
  "Mkt cap": [
    "Mkt cap",
    "市值",
    "時価総額"
  ],
  "model": [
    "model",
    "模型",
    "モデル"
  ],
  "model portfolio": [
    "model portfolio",
    "模型组合",
    "モデルポートフォリオ"
  ],
  "Model w": [
    "Model w",
    "模型权重",
    "モデルウェイト"
  ],
  "Model weight": [
    "Model weight",
    "模型权重",
    "モデルウェイト"
  ],
  "Mom.": [
    "Mom.",
    "动量",
    "モメンタム"
  ],
  "Month end": [
    "Month end",
    "月末",
    "月末"
  ],
  "Monthly bps": [
    "Monthly bps",
    "月度基点",
    "月次bps"
  ],
  "monthly excess, Newey-West t (3 lags). |t| < 2 = not distinguishable from 1/N": [
    "monthly excess, Newey-West t (3 lags). |t| < 2 = not distinguishable from 1/N",
    "月度超额收益，Newey-West t（滞后 3 期）。|t| < 2 表示无法与 1/N 区分",
    "月次超過リターン、Newey-West t（ラグ3）。|t| < 2では1/Nとの差を識別できません"
  ],
  "monthly returns, Newey-West t (3 lags). |t| < 2 = not distinguishable from zero": [
    "monthly returns, Newey-West t (3 lags). |t| < 2 = not distinguishable from zero",
    "月度收益，Newey-West t（滞后 3 期）。|t| < 2 表示无法与零区分",
    "月次リターン、Newey-West t（ラグ3）。|t| < 2ではゼロとの差を識別できません"
  ],
  "Months": [
    "Months",
    "月数",
    "月数"
  ],
  "More columns": [
    "More columns",
    "更多列",
    "列を追加"
  ],
  "n/a (no ledger)": [
    "n/a (no ledger)",
    "不适用（无台账）",
    "該当なし（台帳なし）"
  ],
  "Names": [
    "Names",
    "标的数量",
    "銘柄数"
  ],
  "Names reporting": [
    "Names reporting",
    "财报标的数",
    "決算発表予定銘柄数"
  ],
  "Net": [
    "Net",
    "净敞口",
    "ネット"
  ],
  "Next earnings": [
    "Next earnings",
    "下次财报",
    "次回決算発表"
  ],
  "no": [
    "no",
    "否",
    "いいえ"
  ],
  "No active weights.": [
    "No active weights.",
    "暂无主动权重。",
    "アクティブウェイトがありません。"
  ],
  "No betas.": [
    "No betas.",
    "暂无贝塔。",
    "ベータがありません。"
  ],
  "no BL run yet": [
    "no BL run yet",
    "尚无 BL 运行结果",
    "BLの実行結果がありません"
  ],
  "no compliance checks in this payload": [
    "no compliance checks in this payload",
    "此数据暂无约束检查",
    "このデータには制約チェックがありません"
  ],
  "No construction": [
    "No construction",
    "未选择方法",
    "配分手法なし"
  ],
  "no correlation matrix in this run": [
    "no correlation matrix in this run",
    "本次运行暂无相关矩阵",
    "この実行結果には相関行列がありません"
  ],
  "no current walk-forward artifact": [
    "no current walk-forward artifact",
    "暂无当前滚动前测产物",
    "現在のウォークフォワード結果がありません"
  ],
  "no drawdown deeper than 10%": [
    "no drawdown deeper than 10%",
    "没有超过 10% 的回撤",
    "10%を超えるドローダウンはありません"
  ],
  "No drawdown series.": [
    "No drawdown series.",
    "暂无回撤序列。",
    "ドローダウン系列がありません。"
  ],
  "No earnings date on file: {names}. ": [
    "No earnings date on file: {names}. ",
    "未记录财报日期：{names}。",
    "決算発表日が未登録：{names}。"
  ],
  "No earnings events are recorded within the next {days} days. Calendar completeness is unverified.": [
    "No earnings events are recorded within the next {days} days. Calendar completeness is unverified.",
    "未来 {days} 天未记录财报事件。日历完整性未经核实。",
    "今後{days}日以内の決算イベントは記録されていません。カレンダーの網羅性は未確認です。"
  ],
  "No frontier statistics in this run.": [
    "No frontier statistics in this run.",
    "本次运行暂无前沿统计。",
    "この実行結果にはフロンティア統計がありません。"
  ],
  "No lab weights.": [
    "No lab weights.",
    "暂无实验权重。",
    "ラボのウェイトがありません。"
  ],
  "no ledger": [
    "no ledger",
    "无持仓台账",
    "保有台帳なし"
  ],
  "No live position ledger.": [
    "No live position ledger.",
    "无实时持仓台账。",
    "実保有の台帳はありません。"
  ],
  "No names in the target list.": [
    "No names in the target list.",
    "目标名单为空。",
    "目標銘柄リストが空です。"
  ],
  "No names.": [
    "No names.",
    "暂无标的。",
    "銘柄がありません。"
  ],
  "No pairwise correlations.": [
    "No pairwise correlations.",
    "暂无两两相关性。",
    "銘柄間の相関がありません。"
  ],
  "no pipeline output yet": [
    "no pipeline output yet",
    "尚无流水线输出",
    "パイプラインの出力がありません"
  ],
  "No pipeline output yet. Run the pipeline from the Research page.": [
    "No pipeline output yet. Run the pipeline from the Research page.",
    "尚无流水线输出。请从研究页运行流水线。",
    "パイプラインの出力がありません。研究ページから実行してください。"
  ],
  "no realised betas (insufficient common price history)": [
    "no realised betas (insufficient common price history)",
    "暂无历史估计贝塔（共同价格历史不足）",
    "実現ベータがありません（共通価格履歴が不足）"
  ],
  "no replay in this run": [
    "no replay in this run",
    "本次运行暂无回放",
    "この実行結果には再現データがありません"
  ],
  "No risk contributions for this construction.": [
    "No risk contributions for this construction.",
    "此方法暂无风险贡献。",
    "この手法のリスク寄与がありません。"
  ],
  "No risk contributions.": [
    "No risk contributions.",
    "暂无风险贡献。",
    "リスク寄与がありません。"
  ],
  "no stress scenarios in this payload": [
    "no stress scenarios in this payload",
    "此数据暂无压力情景",
    "このデータにはストレスシナリオがありません"
  ],
  "no target list in the canonical run; run the pipeline from the Research page": [
    "no target list in the canonical run; run the pipeline from the Research page",
    "标准运行无目标名单；请从研究页运行流水线",
    "標準実行結果に目標銘柄リストがありません。研究ページからパイプラインを実行してください"
  ],
  "no target list in this run": [
    "no target list in this run",
    "本次运行暂无目标名单",
    "この実行結果には目標銘柄リストがありません"
  ],
  "no target names": [
    "no target names",
    "暂无目标标的",
    "目標銘柄がありません"
  ],
  "No target weights in this run.": [
    "No target weights in this run.",
    "本次运行暂无目标权重。",
    "この実行結果には目標ウェイトがありません。"
  ],
  "No volume data in the price cache. ": [
    "No volume data in the price cache. ",
    "价格缓存中没有成交量数据。",
    "価格キャッシュに売買高データがありません。"
  ],
  "No weights for this construction.": [
    "No weights for this construction.",
    "此方法暂无权重。",
    "この手法のウェイトがありません。"
  ],
  "non-overlapping (median of {count} phases)": [
    "non-overlapping (median of {count} phases)",
    "非重叠（{count} 个相位的中位数）",
    "非重複（{count}位相の中央値）"
  ],
  "not available": [
    "not available",
    "不可用",
    "取得不可"
  ],
  "not computable": [
    "not computable",
    "无法计算",
    "算出不可"
  ],
  "not computable — no NAV": [
    "not computable — no NAV",
    "无法计算 — 缺少净值",
    "算出不可 — NAVなし"
  ],
  "not computable — no volume": [
    "not computable — no volume",
    "无法计算 — 缺少成交量",
    "算出不可 — 売買高なし"
  ],
  "not computable — no volume/NAV": [
    "not computable — no volume/NAV",
    "无法计算 — 缺少成交量/净值",
    "算出不可 — 売買高/NAVなし"
  ],
  "NOT DECISION-USABLE.": [
    "NOT DECISION-USABLE.",
    "不可用于决策。",
    "意思決定には使用不可。"
  ],
  "Not defined": [
    "Not defined",
    "未定义",
    "未定義"
  ],
  "nothing selected": [
    "nothing selected",
    "未选择序列",
    "系列が未選択です"
  ],
  "Obs": [
    "Obs",
    "观测数",
    "観測数"
  ],
  "of the invested sleeve": [
    "of the invested sleeve",
    "占已投资股票部分",
    "投資スリーブに対する比率"
  ],
  "omega_mode=absolute (the only mode where tau is not cancelled)": [
    "omega_mode=absolute (the only mode where tau is not cancelled)",
    "omega_mode=absolute（唯一不会抵消 tau 的模式）",
    "omega_mode=absolute（tauが相殺されない唯一のモード）"
  ],
  "ongoing": [
    "ongoing",
    "持续中",
    "継続中"
  ],
  "open a ticker for detail · event columns come from the live monitor · target weights, not positions": [
    "open a ticker for detail · event columns come from the live monitor · target weights, not positions",
    "点击代码查看详情 · 事件列来自实时监控 · 此处为目标权重，非实际持仓",
    "銘柄コードから詳細へ · イベント列は監視機能に基づく · 表示は目標ウェイト、実保有ではありません"
  ],
  "optimisation failed: {message}": [
    "optimisation failed: {message}",
    "优化失败：{message}",
    "最適化に失敗：{message}"
  ],
  "Optimising…": [
    "Optimising…",
    "正在优化…",
    "最適化中…"
  ],
  "optimising… (Ledoit-Wolf, Black-Litterman, constrained solves, replay)": [
    "optimising… (Ledoit-Wolf, Black-Litterman, constrained solves, replay)",
    "正在优化…（Ledoit-Wolf、Black-Litterman、约束求解、回放）",
    "最適化中…（Ledoit-Wolf、Black-Litterman、制約付き最適化、再現）"
  ],
  "optimising… {seconds}s": [
    "optimising… {seconds}s",
    "正在优化… {seconds} 秒",
    "最適化中… {seconds}秒"
  ],
  "Optimize": [
    "Optimize",
    "优化",
    "最適化"
  ],
  "Optimizer lab": [
    "Optimizer lab",
    "优化器实验室",
    "最適化ラボ"
  ],
  "Order": [
    "Order",
    "排序",
    "並び順"
  ],
  "Origin": [
    "Origin",
    "原始来源",
    "元の出所"
  ],
  "Out of sample ({start} → {end}, {count} monthly rebalances), adding views earned {return} a year with {vol} more volatility: Sharpe {sharpe}, Newey-West t {tstat}, monthly win rate {win}.": [
    "Out of sample ({start} → {end}, {count} monthly rebalances), adding views earned {return} a year with {vol} more volatility: Sharpe {sharpe}, Newey-West t {tstat}, monthly win rate {win}.",
    "样本外（{start} → {end}，{count} 次月度调仓），加入观点带来年化收益 {return}，同时波动率增加 {vol}：夏普比率变化 {sharpe}，Newey-West t 为 {tstat}，月度胜率 {win}。",
    "サンプル外（{start} → {end}、月次リバランス{count}回）では、ビュー追加による年率リターン {return}、ボラティリティ増加 {vol}。シャープレシオの差 {sharpe}、Newey-West t {tstat}、月次勝率 {win}。"
  ],
  "overlapping": [
    "overlapping",
    "重叠窗口",
    "重複期間"
  ],
  "P&L (% NAV)": [
    "P&L (% NAV)",
    "损益（净值比例）",
    "損益（NAV比率）"
  ],
  "P&L as % of NAV · executable target weights · cash unshocked": [
    "P&L as % of NAV · executable target weights · cash unshocked",
    "损益占净值比例 · 可执行目标权重 · 现金不受冲击",
    "損益のNAV比率 · 実行用目標ウェイト · 現金はショック対象外"
  ],
  "per month": [
    "per month",
    "每月",
    "月次"
  ],
  "Performance by market regime": [
    "Performance by market regime",
    "不同市场状态下的表现",
    "相場局面別パフォーマンス"
  ],
  "Performance matrix": [
    "Performance matrix",
    "表现矩阵",
    "パフォーマンス一覧"
  ],
  "Point": [
    "Point",
    "点位",
    "点"
  ],
  "Point in time from {start} to {end}: each month end, top {top} names by 12-1 momentum, {years}-year Ledoit-Wolf covariance, {prior}, weights capped at {cap}, net of {cost} bps one-way costs.": [
    "Point in time from {start} to {end}: each month end, top {top} names by 12-1 momentum, {years}-year Ledoit-Wolf covariance, {prior}, weights capped at {cap}, net of {cost} bps one-way costs.",
    "时点范围 {start} 至 {end}：每月末选取 12-1 动量前 {top} 只标的，使用 {years} 年 Ledoit-Wolf 协方差、{prior}，权重上限 {cap}，扣除单边 {cost} 基点成本。",
    "対象期間 {start}〜{end}。各月末に12-1モメンタム上位{top}銘柄を選び、{years}年のLedoit-Wolf共分散、{prior}を使用。ウェイト上限 {cap}、片道{cost}bpsのコスト控除後。"
  ],
  "Point-in-time test: at each month end, a calibrated equilibrium prior plus 12-1 momentum views (the fair-value views exist only as today's snapshot, so they cannot be tested historically). Net of 10 bps one-way costs. The universe is today's constituents, so": [
    "Point-in-time test: at each month end, a calibrated equilibrium prior plus 12-1 momentum views (the fair-value views exist only as today's snapshot, so they cannot be tested historically). Net of 10 bps one-way costs. The universe is today's constituents, so",
    "时点检验：每个月末使用校准后的均衡先验与 12-1 动量观点（公允价值观点仅有今日快照，无法进行历史检验）。扣除单边 10 基点成本。股票池为当前成分，因此",
    "時点整合の検証：各月末に校正済み均衡事前分布と12-1モメンタムビューを使用（適正価値ビューは現在のスナップショットのみで、過去検証は不可）。片道10bpsのコスト控除後。投資対象は現在の構成銘柄なので、"
  ],
  "Portfolio": [
    "Portfolio",
    "投资组合",
    "ポートフォリオ"
  ],
  "portfolio · 组合总览": [
    "Portfolio · Overview",
    "投资组合 · 组合总览",
    "ポートフォリオ · 概要"
  ],
  "portfolio · 风险分析": [
    "Portfolio · Risk review",
    "投资组合 · 风险分析",
    "ポートフォリオ · リスク分析"
  ],
  "Portfolio construction checks only. Trade approval, restrictions and execution readiness are unassessed.": [
    "Portfolio construction checks only. Trade approval, restrictions and execution readiness are unassessed.",
    "仅检查组合构建。未评估交易审批、限制与执行准备。",
    "ポートフォリオ構築のチェックのみです。売買承認、取引制限、執行準備は未評価です。"
  ],
  "portfolio construction comparison": [
    "portfolio construction comparison",
    "组合构建方法比较",
    "ポートフォリオ配分手法の比較"
  ],
  "Portfolio diagnostics": [
    "Portfolio diagnostics",
    "组合诊断",
    "ポートフォリオ診断"
  ],
  "Portfolio loaded": [
    "Portfolio loaded",
    "组合已加载",
    "ポートフォリオを読み込みました"
  ],
  "portfolio β SOXX {soxx} · β SPY {spy} (Σ weight × β, cash at 0){days}": [
    "portfolio β SOXX {soxx} · β SPY {spy} (Σ weight × β, cash at 0){days}",
    "组合 β SOXX {soxx} · β SPY {spy}（Σ 权重 × β，现金为 0）{days}",
    "ポートフォリオ β SOXX {soxx} · β SPY {spy}（Σ ウェイト × β、現金は0）{days}"
  ],
  "Pos. mo.": [
    "Pos. mo.",
    "上涨月份占比",
    "プラス月比率"
  ],
  "Position $": [
    "Position $",
    "持仓金额（美元）",
    "保有額（ドル）"
  ],
  "posterior": [
    "posterior",
    "后验",
    "事後"
  ],
  "Price": [
    "Price",
    "价格",
    "価格"
  ],
  "Primary": [
    "Primary",
    "主构建方法",
    "基本配分手法"
  ],
  "Prior → posterior": [
    "Prior → posterior",
    "先验 → 后验",
    "事前分布 → 事後分布"
  ],
  "Prior only · mean-variance": [
    "Prior only · mean-variance",
    "仅先验 · 均值–方差",
    "事前分布のみ · 平均分散"
  ],
  "Prior only · risk parity": [
    "Prior only · risk parity",
    "仅先验 · 风险平价",
    "事前分布のみ · リスクパリティ"
  ],
  "Prior says": [
    "Prior says",
    "先验收益",
    "事前分布のリターン"
  ],
  "Production": [
    "Production",
    "生产方案",
    "運用手法"
  ],
  "Production (Grinold) · max Sharpe": [
    "Production (Grinold) · max Sharpe",
    "生产方案（Grinold）· 最大夏普比率",
    "運用手法（Grinold）· 最大シャープレシオ"
  ],
  "Production (Grinold) · risk parity": [
    "Production (Grinold) · risk parity",
    "生产方案（Grinold）· 风险平价",
    "運用手法（Grinold）· リスクパリティ"
  ],
  "Production ERC weights scaled to the ex-ante volatility target; remainder held in cash.": [
    "Production ERC weights scaled to the ex-ante volatility target; remainder held in cash.",
    "生产 ERC 权重按事前波动率目标缩放，剩余部分配置现金。",
    "運用ERCウェイトを事前ボラティリティ目標に調整し、残りを現金に配分。"
  ],
  "production replica": [
    "production replica",
    "生产方案复现",
    "運用手法の再現"
  ],
  "Production replica": [
    "Production replica",
    "生产方案复现",
    "運用手法の再現"
  ],
  "Production replica (raw cov, vol-target cash)": [
    "Production replica (raw cov, vol-target cash)",
    "生产方案复现（原始协方差，波动率目标现金配置）",
    "運用手法の再現（標本共分散、ボラティリティ目標による現金配分）"
  ],
  "Production vs best vs equal weight": [
    "Production vs best vs equal weight",
    "生产方案、最佳方案与等权比较",
    "運用手法・最良手法・等ウェイトの比較"
  ],
  "Published order": [
    "Published order",
    "发布顺序",
    "公開順"
  ],
  "q (excess p.a.)": [
    "q (excess p.a.)",
    "q（年化超额收益）",
    "q（年率超過リターン）"
  ],
  "re-run the pipeline's optimiser on any subset of the universe with your own constraints (cached 10 min) · outputs are model targets, not positions": [
    "re-run the pipeline's optimiser on any subset of the universe with your own constraints (cached 10 min) · outputs are model targets, not positions",
    "对股票池的任意子集按自定约束重新优化（缓存 10 分钟）· 输出为模型目标，非实际持仓",
    "投資対象の任意の銘柄群を独自の制約で再最適化（10分間キャッシュ）· 出力はモデル上の目標配分"
  ],
  "Reads expected returns": [
    "Reads expected returns",
    "使用预期收益",
    "期待リターンを使用"
  ],
  "Realised betas": [
    "Realised betas",
    "历史估计贝塔",
    "実現ベータ"
  ],
  "Realised betas per target name versus SOXX and SPY": [
    "Realised betas per target name versus SOXX and SPY",
    "各目标标的相对 SOXX 与 SPY 的历史估计贝塔",
    "目標銘柄別のSOXX・SPYに対する実現ベータ"
  ],
  "Rebalance": [
    "Rebalance",
    "调仓",
    "リバランス"
  ],
  "recalculated here": [
    "recalculated here",
    "在此重新计算",
    "ここで再計算し"
  ],
  "reconciles to 100%": [
    "reconciles to 100%",
    "合计核对为 100%",
    "合計100%と整合"
  ],
  "reconciliation unconfirmed": [
    "reconciliation unconfirmed",
    "尚未确认权重一致性",
    "ウェイトの整合性は未確認"
  ],
  "recovered": [
    "recovered",
    "已恢复",
    "回復済み"
  ],
  "Recovery": [
    "Recovery",
    "恢复期",
    "回復期間"
  ],
  "Refresh portfolio data": [
    "Refresh portfolio data",
    "刷新组合数据",
    "ポートフォリオを再読込"
  ],
  "Refresh risk workbench data": [
    "Refresh risk workbench data",
    "刷新风险工作台数据",
    "リスク分析を再読込"
  ],
  "Regime": [
    "Regime",
    "市场状态",
    "相場局面"
  ],
  "regimes derived from the benchmark's own path, no lookahead": [
    "regimes derived from the benchmark's own path, no lookahead",
    "市场状态由基准自身路径确定，无前瞻信息",
    "ベンチマークの推移から先読みせずに相場局面を判定"
  ],
  "remainder after the {target} vol target": [
    "remainder after the {target} vol target",
    "波动率目标 {target} 调整后的剩余比例",
    "ボラティリティ目標 {target} 適用後の残余"
  ],
  "remainder after vol target": [
    "remainder after vol target",
    "波动率目标调整后的剩余比例",
    "ボラティリティ目標適用後の残余"
  ],
  "Remove {ticker} from the lab list": [
    "Remove {ticker} from the lab list",
    "从实验名单移除 {ticker}",
    "ラボのリストから {ticker} を削除"
  ],
  "Replay statistics by series (hindsight illustration)": [
    "Replay statistics by series (hindsight illustration)",
    "各序列回放统计（事后示意）",
    "系列別の再現統計（事後シミュレーション）"
  ],
  "Resampled max Sharpe": [
    "Resampled max Sharpe",
    "重采样最大夏普比率",
    "リサンプリング最大シャープレシオ"
  ],
  "Research status": [
    "Research status",
    "研究状态",
    "調査ステータス"
  ],
  "Return": [
    "Return",
    "收益",
    "リターン"
  ],
  "Return contribution": [
    "Return contribution",
    "收益贡献",
    "リターン寄与"
  ],
  "Return correlations": [
    "Return correlations",
    "收益相关性",
    "リターン相関"
  ],
  "return-aware": [
    "return-aware",
    "收益导向",
    "期待リターンを使用"
  ],
  "Return-aware": [
    "Return-aware",
    "收益导向",
    "期待リターンを使用"
  ],
  "Rev g NTM": [
    "Rev g NTM",
    "未来 12 个月营收增速",
    "今後12か月の売上成長率"
  ],
  "Review findings & data evidence": [
    "Review findings & data evidence",
    "审查发现与数据证据",
    "分析上の指摘とデータの根拠"
  ],
  "Review snapshot exported with selection, findings and data lineage": [
    "Review snapshot exported with selection, findings and data lineage",
    "已导出包含选择、审查发现及数据溯源的快照",
    "選択内容・指摘・データ来歴を含むスナップショットを出力しました"
  ],
  "Review the evidence before using these estimates.": [
    "Review the evidence before using these estimates.",
    "使用估计前请审查证据。",
    "推定値の利用前に根拠を確認してください。"
  ],
  "Review unavailable: {message}. Refresh to retry; previous figures are withheld.": [
    "Review unavailable: {message}. Refresh to retry; previous figures are withheld.",
    "审查不可用：{message}。请刷新重试；此前数值暂不展示。",
    "分析を利用できません：{message}。再読込して再試行してください。以前の数値は表示を保留します。"
  ],
  "Risk by construction": [
    "Risk by construction",
    "各构建方法的风险",
    "配分手法別リスク"
  ],
  "Risk contrib": [
    "Risk contrib",
    "风险贡献",
    "リスク寄与"
  ],
  "Risk contributions": [
    "Risk contributions",
    "风险贡献",
    "リスク寄与"
  ],
  "Risk matrix": [
    "Risk matrix",
    "风险矩阵",
    "リスク一覧"
  ],
  "Risk parity (ERC)": [
    "Risk parity (ERC)",
    "风险平价（ERC）",
    "リスクパリティ（ERC）"
  ],
  "Risk share": [
    "Risk share",
    "风险占比",
    "リスク割合"
  ],
  "Risk vs return": [
    "Risk vs return",
    "风险与收益",
    "リスクとリターン"
  ],
  "Risk workbench loaded": [
    "Risk workbench loaded",
    "风险工作台已加载",
    "リスク分析を読み込みました"
  ],
  "Risk workbench: no target list": [
    "Risk workbench: no target list",
    "风险工作台：无目标名单",
    "リスク分析：目標銘柄リストなし"
  ],
  "Risk-based": [
    "Risk-based",
    "风险导向",
    "リスク重視"
  ],
  "risk-based / no expected-return input": [
    "risk-based / no expected-return input",
    "风险导向 / 不输入预期收益",
    "リスク重視 / 期待リターン入力なし"
  ],
  "risk-only": [
    "risk-only",
    "仅风险",
    "リスクのみ"
  ],
  "Risk-only": [
    "Risk-only",
    "仅风险",
    "リスクのみ"
  ],
  "Rockafellar-Uryasev LP on the loss tail rather than variance.": [
    "Rockafellar-Uryasev LP on the loss tail rather than variance.",
    "Rockafellar-Uryasev：对损失尾部使用线性规划，而非优化方差。",
    "Rockafellar-Uryasev：分散ではなく損失テールに対する線形計画法。"
  ],
  "Rolling 1-year Sharpe": [
    "Rolling 1-year Sharpe",
    "滚动 1 年夏普比率",
    "ローリング1年シャープレシオ"
  ],
  "Run": [
    "Run",
    "运行",
    "実行"
  ],
  "run session {session} · {schema}": [
    "run session {session} · {schema}",
    "运行交易日 {session} · {schema}",
    "実行セッション {session} · {schema}"
  ],
  "running…": [
    "running…",
    "正在运行…",
    "実行中…"
  ],
  "same snapshot · compare within one layer": [
    "same snapshot · compare within one layer",
    "相同快照 · 同一层内比较",
    "同一スナップショット · 同じレイヤー内で比較"
  ],
  "same universe, covariance, expected returns, constraints and rebalance dates — only the construction differs": [
    "same universe, covariance, expected returns, constraints and rebalance dates — only the construction differs",
    "股票池、协方差、预期收益、约束与调仓日均相同 — 仅构建方法不同",
    "投資対象、共分散、期待リターン、制約、リバランス日が同一で、配分手法のみ異なります"
  ],
  "Scenario": [
    "Scenario",
    "情景",
    "シナリオ"
  ],
  "Scope": [
    "Scope",
    "范围",
    "対象範囲"
  ],
  "segment": [
    "segment",
    "行业分组",
    "セグメント"
  ],
  "Segment": [
    "Segment",
    "行业分组",
    "セグメント"
  ],
  "Segment risk parity": [
    "Segment risk parity",
    "行业分组风险平价",
    "セグメント別リスクパリティ"
  ],
  "Select a construction above.": [
    "Select a construction above.",
    "请在上方选择方法。",
    "上で配分手法を選択してください。"
  ],
  "Selected construction": [
    "Selected construction",
    "选定方法",
    "選択した配分手法"
  ],
  "Selected construction details": [
    "Selected construction details",
    "选定方法详情",
    "選択した配分手法の詳細"
  ],
  "Selected risk ↓": [
    "Selected risk ↓",
    "选定风险 ↓",
    "選択したリスク ↓"
  ],
  "Selected tickers": [
    "Selected tickers",
    "已选证券代码",
    "選択済み銘柄コード"
  ],
  "Selected: {method} at {vol} vol / {return} expected return. The frontier uses Grinold returns; the Black-Litterman point uses its posterior return and covariance.": [
    "Selected: {method} at {vol} vol / {return} expected return. The frontier uses Grinold returns; the Black-Litterman point uses its posterior return and covariance.",
    "已选：{method}，波动率 {vol} / 预期收益 {return}。前沿使用 Grinold 收益；Black-Litterman 点位使用后验收益与协方差。",
    "選択：{method}、ボラティリティ {vol} / 期待リターン {return}。フロンティアはGrinoldリターン、Black-Littermanの点は事後リターンと共分散を使用します。"
  ],
  "Selected: {method}. Canonical executable target remains {primary} with a {target} ex-ante vol target; weights {min}–{max}, max {segment} per segment. Returns window {start} → {end} ({days} common days). The simulation below is a hindsight illustration (today's names and weights replayed) and is invalid as a track record. Research only, not investment advice.": [
    "Selected: {method}. Canonical executable target remains {primary} with a {target} ex-ante vol target; weights {min}–{max}, max {segment} per segment. Returns window {start} → {end} ({days} common days). The simulation below is a hindsight illustration (today's names and weights replayed) and is invalid as a track record. Research only, not investment advice.",
    "已选：{method}。标准可执行目标仍采用 {primary}，事前波动率目标 {target}；单标的权重 {min}–{max}，行业分组上限 {segment}。收益窗口 {start} → {end}（{days} 个共同交易日）。下方按今日标的与权重回放，属于事后示意，不能视为业绩记录。仅供研究，不构成投资建议。",
    "選択：{method}。標準の実行用目標は {primary}、事前ボラティリティ目標 {target}。銘柄別ウェイト {min}–{max}、セグメント上限 {segment}。リターン期間 {start} → {end}（共通 {days} 日）。下の再現は現在の銘柄とウェイトを過去に適用した事後シミュレーションで、実績としては無効です。調査目的のみで、投資助言ではありません。"
  ],
  "Selection": [
    "Selection",
    "选择",
    "選択"
  ],
  "Series": [
    "Series",
    "序列",
    "系列"
  ],
  "Series selection": [
    "Series selection",
    "序列选择",
    "系列選択"
  ],
  "Share": [
    "Share",
    "占比",
    "割合"
  ],
  "Share of invested": [
    "Share of invested",
    "占已投资比例",
    "投資部分に対する比率"
  ],
  "share of portfolio variance": [
    "share of portfolio variance",
    "组合方差占比",
    "ポートフォリオ分散の割合"
  ],
  "Share of time": [
    "Share of time",
    "时间占比",
    "期間比率"
  ],
  "Sharpe": [
    "Sharpe",
    "夏普比率",
    "シャープレシオ"
  ],
  "Sharpe (historical daily excess)": [
    "Sharpe (historical daily excess)",
    "夏普比率（历史日度超额收益）",
    "シャープレシオ（過去の日次超過リターン）"
  ],
  "Sharpe lab": [
    "Sharpe lab",
    "实验夏普比率",
    "ラボのシャープレシオ"
  ],
  "Sharpe spread": [
    "Sharpe spread",
    "夏普比率差",
    "シャープレシオの差"
  ],
  "Shock": [
    "Shock",
    "冲击",
    "ショック"
  ],
  "shock {shock}": [
    "shock {shock}",
    "冲击 {shock}",
    "ショック {shock}"
  ],
  "Short": [
    "Short",
    "空头",
    "ショート"
  ],
  "Show all": [
    "Show all",
    "显示全部",
    "すべて表示"
  ],
  "Show diagnostics": [
    "Show diagnostics",
    "显示诊断",
    "診断を表示"
  ],
  "Show full matrix, layers, 1-day tails and confidence intervals": [
    "Show full matrix, layers, 1-day tails and confidence intervals",
    "显示完整矩阵、层、1 日尾部与置信区间",
    "全マトリクス、レイヤー、1日テール、信頼区間を表示"
  ],
  "Show or hide series": [
    "Show or hide series",
    "显示或隐藏序列",
    "系列の表示・非表示"
  ],
  "Showing all columns": [
    "Showing all columns",
    "正在显示全部列",
    "全列を表示"
  ],
  "Showing core columns": [
    "Showing core columns",
    "正在显示核心列",
    "主要列を表示"
  ],
  "Significance vs equal weight": [
    "Significance vs equal weight",
    "相对等权的显著性",
    "等ウェイトに対する有意性"
  ],
  "Snapshot calendar; past or missing vendor dates may understate event concentration.": [
    "Snapshot calendar; past or missing vendor dates may understate event concentration.",
    "此日历为快照；过期或缺失的供应商日期可能低估事件集中度。",
    "スナップショットのカレンダーです。古い日付や未取得の日付によりイベント集中度を過小評価する可能性があります。"
  ],
  "Source": [
    "Source",
    "来源",
    "出所"
  ],
  "sqrt-time reference": [
    "sqrt-time reference",
    "时间平方根参考",
    "時間平方根による参考値"
  ],
  "Start": [
    "Start",
    "开始",
    "開始"
  ],
  "starting…": [
    "starting…",
    "正在启动…",
    "開始中…"
  ],
  "State": [
    "State",
    "状态",
    "状態"
  ],
  "Stress P&L": [
    "Stress P&L",
    "压力情景损益",
    "ストレス損益"
  ],
  "Stress scenarios: shock, P&L as a percentage of NAV, basis": [
    "Stress scenarios: shock, P&L as a percentage of NAV, basis",
    "压力情景：冲击、损益占净值比例、依据",
    "ストレスシナリオ：ショック、損益のNAV比率、算出根拠"
  ],
  "Stress, betas, events, liquidity and constraints below always refer to the published target book.": [
    "Stress, betas, events, liquidity and constraints below always refer to the published target book.",
    "以下压力测试、贝塔、事件、流动性与约束始终针对已发布的目标组合。",
    "以下のストレス、ベータ、イベント、流動性、制約は、公開済みの目標ポートフォリオを対象とします。"
  ],
  "Style": [
    "Style",
    "风格",
    "スタイル"
  ],
  "sum of executable target weights": [
    "sum of executable target weights",
    "可执行目标权重之和",
    "実行用目標ウェイトの合計"
  ],
  "Surprise": [
    "Surprise",
    "偏离先验",
    "事前分布との差"
  ],
  "Survivorship: the walk-forward universe is today’s constituents, so all arms beat SOXX by construction (BL +{spread}). That spread is not evidence for BL.": [
    "Survivorship: the walk-forward universe is today’s constituents, so all arms beat SOXX by construction (BL +{spread}). That spread is not evidence for BL.",
    "幸存者偏差：滚动前测使用当前成分股，因此所有方案天然优于 SOXX（BL +{spread}）。该差异不是 BL 的有效性证据。",
    "生存者バイアス：ウォークフォワードの対象は現在の構成銘柄なので、全手法が構造上SOXXを上回ります（BL +{spread}）。この差はBLの有効性の証拠ではありません。"
  ],
  "t (HAC) vs prior": [
    "t (HAC) vs prior",
    "相对先验的 t（HAC）",
    "事前分布に対するt（HAC）"
  ],
  "Tangency portfolio on the supplied expected returns.": [
    "Tangency portfolio on the supplied expected returns.",
    "基于输入预期收益的切点组合。",
    "入力された期待リターンによる接点ポートフォリオ。"
  ],
  "Target": [
    "Target",
    "目标",
    "目標"
  ],
  "Target allocation": [
    "Target allocation",
    "目标配比",
    "目標配分"
  ],
  "Target constraints": [
    "Target constraints",
    "目标约束",
    "目標制約"
  ],
  "Target executable w": [
    "Target executable w",
    "可执行目标权重",
    "実行用目標ウェイト"
  ],
  "Target list (top 15)": [
    "Target list (top 15)",
    "目标名单（前 15 只）",
    "目標銘柄リスト（上位15銘柄）"
  ],
  "Target list: executable target weights and model inputs per name": [
    "Target list: executable target weights and model inputs per name",
    "目标名单：各标的的可执行目标权重与模型输入",
    "目標銘柄リスト：銘柄別の実行用目標ウェイトとモデル入力"
  ],
  "Target model w": [
    "Target model w",
    "模型目标权重",
    "モデル目標ウェイト"
  ],
  "Target names reporting within the window": [
    "Target names reporting within the window",
    "窗口内发布财报的目标标的",
    "期間内に決算発表を予定する目標銘柄"
  ],
  "target names with earnings in the next {days} days": [
    "target names with earnings in the next {days} days",
    "未来 {days} 天发布财报的目标标的",
    "今後{days}日以内に決算発表を予定する目標銘柄"
  ],
  "Target w": [
    "Target w",
    "目标权重",
    "目標ウェイト"
  ],
  "Target w (run)": [
    "Target w (run)",
    "目标权重（运行）",
    "目標ウェイト（実行結果）"
  ],
  "Target weight": [
    "Target weight",
    "目标权重",
    "目標ウェイト"
  ],
  "Target weight hash": [
    "Target weight hash",
    "目标权重哈希",
    "目標ウェイトのハッシュ"
  ],
  "Target weights": [
    "Target weights",
    "目标权重",
    "目標ウェイト"
  ],
  "Targets + cash": [
    "Targets + cash",
    "目标权重 + 现金",
    "目標ウェイト + 現金"
  ],
  "tau sensitivity": [
    "tau sensitivity",
    "tau 敏感性",
    "tauの感応度"
  ],
  "TE (ann.)": [
    "TE (ann.)",
    "跟踪误差（年化）",
    "トラッキングエラー（年率）"
  ],
  "TE proxies remain research comparisons": [
    "TE proxies remain research comparisons",
    "代理基准跟踪误差仍仅供研究比较",
    "代理ベンチマークのTEは調査上の比較値です"
  ],
  "TE vs SOXX": [
    "TE vs SOXX",
    "相对 SOXX 的跟踪误差",
    "SOXXに対するトラッキングエラー"
  ],
  "The charts remain visible so prior construction results can be inspected while a current artifact is regenerated or verified.": [
    "The charts remain visible so prior construction results can be inspected while a current artifact is regenerated or verified.",
    "图表保留展示，便于在重新生成或核验当前产物时检查历史构建结果。",
    "現在の結果を再生成・検証する間も、以前の配分結果を確認できるようグラフを表示しています。"
  ],
  "the equilibrium prior only": [
    "the equilibrium prior only",
    "仅均衡先验",
    "均衡事前分布のみ"
  ],
  "the names and weights were chosen with today's information and replayed over the past; this cannot be read as performance": [
    "the names and weights were chosen with today's information and replayed over the past; this cannot be read as performance",
    "标的与权重根据今日信息选择并在过去回放，不可视为业绩",
    "現在の情報で選んだ銘柄とウェイトを過去に適用しており、実績として解釈できません"
  ],
  "the null hypothesis": [
    "the null hypothesis",
    "原假设",
    "帰無仮説"
  ],
  "The production construction is <b>{primary}</b>, which ignores expected returns entirely. BL views move E[r] by ~{tilt} bps on average yet change those weights by {turnover}. <b>To act on BL, use a construction that consumes expected returns</b> (mean-variance or max Sharpe).": [
    "The production construction is <b>{primary}</b>, which ignores expected returns entirely. BL views move E[r] by ~{tilt} bps on average yet change those weights by {turnover}. <b>To act on BL, use a construction that consumes expected returns</b> (mean-variance or max Sharpe).",
    "生产构建方法为 <b>{primary}</b>，完全不使用预期收益。BL 观点平均改变 E[r] 约 {tilt} 基点，但权重变化仅为 {turnover}。<b>若要让 BL 影响配置，需使用预期收益的构建方法</b>（均值–方差或最大夏普比率）。",
    "運用手法 <b>{primary}</b>は期待リターンを使用しません。BLビューはE[r]を平均約{tilt}bps変化させますが、ウェイト変化は {turnover}です。<b>BLを配分に反映するには期待リターンを使用する手法</b>（平均分散または最大シャープレシオ）が必要です。"
  ],
  "The production construction, the best diagnostic construction and equal weight on the same walk-forward record": [
    "The production construction, the best diagnostic construction and equal weight on the same walk-forward record",
    "同一滚动前测记录上的生产方法、最佳诊断方法与等权方案",
    "同一ウォークフォワード記録上の運用手法、診断上の最良手法、等ウェイト"
  ],
  "The Risk matrix below reads a different endpoint and is unaffected.": [
    "The Risk matrix below reads a different endpoint and is unaffected.",
    "下方风险矩阵读取另一接口，不受影响。",
    "下のリスク一覧は別のエンドポイントを利用するため影響を受けません。"
  ],
  "The Sharpe above is ex-ante: (expected annual return − the current risk-free rate) / ex-ante annual volatility, for today's selected, fully-invested target portfolio under today's constraints. The replay table uses historical daily excess returns for a different, changing book, sample and cash rate. Neither figure validates the other. Expected return is a model forecast, not a realised or validated result. Risk-only objectives (minimum variance, minimum CVaR) do not optimise expected return.": [
    "The Sharpe above is ex-ante: (expected annual return − the current risk-free rate) / ex-ante annual volatility, for today's selected, fully-invested target portfolio under today's constraints. The replay table uses historical daily excess returns for a different, changing book, sample and cash rate. Neither figure validates the other. Expected return is a model forecast, not a realised or validated result. Risk-only objectives (minimum variance, minimum CVaR) do not optimise expected return.",
    "上方夏普比率为事前估计：今日约束下全额投资的目标组合，其（预期年化收益 − 当前无风险利率）/ 事前年化波动率。回放表使用历史日度超额收益，组合、样本与现金利率均不同，两者不能相互验证。预期收益是模型预测，并非已实现或已验证的结果。仅风险目标（最小方差、最小 CVaR）不优化预期收益。",
    "上のシャープレシオは事前推定です。現在の制約下で全額投資する目標ポートフォリオの（期待年率リターン − 現在の無リスク金利）/ 事前年率ボラティリティを示します。再現表は異なる配分、標本、現金金利の過去の日次超過リターンを使用しており、互いの妥当性を裏付けません。期待リターンはモデル予測で、実現値や検証済みの結果ではありません。最小分散や最小CVaRなどのリスクのみの目的関数は期待リターンを最適化しません。"
  ],
  "The walk-forward test has not been run.": [
    "The walk-forward test has not been run.",
    "尚未运行滚动前测检验。",
    "ウォークフォワード検証は未実行です。"
  ],
  "Thinnest name by 20-day dollar ADV: {thin} at ${min} (target {weight}); deepest {deep} at ${max}. ": [
    "Thinnest name by 20-day dollar ADV: {thin} at ${min} (target {weight}); deepest {deep} at ${max}. ",
    "20 日平均成交额最少：{thin}，${min}（目标权重 {weight}）；最多：{deep}，${max}。",
    "20日平均売買代金が最小：{thin}、${min}（目標 {weight}）。最大：{deep}、${max}。"
  ],
  "three layers": [
    "three layers",
    "三层报告",
    "3レイヤーで報告"
  ],
  "Ticker": [
    "Ticker",
    "证券代码",
    "銘柄コード"
  ],
  "Tickers": [
    "Tickers",
    "证券代码",
    "銘柄コード"
  ],
  "to adopt BL weights": [
    "to adopt BL weights",
    "采用 BL 权重所需",
    "BLウェイトへの移行に必要"
  ],
  "Top 5 by Sharpe": [
    "Top 5 by Sharpe",
    "夏普比率前 5 名",
    "シャープレシオ上位5手法"
  ],
  "top 5 names = {share} of total contribution · {count} names held at some point": [
    "top 5 names = {share} of total contribution · {count} names held at some point",
    "前 5 只标的占总贡献 {share} · 曾配置 {count} 只标的",
    "上位5銘柄が総寄与の {share} · 期間中に {count}銘柄を保有"
  ],
  "Total": [
    "Total",
    "累计收益",
    "累積リターン"
  ],
  "Tracking error": [
    "Tracking error",
    "跟踪误差",
    "トラッキングエラー"
  ],
  "Tracking error · highest first": [
    "Tracking error · highest first",
    "跟踪误差 · 降序",
    "トラッキングエラー · 高い順"
  ],
  "Tracking error vs": [
    "Tracking error vs",
    "跟踪误差基准",
    "トラッキングエラーの比較対象"
  ],
  "Trade": [
    "Trade",
    "交易",
    "売買"
  ],
  "Trough": [
    "Trough",
    "谷底",
    "底"
  ],
  "Turnover": [
    "Turnover",
    "换手率",
    "回転率"
  ],
  "Turnover per month": [
    "Turnover per month",
    "月度换手率",
    "月次回転率"
  ],
  "Turnover vs prod.": [
    "Turnover vs prod.",
    "相对生产方案的换手率",
    "運用手法に対する回転率"
  ],
  "Turnover vs production": [
    "Turnover vs production",
    "相对生产方案的换手率",
    "運用手法に対する回転率"
  ],
  "Turnover vs Sharpe": [
    "Turnover vs Sharpe",
    "换手率与夏普比率",
    "回転率とシャープレシオ"
  ],
  "Two different Sharpe ratios appear on this page and they are not comparable.": [
    "Two different Sharpe ratios appear on this page and they are not comparable.",
    "本页展示两种不同的夏普比率，二者不可直接比较。",
    "このページの2種類のシャープレシオは、直接比較できません。"
  ],
  "Type": [
    "Type",
    "类型",
    "種類"
  ],
  "unavailable": [
    "unavailable",
    "不可用",
    "利用不可"
  ],
  "Universe": [
    "Universe",
    "股票池",
    "投資対象"
  ],
  "universe list unavailable: {message}": [
    "universe list unavailable: {message}",
    "股票池列表不可用：{message}",
    "投資対象リストを利用できません：{message}"
  ],
  "UNVERIFIED HISTORICAL COMPARISON": [
    "UNVERIFIED HISTORICAL COMPARISON",
    "未经核验的历史比较",
    "未検証の履歴比較"
  ],
  "Upside": [
    "Upside",
    "上行空间",
    "上昇余地"
  ],
  "Use this page to compare construction mechanics only; open “method, gates and caveats” for the complete restrictions and lineage.": [
    "Use this page to compare construction mechanics only; open “method, gates and caveats” for the complete restrictions and lineage.",
    "本页仅用于比较构建机制；请展开“方法、门控与注意事项”查看完整限制与数据溯源。",
    "このページは配分手法の仕組みの比較にのみ使用してください。「手法、利用条件、留意点」に全制限と来歴を表示します。"
  ],
  "uses expected returns": [
    "uses expected returns",
    "使用预期收益",
    "期待リターンを使用"
  ],
  "Value": [
    "Value",
    "价值",
    "バリュー"
  ],
  "VaR & Expected Shortfall": [
    "VaR & Expected Shortfall",
    "风险价值（VaR）与预期损失（ES）",
    "VaRと期待ショートフォール（ES）"
  ],
  "Verdict": [
    "Verdict",
    "结论",
    "評価結果"
  ],
  "View": [
    "View",
    "观点",
    "ビュー"
  ],
  "View ablation": [
    "View ablation",
    "观点消融",
    "ビューのアブレーション"
  ],
  "View checks": [
    "View checks",
    "查看检查",
    "チェックを表示"
  ],
  "View event detail": [
    "View event detail",
    "查看事件详情",
    "イベント詳細を表示"
  ],
  "View name-level betas": [
    "View name-level betas",
    "查看各标的贝塔",
    "銘柄別ベータを表示"
  ],
  "View name-level liquidity": [
    "View name-level liquidity",
    "查看各标的流动性",
    "銘柄別流動性を表示"
  ],
  "View scenarios": [
    "View scenarios",
    "查看情景",
    "シナリオを表示"
  ],
  "View unavailable ledger detail": [
    "View unavailable ledger detail",
    "查看缺失台账说明",
    "台帳未整備の詳細を表示"
  ],
  "Viewing the last stored historical artifact; it is not the current run.": [
    "Viewing the last stored historical artifact; it is not the current run.",
    "当前查看上次保存的历史产物，非当前运行。",
    "最後に保存された過去の結果を表示しています。現在の実行結果ではありません。"
  ],
  "Views are not validated out of sample. Keep them as a small tilt, not a driver, until the live forward IC turns positive.": [
    "Views are not validated out of sample. Keep them as a small tilt, not a driver, until the live forward IC turns positive.",
    "观点尚未经样本外验证。在实时前瞻 IC 转正之前，仅宜作为小幅倾斜，而非主要驱动。",
    "ビューはサンプル外で未検証です。リアルタイムのフォワードICがプラスになるまでは、小さなティルトとして扱ってください。"
  ],
  "Views in force": [
    "Views in force",
    "已启用观点",
    "有効なビュー"
  ],
  "views supplied by us": [
    "views supplied by us",
    "本系统提供的观点融合",
    "このシステムで用意したビューを統合します"
  ],
  "Vol": [
    "Vol",
    "波动率",
    "ボラティリティ"
  ],
  "Vol lab": [
    "Vol lab",
    "实验波动率",
    "ラボのボラティリティ"
  ],
  "Vol target": [
    "Vol target",
    "波动率目标",
    "ボラティリティ目標"
  ],
  "vol-target remainder": [
    "vol-target remainder",
    "波动率目标调整后的剩余比例",
    "ボラティリティ目標適用後の残余"
  ],
  "vol-targeted {target}": [
    "vol-targeted {target}",
    "波动率目标 {target}",
    "ボラティリティ目標 {target}"
  ],
  "Volatility": [
    "Volatility",
    "波动率",
    "ボラティリティ"
  ],
  "w proportional to 1/sigma. Risk parity's diagonal-only approximation.": [
    "w proportional to 1/sigma. Risk parity's diagonal-only approximation.",
    "w 与 1/sigma 成正比。仅使用协方差对角项的风险平价近似。",
    "w は1/sigmaに比例。共分散の対角項のみを用いたリスクパリティの近似。"
  ],
  "Walk-forward not run yet.": [
    "Walk-forward not run yet.",
    "尚未运行滚动前测。",
    "ウォークフォワードは未実行です。"
  ],
  "Walk-forward results withheld.": [
    "Walk-forward results withheld.",
    "滚动前测结果暂不展示。",
    "ウォークフォワード結果は表示を保留しています。"
  ],
  "Walk-forward Sharpe": [
    "Walk-forward Sharpe",
    "滚动前测夏普比率",
    "ウォークフォワードのシャープレシオ"
  ],
  "Walk-forward: does adding views beat the prior?": [
    "Walk-forward: does adding views beat the prior?",
    "滚动前测：加入观点是否优于先验？",
    "ウォークフォワード：ビュー追加は事前分布を上回るか？"
  ],
  "weight × β SOXX: contribution to portfolio beta": [
    "weight × β SOXX: contribution to portfolio beta",
    "权重 × β SOXX：对组合贝塔的贡献",
    "ウェイト × β SOXX：ポートフォリオベータへの寄与"
  ],
  "weight × β SPY: contribution to portfolio beta": [
    "weight × β SPY: contribution to portfolio beta",
    "权重 × β SPY：对组合贝塔的贡献",
    "ウェイト × β SPY：ポートフォリオベータへの寄与"
  ],
  "Weight construction": [
    "Weight construction",
    "权重构建方法",
    "ウェイト配分手法"
  ],
  "Weight sum": [
    "Weight sum",
    "权重合计",
    "ウェイト合計"
  ],
  "Weights": [
    "Weights",
    "权重",
    "ウェイト"
  ],
  "Weights & reconciliation": [
    "Weights & reconciliation",
    "权重与核对",
    "ウェイトと整合性確認"
  ],
  "Weights by construction": [
    "Weights by construction",
    "各方法的权重",
    "配分手法別ウェイト"
  ],
  "weights reconcile": [
    "weights reconcile",
    "权重核对一致",
    "ウェイトが整合"
  ],
  "What this compares · method, gates and caveats": [
    "What this compares · method, gates and caveats",
    "比较范围 · 方法、门控与注意事项",
    "比較対象 · 手法、利用条件、留意点"
  ],
  "Where the views come from": [
    "Where the views come from",
    "观点来源",
    "ビューの出所"
  ],
  "Win rate": [
    "Win rate",
    "胜率",
    "勝率"
  ],
  "Window": [
    "Window",
    "窗口",
    "期間"
  ],
  "window {start} → {end}; rf {rf}; constraints {min}–{max} per name, {segment} per segment. Lab weights are model targets, not positions.": [
    "window {start} → {end}; rf {rf}; constraints {min}–{max} per name, {segment} per segment. Lab weights are model targets, not positions.",
    "窗口 {start} → {end}；无风险利率 {rf}；单标的约束 {min}–{max}，行业分组上限 {segment}。实验权重为模型目标，非实际持仓。",
    "期間 {start} → {end}、無リスク金利 {rf}、銘柄別制約 {min}–{max}、セグメント上限 {segment}。ラボのウェイトはモデル目標で、実保有ではありません。"
  ],
  "Window follows the matrix selection": [
    "Window follows the matrix selection",
    "窗口跟随矩阵选择",
    "期間はマトリクスの選択に従います"
  ],
  "Window metrics for the selected series": [
    "Window metrics for the selected series",
    "选定序列的窗口指标",
    "選択系列の期間内指標"
  ],
  "window unavailable": [
    "window unavailable",
    "窗口不可用",
    "期間を利用できません"
  ],
  "within {days} days": [
    "within {days} days",
    "{days} 天内",
    "{days}日以内"
  ],
  "Worst / best session": [
    "Worst / best session",
    "最差 / 最佳交易日",
    "最悪 / 最良セッション"
  ],
  "Worst mo.": [
    "Worst mo.",
    "最差月份",
    "最悪月"
  ],
  "Worst scenario: {scenario} → {pnl} of NAV{beta}; {count} scenarios, cash {cash} unshocked. Beta shocks are linear approximations on realised betas, not a track record.": [
    "Worst scenario: {scenario} → {pnl} of NAV{beta}; {count} scenarios, cash {cash} unshocked. Beta shocks are linear approximations on realised betas, not a track record.",
    "最差情景：{scenario} → 净值的 {pnl}{beta}；共 {count} 个情景，现金 {cash} 不受冲击。贝塔冲击为历史估计贝塔的线性近似，非业绩记录。",
    "最悪シナリオ：{scenario} → NAVの {pnl}{beta}。{count}シナリオ、現金 {cash}はショック対象外。ベータショックは実現ベータによる線形近似で、実績ではありません。"
  ],
  "yes": [
    "yes",
    "是",
    "はい"
  ],
  "Δ Sharpe": [
    "Δ Sharpe",
    "夏普比率变化",
    "シャープレシオの差"
  ],
  "Δ Vol": [
    "Δ Vol",
    "波动率变化",
    "ボラティリティの差"
  ],
  "τ cancels exactly": [
    "τ cancels exactly",
    "τ 完全抵消",
    "τが厳密に相殺されます"
  ],
  "当前为假设的波动率情景。组合总览和调仓历史链接将打开此方法的股票内部权重。": [
    "This is a hypothetical volatility scenario. Portfolio overview and rebalance-history links show this method’s equity-sleeve weights.",
    "当前为假设的波动率情景。组合总览和调仓历史链接将打开此方法的股票内部权重。",
    "現在は仮想ボラティリティシナリオです。ポートフォリオ概要とリバランス履歴のリンクは、この手法の株式部分の配分を開きます。"
  ],
  "查看此方法的调仓历史 →": [
    "View rebalance history for this method →",
    "查看此方法的调仓历史 →",
    "この手法のリバランス履歴を見る →"
  ],
  "查看此方法的风险 →": [
    "View risk for this method →",
    "查看此方法的风险 →",
    "この手法のリスクを見る →"
  ],
  "组合总览 · Target portfolio": [
    "Portfolio overview · Target portfolio",
    "组合总览 · 目标组合",
    "ポートフォリオ概要 · 目標配分"
  ],
  "风险分析 · Risk review": [
    "Risk review",
    "风险分析",
    "リスク分析"
  ]
});
