/* Display-only translations for MarketIntel research, platform and rebalance UI.
 * Source records, IDs, formulas and numerical values are never translated.
 * Each entry is [English, Simplified Chinese, Japanese]; missing entries retain source text.
 */
window.MI_I18N.register({
  "Operations & existing Stock Select integration": [
    "Operations & existing Stock Select integration",
    "运营与现有股票选股系统集成",
    "運用状況と既存の株式選別システムとの連携"
  ],
  "Paper ledger · frozen inputs · LLM usage · execution readiness": [
    "Paper ledger · frozen inputs · LLM usage · execution readiness",
    "模拟交易账本 · 冻结输入 · LLM 用量 · 执行准备情况",
    "ペーパートレード台帳 · 固定済み入力 · LLM 利用量 · 執行準備状況"
  ],
  "platform execution status": [
    "platform execution status",
    "平台执行状态",
    "プラットフォームの執行状況"
  ],
  "NOT VALIDATED": [
    "NOT VALIDATED",
    "尚未验证",
    "未検証"
  ],
  "PAPER ONLY": [
    "PAPER ONLY",
    "仅模拟交易",
    "ペーパートレードのみ"
  ],
  "NO LIVE BROKER": [
    "NO LIVE BROKER",
    "未连接实盘券商",
    "実取引ブローカー未接続"
  ],
  "reading the platform contract…": [
    "reading the platform contract…",
    "正在读取平台接口契约…",
    "プラットフォームの仕様を読み込み中…"
  ],
  "Where this stands": [
    "Where this stands",
    "当前进展",
    "現在の状況"
  ],
  "rows marked": [
    "rows marked",
    "标记为",
    "以下の表示がある行："
  ],
  "from API": [
    "from API",
    "来自 API",
    "API の応答"
  ],
  "are read from the platform response; rows marked": [
    "are read from the platform response; rows marked",
    "读取自平台响应；标记为",
    "はプラットフォームの応答から取得しています。以下の表示がある行："
  ],
  "engine limit": [
    "engine limit",
    "引擎限制",
    "エンジンの制約"
  ],
  "are documented constraints of the paper engine, not live readings. This console is a research view, not advice, and it cannot reach a broker.": [
    "are documented constraints of the paper engine, not live readings. This console is a research view, not advice, and it cannot reach a broker.",
    "为模拟交易引擎已记录的限制，并非实时读取结果。本控制台仅用于研究展示，不构成投资建议，也无法连接券商。",
    "はペーパートレードエンジンの文書化された制約であり、実測値ではありません。このコンソールは研究用で、投資助言ではなく、ブローカーに接続できません。"
  ],
  "LLM usage & budget": [
    "LLM usage & budget",
    "LLM 用量与预算",
    "LLM 利用量と予算"
  ],
  "what Market Intel itself spends on LLM API calls — read-only, from the usage ledger": [
    "what Market Intel itself spends on LLM API calls — read-only, from the usage ledger",
    "Market Intel 自身的 LLM API 调用支出，来自用量账本，仅供读取",
    "利用量台帳に記録された Market Intel 自身の LLM API 費用（読み取り専用）"
  ],
  "Paper ledger": [
    "Paper ledger",
    "模拟交易账本",
    "ペーパートレード台帳"
  ],
  "virtual PAPER/SHADOW sessions · virtual cash and virtual positions in an append-only ledger · no broker connection exists": [
    "virtual PAPER/SHADOW sessions · virtual cash and virtual positions in an append-only ledger · no broker connection exists",
    "虚拟 PAPER/SHADOW 会话 · 仅追加账本中的虚拟现金与持仓 · 未连接券商",
    "仮想の PAPER/SHADOW セッション · 追記専用台帳の仮想現金と仮想ポジション · ブローカー接続なし"
  ],
  "Step": [
    "Step",
    "执行一步",
    "1 ステップ実行"
  ],
  "Kill switch": [
    "Kill switch",
    "紧急停止开关",
    "緊急停止スイッチ"
  ],
  "Paper engine limits.": [
    "Paper engine limits.",
    "模拟交易引擎限制。",
    "ペーパートレードエンジンの制約。"
  ],
  "Execution is": [
    "Execution is",
    "执行仅限",
    "執行は"
  ],
  "intraday only": [
    "intraday only",
    "日内交易",
    "日中取引のみ"
  ],
  ": an order or a mark that crosses a UTC session date fails closed until trading-calendar, dividend, split, symbol-change and financing accounting are connected. Short selling and margin are refused. These are documented engine limits, not values read from the API.": [
    ": an order or a mark that crosses a UTC session date fails closed until trading-calendar, dividend, split, symbol-change and financing accounting are connected. Short selling and margin are refused. These are documented engine limits, not values read from the API.",
    "：在接入交易日历、股息、拆股、代码变更和融资核算前，跨 UTC 交易日的订单或估值将被拒绝。做空与保证金交易也被拒绝。这些是已记录的引擎限制，并非 API 读取值。",
    "です。取引カレンダー、配当、株式分割、銘柄コード変更、資金調達の会計処理が接続されるまで、UTC の取引日をまたぐ注文や時価評価は安全のため拒否されます。空売りと証拠金取引も拒否されます。これらは文書化されたエンジンの制約で、API から取得した値ではありません。"
  ],
  "Start a virtual paper session": [
    "Start a virtual paper session",
    "创建虚拟模拟交易会话",
    "仮想ペーパートレードセッションを開始"
  ],
  "Starting cash": [
    "Starting cash",
    "初始现金",
    "開始時の現金"
  ],
  "Currency": [
    "Currency",
    "币种",
    "通貨"
  ],
  "Max gross leverage": [
    "Max gross leverage",
    "最大总杠杆",
    "最大グロスレバレッジ"
  ],
  "Max quote age (s)": [
    "Max quote age (s)",
    "报价最长有效时间（秒）",
    "許容する気配値の経過時間（秒）"
  ],
  "allow short": [
    "allow short",
    "允许做空",
    "空売りを許可"
  ],
  "allow margin": [
    "allow margin",
    "允许保证金交易",
    "証拠金取引を許可"
  ],
  "refused": [
    "refused",
    "已拒绝",
    "拒否"
  ],
  "Start paper session": [
    "Start paper session",
    "创建模拟交易会话",
    "ペーパートレードを開始"
  ],
  "Currency is fixed to": [
    "Currency is fixed to",
    "币种固定为",
    "通貨は"
  ],
  ": quote-currency and point-in-time FX conversion are not implemented, and the API refuses any other currency. Every session is submitted as": [
    ": quote-currency and point-in-time FX conversion are not implemented, and the API refuses any other currency. Every session is submitted as",
    "：尚未实现报价币种和历史时点汇率转换，API 拒绝其他币种。每个会话均以以下币种提交",
    "に固定されています。気配値の通貨処理と時点整合的な為替換算が未実装のため、API は他の通貨を拒否します。すべてのセッションは次の通貨で送信されます："
  ],
  "The platform API refuses": [
    "The platform API refuses",
    "平台 API 拒绝",
    "プラットフォーム API は次の設定を拒否します："
  ],
  "and": [
    "and",
    "和",
    "および"
  ],
  "until borrow/locate, financing, collateral and dividend/corporate-action accounting are implemented. Every session is submitted with both set to": [
    "until borrow/locate, financing, collateral and dividend/corporate-action accounting are implemented. Every session is submitted with both set to",
    "，直至实现借券与券源确认、融资、抵押品以及股息与公司行动核算。每个会话均将这两个参数设为",
    "借株と貸株確保、資金調達、担保、配当とコーポレートアクションの会計処理が実装されるまで使用できません。すべてのセッションでは、両方の設定を次の値にします："
  ],
  "Frozen run & shared data plane": [
    "Frozen run & shared data plane",
    "冻结运行与共享数据层",
    "固定済み実行と共有データ基盤"
  ],
  "the immutable run context the platform would execute against, its lineage, and what the registry and dataset catalog actually hold": [
    "the immutable run context the platform would execute against, its lineage, and what the registry and dataset catalog actually hold",
    "平台执行所依据的不可变运行上下文、数据血缘，以及注册表和数据集目录的实际内容",
    "執行の基準となる変更不可の実行コンテキスト、その来歴、レジストリとデータセットカタログの実際の登録内容"
  ],
  "Risk & attribution": [
    "Risk & attribution",
    "风险与归因",
    "リスクと要因分析"
  ],
  "only what the platform risk interface returns — an empty tile means the number does not exist yet, never a substituted value": [
    "only what the platform risk interface returns — an empty tile means the number does not exist yet, never a substituted value",
    "仅展示平台风险接口返回的内容；空白指标卡表示该数值尚不存在，绝不以替代值填充",
    "プラットフォームのリスク API が返した内容のみを表示します。空の指標カードは数値がまだ存在しないことを示し、代替値は使用しません。"
  ],
  "Strategy registry": [
    "Strategy registry",
    "策略注册表",
    "戦略レジストリ"
  ],
  "planned strategies and whether an adapter is actually installed": [
    "planned strategies and whether an adapter is actually installed",
    "计划中的策略及其适配器是否已实际安装",
    "計画中の戦略とアダプターの実装状況"
  ],
  "Registered runs": [
    "Registered runs",
    "已登记运行",
    "登録済みの実行"
  ],
  "most recent first, from the platform run registry": [
    "most recent first, from the platform run registry",
    "来自平台运行注册表，按时间倒序排列",
    "プラットフォームの実行レジストリから取得し、新しい順に表示"
  ],
  "Import a strategy result": [
    "Import a strategy result",
    "导入策略结果",
    "戦略の結果をインポート"
  ],
  "上传月度净收益 JSON，或两列 CSV：": [
    "Upload monthly net returns as JSON or a two-column CSV: ",
    "上传月度净收益 JSON，或两列 CSV：",
    "月次のコスト控除後リターンを JSON、または 2 列の CSV でアップロード："
  ],
  "。0.01 表示 1%。完整月份须连续；元数据和原始文件指纹随结果保存。": [
    ". 0.01 means 1%. Complete months must be consecutive; metadata and the original file fingerprint are saved with the result.",
    "。0.01 表示 1%。完整月份须连续；元数据和原始文件指纹随结果保存。",
    "。0.01 は 1% を表します。完全な月のデータが連続している必要があります。メタデータと元ファイルのフィンガープリントも結果とともに保存します。"
  ],
  "File · JSON / CSV": [
    "File · JSON / CSV",
    "文件 · JSON / CSV",
    "ファイル · JSON / CSV"
  ],
  "Strategy ID": [
    "Strategy ID",
    "策略 ID",
    "戦略 ID"
  ],
  "Display name": [
    "Display name",
    "显示名称",
    "表示名"
  ],
  "Variant": [
    "Variant",
    "版本方案",
    "バリアント"
  ],
  "Run ID": [
    "Run ID",
    "运行 ID",
    "実行 ID"
  ],
  "Asset class": [
    "Asset class",
    "资产类别",
    "資産クラス"
  ],
  "Calendar": [
    "Calendar",
    "交易日历",
    "取引カレンダー"
  ],
  "Sample": [
    "Sample",
    "样本类型",
    "サンプル種別"
  ],
  "Historical research": [
    "Historical research",
    "历史研究",
    "過去データによる研究"
  ],
  "Source-declared paper": [
    "Source-declared paper",
    "源文件标注的模拟交易",
    "ソースが申告したペーパートレード"
  ],
  "Source-declared live": [
    "Source-declared live",
    "源文件标注的实盘交易",
    "ソースが申告した実取引"
  ],
  "Cost assumptions": [
    "Cost assumptions",
    "成本假设",
    "コストの前提"
  ],
  "Signal & execution clock": [
    "Signal & execution clock",
    "信号与执行时点",
    "シグナルと執行の時点"
  ],
  "Return construction": [
    "Return construction",
    "收益构建口径",
    "リターンの算出方法"
  ],
  "Research limitations": [
    "Research limitations",
    "研究局限",
    "研究上の制約"
  ],
  "JSON 自带上述字段。CSV 使用表单字段。Paper / live 标签来自源文件，平台未独立认证。": [
    "JSON supplies the fields above. CSV uses the form fields. Paper / live labels are declared by the source and are not independently certified by the platform.",
    "JSON 自带上述字段。CSV 使用表单字段。Paper / live 标签来自源文件，平台未独立认证。",
    "JSON では上記の項目をファイルから取得し、CSV ではフォームの入力を使用します。Paper / live の区分はソースの申告に基づき、プラットフォームが独立に認証したものではありません。"
  ],
  "当前会话可读取和计算。导入需要现有 QuantOps / Admin 账号：": [
    "This session can read and calculate. Import requires an existing QuantOps / Admin account: ",
    "当前会话可读取和计算。导入需要现有 QuantOps / Admin 账号：",
    "現在のセッションでは閲覧と計算ができます。インポートには既存の QuantOps / Admin アカウントが必要です："
  ],
  "Sign in": [
    "Sign in",
    "登录",
    "サインイン"
  ],
  "Import frozen result": [
    "Import frozen result",
    "导入冻结结果",
    "固定済みの結果をインポート"
  ],
  "JSON template": [
    "JSON template",
    "JSON 模板",
    "JSON テンプレート"
  ],
  "Close": [
    "Close",
    "关闭",
    "閉じる"
  ],
  "Activate the paper kill switch": [
    "Activate the paper kill switch",
    "启用模拟交易紧急停止",
    "ペーパートレードの緊急停止を有効化"
  ],
  "This stops further virtual orders on the selected paper session. It is recorded in the append-only ledger and cannot be undone from this page. It has no effect outside the simulation.": [
    "This stops further virtual orders on the selected paper session. It is recorded in the append-only ledger and cannot be undone from this page. It has no effect outside the simulation.",
    "这将停止所选模拟交易会话的后续虚拟订单。操作会记录在仅追加账本中，无法在本页面撤销。该操作仅影响模拟环境。",
    "選択したペーパートレードセッションの以後の仮想注文を停止します。追記専用台帳に記録され、このページからは取り消せません。シミュレーション外には影響しません。"
  ],
  "Reason (required)": [
    "Reason (required)",
    "原因（必填）",
    "理由（必須）"
  ],
  "why the session is being stopped": [
    "why the session is being stopped",
    "说明停止此会话的原因",
    "セッションを停止する理由"
  ],
  "Cancel": [
    "Cancel",
    "取消",
    "キャンセル"
  ],
  "Activate kill switch": [
    "Activate kill switch",
    "启用紧急停止",
    "緊急停止を有効化"
  ],
  "Reference adapter": [
    "Reference adapter",
    "参考适配器",
    "参照アダプター"
  ],
  "Adapter not installed": [
    "Adapter not installed",
    "适配器未安装",
    "アダプター未実装"
  ],
  "VaR 99% 1-day": [
    "VaR 99% 1-day",
    "1 日 VaR（99%）",
    "1 日 VaR（99%）"
  ],
  "VaR 99% 10-day": [
    "VaR 99% 10-day",
    "10 日 VaR（99%）",
    "10 日 VaR（99%）"
  ],
  "Expected Shortfall 97.5%": [
    "Expected Shortfall 97.5%",
    "预期损失（97.5%）",
    "期待ショートフォール（97.5%）"
  ],
  "Tracking Error": [
    "Tracking Error",
    "跟踪误差",
    "トラッキングエラー"
  ],
  "of NAV": [
    "of NAV",
    "占净值比例",
    "純資産総額に対する割合"
  ],
  "annualized": [
    "annualized",
    "年化",
    "年率換算"
  ],
  "Fills were processed and new virtual orders were submitted.": [
    "Fills were processed and new virtual orders were submitted.",
    "已处理成交并提交新的虚拟订单。",
    "約定を処理し、新たな仮想注文を送信しました。"
  ],
  "Open virtual orders were filled. No new order was needed.": [
    "Open virtual orders were filled. No new order was needed.",
    "未完成的虚拟订单已成交，无需新增订单。",
    "未約定の仮想注文が約定しました。新たな注文は不要でした。"
  ],
  "New virtual orders were submitted. Nothing filled in this step.": [
    "New virtual orders were submitted. Nothing filled in this step.",
    "已提交新的虚拟订单；本步没有成交。",
    "新たな仮想注文を送信しました。このステップでの約定はありません。"
  ],
  "Nothing changed: the virtual book already matches the frozen target.": [
    "Nothing changed: the virtual book already matches the frozen target.",
    "无变化：虚拟持仓已与冻结目标一致。",
    "変更はありません。仮想ポジションはすでに固定済み目標と一致しています。"
  ],
  "yes": [
    "yes",
    "是",
    "はい"
  ],
  "no": [
    "no",
    "否",
    "いいえ"
  ],
  "Copy": [
    "Copy",
    "复制",
    "コピー"
  ],
  "Show full": [
    "Show full",
    "显示完整值",
    "全体を表示"
  ],
  "Hide": [
    "Hide",
    "收起",
    "折りたたむ"
  ],
  "Copied": [
    "Copied",
    "已复制",
    "コピーしました"
  ],
  "Copy failed": [
    "Copy failed",
    "复制失败",
    "コピーに失敗しました"
  ],
  "Copied the full value to the clipboard": [
    "Copied the full value to the clipboard",
    "已将完整值复制到剪贴板",
    "値全体をクリップボードにコピーしました"
  ],
  "Copy failed — select the value and copy it manually": [
    "Copy failed — select the value and copy it manually",
    "复制失败，请选中该值后手动复制",
    "コピーに失敗しました。値を選択して手動でコピーしてください。"
  ],
  "Full value shown": [
    "Full value shown",
    "已显示完整值",
    "値全体を表示しました"
  ],
  "Value truncated": [
    "Value truncated",
    "已缩略显示",
    "省略表示にしました"
  ],
  "↻ Refresh": [
    "↻ Refresh",
    "↻ 刷新",
    "↻ 更新"
  ],
  "Refresh platform data": [
    "Refresh platform data",
    "刷新平台数据",
    "プラットフォームのデータを更新"
  ],
  "run: none": [
    "run: none",
    "运行：无",
    "実行：なし"
  ],
  "NOT ACTIONABLE": [
    "NOT ACTIONABLE",
    "不可执行",
    "執行不可"
  ],
  "Can the research be used right now?": [
    "Can the research be used right now?",
    "目前可以使用这些研究结果吗？",
    "この研究結果を今すぐ利用できますか？"
  ],
  "What is the frozen run?": [
    "What is the frozen run?",
    "冻结运行是什么？",
    "固定済みの実行とは何ですか？"
  ],
  "Is data lineage complete?": [
    "Is data lineage complete?",
    "数据血缘是否完整？",
    "データの来歴は完全ですか？"
  ],
  "Is paper / shadow running?": [
    "Is paper / shadow running?",
    "模拟／影子交易是否在运行？",
    "ペーパー／シャドートレードは稼働していますか？"
  ],
  "What can the paper engine do?": [
    "What can the paper engine do?",
    "模拟交易引擎支持哪些操作？",
    "ペーパートレードエンジンは何ができますか？"
  ],
  "What is the next blocker?": [
    "What is the next blocker?",
    "下一个阻碍是什么？",
    "次の阻害要因は何ですか？"
  ],
  "There is no frozen run context to use.": [
    "There is no frozen run context to use.",
    "没有可用的冻结运行上下文。",
    "利用できる固定済み実行コンテキストがありません。"
  ],
  "The API reports no blockers.": [
    "The API reports no blockers.",
    "API 未报告阻碍。",
    "API から阻害要因の報告はありません。"
  ],
  "No blockers reported by the API.": [
    "No blockers reported by the API.",
    "API 未报告阻碍。",
    "API から阻害要因の報告はありません。"
  ],
  "The API reported no required dataset roles.": [
    "The API reported no required dataset roles.",
    "API 未报告必需的数据集角色。",
    "API から必須のデータセットの役割は報告されていません。"
  ],
  "bound to a content-addressed version": [
    "bound to a content-addressed version",
    "已绑定内容寻址版本",
    "内容アドレスで識別するバージョンに紐付け済み"
  ],
  "MISSING": [
    "MISSING",
    "缺失",
    "欠落"
  ],
  "no content-addressed version is bound for this role": [
    "no content-addressed version is bound for this role",
    "此角色未绑定内容寻址版本",
    "この役割には内容アドレスで識別するバージョンが紐付けられていません"
  ],
  "The platform reports no frozen run context. Run identity, context hash and lineage cannot be shown.": [
    "The platform reports no frozen run context. Run identity, context hash and lineage cannot be shown.",
    "平台未报告冻结运行上下文，无法显示运行标识、上下文哈希与数据血缘。",
    "プラットフォームに固定済み実行コンテキストがないため、実行 ID、コンテキストハッシュ、来歴を表示できません。"
  ],
  "Frozen dataset lineage": [
    "Frozen dataset lineage",
    "冻结数据集血缘",
    "固定済みデータセットの来歴"
  ],
  "complete": [
    "complete",
    "完整",
    "完全"
  ],
  "incomplete": [
    "incomplete",
    "不完整",
    "不完全"
  ],
  "roles bound": [
    "roles bound",
    "已绑定角色",
    "紐付け済みの役割"
  ],
  "reason": [
    "reason",
    "原因",
    "理由"
  ],
  "Registry": [
    "Registry",
    "注册表",
    "レジストリ"
  ],
  "Strategies": [
    "Strategies",
    "策略",
    "戦略"
  ],
  "Runs": [
    "Runs",
    "运行",
    "実行"
  ],
  "Artifacts": [
    "Artifacts",
    "产物",
    "成果物"
  ],
  "Shared dataset catalog": [
    "Shared dataset catalog",
    "共享数据集目录",
    "共有データセットカタログ"
  ],
  "Requests": [
    "Requests",
    "请求",
    "リクエスト"
  ],
  "Versions": [
    "Versions",
    "版本",
    "バージョン"
  ],
  "Unique payloads": [
    "Unique payloads",
    "唯一数据载荷",
    "一意のペイロード"
  ],
  "By finality": [
    "By finality",
    "按确认状态",
    "確定状況別"
  ],
  "Capabilities": [
    "Capabilities",
    "能力",
    "機能"
  ],
  "Strategy": [
    "Strategy",
    "策略",
    "戦略"
  ],
  "Asset classes": [
    "Asset classes",
    "资产类别",
    "資産クラス"
  ],
  "State": [
    "State",
    "状态",
    "状態"
  ],
  "Implementation": [
    "Implementation",
    "实现状态",
    "実装状況"
  ],
  "Version": [
    "Version",
    "版本",
    "バージョン"
  ],
  "Paper": [
    "Paper",
    "模拟交易",
    "ペーパートレード"
  ],
  "Installed": [
    "Installed",
    "已安装",
    "実装済み"
  ],
  "Planned": [
    "Planned",
    "计划中",
    "計画中"
  ],
  "no strategies returned": [
    "no strategies returned",
    "未返回策略",
    "戦略が返されていません"
  ],
  "Generated": [
    "Generated",
    "生成时间",
    "生成日時"
  ],
  "Run": [
    "Run",
    "运行",
    "実行"
  ],
  "Context hash": [
    "Context hash",
    "上下文哈希",
    "コンテキストハッシュ"
  ],
  "Validation": [
    "Validation",
    "验证",
    "検証"
  ],
  "no runs registered": [
    "no runs registered",
    "尚未登记运行",
    "実行が登録されていません"
  ],
  "unavailable": [
    "unavailable",
    "不可用",
    "取得不可"
  ],
  "available": [
    "available",
    "可用",
    "取得可能"
  ],
  "Risk workbench": [
    "Risk workbench",
    "风险工作台",
    "リスクワークベンチ"
  ],
  "Construction scope": [
    "Construction scope",
    "组合构建范围",
    "ポートフォリオ構築の対象範囲"
  ],
  "algorithm scope": [
    "algorithm scope",
    "算法范围",
    "アルゴリズムの対象範囲"
  ],
  "landed weights": [
    "landed weights",
    "落地权重",
    "執行に使用する確定済みウェイト"
  ],
  "layer": [
    "layer",
    "层级",
    "レイヤー"
  ],
  "requested window": [
    "requested window",
    "请求窗口",
    "指定した期間"
  ],
  "weight identity": [
    "weight identity",
    "权重标识",
    "ウェイトの識別情報"
  ],
  "Binding": [
    "Binding",
    "绑定",
    "紐付け"
  ],
  "payload schema": [
    "payload schema",
    "数据载荷模式",
    "ペイロードのスキーマ"
  ],
  "strategy": [
    "strategy",
    "策略",
    "戦略"
  ],
  "portfolio": [
    "portfolio",
    "组合",
    "ポートフォリオ"
  ],
  "Calculation lineage": [
    "Calculation lineage",
    "计算血缘",
    "計算の来歴"
  ],
  "frozen run reconciliation": [
    "frozen run reconciliation",
    "冻结运行对账",
    "固定済み実行との照合"
  ],
  "frozen inputs consumed": [
    "frozen inputs consumed",
    "已使用冻结输入",
    "固定済み入力を使用"
  ],
  "legacy pipeline manifest — not a content-addressed lineage": [
    "legacy pipeline manifest — not a content-addressed lineage",
    "旧版流水线清单，不属于内容寻址血缘",
    "従来のパイプラインマニフェスト（内容アドレスによる来歴ではありません）"
  ],
  "Frozen risk inputs": [
    "Frozen risk inputs",
    "冻结风险输入",
    "固定済みリスク計算の入力"
  ],
  "Why the four metrics do not exist": [
    "Why the four metrics do not exist",
    "为何这四项指标不存在",
    "4 つの指標が存在しない理由"
  ],
  "span requested": [
    "span requested",
    "请求时间范围",
    "指定した日付範囲"
  ],
  "sessions available": [
    "sessions available",
    "可用交易日数",
    "利用可能な取引日数"
  ],
  "trailing uninterrupted sessions": [
    "trailing uninterrupted sessions",
    "最近连续交易日数",
    "直近の連続した取引日数"
  ],
  "limiting holding": [
    "limiting holding",
    "受限持仓",
    "制約となる保有銘柄"
  ],
  "shorter window or substitute": [
    "shorter window or substitute",
    "缩短窗口或替代数据",
    "期間の短縮または代替データ"
  ],
  "refused — no fallback sample was used": [
    "refused — no fallback sample was used",
    "已拒绝，未使用替代样本",
    "拒否：代替サンプルは使用していません"
  ],
  "Limiting holdings": [
    "Limiting holdings",
    "受限持仓",
    "制約となる保有銘柄"
  ],
  "Ticker": [
    "Ticker",
    "股票代码",
    "ティッカー"
  ],
  "First bar": [
    "First bar",
    "首个行情数据点",
    "最初の価格データ"
  ],
  "Required start": [
    "Required start",
    "所需起始日期",
    "必要な開始日"
  ],
  "Reason": [
    "Reason",
    "原因",
    "理由"
  ],
  "no limiting holdings were reported": [
    "no limiting holdings were reported",
    "未报告受限持仓",
    "制約となる保有銘柄の報告はありません"
  ],
  "Attribution": [
    "Attribution",
    "归因",
    "要因分析"
  ],
  "Attribution components": [
    "Attribution components",
    "归因分项",
    "要因分析の内訳"
  ],
  "Component": [
    "Component",
    "分项",
    "項目"
  ],
  "Contribution": [
    "Contribution",
    "贡献",
    "寄与"
  ],
  "Metadata": [
    "Metadata",
    "元数据",
    "メタデータ"
  ],
  "Independent ledger witness": [
    "Independent ledger witness",
    "独立账本见证",
    "独立した台帳の検証記録"
  ],
  "The API returned no external checkpoint status for this session.": [
    "The API returned no external checkpoint status for this session.",
    "API 未返回此会话的外部检查点状态。",
    "このセッションの外部チェックポイントの状態が API から返されていません。"
  ],
  "not configured": [
    "not configured",
    "未配置",
    "未設定"
  ],
  "synchronized": [
    "synchronized",
    "已同步",
    "同期済み"
  ],
  "configured, not synchronized": [
    "configured, not synchronized",
    "已配置，尚未同步",
    "設定済み、未同期"
  ],
  "independent trust domain required": [
    "independent trust domain required",
    "需要独立信任域",
    "独立した信頼領域が必要"
  ],
  "configured": [
    "configured",
    "已配置",
    "設定済み"
  ],
  "witness sequence": [
    "witness sequence",
    "见证序号",
    "検証記録のシーケンス番号"
  ],
  "witness event_hash": [
    "witness event_hash",
    "见证 event_hash",
    "検証記録の event_hash"
  ],
  "witness timestamp": [
    "witness timestamp",
    "见证时间戳",
    "検証記録のタイムスタンプ"
  ],
  "local ledger sequence": [
    "local ledger sequence",
    "本地账本序号",
    "ローカル台帳のシーケンス番号"
  ],
  "local ledger event_hash": [
    "local ledger event_hash",
    "本地账本 event_hash",
    "ローカル台帳の event_hash"
  ],
  "rolling window length not reported": [
    "rolling window length not reported",
    "未报告滚动窗口长度",
    "ローリング期間の長さが報告されていません"
  ],
  "What this is.": [
    "What this is.",
    "说明。",
    "表示内容について。"
  ],
  "Market Intel’s own LLM API calls — event classification, briefs and Ask — as metered in its usage ledger.": [
    "Market Intel’s own LLM API calls — event classification, briefs and Ask — as metered in its usage ledger.",
    "Market Intel 自身的 LLM API 调用，包括事件分类、简报与问答，按用量账本记录统计。",
    "Market Intel 自身による LLM API 呼び出し（イベント分類、ブリーフィング、質問）の利用量台帳に基づく集計です。"
  ],
  "not": [
    "not",
    "不属于",
    "該当しません："
  ],
  "the token count of an interactive chat session, and it is not a provider invoice.": [
    "the token count of an interactive chat session, and it is not a provider invoice.",
    "交互式聊天会话的 token 数，也不属于服务商账单。",
    "対話型チャットセッションのトークン数や、プロバイダーの請求書。"
  ],
  "Budget not configured": [
    "Budget not configured",
    "预算未配置",
    "予算未設定"
  ],
  "no token limit is set for this deployment, so nothing is capped": [
    "no token limit is set for this deployment, so nothing is capped",
    "此部署未设置 token 上限，因此不限制用量",
    "この環境ではトークン上限が設定されていないため、利用量を制限していません"
  ],
  "Budget state unknown": [
    "Budget state unknown",
    "预算状态未知",
    "予算の状態は不明"
  ],
  "the usage ledger could not be read, so neither the limit nor the usage can be reported": [
    "the usage ledger could not be read, so neither the limit nor the usage can be reported",
    "无法读取用量账本，因此无法报告上限或用量",
    "利用量台帳を読み取れないため、上限と利用量を報告できません"
  ],
  "tokens used of the configured window budget": [
    "tokens used of the configured window budget",
    "已使用 token／已配置窗口预算",
    "設定された期間予算に対するトークン使用量"
  ],
  "Cost": [
    "Cost",
    "成本",
    "費用"
  ],
  "recorded known cost": [
    "recorded known cost",
    "已记录的已知成本",
    "記録済みの確認可能な費用"
  ],
  "calls without a recorded cost": [
    "calls without a recorded cost",
    "未记录成本的调用",
    "費用が記録されていない呼び出し"
  ],
  "current pricing match": [
    "current pricing match",
    "当前定价匹配情况",
    "現在の料金との一致状況"
  ],
  "Recorded known cost is what the usage ledger itself priced for the calls in this window. It is not a provider invoice, and no price on this page is inferred or estimated.": [
    "Recorded known cost is what the usage ledger itself priced for the calls in this window. It is not a provider invoice, and no price on this page is inferred or estimated.",
    "已记录的已知成本来自用量账本对本窗口调用的计价，并非服务商账单。本页面不会推算或估计价格。",
    "確認可能な記録済み費用は、この期間の呼び出しについて利用量台帳が算定した金額です。プロバイダーの請求書ではなく、このページでは価格の推測や見積もりを行いません。"
  ],
  "cost budget": [
    "cost budget",
    "成本预算",
    "費用予算"
  ],
  "configured limit, not a measured spend": [
    "configured limit, not a measured spend",
    "配置上限，并非实测支出",
    "設定された上限であり、実際の支出額ではありません"
  ],
  "known used": [
    "known used",
    "已知已用金额",
    "確認可能な使用額"
  ],
  "remaining": [
    "remaining",
    "剩余",
    "残額"
  ],
  "status": [
    "status",
    "状态",
    "状態"
  ],
  "Totals in the window": [
    "Totals in the window",
    "本窗口合计",
    "期間内の合計"
  ],
  "Calls": [
    "Calls",
    "调用次数",
    "呼び出し回数"
  ],
  "Input tokens": [
    "Input tokens",
    "输入 token",
    "入力トークン"
  ],
  "Output tokens": [
    "Output tokens",
    "输出 token",
    "出力トークン"
  ],
  "Cache tokens": [
    "Cache tokens",
    "缓存 token",
    "キャッシュトークン"
  ],
  "Total tokens": [
    "Total tokens",
    "token 总数",
    "合計トークン"
  ],
  "LLM usage by provider, model and operation": [
    "LLM usage by provider, model and operation",
    "按服务商、模型与操作统计的 LLM 用量",
    "プロバイダー・モデル・操作別の LLM 利用量"
  ],
  "Provider": [
    "Provider",
    "服务商",
    "プロバイダー"
  ],
  "Model": [
    "Model",
    "模型",
    "モデル"
  ],
  "Operation": [
    "Operation",
    "操作",
    "操作"
  ],
  "Input": [
    "Input",
    "输入",
    "入力"
  ],
  "Output": [
    "Output",
    "输出",
    "出力"
  ],
  "Cache": [
    "Cache",
    "缓存",
    "キャッシュ"
  ],
  "Total": [
    "Total",
    "合计",
    "合計"
  ],
  "Known cost": [
    "Known cost",
    "已知成本",
    "確認可能な費用"
  ],
  "Cost status": [
    "Cost status",
    "成本状态",
    "費用の状態"
  ],
  "no metered calls in the window": [
    "no metered calls in the window",
    "本窗口没有已计量调用",
    "この期間に計測された呼び出しはありません"
  ],
  "Recent calls": [
    "Recent calls",
    "最近调用",
    "最近の呼び出し"
  ],
  "Recent LLM calls": [
    "Recent LLM calls",
    "最近 LLM 调用",
    "最近の LLM 呼び出し"
  ],
  "When": [
    "When",
    "时间",
    "日時"
  ],
  "Status": [
    "Status",
    "状态",
    "状態"
  ],
  "Usage reported": [
    "Usage reported",
    "已报告用量",
    "利用量の報告あり"
  ],
  "no calls recorded yet": [
    "no calls recorded yet",
    "尚无调用记录",
    "呼び出しはまだ記録されていません"
  ],
  "No virtual paper sessions exist yet.": [
    "No virtual paper sessions exist yet.",
    "尚无虚拟模拟交易会话。",
    "仮想ペーパートレードセッションはまだありません。"
  ],
  "Select a session above to see its ledger.": [
    "Select a session above to see its ledger.",
    "选择上方会话以查看账本。",
    "上のセッションを選択して台帳を表示してください。"
  ],
  "Fixed book binding": [
    "Fixed book binding",
    "固定组合绑定",
    "固定されたポートフォリオの紐付け"
  ],
  "This ledger carries no book binding.": [
    "This ledger carries no book binding.",
    "此账本未绑定组合。",
    "この台帳にはポートフォリオの紐付けがありません。"
  ],
  "The binding is fixed for the life of the session: runs may only advance inside this book, and an order from a different frozen run or context is refused.": [
    "The binding is fixed for the life of the session: runs may only advance inside this book, and an order from a different frozen run or context is refused.",
    "会话存续期间组合绑定固定不变：运行只能在此组合内推进，来自其他冻结运行或上下文的订单将被拒绝。",
    "セッション中は紐付けが固定されます。実行はこのポートフォリオ内でのみ進められ、別の固定済み実行やコンテキストに基づく注文は拒否されます。"
  ],
  "Append-only ledger head": [
    "Append-only ledger head",
    "仅追加账本头部",
    "追記専用台帳の最新状態"
  ],
  "sequence": [
    "sequence",
    "序号",
    "シーケンス番号"
  ],
  "timestamp": [
    "timestamp",
    "时间戳",
    "タイムスタンプ"
  ],
  "checkpoint": [
    "checkpoint",
    "检查点",
    "チェックポイント"
  ],
  "operation receipts": [
    "operation receipts",
    "操作回执",
    "操作の受領記録"
  ],
  "open orders": [
    "open orders",
    "未完成订单",
    "未約定注文"
  ],
  "Virtual cash": [
    "Virtual cash",
    "虚拟现金",
    "仮想現金"
  ],
  "Market value": [
    "Market value",
    "市值",
    "時価"
  ],
  "Equity": [
    "Equity",
    "权益",
    "資産総額"
  ],
  "Gross exposure": [
    "Gross exposure",
    "总敞口",
    "グロスエクスポージャー"
  ],
  "Gross leverage": [
    "Gross leverage",
    "总杠杆",
    "グロスレバレッジ"
  ],
  "Virtual positions": [
    "Virtual positions",
    "虚拟持仓",
    "仮想ポジション"
  ],
  "Symbol": [
    "Symbol",
    "代码",
    "銘柄コード"
  ],
  "Quantity": [
    "Quantity",
    "数量",
    "数量"
  ],
  "Average cost": [
    "Average cost",
    "平均成本",
    "平均取得価格"
  ],
  "Mark": [
    "Mark",
    "估值价格",
    "評価価格"
  ],
  "Mark time": [
    "Mark time",
    "估值时间",
    "評価時点"
  ],
  "Mark status": [
    "Mark status",
    "估值状态",
    "評価価格の状態"
  ],
  "Unrealised": [
    "Unrealised",
    "未实现损益",
    "未実現損益"
  ],
  "current": [
    "current",
    "当前有效",
    "最新"
  ],
  "no virtual positions": [
    "no virtual positions",
    "没有虚拟持仓",
    "仮想ポジションはありません"
  ],
  "Orders": [
    "Orders",
    "订单",
    "注文"
  ],
  "Virtual orders": [
    "Virtual orders",
    "虚拟订单",
    "仮想注文"
  ],
  "Order": [
    "Order",
    "订单",
    "注文"
  ],
  "Side": [
    "Side",
    "买卖方向",
    "売買区分"
  ],
  "Filled": [
    "Filled",
    "已成交数量",
    "約定済み数量"
  ],
  "Order time": [
    "Order time",
    "下单时间",
    "注文時刻"
  ],
  "no orders": [
    "no orders",
    "没有订单",
    "注文はありません"
  ],
  "Fills": [
    "Fills",
    "成交",
    "約定"
  ],
  "Virtual fills": [
    "Virtual fills",
    "虚拟成交",
    "仮想約定"
  ],
  "Fill": [
    "Fill",
    "成交",
    "約定"
  ],
  "Price": [
    "Price",
    "价格",
    "価格"
  ],
  "Total cost": [
    "Total cost",
    "总成本",
    "合計コスト"
  ],
  "Fill time": [
    "Fill time",
    "成交时间",
    "約定時刻"
  ],
  "no fills": [
    "no fills",
    "没有成交",
    "約定はありません"
  ],
  "Costs": [
    "Costs",
    "成本",
    "コスト"
  ],
  "commission": [
    "commission",
    "佣金",
    "手数料"
  ],
  "spread": [
    "spread",
    "买卖价差",
    "売買スプレッド"
  ],
  "slippage": [
    "slippage",
    "滑点",
    "スリッページ"
  ],
  "total": [
    "total",
    "合计",
    "合計"
  ],
  "Reconciliation": [
    "Reconciliation",
    "对账",
    "照合"
  ],
  "ok": [
    "ok",
    "一致",
    "一致"
  ],
  "tolerance": [
    "tolerance",
    "容差",
    "許容誤差"
  ],
  "none": [
    "none",
    "无",
    "なし"
  ],
  "Last paper step": [
    "Last paper step",
    "最近模拟交易步骤",
    "直近のペーパートレードのステップ"
  ],
  "no — fills cannot be back-dated": [
    "no — fills cannot be back-dated",
    "否：成交不能追溯记账",
    "いいえ：約定の遡及記録はできません"
  ],
  "receipt sequence": [
    "receipt sequence",
    "回执序号",
    "受領記録のシーケンス番号"
  ],
  "receipt event_hash": [
    "receipt event_hash",
    "回执 event_hash",
    "受領記録の event_hash"
  ],
  "receipt timestamp": [
    "receipt timestamp",
    "回执时间戳",
    "受領記録のタイムスタンプ"
  ],
  "The receipt is the ledger event that makes this step replayable: a repeat of the same request returns this same result instead of stepping again.": [
    "The receipt is the ledger event that makes this step replayable: a repeat of the same request returns this same result instead of stepping again.",
    "回执是支持本步重放的账本事件：重复相同请求会返回相同结果，不会再次执行此步。",
    "受領記録はこのステップの再現性を確保する台帳イベントです。同じ要求を繰り返すと同じ結果が返り、ステップが再実行されることはありません。"
  ],
  "Sizing evidence": [
    "Sizing evidence",
    "仓位计算证据",
    "ポジションサイズの根拠"
  ],
  "Instrument": [
    "Instrument",
    "金融工具",
    "金融商品"
  ],
  "Target weight": [
    "Target weight",
    "目标权重",
    "目標ウェイト"
  ],
  "Sizing equity": [
    "Sizing equity",
    "仓位计算权益",
    "サイズ計算に使用する資産総額"
  ],
  "Target quantity": [
    "Target quantity",
    "目标数量",
    "目標数量"
  ],
  "Quote": [
    "Quote",
    "报价",
    "気配値"
  ],
  "Quote lineage": [
    "Quote lineage",
    "报价血缘",
    "気配値の来歴"
  ],
  "the API returned no sizing evidence": [
    "the API returned no sizing evidence",
    "API 未返回仓位计算证据",
    "API からサイズ計算の根拠が返されていません"
  ],
  "Observed": [
    "Observed",
    "观测时间",
    "観測日時"
  ],
  "Acquired": [
    "Acquired",
    "获取时间",
    "取得日時"
  ],
  "Finality": [
    "Finality",
    "确认状态",
    "確定状況"
  ],
  "Source": [
    "Source",
    "来源",
    "ソース"
  ],
  "Content hash": [
    "Content hash",
    "内容哈希",
    "コンテンツハッシュ"
  ],
  "no quote lineage returned": [
    "no quote lineage returned",
    "未返回报价血缘",
    "気配値の来歴は返されていません"
  ],
  "Step orders": [
    "Step orders",
    "本步订单",
    "このステップの注文"
  ],
  "no orders in this step": [
    "no orders in this step",
    "本步没有订单",
    "このステップに注文はありません"
  ],
  "Step fills": [
    "Step fills",
    "本步成交",
    "このステップの約定"
  ],
  "no fills in this step": [
    "no fills in this step",
    "本步没有成交",
    "このステップに約定はありません"
  ],
  "your role is not permitted to operate paper sessions": [
    "your role is not permitted to operate paper sessions",
    "您的角色无权操作模拟交易会话",
    "お使いの権限ではペーパートレードセッションを操作できません"
  ],
  "the API does not report a paper ledger capability": [
    "the API does not report a paper ledger capability",
    "API 未报告模拟交易账本能力",
    "API からペーパートレード台帳機能が報告されていません"
  ],
  "the backend may refuse this step: it requires confirmed, content-addressed quote versions": [
    "the backend may refuse this step: it requires confirmed, content-addressed quote versions",
    "后端可能拒绝本步：需要已确认的内容寻址报价版本",
    "このステップはサーバー側で拒否される可能性があります。確定済みで、内容アドレスで識別された気配値のバージョンが必要です。"
  ],
  "the selected session has its kill switch active": [
    "the selected session has its kill switch active",
    "所选会话的紧急停止已启用",
    "選択したセッションでは緊急停止が有効です"
  ],
  "select a paper session first": [
    "select a paper session first",
    "请先选择模拟交易会话",
    "先にペーパートレードセッションを選択してください"
  ],
  "the kill switch is already active on this session": [
    "the kill switch is already active on this session",
    "此会话的紧急停止已启用",
    "このセッションでは緊急停止がすでに有効です"
  ],
  "Pinned quote versions are supported by the API.": [
    "Pinned quote versions are supported by the API.",
    "API 支持固定报价版本。",
    "API は固定した気配値のバージョンに対応しています。"
  ],
  "Step binds a confirmed, content-addressed quote version for every symbol in the book.": [
    "Step binds a confirmed, content-addressed quote version for every symbol in the book.",
    "执行一步时会为组合内每个代码绑定已确认的内容寻址报价版本。",
    "ステップ実行時には、ポートフォリオ内の各銘柄に確定済みの気配値バージョンを内容アドレスで紐付けます。"
  ],
  "no virtual paper sessions yet": [
    "no virtual paper sessions yet",
    "尚无虚拟模拟交易会话",
    "仮想ペーパートレードセッションはまだありません"
  ],
  "the request was refused": [
    "the request was refused",
    "请求已被拒绝",
    "要求が拒否されました"
  ],
  "Starting…": [
    "Starting…",
    "正在创建…",
    "開始中…"
  ],
  "Virtual paper session created": [
    "Virtual paper session created",
    "已创建虚拟模拟交易会话",
    "仮想ペーパートレードセッションを作成しました"
  ],
  "Session committed; witness checkpoint pending": [
    "Session committed; witness checkpoint pending",
    "会话已提交；见证检查点待同步",
    "セッションを確定しました。検証用チェックポイントの同期待ちです"
  ],
  "Unresolved: the API returned an unrecognised mutation state": [
    "Unresolved: the API returned an unrecognised mutation state",
    "尚未解决：API 返回无法识别的变更状态",
    "未解決：API が未認識の変更状態を返しました"
  ],
  "Stepping…": [
    "Stepping…",
    "正在执行…",
    "ステップ実行中…"
  ],
  "Paper step recorded": [
    "Paper step recorded",
    "已记录模拟交易步骤",
    "ペーパートレードのステップを記録しました"
  ],
  "Step committed; witness checkpoint pending": [
    "Step committed; witness checkpoint pending",
    "步骤已提交；见证检查点待同步",
    "ステップを確定しました。検証用チェックポイントの同期待ちです"
  ],
  "A reason is required to activate the kill switch.": [
    "A reason is required to activate the kill switch.",
    "启用紧急停止必须填写原因。",
    "緊急停止を有効にするには理由を入力してください。"
  ],
  "Activating…": [
    "Activating…",
    "正在启用…",
    "有効化中…"
  ],
  "Kill switch activated": [
    "Kill switch activated",
    "紧急停止已启用",
    "緊急停止を有効にしました"
  ],
  "Kill switch committed; witness checkpoint pending": [
    "Kill switch committed; witness checkpoint pending",
    "紧急停止已提交；见证检查点待同步",
    "緊急停止を確定しました。検証用チェックポイントの同期待ちです"
  ],
  "Market Intel · Platform": [
    "Market Intel · Platform",
    "Market Intel · 多策略平台",
    "Market Intel · 戦略プラットフォーム"
  ],
  "platform console · multi-strategy research": [
    "platform console · multi-strategy research",
    "平台控制台 · 多策略研究",
    "プラットフォーム管理 · 複数戦略の研究"
  ],
  "股票调仓历史 →": [
    "Stock rebalance history →",
    "股票调仓历史 →",
    "株式リバランス履歴 →"
  ],
  "按日期查看目标组合、权重变化与模拟成交记录": [
    "View target portfolios, weight changes and simulated fills by date",
    "按日期查看目标组合、权重变化与模拟成交记录",
    "日付ごとに目標ポートフォリオ、ウェイトの変化、シミュレーション約定を確認"
  ],
  "RESEARCH / MULTI-STRATEGY": [
    "RESEARCH / MULTI-STRATEGY",
    "研究 / 多策略",
    "研究 / 複数戦略"
  ],
  "Measure risk. Allocate capital.": [
    "Measure risk. Allocate capital.",
    "衡量风险，配置资金。",
    "リスクを測り、資金を配分する。"
  ],
  "同时查看日历收益、真实持仓活动与资金占用，按历史可用风险配置策略。": [
    "Review calendar returns, actual position activity and capital use; allocate to strategies using available historical risk.",
    "同时查看日历收益、真实持仓活动与资金占用，按历史可用风险配置策略。",
    "暦日ベースの収益、実際の保有状況、資金使用状況を確認し、利用可能な過去のリスク情報に基づいて戦略へ配分します。"
  ],
  "读取策略…": [
    "Loading strategies…",
    "读取策略…",
    "戦略を読み込み中…"
  ],
  "配置方法": [
    "Allocation method",
    "配置方法",
    "配分方法"
  ],
  "Activity risk budget · 活动风险预算": [
    "Activity risk budget",
    "活动风险预算",
    "保有期間のリスク予算"
  ],
  "Manual capital · 手动资金情景": [
    "Manual capital scenario",
    "手动资金情景",
    "手動の資金配分シナリオ"
  ],
  "Calculate joint performance": [
    "Calculate joint performance",
    "计算联合表现",
    "合成パフォーマンスを計算"
  ],
  "Equal capital · 等额参考": [
    "Equal capital reference",
    "等额资金参考",
    "均等資金配分の参考値"
  ],
  "USD strategies only": [
    "USD strategies only",
    "仅美元策略",
    "米ドル戦略のみ"
  ],
  "Import strategy": [
    "Import strategy",
    "导入策略",
    "戦略をインポート"
  ],
  "Refresh catalog": [
    "Refresh catalog",
    "刷新目录",
    "カタログを更新"
  ],
  "风险预算使用此前 36 个月数据，季度调整。持仓期风险、最差损失与最低风险尺度共同约束资金；估计不足和触及上限的预算保留现金。": [
    "Risk budgets use the preceding 36 months and adjust quarterly. Active-period risk, worst loss and a minimum risk scale constrain capital; budgets with insufficient evidence or binding caps stay in cash.",
    "风险预算使用此前 36 个月数据，季度调整。持仓期风险、最差损失与最低风险尺度共同约束资金；估计不足和触及上限的预算保留现金。",
    "直前36か月のデータでリスク予算を算出し、四半期ごとに調整します。保有期間のリスク、最大損失、リスク尺度の下限で資金配分を制限し、推定に十分な根拠がない分や上限を超える分は現金に残します。"
  ],
  "Allocation policy · 资金分配规则": [
    "Allocation policy",
    "资金分配规则",
    "資金配分ルール"
  ],
  "每次决策只读取此前 36 个月。风险尺度取 max（日历收益标准差，活动期收益标准差，活动期最差损失 ÷ 3，0.30%）。日频策略至少 20 个活动日；事件策略至少 12 个事件日期。风险倒数分配资金，单策略上限 40%、事件策略上限 10%，剩余资金为现金。该规则未优化历史 Sharpe；活动期统计不年化。源策略仍使用各自研究逻辑。": [
    "Each decision uses only the preceding 36 months. Risk scale is max(calendar return standard deviation, active return standard deviation, worst active loss ÷ 3, 0.30%). Daily strategies require at least 20 active days; event strategies require at least 12 event dates. Capital is allocated inversely to risk, capped at 40% per strategy and 10% per event strategy; the remainder stays in cash. This rule does not optimize historical Sharpe. Active-period statistics are not annualized. Source strategies retain their own research logic.",
    "每次决策只读取此前 36 个月。风险尺度取 max（日历收益标准差，活动期收益标准差，活动期最差损失 ÷ 3，0.30%）。日频策略至少 20 个活动日；事件策略至少 12 个事件日期。风险倒数分配资金，单策略上限 40%、事件策略上限 10%，剩余资金为现金。该规则未优化历史 Sharpe；活动期统计不年化。源策略仍使用各自研究逻辑。",
    "各判断は直前36か月のみを使用します。リスク尺度は max（暦日収益の標準偏差、保有期間収益の標準偏差、保有期間の最大損失 ÷ 3、0.30%）。日次戦略は20保有日以上、イベント戦略は12イベント日以上が必要です。リスクの逆数で配分し、1戦略40%、イベント戦略10%を上限に、残額は現金とします。過去のSharpeを最適化するルールではなく、保有期間の統計は年率換算しません。元の戦略の研究ロジックは維持します。"
  ],
  "Strategy allocation & versions · 调整策略、版本与资金配置": [
    "Strategy allocation & versions",
    "策略、版本与资金配置",
    "戦略・バージョン・資金配分"
  ],
  "组合口径": [
    "Currency basis",
    "组合币种口径",
    "ポートフォリオの通貨基準"
  ],
  "Fixed FX · 固定汇率研究情景": [
    "Fixed FX research scenario",
    "固定汇率研究情景",
    "固定為替の研究シナリオ"
  ],
  "Native currency · 同币种": [
    "Same currency",
    "同币种",
    "同一通貨"
  ],
  "资金调整频率": [
    "Capital rebalance frequency",
    "资金调整频率",
    "資金配分の調整頻度"
  ],
  "Quarterly · 季度风险预算调整": [
    "Quarterly risk budget adjustment",
    "季度风险预算调整",
    "四半期のリスク予算調整"
  ],
  "Initial allocation · 各账户自然漂移": [
    "Initial allocation · account weights drift",
    "初始配置 · 各账户自然漂移",
    "初期配分・口座比率は自然変動"
  ],
  "Monthly rebalance · 月末恢复比例": [
    "Monthly rebalance · restore weights at month-end",
    "月度再平衡 · 月末恢复比例",
    "月次リバランス・月末に比率を復元"
  ],
  "账户调配成本 · bps": [
    "Account reallocation cost · bps",
    "账户调配成本 · bps",
    "口座間の配分調整コスト · bps"
  ],
  "起始完整月份": [
    "First complete month",
    "起始完整月份",
    "開始の完全月"
  ],
  "结束完整月份": [
    "Last complete month",
    "结束完整月份",
    "終了の完全月"
  ],
  "MEASUREMENT / BEFORE ALLOCATION": [
    "MEASUREMENT / BEFORE ALLOCATION",
    "度量 / 配置之前",
    "測定 / 配分の前"
  ],
  "Activity & capital use · 持仓与资金效率": [
    "Activity & capital use",
    "持仓活动与资金占用",
    "保有状況と資金使用"
  ],
  "日历收益包含策略现金期。活动期统计仅使用账本证明的持仓日或事件日期，单位 bps（1 bp = 0.01%），不年化。零收益持仓日仍计为活动。": [
    "Calendar returns include periods when the strategy holds cash. Active-period statistics use only ledger-evidenced holding days or event dates, in bps (1 bp = 0.01%), without annualization. Holding days with zero return still count as active.",
    "日历收益包含策略现金期。活动期统计仅使用账本证明的持仓日或事件日期，单位 bps（1 bp = 0.01%），不年化。零收益持仓日仍计为活动。",
    "暦日収益には現金保有期間も含みます。保有期間の統計は、台帳で裏付けられた保有日またはイベント日のみを用い、bps（1 bp = 0.01%）で表示し、年率換算しません。収益がゼロの保有日も活動日として数えます。"
  ],
  "资金复用尚未建模。持仓名义金额、策略资本和保证金分别展示；缺少历史抵押品、融资与同时开仓约束时，空闲资金不视为可随时转用。": [
    "Capital reuse has not been modeled. Position notional, strategy capital and margin are shown separately. Without historical collateral, financing and simultaneous-position constraints, idle capital is not assumed freely transferable.",
    "资金复用尚未建模。持仓名义金额、策略资本和保证金分别展示；缺少历史抵押品、融资与同时开仓约束时，空闲资金不视为可随时转用。",
    "資金の再利用は未モデル化です。ポジション想定元本、戦略資本、証拠金を別々に表示します。過去の担保、資金調達、同時保有の制約がない場合、遊休資金を自由に転用できるとは仮定しません。"
  ],
  "Measurement clocks & evidence · 时点与证据": [
    "Measurement timing & evidence",
    "度量时点与证据",
    "測定時点と根拠"
  ],
  "Combined performance": [
    "Combined performance",
    "联合表现",
    "合成パフォーマンス"
  ],
  "Export evidence · JSON": [
    "Export evidence · JSON",
    "导出证据 · JSON",
    "根拠をエクスポート · JSON"
  ],
  "Risk budget decisions · 风险预算结果": [
    "Risk budget decisions",
    "风险预算结果",
    "リスク予算の結果"
  ],
  "Same-window comparison · 相同评估期": [
    "Same-window comparison",
    "相同评估期对照",
    "同一評価期間の比較"
  ],
  "Quarterly decisions · 训练范围与资金调整": [
    "Quarterly decisions · training window & reallocation",
    "季度决策 · 训练范围与资金调整",
    "四半期判断・学習範囲と配分調整"
  ],
  "EXPOSURE / SENSITIVITY": [
    "EXPOSURE / SENSITIVITY",
    "敞口 / 敏感性",
    "エクスポージャー / 感応度"
  ],
  "Increase exposure · 增加敞口是否值得": [
    "Is increasing exposure worthwhile?",
    "增加敞口是否值得",
    "エクスポージャー拡大の効果"
  ],
  "Compare exposure scenarios": [
    "Compare exposure scenarios",
    "比较敞口情景",
    "エクスポージャーシナリオを比較"
  ],
  "Export comparison": [
    "Export comparison",
    "导出比较结果",
    "比較結果をエクスポート"
  ],
  "比较两种调整：用组合剩余现金增加策略资金；保持资金配置、放大原有仓位。所有情景使用当前相同评估期，Vol 仓位倍数保持 1×。倍率按每日／事件收益重算，交易窗口保持源定义。": [
    "Compare two adjustments: increase strategy capital using portfolio cash, or keep capital allocations and scale existing positions. All scenarios use the same current evaluation period; the Vol position multiplier remains 1×. Multipliers are recalculated from daily/event returns, retaining source trading windows.",
    "比较两种调整：用组合剩余现金增加策略资金；保持资金配置、放大原有仓位。所有情景使用当前相同评估期，Vol 仓位倍数保持 1×。倍率按每日／事件收益重算，交易窗口保持源定义。",
    "ポートフォリオの残余現金で戦略資本を増やす方法と、資金配分を維持して既存ポジションを拡大する方法を比較します。すべて同じ評価期間を使用し、Volの倍率は1×を維持します。日次・イベント収益から倍率を再計算し、取引期間は元の定義を保ちます。"
  ],
  "运行后查看收益、回撤、风险贡献与 MSCI 事件成本敏感性。": [
    "Run the comparison to view returns, drawdowns, risk contributions and MSCI event cost sensitivity.",
    "运行后查看收益、回撤、风险贡献与 MSCI 事件成本敏感性。",
    "比較後に収益、ドローダウン、リスク寄与、MSCIイベントのコスト感応度を確認できます。"
  ],
  "Capital from cash · 从剩余现金增加资金": [
    "Capital from remaining cash",
    "从剩余现金增加资金",
    "残余現金からの資金追加"
  ],
  "各决策期相对原目标最多增加 10 个百分点（不逐季累计），或按所选三项原比例分配剩余现金。仅使用未分配现金；这是对原资金上限的显式情景修改。": [
    "Each decision period adds at most 10 percentage points to the original target (without accumulating quarterly), or distributes remaining cash in the original proportions of the three selected strategies. Only unallocated cash is used; this explicitly changes the original capital cap for the scenario.",
    "各决策期相对原目标最多增加 10 个百分点（不逐季累计），或按所选三项原比例分配剩余现金。仅使用未分配现金；这是对原资金上限的显式情景修改。",
    "各判断期に元の目標へ最大10パーセントポイント追加します（四半期ごとに累積しません）。または選択した3戦略の元の比率で残余現金を配分します。未配分の現金のみを使用し、元の資金上限をシナリオ上で明示的に変更します。"
  ],
  "Position multipliers · 原仓位倍数": [
    "Existing-position multipliers",
    "原仓位倍数",
    "既存ポジションの倍率"
  ],
  "资金账户与现金比例沿用原规则。新增融资、借券、冲击成本及追加保证金尚未计入；结果表示理论敏感性，无法证明可执行杠杆。": [
    "Capital accounts and cash ratios follow the original rules. Additional financing, stock borrow, market impact and margin calls are not included; results show theoretical sensitivity and do not establish executable leverage.",
    "资金账户与现金比例沿用原规则。新增融资、借券、冲击成本及追加保证金尚未计入；结果表示理论敏感性，无法证明可执行杠杆。",
    "資金口座と現金比率は元のルールに従います。追加の資金調達、借株、市場インパクト、追証は未計上です。結果は理論上の感応度であり、実行可能なレバレッジの根拠にはなりません。"
  ],
  "MSCI · 事件期与成本": [
    "MSCI · event periods & costs",
    "MSCI · 事件期与成本",
    "MSCI · イベント期間とコスト"
  ],
  "Exposure assumptions & evidence": [
    "Exposure assumptions & evidence",
    "敞口假设与证据",
    "エクスポージャーの前提と根拠"
  ],
  "Growth of 1 unit": [
    "Growth of 1 unit",
    "1 单位资金的增长",
    "1単位の資産推移"
  ],
  "共同区间重新归一。Joint 为配置情景；细线为各策略独立净值，按相同月份计算。": [
    "Rebased over the common period. Joint is the allocation scenario; thin lines show each strategy's standalone NAV over the same months.",
    "共同区间重新归一。联合曲线为配置情景；细线为各策略独立净值，按相同月份计算。",
    "共通期間で基準値を再設定します。合成曲線は配分シナリオ、細線は同じ月で計算した各戦略単独のNAVです。"
  ],
  "Drawdown · 月末": [
    "Drawdown · month-end",
    "回撤 · 月末",
    "ドローダウン · 月末"
  ],
  "Return & risk contribution": [
    "Return & risk contribution",
    "收益与风险贡献",
    "収益・リスク寄与"
  ],
  "收益贡献为初始组合资金的百分点。风险贡献为策略当月 P&L 收益与组合收益的协方差 / 组合方差；可为负。": [
    "Return contribution is in percentage points of initial portfolio capital. Risk contribution is covariance(strategy monthly P&L return, portfolio return) / portfolio variance; it can be negative.",
    "收益贡献为初始组合资金的百分点。风险贡献为策略当月 P&L 收益与组合收益的协方差 / 组合方差；可为负。",
    "収益寄与は初期ポートフォリオ資金に対するパーセントポイントです。リスク寄与は戦略の月次P&L収益とポートフォリオ収益の共分散 / ポートフォリオ分散で、負になる場合があります。"
  ],
  "Return correlation": [
    "Return correlation",
    "收益相关性",
    "収益相関"
  ],
  "相同完整月份，Pearson 相关性。恒定序列显示 N/A。": [
    "Pearson correlation over the same complete months. Constant series show N/A.",
    "相同完整月份，Pearson 相关性。恒定序列显示 N/A。",
    "同じ完全月を用いたPearson相関です。定数系列はN/Aと表示します。"
  ],
  "Monthly returns · 月度收益与净值": [
    "Monthly returns & NAV",
    "月度收益与净值",
    "月次収益とNAV"
  ],
  "Evidence, assumptions & research limits": [
    "Evidence, assumptions & research limits",
    "证据、假设与研究局限",
    "根拠・前提・研究上の制約"
  ],
  "手动资金情景：设置独立策略账户的资本比例。等额配置仅作参考，风险贡献可能明显不同。": [
    "Manual capital scenario: set capital weights for standalone strategy accounts. Equal allocation is a reference only; risk contributions may differ substantially.",
    "手动资金情景：设置独立策略账户的资本比例。等额配置仅作参考，风险贡献可能明显不同。",
    "手動シナリオでは各戦略口座の資本比率を設定します。均等配分は参考値で、リスク寄与は大きく異なる場合があります。"
  ],
  "配置已更改，重新计算以查看结果。": [
    "Allocation changed. Recalculate to view results.",
    "配置已更改，重新计算以查看结果。",
    "配分を変更しました。結果を見るには再計算してください。"
  ],
  "无法读取服务响应。": [
    "Unable to read the service response.",
    "无法读取服务响应。",
    "サービスの応答を読み取れません。"
  ],
  "正在读取冻结策略目录…": [
    "Loading the frozen strategy catalog…",
    "正在读取冻结策略目录…",
    "固定済み戦略カタログを読み込み中…"
  ],
  "目录已更新，重新计算以查看结果。": [
    "Catalog updated. Recalculate to view results.",
    "目录已更新，重新计算以查看结果。",
    "カタログを更新しました。結果を見るには再計算してください。"
  ],
  "目录已加载。": [
    "Catalog loaded.",
    "目录已加载。",
    "カタログを読み込みました。"
  ],
  "策略目录不可用。": [
    "Strategy catalog unavailable.",
    "策略目录不可用。",
    "戦略カタログを利用できません。"
  ],
  "正在核对活动、资金占用与共同区间…": [
    "Checking activity, capital use and the common period…",
    "正在核对活动、资金占用与共同区间…",
    "保有状況、資金使用、共通期間を確認中…"
  ],
  "请选择至少一个有真实研究净值的策略。": [
    "Select at least one strategy with an available research NAV.",
    "请选择至少一个有真实研究净值的策略。",
    "研究NAVが利用可能な戦略を1つ以上選択してください。"
  ],
  "资金配置须为 0–100%，总和大于 0 且不超过 100%。": [
    "Capital weights must be 0–100%, with a total above 0 and no greater than 100%.",
    "资金配置须为 0–100%，总和大于 0 且不超过 100%。",
    "資金配分は0–100%、合計は0を超え100%以下である必要があります。"
  ],
  "活动度量完成，正在计算资金情景…": [
    "Activity measurement complete. Calculating the capital scenario…",
    "活动度量完成，正在计算资金情景…",
    "保有状況の測定が完了しました。資金シナリオを計算中…"
  ],
  "计算完成 · NOT VALIDATED · 历史研究情景": [
    "Calculation complete · NOT VALIDATED · historical research scenario",
    "计算完成 · 尚未验证 · 历史研究情景",
    "計算完了 · 未検証 · 過去データの研究シナリオ"
  ],
  "计算超时，请重试。": [
    "Calculation timed out. Please retry.",
    "计算超时，请重试。",
    "計算がタイムアウトしました。再試行してください。"
  ],
  "活动度量已保留，当前配置无法生成联合表现。": [
    "Activity measurements retained; joint performance is unavailable for this allocation.",
    "活动度量已保留，当前配置无法生成联合表现。",
    "保有状況の測定は保持されています。この配分では合成パフォーマンスを算出できません。"
  ],
  "活动度量与联合表现不可用。": [
    "Activity measurement and joint performance unavailable.",
    "活动度量与联合表现不可用。",
    "保有状況と合成パフォーマンスを利用できません。"
  ],
  "RESEARCH CONNECTED": [
    "RESEARCH CONNECTED",
    "已连接研究结果",
    "研究結果を接続済み"
  ],
  "AWAITING NAV": [
    "AWAITING NAV",
    "等待净值",
    "NAV待ち"
  ],
  "加入组合与度量": [
    "Include in portfolio & measurement",
    "加入组合与度量",
    "ポートフォリオと測定に含める"
  ],
  "研究版本": [
    "Research variant",
    "研究版本",
    "研究バージョン"
  ],
  "Scope & evidence": [
    "Scope & evidence",
    "范围与证据",
    "範囲と根拠"
  ],
  "Clock:": [
    "Timing:",
    "时点：",
    "時点："
  ],
  "Costs:": [
    "Costs:",
    "成本：",
    "コスト："
  ],
  "Capital": [
    "Capital",
    "资金",
    "資金"
  ],
  "按风险": [
    "By risk",
    "按风险",
    "リスクに基づく"
  ],
  "计算后显示实际预算；无预设等额权重。": [
    "Actual budgets appear after calculation; no equal weights are preset.",
    "计算后显示实际预算；无预设等额权重。",
    "計算後に実際の予算を表示します。均等ウェイトは事前設定していません。"
  ],
  "收益不可用 · 不加入合成曲线": [
    "Returns unavailable · excluded from joint curve",
    "收益不可用 · 不加入合成曲线",
    "収益データなし · 合成曲線から除外"
  ],
  "Calendar return / CAGR": [
    "Calendar return / CAGR",
    "日历收益 / CAGR",
    "暦日収益 / CAGR"
  ],
  "Actual activity": [
    "Actual activity",
    "实际活动",
    "実際の保有状況"
  ],
  "Active mean": [
    "Active-period mean",
    "活动期均值",
    "保有期間の平均"
  ],
  "Active σ": [
    "Active-period σ",
    "活动期 σ",
    "保有期間のσ"
  ],
  "Worst active": [
    "Worst active return",
    "活动期最差收益",
    "保有期間の最悪収益"
  ],
  "Gross · start / prior mean": [
    "Gross · opening / prior mean",
    "总敞口 · 开仓 / 前次均值",
    "グロス · 開始時 / 前回平均"
  ],
  "Gross · reported mean / peak": [
    "Gross · reported mean / peak",
    "总敞口 · 报告均值 / 峰值",
    "グロス · 報告平均 / 最大値"
  ],
  "Margin · mean / peak": [
    "Margin · mean / peak",
    "保证金 · 均值 / 峰值",
    "証拠金 · 平均 / 最大値"
  ],
  "Unavailable": [
    "Unavailable",
    "不可用",
    "利用不可"
  ],
  "前次源快照": [
    "Prior source snapshot",
    "前次源快照",
    "前回のソーススナップショット"
  ],
  "事件开仓": [
    "Event opening",
    "事件开仓",
    "イベント開始時"
  ],
  "前收盘": [
    "Previous close",
    "前收盘",
    "前日終値"
  ],
  "本次源快照": [
    "Current source snapshot",
    "本次源快照",
    "今回のソーススナップショット"
  ],
  "退出后": [
    "After exit",
    "退出后",
    "決済後"
  ],
  "源记录": [
    "Source record",
    "源记录",
    "ソース記録"
  ],
  "Exposure clocks · 每项按源时点报告": [
    "Exposure timing · reported at source timing",
    "敞口时点 · 每项按源时点报告",
    "エクスポージャーの時点 · ソースの時点で報告"
  ],
  "：以下均为源记录的名义金额 / 对应 NAV；峰值仅为这些记录的最大值，不能代表共同口径的日内峰值。N/A / Unavailable 表示证据缺失。": [
    ": Values below are source-recorded notional / corresponding NAV. Peaks are maxima of those records, not comparable intraday peaks. N/A / Unavailable indicates missing evidence.",
    "：以下均为源记录的名义金额 / 对应 NAV；峰值仅为这些记录的最大值，不能代表共同口径的日内峰值。N/A / Unavailable 表示证据缺失。",
    "：以下はソース記録の想定元本 / 対応NAVです。最大値は当該記録の最大値で、共通基準の日中最大値を意味しません。N/A / Unavailableは根拠の欠落を示します。"
  ],
  "Exposure clock:": [
    "Exposure timing:",
    "敞口时点：",
    "エクスポージャーの時点："
  ],
  "Capital:": [
    "Capital:",
    "资本：",
    "資本："
  ],
  "Margin:": [
    "Margin:",
    "保证金：",
    "証拠金："
  ],
  "First evaluation allocation": [
    "Initial evaluation allocation",
    "评估期初始配置",
    "評価開始時の配分"
  ],
  "Latest target allocation": [
    "Latest target allocation",
    "最新目标配置",
    "最新の目標配分"
  ],
  "Unallocated cash": [
    "Unallocated cash",
    "未分配现金",
    "未配分の現金"
  ],
  "Same evaluation window": [
    "Same evaluation window",
    "相同评估窗口",
    "同一評価期間"
  ],
  "Cumulative": [
    "Cumulative",
    "累计收益",
    "累積収益"
  ],
  "Annualized vol": [
    "Annualized volatility",
    "年化波动率",
    "年率ボラティリティ"
  ],
  "Month-end drawdown": [
    "Month-end drawdown",
    "月末回撤",
    "月末ドローダウン"
  ],
  "Sharpe · 0% RF": [
    "Sharpe · 0% RF",
    "Sharpe · 无风险收益率 0%",
    "Sharpe · 無リスク金利0%"
  ],
  "Activity risk budget": [
    "Activity risk budget",
    "活动风险预算",
    "保有期間のリスク予算"
  ],
  "Equal capital · initial": [
    "Equal capital · initial",
    "等额资金 · 初始配置",
    "均等資金 · 初期配分"
  ],
  "Equal capital · quarterly": [
    "Equal capital · quarterly",
    "等额资金 · 季度调整",
    "均等資金 · 四半期調整"
  ],
  "Decision": [
    "Decision",
    "决策月份",
    "判断月"
  ],
  "Prior-data window": [
    "Prior-data window",
    "历史数据窗口",
    "過去データの期間"
  ],
  "Cash": [
    "Cash",
    "现金",
    "現金"
  ],
  "Samples / active": [
    "Samples / active",
    "样本数 / 活动数",
    "サンプル / 保有期間"
  ],
  "Calendar σ": [
    "Calendar σ",
    "日历 σ",
    "暦日σ"
  ],
  "Worst loss / 3": [
    "Worst loss / 3",
    "最差损失 / 3",
    "最大損失 / 3"
  ],
  "Floor": [
    "Floor",
    "下限",
    "下限"
  ],
  "Risk unit": [
    "Risk unit",
    "风险尺度",
    "リスク尺度"
  ],
  "Eligibility": [
    "Eligibility",
    "估计条件",
    "推定条件"
  ],
  "Capped · 触及上限": [
    "Capped",
    "触及上限",
    "上限に到達"
  ],
  "Eligible · 可估计": [
    "Eligible",
    "可估计",
    "推定可能"
  ],
  "Insufficient evidence": [
    "Insufficient evidence",
    "证据不足",
    "根拠不足"
  ],
  "Monthly joint drawdown": [
    "Monthly joint drawdown",
    "联合组合月度回撤",
    "合成ポートフォリオの月次ドローダウン"
  ],
  "Normalized joint and strategy NAV curves": [
    "Normalized joint and strategy NAV curves",
    "归一化的联合与策略净值曲线",
    "基準化した合成・戦略別NAV曲線"
  ],
  "Joint month-end drawdown": [
    "Joint month-end drawdown",
    "联合组合月末回撤",
    "合成ポートフォリオの月末ドローダウン"
  ],
  "Growth of one unit over the complete common interval": [
    "Growth of one unit over the complete common interval",
    "完整共同区间内 1 单位资金的增长",
    "共通の完全期間における1単位の資産推移"
  ],
  "Start": [
    "Start",
    "起点",
    "開始"
  ],
  "Joint": [
    "Joint",
    "联合组合",
    "合成ポートフォリオ"
  ],
  "Cumulative return": [
    "Cumulative return",
    "累计收益",
    "累積収益"
  ],
  "Annualized volatility": [
    "Annualized volatility",
    "年化波动率",
    "年率ボラティリティ"
  ],
  "Month-end max drawdown": [
    "Month-end max drawdown",
    "月末最大回撤",
    "月末最大ドローダウン"
  ],
  "Sharpe · 0% RF diagnostic": [
    "Sharpe · 0% RF diagnostic",
    "Sharpe · 0% 无风险收益率诊断",
    "Sharpe · 無リスク金利0%の診断値"
  ],
  "固定汇率研究情景：忽略汇率变动、换汇与对冲成本。月末回撤可能低于日内真实风险。": [
    "Fixed-FX research scenario: FX movements, conversion and hedging costs are omitted. Month-end drawdowns may understate intraday risk.",
    "固定汇率研究情景：忽略汇率变动、换汇与对冲成本。月末回撤可能低于日内真实风险。",
    "固定為替の研究シナリオです。為替変動、両替、ヘッジコストは含めません。月末ドローダウンは実際の日中リスクを過小評価する場合があります。"
  ],
  "同币种历史研究情景：独立资金账户，现金收益 0%。月末回撤可能低于日内真实风险。": [
    "Same-currency historical research scenario: standalone capital accounts, cash return 0%. Month-end drawdowns may understate intraday risk.",
    "同币种历史研究情景：独立资金账户，现金收益 0%。月末回撤可能低于日内真实风险。",
    "同一通貨の過去データ研究シナリオです。独立した資金口座で、現金収益は0%です。月末ドローダウンは実際の日中リスクを過小評価する場合があります。"
  ],
  "组合波动为零或信息不足，风险贡献无法估计。": [
    "Risk contribution cannot be estimated: portfolio volatility is zero or evidence is insufficient.",
    "组合波动为零或信息不足，风险贡献无法估计。",
    "ポートフォリオのボラティリティがゼロ、または情報が不足しているため、リスク寄与を推定できません。"
  ],
  "Month": [
    "Month",
    "月份",
    "月"
  ],
  "Joint return": [
    "Joint return",
    "联合组合收益",
    "合成収益"
  ],
  "Joint NAV": [
    "Joint NAV",
    "联合组合净值",
    "合成NAV"
  ],
  "Drawdown": [
    "Drawdown",
    "回撤",
    "ドローダウン"
  ],
  "NOT VALIDATED · RESEARCH SCENARIO": [
    "NOT VALIDATED · RESEARCH SCENARIO",
    "尚未验证 · 研究情景",
    "未検証 · 研究シナリオ"
  ],
  "口径参考：": [
    "Methodology reference:",
    "口径参考：",
    "計算方法の参考："
  ],
  "。本平台未声明 GIPS 合规。": [
    ". This platform does not claim GIPS compliance.",
    "。本平台未声明 GIPS 合规。",
    "。本プラットフォームはGIPS準拠を表明していません。"
  ],
  "正在对同一批冻结策略计算敞口与资金情景…": [
    "Calculating exposure and capital scenarios for the same frozen strategies…",
    "正在对同一批冻结策略计算敞口与资金情景…",
    "同じ固定済み戦略のエクスポージャーと資金シナリオを計算中…"
  ],
  "比较完成 · 原资金方案未改动 · NOT VALIDATED": [
    "Comparison complete · original allocation retained · NOT VALIDATED",
    "比较完成 · 原资金方案未改动 · 尚未验证",
    "比較完了 · 元の配分を維持 · 未検証"
  ],
  "敞口比较超时，请重试。": [
    "Exposure comparison timed out. Please retry.",
    "敞口比较超时，请重试。",
    "エクスポージャー比較がタイムアウトしました。再試行してください。"
  ],
  "Scenario": [
    "Scenario",
    "情景",
    "シナリオ"
  ],
  "Return": [
    "Return",
    "收益",
    "収益"
  ],
  "Δ return · pp": [
    "Δ return · pp",
    "收益变化 · 百分点",
    "収益差 · パーセントポイント"
  ],
  "Annualized monthly vol": [
    "Annualized monthly volatility",
    "月度收益年化波动率",
    "月次収益の年率ボラティリティ"
  ],
  "Largest risk share": [
    "Largest risk share",
    "最大风险贡献",
    "最大リスク寄与"
  ],
  "Latest cash target": [
    "Latest cash target",
    "最新现金目标",
    "最新の現金目標"
  ],
  "MSCI multiplier": [
    "MSCI multiplier",
    "MSCI 倍率",
    "MSCI倍率"
  ],
  "Extra cost / gross · bp": [
    "Extra cost / gross · bp",
    "额外成本 / 总敞口 · bp",
    "追加コスト / グロス · bp"
  ],
  "Strategy cumulative return": [
    "Strategy cumulative return",
    "策略累计收益",
    "戦略の累積収益"
  ],
  "Mean / event · bp": [
    "Mean / event · bp",
    "事件均值 · bp",
    "イベント平均 · bp"
  ],
  "Worst event · bp": [
    "Worst event · bp",
    "最差事件 · bp",
    "最悪イベント · bp"
  ],
  "正在校验并导入…": [
    "Validating and importing…",
    "正在校验并导入…",
    "検証・インポート中…"
  ],
  "文件超过 2 MB。": [
    "File exceeds 2 MB.",
    "文件超过 2 MB。",
    "ファイルが2 MBを超えています。"
  ],
  "CSV header 必须为 month,return": [
    "CSV header must be month,return",
    "CSV 表头必须为 month,return",
    "CSVヘッダーはmonth,returnである必要があります"
  ],
  "CSV 每行必须有月份和非空收益。": [
    "Each CSV row must contain a month and a nonempty return.",
    "CSV 每行必须有月份和非空收益。",
    "CSVの各行には月と空でない収益が必要です。"
  ],
  "Market Intel · Research": [
    "Market Intel · Research",
    "Market Intel · 研究",
    "Market Intel · 研究"
  ],
  "model health · strategic research · files · pipeline": [
    "model health · strategic research · files · pipeline",
    "模型状态 · 战略研究 · 文件 · 分析流程",
    "モデル状態 · 戦略研究 · ファイル · 分析パイプライン"
  ],
  "系统运行与部署准备": [
    "System status & deployment readiness",
    "系统运行与部署准备",
    "システム稼働と導入準備"
  ],
  "漏日与运行记录": [
    "Missing sessions & run records",
    "漏日与运行记录",
    "欠落セッションと実行記録"
  ],
  "主观评分 · 前向证据": [
    "Judgment scores · forward evidence",
    "主观评分 · 前向证据",
    "判断スコア · フォワード検証の根拠"
  ],
  "数值基线 A / A + 固定规则 / A + LLM 判断": [
    "Numerical baseline A / A + fixed rules / A + LLM judgment",
    "数值基线 A / A + 固定规则 / A + LLM 判断",
    "数値ベースラインA / A + 固定ルール / A + LLM判断"
  ],
  "按真正登记时间向前积累；已有评分的时间、模型或证据缺失会单独标记。日历成熟仍须检查价格标签，短期相关性不能替代扣除成本后的收益验证。": [
    "Evidence accumulates from actual registration times. Missing score timing, models or evidence are flagged separately. Mature calendar horizons still require price-label checks; short-term correlation cannot replace return validation after costs.",
    "按真正登记时间向前积累；已有评分的时间、模型或证据缺失会单独标记。日历成熟仍须检查价格标签，短期相关性不能替代扣除成本后的收益验证。",
    "実際の登録時点から根拠を蓄積します。既存スコアの時点、モデル、根拠の欠落は別途表示します。期間が経過しても価格ラベルの確認が必要で、短期の相関はコスト控除後の収益検証に代わりません。"
  ],
  "评分改进对照": [
    "Score improvement comparison",
    "评分改进对照",
    "スコア改善の比較"
  ],
  "E2 对稀缺性与战略风险评分加入证据覆盖率、置信度和时间衰减；共用本次分析的配置与前次持仓。此处仅显示研究对照，不改变主组合。": [
    "E2 adds evidence coverage, confidence and time decay to scarcity and strategic-risk scores, using this analysis configuration and prior holdings. This research comparison does not change the main portfolio.",
    "E2 对稀缺性与战略风险评分加入证据覆盖率、置信度和时间衰减；共用本次分析的配置与前次持仓。此处仅显示研究对照，不改变主组合。",
    "E2は希少性と戦略リスクのスコアに根拠の網羅性、信頼度、時間減衰を追加し、今回の設定と前回の保有を共用します。研究上の比較表示で、主ポートフォリオを変更しません。"
  ],
  "Validation verdict": [
    "Validation verdict",
    "验证结论",
    "検証結果"
  ],
  "Pipeline": [
    "Pipeline",
    "分析流程",
    "分析パイプライン"
  ],
  "Run the full pipeline (5 to 10 minutes)": [
    "Run the full pipeline (5 to 10 minutes)",
    "运行完整分析流程（5 至 10 分钟）",
    "全パイプラインを実行（5〜10分）"
  ],
  "▶ Run full pipeline": [
    "▶ Run full pipeline",
    "▶ 运行完整分析流程",
    "▶ 全パイプラインを実行"
  ],
  "Re-score using cached data": [
    "Re-score using cached data",
    "使用缓存数据重新评分",
    "キャッシュデータで再採点"
  ],
  "▶ Re-score (cached data)": [
    "▶ Re-score (cached data)",
    "▶ 重新评分（缓存数据）",
    "▶ 再採点（キャッシュデータ）"
  ],
  "运行日志": [
    "Run log",
    "运行日志",
    "実行ログ"
  ],
  "Point-in-time IC (2012-2026)": [
    "Point-in-time IC (2012-2026)",
    "时点一致 IC（2012-2026）",
    "時点整合IC（2012-2026）"
  ],
  "Point-in-time rank IC by horizon and signal": [
    "Point-in-time rank IC by horizon and signal",
    "按期限与信号的时点一致排名 IC",
    "期間・シグナル別の時点整合順位IC"
  ],
  "Horizon": [
    "Horizon",
    "期限",
    "期間"
  ],
  "Signal": [
    "Signal",
    "信号",
    "シグナル"
  ],
  "Mean IC": [
    "Mean IC",
    "平均 IC",
    "平均IC"
  ],
  "% positive": [
    "% positive",
    "正值比例",
    "正の値の比率"
  ],
  "Feature IC (1m)": [
    "Feature IC (1m)",
    "特征 IC（1 个月）",
    "特徴量IC（1か月）"
  ],
  "top 20 by |IC|": [
    "top 20 by |IC|",
    "按 |IC| 排名前 20",
    "|IC| 上位20件"
  ],
  "Live forward IC (daily archive)": [
    "Live forward IC (daily archive)",
    "实时前向 IC（日频归档）",
    "実観測フォワードIC（日次アーカイブ）"
  ],
  "Run history": [
    "Run history",
    "运行历史",
    "実行履歴"
  ],
  "Strategic role (LLM deep research, evidence-logged)": [
    "Strategic role (LLM deep research, evidence-logged)",
    "战略角色（LLM 深度研究，保留证据）",
    "戦略的役割（LLM調査・根拠記録済み）"
  ],
  "Strategic assessments per name: scores, pillar, confidence and thesis": [
    "Strategic assessments per name: scores, pillar, confidence and thesis",
    "逐股战略评估：评分、支柱、置信度与论点",
    "銘柄別戦略評価：スコア、柱、信頼度、論点"
  ],
  "Strategic": [
    "Strategic",
    "战略评分",
    "戦略スコア"
  ],
  "Scarcity": [
    "Scarcity",
    "稀缺性",
    "希少性"
  ],
  "Pillar": [
    "Pillar",
    "支柱",
    "柱"
  ],
  "Conf.": [
    "Conf.",
    "置信度",
    "信頼度"
  ],
  "Confidence": [
    "Confidence",
    "置信度",
    "信頼度"
  ],
  "Capped": [
    "Capped",
    "已限幅",
    "上限制約"
  ],
  "Thesis": [
    "Thesis",
    "论点",
    "投資論点"
  ],
  "Validation charts": [
    "Validation charts",
    "验证图表",
    "検証チャート"
  ],
  "open full screen": [
    "open full screen",
    "全屏打开",
    "全画面で開く"
  ],
  "Files & chart gallery": [
    "Files & chart gallery",
    "文件与图表库",
    "ファイルとチャート一覧"
  ],
  "Methodology (validation report)": [
    "Methodology (validation report)",
    "方法论（验证报告）",
    "方法論（検証レポート）"
  ],
  "Close chart viewer": [
    "Close chart viewer",
    "关闭图表查看器",
    "チャートビューアを閉じる"
  ],
  "✕ Close": [
    "✕ Close",
    "✕ 关闭",
    "✕ 閉じる"
  ],
  "Missing evidence": [
    "Missing evidence",
    "缺失证据",
    "不足している根拠"
  ],
  "All gates pass.": [
    "All gates pass.",
    "所有门槛均通过。",
    "すべての条件を満たしています。"
  ],
  "Weights are model targets, not positions. Until the gates pass the score supports analyst review and monitoring only; refresh model inputs with the pipeline below.": [
    "Weights are model targets, not positions. Until the gates pass the score supports analyst review and monitoring only; refresh model inputs with the pipeline below.",
    "权重是模型目标，并非实际持仓。门槛通过前，评分仅用于分析师复核和监控；可通过下方流程更新模型输入。",
    "ウェイトはモデル目標で、実際の保有ではありません。条件を満たすまでは分析者の確認とモニタリングにのみ使用し、下のパイプラインで入力を更新します。"
  ],
  "Live forward IC by horizon and signal": [
    "Live forward IC by horizon and signal",
    "按期限与信号的实时前向 IC",
    "期間・シグナル別の実観測フォワードIC"
  ],
  "证据范围": [
    "Evidence scope",
    "证据范围",
    "根拠の範囲"
  ],
  "描述性 IC；不报告显著性": [
    "Descriptive IC; significance is not reported",
    "描述性 IC；不报告显著性",
    "記述的IC・有意性は報告しません"
  ],
  "Kill criteria:": [
    "Kill criteria:",
    "终止条件：",
    "中止条件："
  ],
  "Watch:": [
    "Watch:",
    "关注事项：",
    "注視事項："
  ],
  "Daily static dashboard": [
    "Daily static dashboard",
    "每日静态仪表盘",
    "日次の静的ダッシュボード"
  ],
  "no point-in-time validation output": [
    "No point-in-time validation output",
    "暂无时点一致验证结果",
    "時点整合検証の結果がありません"
  ],
  "no validation charts": [
    "No validation charts",
    "暂无验证图表",
    "検証チャートがありません"
  ],
  "no validation report": [
    "No validation report",
    "暂无验证报告",
    "検証レポートがありません"
  ],
  "no feature IC output": [
    "No feature IC output",
    "暂无特征 IC 结果",
    "特徴量ICの結果がありません"
  ],
  "no strategic assessments stored": [
    "No strategic assessments stored",
    "暂无已保存的战略评估",
    "保存済み戦略評価がありません"
  ],
  "No strategic assessments.": [
    "No strategic assessments.",
    "暂无战略评估。",
    "戦略評価がありません。"
  ],
  "no pipeline charts yet": [
    "No pipeline charts yet",
    "暂无分析流程图表",
    "パイプラインのチャートがまだありません"
  ],
  "Loading research": [
    "Loading research",
    "正在读取研究数据",
    "研究データを読み込み中"
  ],
  "Point-in-time 3m HAC t ≥ 2": [
    "Point-in-time 3m HAC t ≥ 2",
    "时点一致 3 个月 HAC t ≥ 2",
    "時点整合3か月HAC t ≥ 2"
  ],
  "Walk-forward 3m t ≥ 2": [
    "Walk-forward 3m t ≥ 2",
    "滚动前推 3 个月 t ≥ 2",
    "ウォークフォワード3か月 t ≥ 2"
  ],
  "Live forward: ≥ 12 snapshots with 12m returns": [
    "Live forward: ≥ 12 snapshots with 12m returns",
    "实时前向：≥ 12 个具备 12 个月收益的快照",
    "実観測フォワード：12か月収益を持つスナップショットが12件以上"
  ],
  "not evaluated": [
    "not evaluated",
    "尚未评估",
    "未評価"
  ],
  "NOT VALIDATED · exploratory": [
    "NOT VALIDATED · exploratory",
    "尚未验证 · 探索性研究",
    "未検証 · 探索的研究"
  ],
  "运行状态读取失败；当前显示可能已过期。": [
    "Unable to load run status; displayed content may be stale.",
    "运行状态读取失败；当前显示可能已过期。",
    "実行状態を読み込めません。表示が古い可能性があります。"
  ],
  "本次没有已保存的日志。": [
    "No saved log for this run.",
    "本次没有已保存的日志。",
    "今回の実行に保存済みログはありません。"
  ],
  "starting…": [
    "starting…",
    "正在启动…",
    "開始中…"
  ],
  "running (5-10 min for a full run)": [
    "running (5-10 min for a full run)",
    "正在运行（完整流程需 5-10 分钟）",
    "実行中（全実行は5〜10分）"
  ],
  "already running": [
    "already running",
    "已在运行",
    "すでに実行中"
  ],
  "Market Intel · 调仓历史": [
    "Market Intel · Rebalance history",
    "Market Intel · 调仓历史",
    "Market Intel · リバランス履歴"
  ],
  "股票选股 · 调仓历史": [
    "Stock Select · rebalance history",
    "股票选股 · 调仓历史",
    "株式選定 · リバランス履歴"
  ],
  "调仓历史": [
    "Rebalance history",
    "调仓历史",
    "リバランス履歴"
  ],
  "刷新记录": [
    "Refresh records",
    "刷新记录",
    "記録を更新"
  ],
  "每种 construction 每日独立更新并保留目标。默认展示每天的最终归档版本；切换查看方法不改变主组合设置。": [
    "Each construction updates independently each day and retains its targets. The daily final archived version is shown by default. Switching the viewed method does not change the main portfolio settings.",
    "每种组合方法每日独立更新并保留目标。默认展示每天的最终归档版本；切换查看方法不改变主组合设置。",
    "各構築方法は日次で独立更新し、目標を保存します。標準ではその日の最終アーカイブを表示します。表示方法の切替は主ポートフォリオの設定を変更しません。"
  ],
  "主组合 · 当时选用的方法": [
    "Main portfolio · method selected at the time",
    "主组合 · 当时选用的方法",
    "主ポートフォリオ · 当時の選択方法"
  ],
  "权重口径": [
    "Weight basis",
    "权重口径",
    "ウェイトの基準"
  ],
  "账户目标 · 含现金与波动率控制": [
    "Account targets · cash & volatility control",
    "账户目标 · 含现金与波动率控制",
    "口座目標 · 現金・ボラティリティ調整込み"
  ],
  "股票内部权重 · 100% 股票": [
    "Equity-only weights · 100% stocks",
    "股票内部权重 · 100% 股票",
    "株式内ウェイト · 株式100%"
  ],
  "各方法与自己的上一期目标比较。历史未保存的目标不补写；可切换口径查看当时的模型权重。": [
    "Each method is compared with its own previous targets. Unsaved historical targets are not backfilled; switch the basis to view the model weights recorded at the time.",
    "各方法与自己的上一期目标比较。历史未保存的目标不补写；可切换口径查看当时的模型权重。",
    "各方法をその前回目標と比較します。未保存の過去目標は補完しません。基準を切り替えて当時のモデルウェイトを確認できます。"
  ],
  "目标权重与现金均为计划值；目标调整比例不等于实际换手率。价格漂移会改变现有持仓权重，即使目标不变，也可能需要交易。成交仅来自平台模拟账本，未接入实盘成交。研究状态：NOT VALIDATED。": [
    "Target weights and cash are planned values; target turnover is not actual trading turnover. Price drift changes current weights, so trades may be needed even when targets are unchanged. Fills come only from the platform's simulated ledger; live fills are not connected. Research status: NOT VALIDATED.",
    "目标权重与现金均为计划值；目标调整比例不等于实际换手率。价格漂移会改变现有持仓权重，即使目标不变，也可能需要交易。成交仅来自平台模拟账本，未接入实盘成交。研究状态：尚未验证。",
    "目標ウェイトと現金は計画値で、目標調整比率は実売買の回転率ではありません。価格変動で保有比率が変わるため、目標が同じでも売買が必要な場合があります。約定はシミュレーション台帳のみで、実取引の約定は未接続です。研究状態：未検証。"
  ],
  "每日记录": [
    "Daily records",
    "每日记录",
    "日次記録"
  ],
  "开始日期": [
    "Start date",
    "开始日期",
    "開始日"
  ],
  "结束日期": [
    "End date",
    "结束日期",
    "終了日"
  ],
  "股票代码": [
    "Ticker",
    "股票代码",
    "銘柄コード"
  ],
  "例如 NVDA": [
    "e.g. NVDA",
    "例如 NVDA",
    "例：NVDA"
  ],
  "包含同日旧版本": [
    "Include earlier same-day versions",
    "包含同日旧版本",
    "同日の旧バージョンを含める"
  ],
  "选择一条记录": [
    "Select a record",
    "选择一条记录",
    "記録を選択"
  ],
  "初始目标": [
    "Initial target",
    "初始目标",
    "初期目標"
  ],
  "无法比较": [
    "Not comparable",
    "无法比较",
    "比較不可"
  ],
  "新增": [
    "Added",
    "新增",
    "追加"
  ],
  "移出": [
    "Removed",
    "移出",
    "除外"
  ],
  "上调": [
    "Increased",
    "上调",
    "引き上げ"
  ],
  "下调": [
    "Decreased",
    "下调",
    "引き下げ"
  ],
  "不变": [
    "Unchanged",
    "不变",
    "変更なし"
  ],
  "时窗开放": [
    "Window open",
    "时窗开放",
    "実行時間帯内"
  ],
  "等待开盘": [
    "Awaiting open",
    "等待开盘",
    "寄り付き待ち"
  ],
  "已过期": [
    "Expired",
    "已过期",
    "期限切れ"
  ],
  "生成条件未通过": [
    "Generation checks failed",
    "生成条件未通过",
    "生成条件を満たさず"
  ],
  "时间无法核验": [
    "Timing unverified",
    "时间无法核验",
    "時点を確認できません"
  ],
  "无记录": [
    "No record",
    "无记录",
    "記録なし"
  ],
  "有模拟成交": [
    "Simulated fills recorded",
    "有模拟成交",
    "シミュレーション約定あり"
  ],
  "仅有模拟订单": [
    "Simulated orders only",
    "仅有模拟订单",
    "シミュレーション注文のみ"
  ],
  "未记录模拟成交": [
    "No simulated fills recorded",
    "未记录模拟成交",
    "シミュレーション約定の記録なし"
  ],
  "成交状态未知": [
    "Fill status unknown",
    "成交状态未知",
    "約定状態不明"
  ],
  "独立目标记录": [
    "Standalone target record",
    "独立目标记录",
    "独立した目標記録"
  ],
  "此版本归档缺失或不一致，无法展示明细。": [
    "This archive is missing or inconsistent; details are unavailable.",
    "此版本归档缺失或不一致，无法展示明细。",
    "このバージョンのアーカイブが欠落または不整合のため、詳細を表示できません。"
  ],
  "首条归档，无前一目标可比较": [
    "First archive; no preceding target to compare",
    "首条归档，无前一目标可比较",
    "初回アーカイブ・比較する前回目標なし"
  ],
  "前一目标归档缺失，变化值不可用": [
    "Previous target archive missing; changes unavailable",
    "前一目标归档缺失，变化值不可用",
    "前回目標のアーカイブなし・変化量を算出不可"
  ],
  "以下为模拟成交记录；部分成交不代表目标组合已全部完成。不同模拟账户分别列示。": [
    "These are simulated fills. Partial fills do not mean the target portfolio is fully implemented. Simulated accounts are shown separately.",
    "以下为模拟成交记录；部分成交不代表目标组合已全部完成。不同模拟账户分别列示。",
    "以下はシミュレーション約定です。一部約定は目標ポートフォリオ全体の完成を意味しません。シミュレーション口座別に表示します。"
  ],
  "时间（美东）": [
    "Time (ET)",
    "时间（美东）",
    "時刻（米東部）"
  ],
  "股票 / 方向": [
    "Ticker / side",
    "股票 / 方向",
    "銘柄 / 売買方向"
  ],
  "价格 / 币种": [
    "Price / currency",
    "价格 / 币种",
    "価格 / 通貨"
  ],
  "总执行成本 / 现金佣金": [
    "Total execution cost / cash commission",
    "总执行成本 / 现金佣金",
    "総執行コスト / 現金手数料"
  ],
  "模拟账户": [
    "Simulated account",
    "模拟账户",
    "シミュレーション口座"
  ],
  "独立账本检查通过": [
    "Independent ledger check passed",
    "独立账本检查通过",
    "独立台帳の確認済み"
  ],
  "独立账本检查未确认": [
    "Independent ledger check unconfirmed",
    "独立账本检查未确认",
    "独立台帳の確認未完了"
  ],
  "当前可读取的模拟账本没有关联此版本的成交。": [
    "The currently readable simulated ledger has no fills linked to this version.",
    "当前可读取的模拟账本没有关联此版本的成交。",
    "現在読み取れるシミュレーション台帳に、このバージョンに紐づく約定はありません。"
  ],
  "目标生成、通过数据检查或版本确认均不代表已成交。": [
    "Target generation, data-check passage or version confirmation does not establish fills.",
    "目标生成、通过数据检查或版本确认均不代表已成交。",
    "目標生成、データ確認の通過、バージョン確認は約定を意味しません。"
  ],
  "主组合": [
    "Main portfolio",
    "主组合",
    "主ポートフォリオ"
  ],
  "股票内部权重": [
    "Equity-only weights",
    "股票内部权重",
    "株式内ウェイト"
  ],
  "含现金账户目标": [
    "Account targets including cash",
    "含现金账户目标",
    "現金を含む口座目標"
  ],
  "每日最终版本": [
    "Daily final version",
    "每日最终版本",
    "日次最終バージョン"
  ],
  "同日旧版本": [
    "Earlier same-day version",
    "同日旧版本",
    "同日の旧バージョン"
  ],
  "日频目标": [
    "Daily targets",
    "日频目标",
    "日次目標"
  ],
  "历史目标": [
    "Historical targets",
    "历史目标",
    "過去目標"
  ],
  "股票": [
    "Ticker",
    "股票",
    "銘柄"
  ],
  "变化": [
    "Change",
    "变化",
    "変化"
  ],
  "前次目标": [
    "Previous target",
    "前次目标",
    "前回目標"
  ],
  "本次目标": [
    "Current target",
    "本次目标",
    "今回目標"
  ],
  "权重变化": [
    "Weight change",
    "权重变化",
    "ウェイト変化"
  ],
  "现金": [
    "Cash",
    "现金",
    "現金"
  ],
  "目标": [
    "Target",
    "目标",
    "目標"
  ],
  "模拟成交": [
    "Simulated fills",
    "模拟成交",
    "シミュレーション約定"
  ],
  "查看模拟账户 →": [
    "View simulated account →",
    "查看模拟账户 →",
    "シミュレーション口座を確認 →"
  ],
  "归档与数据检查": [
    "Archive & data checks",
    "归档与数据检查",
    "アーカイブとデータ確認"
  ],
  "通过": [
    "Passed",
    "通过",
    "通過"
  ],
  "未通过": [
    "Failed",
    "未通过",
    "未通過"
  ],
  "未记录": [
    "Not recorded",
    "未记录",
    "未記録"
  ],
  "这些状态仅说明目标记录，不能作为交易执行或收益验证。": [
    "These states describe target records only; they do not validate execution or returns.",
    "这些状态仅说明目标记录，不能作为交易执行或收益验证。",
    "これらの状態は目標記録のみを表し、執行や収益の検証にはなりません。"
  ],
  "目标日期 / 生成时间": [
    "Target date / generated time",
    "目标日期 / 生成时间",
    "目標日 / 生成時刻"
  ],
  "新增 / 移出": [
    "Added / removed",
    "新增 / 移出",
    "追加 / 除外"
  ],
  "目标调整": [
    "Target turnover",
    "目标调整",
    "目標調整比率"
  ],
  "最终版本": [
    "Final version",
    "最终版本",
    "最終バージョン"
  ],
  "旧版本": [
    "Earlier version",
    "旧版本",
    "旧バージョン"
  ],
  "归档缺失": [
    "Archive missing",
    "归档缺失",
    "アーカイブ欠落"
  ],
  "无法核对": [
    "Unverified",
    "无法核对",
    "確認不可"
  ],
  "没有符合筛选条件的记录。": [
    "No records match the filters.",
    "没有符合筛选条件的记录。",
    "条件に一致する記録がありません。"
  ],
  "尚未生成目标的日期不会出现在历史中。": [
    "Dates without generated targets do not appear in history.",
    "尚未生成目标的日期不会出现在历史中。",
    "目標未生成の日付は履歴に表示されません。"
  ],
  "生成并归档目标后，可在这里查看逐股变化。": [
    "After targets are generated and archived, per-stock changes appear here.",
    "生成并归档目标后，可在这里查看逐股变化。",
    "目標を生成・保存すると、銘柄別の変化をここで確認できます。"
  ],
  "本口径已归档日期": [
    "Archived dates on this basis",
    "本口径已归档日期",
    "この基準の保存日数"
  ],
  "最近目标": [
    "Latest target",
    "最近目标",
    "最新目標"
  ],
  "暂无": [
    "None yet",
    "暂无",
    "まだありません"
  ],
  "按生成日期展示": [
    "Shown by generation date",
    "按生成日期展示",
    "生成日で表示"
  ],
  "最新现金目标": [
    "Latest cash target",
    "最新现金目标",
    "最新の現金目標"
  ],
  "股票内部权重不配置现金": [
    "Equity-only weights do not allocate cash",
    "股票内部权重不配置现金",
    "株式内ウェイトは現金を配分しません"
  ],
  "随各方法风险水平更新": [
    "Updated with each method's risk level",
    "随各方法风险水平更新",
    "各方法のリスク水準に応じて更新"
  ],
  "有模拟成交的版本": [
    "Versions with simulated fills",
    "有模拟成交的版本",
    "シミュレーション約定のあるバージョン"
  ],
  "当前可读取的模拟账本": [
    "Currently readable simulated ledger",
    "当前可读取的模拟账本",
    "現在読み取れるシミュレーション台帳"
  ],
  "账本未完整读取": [
    "Ledger not fully read",
    "账本未完整读取",
    "台帳の読み取りが未完了"
  ],
  "主组合保留当时实际选用的方法及切换前后的目标；其他方法在各自历史中连续追踪。": [
    "The main portfolio retains the method used at the time and targets before/after method changes; other methods continue in their own histories.",
    "主组合保留当时实际选用的方法及切换前后的目标；其他方法在各自历史中连续追踪。",
    "主ポートフォリオは当時の方法と切替前後の目標を保持します。他の方法は各自の履歴で継続追跡します。"
  ],
  "此方法与自身上一期同口径目标比较。账户目标采用各自波动率缩放；旧日期未归档的账户目标不补写，可查看股票内部权重。": [
    "This method is compared with its own previous targets on the same basis. Account targets use method-specific volatility scaling. Unarchived historical account targets are not backfilled; equity-only weights remain available.",
    "此方法与自身上一期同口径目标比较。账户目标采用各自波动率缩放；旧日期未归档的账户目标不补写，可查看股票内部权重。",
    "同じ基準でこの方法の前回目標と比較します。口座目標は各方法のボラティリティ調整を用います。未保存の過去口座目標は補完せず、株式内ウェイトを確認できます。"
  ],
  "历史记录读取失败，请重试。": [
    "Unable to load history. Please retry.",
    "历史记录读取失败，请重试。",
    "履歴を読み込めません。再試行してください。"
  ],
  "当前显示内容可能过期。": [
    "Displayed content may be stale.",
    "当前显示内容可能过期。",
    "表示内容が古い可能性があります。"
  ],
  "读取中…": [
    "Loading…",
    "读取中…",
    "読み込み中…"
  ],
  "无": [
    "None",
    "无",
    "なし"
  ],
  "{value} 个百分点": [
    "{value} pp",
    "{value} 个百分点",
    "{value} パーセントポイント"
  ],
  "比较基准：{date} 的目标组合": [
    "Comparison: target portfolio on {date}",
    "比较基准：{date} 的目标组合",
    "比較基準：{date}の目標ポートフォリオ"
  ],
  "{count} 条": [
    "{count} records",
    "{count} 条",
    "{count}件"
  ],
  "{date} · 目标变化": [
    "{date} · target changes",
    "{date} · 目标变化",
    "{date} · 目標の変化"
  ],
  "共 {count} 个版本": [
    "{count} versions in total",
    "共 {count} 个版本",
    "合計{count}バージョン"
  ],
  "生成：{time}": [
    "Generated: {time}",
    "生成：{time}",
    "生成：{time}"
  ],
  "行情截止：{date}": [
    "Price cut-off: {date}",
    "行情截止：{date}",
    "価格の基準日：{date}"
  ],
  "计划执行：{date}": [
    "Planned execution: {date}",
    "计划执行：{date}",
    "執行予定：{date}"
  ],
  "原记录执行时窗：{start} → {end}；{status}。仅检查原记录时窗，不代表账户可下单。": [
    "Original execution window: {start} → {end}; {status}. This checks the recorded window only and does not establish that the account can submit orders.",
    "原记录执行时窗：{start} → {end}；{status}。仅检查原记录时窗，不代表账户可下单。",
    "元の執行時間帯：{start} → {end}、{status}。記録上の時間帯のみを確認し、口座が発注可能であることを示すものではありません。"
  ],
  "。目标调整比例 {turnover}，包含现金变化。": [
    ". Target turnover {turnover}, including cash changes.",
    "。目标调整比例 {turnover}，包含现金变化。",
    "。現金変化を含む目標調整比率は{turnover}です。"
  ],
  "主组合在此版本切换方法：{previous} → {current}。各方法的独立历史仍保留。": [
    "The main portfolio changed method in this version: {previous} → {current}. Each method's independent history is retained.",
    "主组合在此版本切换方法：{previous} → {current}。各方法的独立历史仍保留。",
    "このバージョンで主ポートフォリオの方法を切り替えました：{previous} → {current}。各方法の独立した履歴を保持します。"
  ],
  "新增：{names}": [
    "Added: {names}",
    "新增：{names}",
    "追加：{names}"
  ],
  "移出：{names}": [
    "Removed: {names}",
    "移出：{names}",
    "除外：{names}"
  ],
  "佣金 {value}": [
    "Commission {value}",
    "佣金 {value}",
    "手数料 {value}"
  ],
  "版本：{id}": [
    "Version: {id}",
    "版本：{id}",
    "バージョン：{id}"
  ],
  "比较版本：{id}": [
    "Compared version: {id}",
    "比较版本：{id}",
    "比較バージョン：{id}"
  ],
  "替代版本：{id}": [
    "Superseded version: {id}",
    "替代版本：{id}",
    "置換されたバージョン：{id}"
  ],
  "生成时数据检查：{status}": [
    "Data checks at generation: {status}",
    "生成时数据检查：{status}",
    "生成時のデータ確認：{status}"
  ],
  "Market Intel · sign in": [
    "Market Intel · sign in",
    "Market Intel · 登录",
    "Market Intel · ログイン"
  ],
  "AI & semiconductor value chain · private research dashboard": [
    "AI & semiconductor value chain · private research dashboard",
    "AI 与半导体产业链 · 私人研究仪表盘",
    "AI・半導体バリューチェーン · 専用研究ダッシュボード"
  ],
  "Access token": [
    "Access token",
    "访问令牌",
    "アクセストークン"
  ],
  "access token": [
    "access token",
    "访问令牌",
    "アクセストークン"
  ],
  "Token:": [
    "Token:",
    "令牌：",
    "トークン："
  ],
  "on the host.": [
    "on the host.",
    "请在主机上运行。",
    "ホスト上で実行してください。"
  ],
  "目标时窗开放": [
    "Target window open",
    "目标时窗开放",
    "目標の実行時間帯内"
  ],
  "目标已过期": [
    "Target expired",
    "目标已过期",
    "目標期限切れ"
  ],
  "已阻断": [
    "Blocked",
    "已阻断",
    "ブロック済み"
  ],
  "无目标": [
    "No target",
    "无目标",
    "目標なし"
  ],
  "正在轮询": [
    "Polling",
    "正在轮询",
    "定期確認中"
  ],
  "心跳过期": [
    "Heartbeat stale",
    "心跳过期",
    "ハートビート期限切れ"
  ],
  "无心跳": [
    "No heartbeat",
    "无心跳",
    "ハートビートなし"
  ],
  "记录无效": [
    "Invalid record",
    "记录无效",
    "無効な記録"
  ],
  "已记录": [
    "Recorded",
    "已记录",
    "記録済み"
  ],
  "有缺口": [
    "Gaps detected",
    "有缺口",
    "欠落あり"
  ],
  "未知": [
    "Unknown",
    "未知",
    "不明"
  ],
  "完整": [
    "Complete",
    "完整",
    "完全"
  ],
  "记录不一致": [
    "Inconsistent records",
    "记录不一致",
    "記録不整合"
  ],
  "暂无证据": [
    "No evidence yet",
    "暂无证据",
    "根拠未取得"
  ],
  "尚未接通": [
    "Not connected yet",
    "尚未接通",
    "未接続"
  ],
  "尚未开始": [
    "Not started",
    "尚未开始",
    "未開始"
  ],
  "账本无效": [
    "Invalid ledger",
    "账本无效",
    "台帳無効"
  ],
  "本地重放一致": [
    "Local replay verified",
    "本地重放一致",
    "ローカル再現を確認済み"
  ],
  "数值基线 A": [
    "Numerical baseline A",
    "数值基线 A",
    "数値ベースラインA"
  ],
  "A + 固定文本规则": [
    "A + fixed text rules",
    "A + 固定文本规则",
    "A + 固定テキストルール"
  ],
  "A + LLM 判断": [
    "A + LLM judgment",
    "A + LLM 判断",
    "A + LLM判断"
  ],
  "本次尚无登记": [
    "No registration for this run",
    "本次尚无登记",
    "今回の実行に登録なし"
  ],
  "本次登记失败": [
    "Registration failed for this run",
    "本次登记失败",
    "今回の登録に失敗"
  ],
  "记录无法核验": [
    "Record unverified",
    "记录无法核验",
    "記録を確認できません"
  ],
  "属于其他运行": [
    "Belongs to another run",
    "属于其他运行",
    "別の実行に属します"
  ],
  "仅诊断回放": [
    "Diagnostic replay only",
    "仅诊断回放",
    "診断用リプレイのみ"
  ],
  "证据不足，暂不能比较": [
    "Insufficient evidence for comparison",
    "证据不足，暂不能比较",
    "根拠不足のため比較不可"
  ],
  "正在积累前向观察": [
    "Accumulating forward observations",
    "正在积累前向观察",
    "フォワード観測を蓄積中"
  ],
  "；可比较评分 {paired} / {rows}。登记于 {time}。": [
    "; comparable scores {paired} / {rows}. Registered at {time}.",
    "；可比较评分 {paired} / {rows}。登记于 {time}。",
    "、比較可能なスコア {paired} / {rows}。登録時点：{time}。"
  ],
  "同一可比范围内，加入：{added}；移出：{removed}。全候选池覆盖：{coverage}。": [
    "Within the same comparable scope, added: {added}; removed: {removed}. Full candidate-universe coverage: {coverage}.",
    "同一可比范围内，加入：{added}；移出：{removed}。全候选池覆盖：{coverage}。",
    "同一の比較範囲で追加：{added}、除外：{removed}。候補全体の網羅性：{coverage}。"
  ],
  "不完整": [
    "Incomplete",
    "不完整",
    "不完全"
  ],
  "。缺失的证据不会补写为历史评分。": [
    ". Missing evidence is not backfilled as historical scores.",
    "。缺失的证据不会补写为历史评分。",
    "。不足する根拠を過去スコアとして補完しません。"
  ],
  "下次准备时间（美东）：{time}。本次读取固定到 {run}。": [
    "Next preparation (ET): {time}. This read is pinned to {run}.",
    "下次准备时间（美东）：{time}。本次读取固定到 {run}。",
    "次回の準備時刻（米東部）：{time}。今回の読み取りは{run}に固定されています。"
  ],
  "无运行记录": [
    "No run record",
    "无运行记录",
    "実行記録なし"
  ],
  "检查范围起点：{value}": [
    "Coverage starts: {value}",
    "检查范围起点：{value}",
    "確認範囲の開始：{value}"
  ],
  "未记录：{value}": [
    "Unrecorded: {value}",
    "未记录：{value}",
    "未記録：{value}"
  ],
  "失败：{value}": [
    "Failed: {value}",
    "失败：{value}",
    "失敗：{value}"
  ],
  "不完整：{value}": [
    "Incomplete: {value}",
    "不完整：{value}",
    "不完全：{value}"
  ],
  "未发现": [
    "None found",
    "未发现",
    "検出なし"
  ],
  "这些状态不会补写历史目标或创建成交。": [
    "These statuses do not backfill historical targets or create fills.",
    "这些状态不会补写历史目标或创建成交。",
    "これらの状態は過去目標を補完せず、約定を作成しません。"
  ],
  "已登记 {versions} 个版本，覆盖 {dates} 个实际登记日。首次登记：{first}。": [
    "{versions} versions registered across {dates} actual registration dates. First registration: {first}.",
    "已登记 {versions} 个版本，覆盖 {dates} 个实际登记日。首次登记：{first}。",
    "{versions}バージョンを{dates}の実登録日に登録済み。初回登録：{first}。"
  ],
  "战略模型配置：{availability}；该归档记录的复评数量：{count}。监控新闻所用的模型配置单独管理。": [
    "Strategic model configuration: {availability}; reassessments in this archive: {count}. The model configuration for news monitoring is managed separately.",
    "战略模型配置：{availability}；该归档记录的复评数量：{count}。监控新闻所用的模型配置单独管理。",
    "戦略モデルの設定：{availability}、このアーカイブの再評価数：{count}。ニュース監視のモデル設定は別管理です。"
  ],
  "可尝试调用（未验证请求）": [
    "Call can be attempted (request unverified)",
    "可尝试调用（未验证请求）",
    "呼出可能（リクエスト未検証）"
  ],
  "当前不可调用": [
    "Currently unavailable for calls",
    "当前不可调用",
    "現在呼び出し不可"
  ],
  "前向登记存在完整性错误，无法作为验证证据。": [
    "Forward registration has integrity errors and cannot serve as validation evidence.",
    "前向登记存在完整性错误，无法作为验证证据。",
    "フォワード登録に整合性エラーがあり、検証根拠に使用できません。"
  ],
  "最近一次前向登记或评价失败，请检查运行日志。": [
    "The latest forward registration or evaluation failed. Check the run log.",
    "最近一次前向登记或评价失败，请检查运行日志。",
    "直近のフォワード登録または評価に失敗しました。実行ログを確認してください。"
  ],
  "各组评分记录的来源完整性（证券 × 版本）": [
    "Score provenance completeness by group (security × version)",
    "各组评分记录的来源完整性（证券 × 版本）",
    "グループ別のスコア出典の完全性（銘柄 × バージョン）"
  ],
  "研究组": [
    "Research group",
    "研究组",
    "研究グループ"
  ],
  "来源齐备": [
    "Provenance complete",
    "来源齐备",
    "出典完備"
  ],
  "缺少证据 / 不可用": [
    "Missing evidence / unavailable",
    "缺少证据 / 不可用",
    "根拠欠落 / 利用不可"
  ],
  "总数": [
    "Total",
    "总数",
    "合計"
  ],
  "检查于 {time}": [
    "Checked at {time}",
    "检查于 {time}",
    "確認時点：{time}"
  ],
  "当前目标": [
    "Current target",
    "当前目标",
    "現在の目標"
  ],
  "仅时窗与归档输入检查": [
    "Window and archived-input checks only",
    "仅时窗与归档输入检查",
    "時間帯と保存入力の確認のみ"
  ],
  "后台任务": [
    "Background task",
    "后台任务",
    "バックグラウンドタスク"
  ],
  "只读轮询心跳": [
    "Read-only polling heartbeat",
    "只读轮询心跳",
    "読み取り専用の定期確認"
  ],
  "方法归档": [
    "Construction archives",
    "方法归档",
    "構築方法のアーカイブ"
  ],
  "独立目标与现金": [
    "Independent targets & cash",
    "独立目标与现金",
    "独立した目標と現金"
  ],
  "日频缺口": [
    "Daily gaps",
    "日频缺口",
    "日次の欠落"
  ],
  "未记录 / 失败 / 不完整": [
    "Unrecorded / failed / incomplete",
    "未记录 / 失败 / 不完整",
    "未記録 / 失敗 / 不完全"
  ],
  "证据无法读取；请刷新重试。": [
    "Unable to read evidence. Refresh to retry.",
    "证据无法读取；请刷新重试。",
    "根拠を読み込めません。更新して再試行してください。"
  ],
  "评分对照无法读取。": [
    "Unable to read score comparison.",
    "评分对照无法读取。",
    "スコア比較を読み込めません。"
  ],
  "运行状态读取失败": [
    "Unable to read run status",
    "运行状态读取失败",
    "実行状態を読み込めません"
  ],
  "前向证据读取失败": [
    "Unable to read forward evidence",
    "前向证据读取失败",
    "フォワード検証の根拠を読み込めません"
  ],
  "日频更新完成": [
    "Daily update complete",
    "日频更新完成",
    "日次更新完了"
  ],
  "目标已生成，执行受阻": [
    "Target generated; execution blocked",
    "目标已生成，执行受阻",
    "目標生成済み・執行ブロック"
  ],
  "手动更新完成": [
    "Manual update complete",
    "手动更新完成",
    "手動更新完了"
  ],
  "更新失败": [
    "Update failed",
    "更新失败",
    "更新失敗"
  ],
  "运行中断": [
    "Run interrupted",
    "运行中断",
    "実行中断"
  ],
  "运行记录异常": [
    "Invalid run record",
    "运行记录异常",
    "実行記録異常"
  ],
  "正在更新": [
    "Updating",
    "正在更新",
    "更新中"
  ],
  "暂无运行记录": [
    "No run records yet",
    "暂无运行记录",
    "実行記録がまだありません"
  ],
  "自动日频": [
    "Automatic daily task",
    "自动日频",
    "自動日次タスク"
  ],
  "手动任务": [
    "Manual task",
    "手动任务",
    "手動タスク"
  ],
  "外部任务": [
    "External task",
    "外部任务",
    "外部タスク"
  ],
  "未知来源": [
    "Unknown source",
    "未知来源",
    "出所不明"
  ],
  "已同步": [
    "Synced",
    "已同步",
    "同期済み"
  ],
  "同步中": [
    "Syncing",
    "同步中",
    "同期中"
  ],
  "等待发布": [
    "Awaiting publication",
    "等待发布",
    "公開待ち"
  ],
  "同步受阻": [
    "Sync blocked",
    "同步受阻",
    "同期ブロック"
  ],
  "同步失败": [
    "Sync failed",
    "同步失败",
    "同期失敗"
  ],
  "已停止": [
    "Stopped",
    "已停止",
    "停止済み"
  ],
  "未启动": [
    "Not started",
    "未启动",
    "未開始"
  ],
  "等待新版本": [
    "Awaiting a new version",
    "等待新版本",
    "新バージョン待ち"
  ],
  "正常轮询": [
    "Polling normally",
    "正常轮询",
    "定期確認は正常"
  ],
  "轮询过期": [
    "Polling stale",
    "轮询过期",
    "定期確認が古い状態"
  ],
  "时钟异常": [
    "Clock invalid",
    "时钟异常",
    "時刻異常"
  ],
  "最近任务": [
    "Latest task",
    "最近任务",
    "直近のタスク"
  ],
  "组合日期": [
    "Portfolio date",
    "组合日期",
    "ポートフォリオ日付"
  ],
  "自动调度": [
    "Automatic scheduler",
    "自动调度",
    "自動スケジューラー"
  ],
  "平台历史": [
    "Platform history",
    "平台历史",
    "プラットフォーム履歴"
  ],
  "完成 {time}": [
    "Finished {time}",
    "完成 {time}",
    "完了 {time}"
  ],
  "开始 {time}": [
    "Started {time}",
    "开始 {time}",
    "開始 {time}"
  ],
  "时间未记录": [
    "Time not recorded",
    "时间未记录",
    "時刻未記録"
  ],
  "（洛杉矶）": [
    " (Los Angeles)",
    "（洛杉矶）",
    "（ロサンゼルス）"
  ],
  "生成 {time}（洛杉矶）": [
    "Generated {time} (Los Angeles)",
    "生成 {time}（洛杉矶）",
    "生成 {time}（ロサンゼルス）"
  ],
  "最近检查 {time}（洛杉矶）": [
    "Last checked {time} (Los Angeles)",
    "最近检查 {time}（洛杉矶）",
    "直近の確認 {time}（ロサンゼルス）"
  ],
  "下一次准备：{la} 洛杉矶 / {et} 美东。每 5 分钟检查；电脑须保持唤醒。": [
    "Next preparation: {la} Los Angeles / {et} ET. Checked every 5 minutes; the computer must stay awake.",
    "下一次准备：{la} 洛杉矶 / {et} 美东。每 5 分钟检查；电脑须保持唤醒。",
    "次回の準備：{la} ロサンゼルス / {et} 米東部。5分ごとに確認し、コンピューターは起動状態に保つ必要があります。"
  ],
  "下次自动准备时间未知。": [
    "Next automatic preparation time is unknown.",
    "下次自动准备时间未知。",
    "次回の自動準備時刻は不明です。"
  ],
  "日志来源未知": [
    "Log source unknown",
    "日志来源未知",
    "ログの出所不明"
  ],
  " · 文件更新 {time}（洛杉矶）": [
    " · file updated {time} (Los Angeles)",
    " · 文件更新 {time}（洛杉矶）",
    " · ファイル更新 {time}（ロサンゼルス）"
  ],
  "任务状态已更新": [
    "Task status updated",
    "任务状态已更新",
    "タスク状態を更新しました"
  ],
  "自动日频任务日志": [
    "Automatic daily task log",
    "自动日频任务日志",
    "自動日次タスクのログ"
  ],
  "Invalid token.": [
    "Invalid token.",
    "令牌无效。",
    "トークンが無効です。"
  ],
  "Too many attempts. Try again later.": [
    "Too many attempts. Try again later.",
    "尝试次数过多，请稍后重试。",
    "試行回数が多すぎます。しばらくしてから再試行してください。"
  ],
  "Session expired. Sign in again.": [
    "Session expired. Sign in again.",
    "会话已过期，请重新登录。",
    "セッションの有効期限が切れました。再度ログインしてください。"
  ],
  "Include {name}": [
    "Include {name}",
    "加入 {name}",
    "{name}を含める"
  ],
  "{name} variant": [
    "{name} variant",
    "{name} 的研究版本",
    "{name}の研究バージョン"
  ],
  "{name} capital allocation percent": [
    "{name} capital allocation percent",
    "{name} 的资金配置百分比",
    "{name}の資金配分率"
  ],
  "{count} complete months": [
    "{count} complete months",
    "{count} 个完整月份",
    "{count}完全月"
  ],
  "{connected} connected / {total} strategies · NOT VALIDATED": [
    "{connected} connected / {total} strategies · NOT VALIDATED",
    "已连接 {connected} / {total} 个策略 · 尚未验证",
    "{total}戦略中{connected}を接続 · 未検証"
  ],
  "Awaiting NAV · 尚未计入：": [
    "Awaiting NAV · excluded:",
    "等待净值 · 尚未计入：",
    "NAV待ち · 未算入："
  ],
  " — {count} 个策略参与风险预算 · 资金比例由过去数据确定，上限外预算保留现金。": [
    " — {count} strategies in the risk budget · weights use prior data; amounts above caps stay in cash.",
    " — {count} 个策略参与风险预算 · 资金比例由过去数据确定，上限外预算保留现金。",
    " — {count}戦略をリスク予算に含めます。過去データで比率を決定し、上限超過分は現金に残します。"
  ],
  "Allocated {allocated}% · Unallocated cash {cash}% · 各策略现金留在自己的账户。": [
    "Allocated {allocated}% · Unallocated cash {cash}% · each strategy's cash stays in its own account.",
    "已配置 {allocated}% · 未分配现金 {cash}% · 各策略现金留在自己的账户。",
    "配分済み {allocated}% · 未配分現金 {cash}% · 各戦略の現金はそれぞれの口座に残します。"
  ],
  "{start} — {end} · 原始策略活动与资本时点 · NOT VALIDATED": [
    "{start} — {end} · source strategy activity & capital timing · NOT VALIDATED",
    "{start} — {end} · 原始策略活动与资本时点 · 尚未验证",
    "{start} — {end} · 元の戦略の保有状況と資本時点 · 未検証"
  ],
  "{count} 个事件日期": [
    "{count} event dates",
    "{count} 个事件日期",
    "{count}イベント日"
  ],
  "{active} / {total} 日 · {fraction}": [
    "{active} / {total} days · {fraction}",
    "{active} / {total} 日 · {fraction}",
    "{active} / {total}日 · {fraction}"
  ],
  "{start} — {end} · {months} complete months · {currency} · {count} sleeves": [
    "{start} — {end} · {months} complete months · {currency} · {count} sleeves",
    "{start} — {end} · {months} 个完整月份 · {currency} · {count} 个策略账户",
    "{start} — {end} · {months}完全月 · {currency} · {count}戦略口座"
  ],
  "{name} 占初始资金 {weight}，贡献组合方差的 {risk}。该贡献按实际评估期计算；预算上限和现金比例另见配置结果。": [
    "{name} receives {weight} of initial capital and contributes {risk} of portfolio variance. This uses the actual evaluation period; budget caps and cash weights are shown in the allocation results.",
    "{name} 占初始资金 {weight}，贡献组合方差的 {risk}。该贡献按实际评估期计算；预算上限和现金比例另见配置结果。",
    "{name}は初期資金の{weight}を受け取り、ポートフォリオ分散の{risk}に寄与します。実際の評価期間で計算し、予算上限と現金比率は配分結果に表示します。"
  ],
  "{start} — {end} · {months} 个月 · {currency}。固定情景网格；融资与杠杆可执行性未验证。": [
    "{start} — {end} · {months} months · {currency}. Fixed scenario grid; financing and leverage executability are unverified.",
    "{start} — {end} · {months} 个月 · {currency}。固定情景网格；融资与杠杆可执行性未验证。",
    "{start} — {end} · {months}か月 · {currency}。固定シナリオを比較し、資金調達とレバレッジの実行可能性は未検証です。"
  ],
  "敞口比较未完成：{error}": [
    "Exposure comparison incomplete: {error}",
    "敞口比较未完成：{error}",
    "エクスポージャー比較未完了：{error}"
  ],
  "导入未完成：{error}": [
    "Import incomplete: {error}",
    "导入未完成：{error}",
    "インポート未完了：{error}"
  ],
  "已冻结导入 · {status}。新策略默认不勾选，请在面板选择后计算。": [
    "Frozen import complete · {status}. New strategies are unselected by default; select them in the panel before calculating.",
    "已冻结导入 · {status}。新策略默认不勾选，请在面板选择后计算。",
    "固定結果のインポート完了 · {status}。新しい戦略は標準では未選択です。パネルで選んでから計算してください。"
  ],
  "Choose a construction.": [
    "Choose a construction.",
    "请选择组合方法。",
    "構築方法を選択してください。"
  ],
  "This construction has no book in this layer.": [
    "This construction has no book in this layer.",
    "此组合方法在该层没有账簿。",
    "この構築方法には選択したレイヤーの帳簿がありません。"
  ],
  "This exact window is unavailable.": [
    "This exact window is unavailable.",
    "此完整窗口不可用。",
    "この期間全体を利用できません。"
  ],
  "Both risk snapshots are required.": [
    "Both risk snapshots are required.",
    "需要两份风险快照。",
    "両方のリスクスナップショットが必要です。"
  ],
  "Run IDs differ or are missing. Refresh the complete review.": [
    "Run IDs differ or are missing. Refresh the complete review.",
    "运行 ID 不一致或缺失，请刷新整个复核页面。",
    "実行IDが不一致または欠落しています。レビュー全体を更新してください。"
  ],
  "Risk snapshot mismatch: {key}. Refresh the complete review.": [
    "Risk snapshot mismatch: {key}. Refresh the complete review.",
    "风险快照不一致：{key}。请刷新整个复核页面。",
    "リスクスナップショット不一致：{key}。レビュー全体を更新してください。"
  ],
  "Repair price gaps": [
    "Repair price gaps",
    "修复价格缺口",
    "価格データの欠落を修復"
  ],
  "{count} target names: {names}. Rebuild the snapshot before investment use.": [
    "{count} target names: {names}. Rebuild the snapshot before investment use.",
    "{count} 只目标证券：{names}。用于投资前请重建快照。",
    "対象銘柄{count}件：{names}。投資判断に使う前にスナップショットを再構築してください。"
  ],
  "Run is not actionable": [
    "Run is not actionable",
    "运行结果不可用于执行",
    "実行結果は執行に利用できません"
  ],
  "Actionability has not been established.": [
    "Actionability has not been established.",
    "尚未确认可执行性。",
    "執行可能性を確認していません。"
  ],
  "Legacy risk service": [
    "Legacy risk service",
    "旧版风险服务",
    "旧バージョンのリスクサービス"
  ],
  "Unknown version": [
    "Unknown version",
    "版本未知",
    "バージョン不明"
  ],
  "{version} is being served. Figures are diagnostic; the current risk.v4 controls are not active on this service.": [
    "{version} is being served. Figures are diagnostic; the current risk.v4 controls are not active on this service.",
    "当前服务版本为 {version}。数值仅用于诊断；当前 risk.v4 控制在此服务上未启用。",
    "現在のサービスは{version}です。数値は診断用で、このサービスではrisk.v4の管理機能は稼働していません。"
  ],
  "3Y coverage unavailable": [
    "3Y coverage unavailable",
    "3 年覆盖不可用",
    "3年の範囲を利用不可"
  ],
  "{unavailable} of {total} labels lack this window. ": [
    "{unavailable} of {total} labels lack this window. ",
    "{total} 项中有 {unavailable} 项缺少此窗口。",
    "{total}件中{unavailable}件はこの期間が不足しています。"
  ],
  "Insufficient price history.": [
    "Insufficient price history.",
    "价格历史不足。",
    "価格履歴が不足しています。"
  ],
  "Define the mandate benchmark": [
    "Define the mandate benchmark",
    "定义委托基准",
    "運用委託のベンチマークを定義"
  ],
  "Current-universe and SOXX tracking error are research comparisons. No mandate active-risk limit can be assessed.": [
    "Current-universe and SOXX tracking error are research comparisons. No mandate active-risk limit can be assessed.",
    "当前证券池与 SOXX 的跟踪误差仅为研究对照，无法评估委托组合的主动风险上限。",
    "現ユニバースとSOXXに対するトラッキングエラーは研究上の比較です。運用委託のアクティブリスク上限を評価できません。"
  ],
  "Execution capacity unassessed": [
    "Execution capacity unassessed",
    "执行容量尚未评估",
    "執行能力は未評価"
  ],
  "A verified NAV, position ledger and execution assumptions are required for liquidation time and market impact.": [
    "A verified NAV, position ledger and execution assumptions are required for liquidation time and market impact.",
    "估计平仓时间与市场冲击需要经核验的 NAV、持仓账本和执行假设。",
    "清算所要時間と市場インパクトの評価には、検証済みNAV、ポジション台帳、執行の前提が必要です。"
  ],
  "Resolve target constraint checks": [
    "Resolve target constraint checks",
    "解决目标约束检查问题",
    "目標制約の確認事項を解決"
  ],
  "{failed} failed; {unknown} unknown. Unknown checks do not count as passes.": [
    "{failed} failed; {unknown} unknown. Unknown checks do not count as passes.",
    "{failed} 项未通过；{unknown} 项未知。未知检查不计为通过。",
    "未通過{failed}件、不明{unknown}件。不明の確認項目は通過扱いにしません。"
  ],
  "{months}-month IC time series": [
    "{months}-month IC time series",
    "{months} 个月 IC 时间序列",
    "{months}か月IC時系列"
  ],
  "{ticker} tearsheet": [
    "{ticker} tearsheet",
    "{ticker} 单股研究摘要",
    "{ticker}銘柄レポート"
  ],
  "Open {title} full screen": [
    "Open {title} full screen",
    "全屏打开 {title}",
    "{title}を全画面で開く"
  ],
  "{count} names": [
    "{count} names",
    "{count} 只证券",
    "{count}銘柄"
  ],
  "{count} runs": [
    "{count} runs",
    "{count} 次运行",
    "{count}回の実行"
  ],
  "failed to load: {error}": [
    "failed to load: {error}",
    "读取失败：{error}",
    "読み込み失敗：{error}"
  ],
  " · last refresh dry-run": [
    " · last refresh dry-run",
    " · 上次刷新为试运行",
    " · 前回の更新は試行実行"
  ],
  "Download the investor deck, {size} MB": [
    "Download the investor deck, {size} MB",
    "下载投资者演示文稿，{size} MB",
    "投資家向け資料をダウンロード、{size} MB"
  ],
  "⬇ Investor deck ({size} MB)": [
    "⬇ Investor deck ({size} MB)",
    "⬇ 投资者演示文稿（{size} MB）",
    "⬇ 投資家向け資料（{size} MB）"
  ],
  "running": [
    "running",
    "正在运行",
    "実行中"
  ],
  " since {time} UTC": [
    " since {time} UTC",
    "，开始于 {time} UTC",
    "、開始 {time} UTC"
  ],
  " · stages: {stages}": [
    " · stages: {stages}",
    " · 阶段：{stages}",
    " · ステージ：{stages}"
  ],
  " · full run, 5-10 min": [
    " · full run, 5-10 min",
    " · 完整流程，5-10 分钟",
    " · 全実行、5〜10分"
  ],
  " · pages refresh when it finishes": [
    " · pages refresh when it finishes",
    " · 完成后页面自动刷新",
    " · 完了後にページを更新"
  ],
  "could not start: {error}": [
    "could not start: {error}",
    "无法启动：{error}",
    "開始できません：{error}"
  ],
  "load failed: {error}": [
    "load failed: {error}",
    "读取失败：{error}",
    "読み込み失敗：{error}"
  ],
  "Feature-level IC (1-month)": [
    "Feature-level IC (1-month)",
    "特征级 IC（1 个月）",
    "特徴量IC（1か月）"
  ],
  "IC decay by horizon": [
    "IC decay by horizon",
    "IC 随期限衰减",
    "期間別のIC減衰"
  ],
  "Permutation and random-weight null distributions": [
    "Permutation and random-weight null distributions",
    "置换与随机权重的零假设分布",
    "置換・ランダムウェイトの帰無分布"
  ],
  "Quintile forward returns": [
    "Quintile forward returns",
    "五分位前瞻收益",
    "五分位のフォワード収益"
  ],
  "Top-15 replay (hindsight illustration)": [
    "Top-15 replay (hindsight illustration)",
    "前 15 名回放（事后示例）",
    "上位15銘柄リプレイ（事後例示）"
  ],
  "Walk-forward pillar weights": [
    "Walk-forward pillar weights",
    "滚动前推支柱权重",
    "ウォークフォワードの柱ウェイト"
  ],
  "Hindsight replay of the target list": [
    "Hindsight replay of the target list",
    "目标名单的事后回放",
    "目標銘柄リストの事後リプレイ"
  ],
  "Return correlation heatmap": [
    "Return correlation heatmap",
    "收益相关性热图",
    "収益相関ヒートマップ"
  ],
  "Efficient frontier": [
    "Efficient frontier",
    "有效前沿",
    "効率的フロンティア"
  ],
  "Estimate revisions": [
    "Estimate revisions",
    "预期修正",
    "業績予想の修正"
  ],
  "Growth profile": [
    "Growth profile",
    "增长特征",
    "成長プロファイル"
  ],
  "Macro backdrop": [
    "Macro backdrop",
    "宏观背景",
    "マクロ環境"
  ],
  "Margin profile": [
    "Margin profile",
    "利润率特征",
    "利益率プロファイル"
  ],
  "Target weights": [
    "Target weights",
    "目标权重",
    "目標ウェイト"
  ],
  "Quality map": [
    "Quality map",
    "质量分布",
    "クオリティマップ"
  ],
  "Relative performance": [
    "Relative performance",
    "相对表现",
    "相対パフォーマンス"
  ],
  "Risk contributions": [
    "Risk contributions",
    "风险贡献",
    "リスク寄与"
  ],
  "Composite score ranking": [
    "Composite score ranking",
    "综合评分排名",
    "総合スコア順位"
  ],
  "Screening funnel": [
    "Screening funnel",
    "筛选漏斗",
    "スクリーニングの絞り込み"
  ],
  "Segment structure": [
    "Segment structure",
    "细分领域结构",
    "セグメント構成"
  ],
  "Upside by valuation method": [
    "Upside by valuation method",
    "各估值方法的上涨空间",
    "評価方法別の上昇余地"
  ],
  "Valuation vs growth": [
    "Valuation vs growth",
    "估值与增长",
    "バリュエーションと成長"
  ],
  "No live forward IC yet: {count} snapshot(s) archived; snapshots with realised forward returns: {horizons}. The table appears once at least one snapshot has realised forward returns for 20+ names; the 12m gate needs ≥ 12 snapshots.": [
    "No live forward IC yet: {count} snapshot(s) archived; snapshots with realised forward returns: {horizons}. The table appears once at least one snapshot has realised forward returns for 20+ names; the 12m gate needs ≥ 12 snapshots.",
    "暂无实时前向 IC：已归档 {count} 个快照；已有已实现前瞻收益的快照：{horizons}。至少一个快照具备 20 只以上证券的前瞻收益时才显示表格；12 个月门槛需要 ≥ 12 个快照。",
    "実観測フォワードICはまだありません。保存済みスナップショット{count}件、実現フォワード収益のある件数：{horizons}。20銘柄以上のフォワード収益を持つスナップショットが1件以上になると表を表示します。12か月条件には12件以上が必要です。"
  ],
  "Only {archive}; weight and rank history appears after the next daily runs.": [
    "Only {archive}; weight and rank history appears after the next daily runs.",
    "目前仅有{archive}；后续日频运行后将显示权重与排名历史。",
    "現在は{archive}のみです。今後の日次実行後にウェイトと順位の履歴を表示します。"
  ],
  "one run archived ({date})": [
    "one run archived ({date})",
    "一次已归档运行（{date}）",
    "保存済み実行1件（{date}）"
  ],
  "no runs archived": [
    "no runs archived",
    "尚无已归档运行",
    "保存済み実行なし"
  ],
  "Phase reported by the API: {phase}": [
    "Phase reported by the API: {phase}",
    "API 报告的阶段：{phase}",
    "APIが報告したフェーズ：{phase}"
  ]
});
