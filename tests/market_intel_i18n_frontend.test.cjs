'use strict';
// Real DOM checks against synthetic pages only. No app API or research run.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const STATIC = path.join(__dirname, '../market_intel/static');
const CATALOGS = ['i18n-common.js', 'i18n-home.js', 'i18n-portfolio.js', 'i18n-platform.js'];
const read = name => fs.readFileSync(path.join(STATIC, name), 'utf8');

test('translation catalogs contain complete authored strings in all three languages', () => {
  const rows = [];
  const context = vm.createContext({window: {MI_I18N: {register: entries => rows.push(...Object.entries(entries))}}});
  for (const file of CATALOGS) vm.runInContext(read(file), context, {filename: file});
  assert.ok(rows.length > 50, 'Expected meaningful navigation, help and state coverage');
  for (const [source, values] of rows) {
    assert.equal(typeof source, 'string');
    const row = Array.isArray(values) ? values : [values.en, values.zh, values.ja];
    assert.equal(row.length, 3, source);
    for (const value of row) assert.equal(typeof value, 'string', source);
    assert.ok(row[0].trim(), `English fallback missing: ${source}`);
    assert.ok(row[1].trim(), `Chinese translation missing: ${source}`);
    assert.ok(row[2].trim(), `Japanese translation missing: ${source}`);
    const placeholders = value => [...value.matchAll(/\{([a-zA-Z][\w]*)\}/g)].map(m => m[1]).sort();
    assert.deepEqual(placeholders(row[1]), placeholders(row[0]), source);
    assert.deepEqual(placeholders(row[2]), placeholders(row[0]), source);
  }
  // Absolute-value bars in authored help must survive catalog delimiter parsing.
  const statistic = rows.find(([source]) => source === 'monthly excess, Newey-West t (3 lags). |t| < 2 = not distinguishable from 1/N');
  assert.ok(statistic, 'Missing complete Newey-West help source (check pipe delimiter escaping)');
  for (const value of statistic[1]) assert.ok(value.includes('|t|'), value);
});

let playwright;
for (const candidate of [process.env.MARKET_INTEL_PLAYWRIGHT_PATH, 'playwright'].filter(Boolean)) {
  try { playwright = require(candidate); break; } catch (_) {}
}
let browser, browserError;
test.before(async () => {
  if (!playwright || process.env.MARKET_INTEL_I18N_BROWSER !== '1') return;
  const chrome = process.env.MARKET_INTEL_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const options = {headless: true};
  if (fs.existsSync(chrome)) options.executablePath = chrome;
  try { browser = await playwright.chromium.launch(options); } catch (error) { browserError = error; }
});
test.after(async () => { if (browser) await browser.close(); });

const FIXTURE = `<!doctype html><html lang="en"><head><title>Fixture heading</title></head><body>
<header class="top"><h1>Market Intel</h1><nav id="nav"><a href="/portfolio">Home</a></nav></header>
<main><h2 id="heading">Fixture heading</h2><button id="refresh" title="Fixture help" aria-label="Fixture help">Fixture loading</button>
<input id="question" value="Home" placeholder="Fixture help"><textarea id="note">Home</textarea>
<p id="original-news" translate="no" title="Fixture help">Home</p><div data-no-i18n><span id="evidence">Home</span></div>
<div class="ev"><a id="news-title" class="title">Home</a><p id="news-line" class="line">Home</p></div>
<div class="answer" id="answer">Home</div><div class="cn" id="company">Home</div>
<code id="formula">direction/2 × materiality × confidence</code><pre id="literal">Home</pre>
<table><thead><tr><th id="column">Home</th></tr></thead><tbody><tr>
<td id="data-text">Home</td><td id="number">-0.0123456789</td><td id="identifier">run_2026-09-30_NVDA</td>
<td id="explicit-label" data-i18n-label>Home</td></tr></tbody></table>
<div class="js-plotly-plot"><svg><text id="source-svg">Home</text></svg></div>
<div id="dynamic"></div><div class="plot" id="chart"></div></main></body></html>`;
const DICTIONARY = {
  'Fixture heading': ['Fixture heading', '测试标题', 'テスト見出し'],
  'Fixture help': ['Fixture help', '测试说明', 'テストの説明'],
  'Fixture loading': ['Fixture loading', '正在加载', '読み込み中'],
  Home: ['Home', '首页', 'ホーム'],
  'Fixture x': ['Fixture x', '日期', '日付'],
  'Fixture y': ['Fixture y', '收益', 'リターン'],
  'Fixture annotation': ['Fixture annotation', '说明', '注記'],
  'Fixture count {count}, symbol {symbol}': ['Count {count}, symbol {symbol}', '数量 {count}，代码 {symbol}', '件数 {count}、銘柄 {symbol}'],
  'Fallback fixture': ['Safe fallback', '', '']
};

// A small, dependency-free DOM model exercises the source-record rules even in
// environments that cannot launch a browser. Browser tests below cover real DOM
// selectors, observers, and layout separately when explicitly enabled.
function memoryDOM({stored, denyStorage = false} = {}) {
  const listeners = new Map(), storage = new Map(stored ? [['mi-language', stored]] : []);
  const simple = (el, selector) => {
    const tag = /^([a-z][\w-]*)/i.exec(selector);
    if (tag && el.tagName.toLowerCase() !== tag[1].toLowerCase()) return false;
    for (const m of selector.matchAll(/\.([\w-]+)/g)) if (!el.className.split(/\s+/).includes(m[1])) return false;
    for (const m of selector.matchAll(/#([\w-]+)/g)) if (el.id !== m[1]) return false;
    for (const m of selector.matchAll(/\[([\w-]+)(?:=["']?([^\]"']+)["']?)?\]/g)) {
      if (!el.hasAttribute(m[1]) || (m[2] !== undefined && el.getAttribute(m[1]) !== m[2])) return false;
    }
    return true;
  };
  const matches = (el, selectors) => selectors.split(',').some(selector => {
    const parts = selector.trim().split(/\s+/); if (!simple(el, parts.pop())) return false;
    let parent = el.parentElement;
    while (parts.length) {
      const part = parts.pop(); while (parent && !simple(parent, part)) parent = parent.parentElement;
      if (!parent) return false; parent = parent.parentElement;
    }
    return true;
  });
  class Text {
    constructor(value) { this.nodeType = 3; this.nodeValue = value; this.parentElement = null; }
    get textContent() { return this.nodeValue; }
  }
  class Element {
    constructor(tag) {
      this.nodeType = 1; this.tagName = tag.toUpperCase(); this.childNodes = []; this.parentElement = null;
      this.attributes = new Map(); this.dataset = {}; this.value = ''; this.events = new Map();
      this.classList = {add: (...values) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...values])].join(' '); }};
    }
    get id() { return this.getAttribute('id') || ''; } set id(v) { this.setAttribute('id', v); }
    get className() { return this.getAttribute('class') || ''; } set className(v) { this.setAttribute('class', v); }
    get firstChild() { return this.childNodes[0] || null; }
    get textContent() { return this.childNodes.map(c => c.textContent).join(''); }
    set textContent(v) { this.childNodes = []; if (v !== '') this.appendChild(new Text(String(v))); }
    setAttribute(k, v) { this.attributes.set(k, String(v)); }
    getAttribute(k) { return this.attributes.has(k) ? this.attributes.get(k) : null; }
    hasAttribute(k) { return this.attributes.has(k); }
    appendChild(node) { node.parentElement = this; this.childNodes.push(node); return node; }
    insertBefore(node, before) { node.parentElement = this; const i = this.childNodes.indexOf(before); this.childNodes.splice(i < 0 ? 0 : i, 0, node); return node; }
    matches(s) { return matches(this, s); }
    closest(s) { for (let e = this; e; e = e.parentElement) if (e.matches(s)) return e; return null; }
    querySelectorAll(s) { const out = []; const visit = e => { for (const c of e.childNodes) if (c.nodeType === 1) { if (c.matches(s)) out.push(c); visit(c); } }; visit(this); return out; }
    querySelector(s) { return this.querySelectorAll(s)[0] || null; }
    addEventListener(name, fn) { this.events.set(name, [...(this.events.get(name) || []), fn]); }
  }
  const html = new Element('html'), head = html.appendChild(new Element('head')), body = html.appendChild(new Element('body'));
  const document = {nodeType: 9, documentElement: html, head, body, readyState: 'loading',
    createElement: tag => new Element(tag),
    getElementById: id => html.querySelector('#' + id),
    querySelector: s => html.querySelector(s),
    querySelectorAll: s => html.querySelectorAll(s),
    addEventListener(name, fn) { listeners.set(name, [...(listeners.get(name) || []), fn]); },
    dispatchEvent(event) { for (const fn of listeners.get(event.type) || []) fn(event); }
  };
  const el = (tag, parent = body, text = '', attributes = {}) => {
    const node = document.createElement(tag); for (const [k, v] of Object.entries(attributes)) node.setAttribute(k, v);
    node.textContent = text; parent.appendChild(node); return node;
  };
  el('title', head, 'Fixture heading'); el('header', body, '', {class: 'top'});
  const ctx = {document, console, setTimeout, clearTimeout, Promise, Map, WeakMap,
    CustomEvent: class { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    matchMedia: () => ({matches: false}), addEventListener() {},
    MutationObserver: class { constructor(fn) { ctx.flushObserver = fn; } observe() {} },
    localStorage: {getItem(key) { if (denyStorage) throw Error('Blocked storage'); return storage.get(key) || null; },
      setItem(key, value) { if (denyStorage) throw Error('Blocked storage'); storage.set(key, value); }}
  };
  ctx.window = ctx; vm.createContext(ctx); vm.runInContext(read('i18n.js'), ctx);
  ctx.MI_I18N.register(DICTIONARY); ctx.MI_I18N.init();
  return {ctx, document, el, storage};
}

test('offline runtime repeatedly restores original labels and survives persistence failures', () => {
  for (const options of [{}, {stored: 'invalid'}, {denyStorage: true}]) {
    const h = memoryDOM(options), api = h.ctx.MI_I18N;
    assert.equal(api.language, 'en');
    const heading = h.el('h2', undefined, 'Fixture heading');
    const button = h.el('button', undefined, 'Fixture loading', {'aria-label': 'Fixture help', title: 'Fixture help'});
    for (const [code, title, help, state] of [['zh', '测试标题', '测试说明', '正在加载'], ['ja', 'テスト見出し', 'テストの説明', '読み込み中'], ['en', 'Fixture heading', 'Fixture help', 'Fixture loading'], ['ja', 'テスト見出し', 'テストの説明', '読み込み中']]) {
      api.setLanguage(code); assert.equal(heading.textContent, title); assert.equal(button.getAttribute('aria-label'), help); assert.equal(button.textContent, state);
    }
    if (!options.denyStorage) assert.equal(memoryDOM({stored: h.storage.get('mi-language')}).ctx.MI_I18N.language, 'ja');
    assert.equal(api.t('Fallback fixture'), 'Safe fallback'); assert.equal(api.t('unknown original'), 'unknown original');
    assert.equal(api.t('Fixture count {count}, symbol {symbol}', {count: '0.0123456789', symbol: 'NVDA'}), '件数 0.0123456789、銘柄 NVDA');
    assert.equal(api.setLanguage('invalid'), 'en');
  }
});

test('offline runtime preserves source cells and exclusion regions while translating opt-in labels', async () => {
  const h = memoryDOM(), api = h.ctx.MI_I18N, preserved = [];
  for (const [tag, attributes] of [['p', {translate: 'no'}], ['div', {'data-no-i18n': ''}], ['pre', {}], ['code', {}], ['textarea', {}], ['div', {class: 'answer'}], ['div', {class: 'cn'}]]) preserved.push(h.el(tag, undefined, 'Home', attributes));
  const table = h.el('table'), tbody = h.el('tbody', table), row = h.el('tr', tbody);
  preserved.push(h.el('th', row, 'Home'), h.el('td', row, 'Home'), h.el('td', row, '-0.0123456789'), h.el('td', row, 'run_2026-09-30_NVDA'));
  const cardMetrics = h.el('div', undefined, '', {class: 'cm'});
  preserved.push(h.el('span', cardMetrics, 'Home')); // An owner/title is source text even when it matches a UI key.
  const explicit = h.el('td', row, 'Home', {'data-i18n-label': ''});
  const formula = h.el('p', undefined, 'direction/2 × materiality × confidence'); preserved.push(formula);
  const before = preserved.map(e => e.textContent);
  for (const [code, label] of [['zh', '首页'], ['ja', 'ホーム'], ['en', 'Home'], ['zh', '首页'], ['en', 'Home']]) {
    api.setLanguage(code); assert.deepEqual(preserved.map(e => e.textContent), before); assert.equal(explicit.textContent, label);
  }
  api.setLanguage('ja'); const late = h.el('button', undefined, '首页'); h.ctx.flushObserver(); await Promise.resolve();
  assert.equal(late.textContent, 'ホーム'); late.textContent = 'Fixture loading'; h.ctx.flushObserver(); await Promise.resolve();
  assert.equal(late.textContent, '読み込み中'); api.setLanguage('en'); assert.equal(late.textContent, 'Fixture loading');
});

test('offline chart copies display labels and preserves original numerical/source references', async () => {
  const h = memoryDOM(), calls = []; h.el('div', undefined, '', {id: 'chart'});
  h.ctx.Plotly = {react: async (el, traces, layout) => { calls.push({traces, layout}); }};
  vm.runInContext(read('base.js'), h.ctx);
  const traces = [{name: 'Home', x: ['2026-09-29', '2026-09-30'], y: [0.0123456789, -0.9], customdata: ['NVDA', 'Home']}];
  const layout = {title: 'Fixture heading', xaxis: {title: 'Fixture x'}, yaxis2: {title: {text: 'Fixture y'}}, annotations: [{text: 'Fixture annotation', x: 0.0123456789, y: -0.9}]};
  const before = JSON.stringify({traces, layout}); await h.ctx.plot('chart', traces, layout);
  for (const [code, name, title, y] of [['zh', '首页', '测试标题', '收益'], ['ja', 'ホーム', 'テスト見出し', 'リターン'], ['en', 'Home', 'Fixture heading', 'Fixture y']]) {
    h.ctx.MI_I18N.setLanguage(code); await Promise.resolve(); await Promise.resolve();
    const call = calls.at(-1); assert.equal(call.traces[0].name, name); assert.equal(call.layout.title, title); assert.equal(call.layout.yaxis2.title.text, y);
    assert.equal(call.traces[0].x, traces[0].x); assert.equal(call.traces[0].y, traces[0].y); assert.equal(call.traces[0].customdata, traces[0].customdata);
    assert.equal(JSON.stringify({traces, layout}), before);
  }
});

async function pageFor(t, options = {}) {
  if (process.env.MARKET_INTEL_I18N_BROWSER !== '1') { t.skip('Set MARKET_INTEL_I18N_BROWSER=1 to enable isolated headless browser checks'); return null; }
  if (!playwright) { t.skip('Playwright is optional; install/use an existing browser runtime to run DOM checks'); return null; }
  assert.ifError(browserError);
  const context = await browser.newContext({viewport: options.viewport || {width: 1280, height: 900}});
  t.after(() => context.close());
  if (options.storage) await context.addInitScript(({stored, deny}) => {
    if (deny) Object.defineProperty(window, 'localStorage', {get() { throw new DOMException('Blocked storage', 'SecurityError'); }});
    else localStorage.setItem('mi-language', stored);
  }, options.storage);
  const page = await context.newPage();
  await page.route('http://market-intel.test/**', route => route.fulfill({contentType: 'text/html', body: options.html || FIXTURE}));
  await page.goto('http://market-intel.test/');
  const load = async () => {
    await page.addScriptTag({content: read('i18n.js')});
    for (const name of CATALOGS) if (fs.existsSync(path.join(STATIC, name))) await page.addScriptTag({content: read(name)});
    await page.evaluate(dict => { MI_I18N.register(dict); MI_I18N.translate(); }, DICTIONARY);
  };
  await load();
  return {page, context, reload: async () => { await page.reload(); await load(); }};
}
const select = async (page, language) => {
  await page.selectOption('#mi-language', language);
  await page.waitForFunction(code => MI_I18N.language === code && document.documentElement.getAttribute('data-language') === code, language);
};

test('visible selector repeatedly switches text, accessible labels, table headings and templates', async t => {
  const h = await pageFor(t); if (!h) return; const {page} = h;
  assert.deepEqual(await page.locator('#mi-language option').allTextContents(), ['English', '中文', '日本語']);
  assert.equal(await page.locator('#mi-language').getAttribute('aria-label'), 'English / 中文 / 日本語');
  assert.equal(await page.locator('#mi-language').isVisible(), true);
  await page.evaluate(() => { window.languageEvents = []; document.addEventListener('languagechange', e => languageEvents.push(e.detail.language)); });
  for (const [language, heading, home, help, loading, tag] of [
    ['zh', '测试标题', '首页', '测试说明', '正在加载', 'zh-CN'],
    ['ja', 'テスト見出し', 'ホーム', 'テストの説明', '読み込み中', 'ja'],
    ['en', 'Fixture heading', 'Home', 'Fixture help', 'Fixture loading', 'en'],
    ['ja', 'テスト見出し', 'ホーム', 'テストの説明', '読み込み中', 'ja'],
    ['zh', '测试标题', '首页', '测试说明', '正在加载', 'zh-CN'],
    ['en', 'Fixture heading', 'Home', 'Fixture help', 'Fixture loading', 'en']]) {
    await select(page, language);
    assert.equal(await page.locator('#heading').textContent(), heading);
    assert.equal(await page.title(), heading);
    assert.equal(await page.locator('#nav a').textContent(), home);
    assert.equal(await page.locator('#column').textContent(), home);
    assert.equal(await page.locator('#explicit-label').textContent(), home);
    assert.equal(await page.locator('#refresh').textContent(), loading);
    assert.equal(await page.locator('#refresh').getAttribute('title'), help);
    assert.equal(await page.locator('#refresh').getAttribute('aria-label'), help);
    assert.equal(await page.locator('#question').getAttribute('placeholder'), help);
    assert.equal(await page.locator('html').getAttribute('lang'), tag);
  }
  assert.deepEqual(await page.evaluate(() => languageEvents), ['zh', 'ja', 'en', 'ja', 'zh', 'en']);
  await select(page, 'ja');
  assert.equal(await page.evaluate(() => t('Fallback fixture')), 'Safe fallback');
  assert.equal(await page.evaluate(() => t('unknown original phrase')), 'unknown original phrase');
  assert.equal(await page.evaluate(() => t('Fixture count {count}, symbol {symbol}', {count: '0.0123456789', symbol: 'NVDA'})), '件数 0.0123456789、銘柄 NVDA');
});

test('selected language survives refresh and restores Chinese-source aliases', async t => {
  const h = await pageFor(t); if (!h) return;
  await select(h.page, 'ja');
  assert.equal(await h.page.evaluate(() => localStorage.getItem('mi-language')), 'ja');
  await h.reload();
  assert.equal(await h.page.locator('#mi-language').inputValue(), 'ja');
  assert.equal(await h.page.locator('#heading').textContent(), 'テスト見出し');
  await h.page.evaluate(() => { const b = document.createElement('button'); b.id = 'alias'; b.textContent = '首页'; document.body.append(b); });
  await h.page.waitForFunction(() => document.getElementById('alias').textContent === 'ホーム');
  await select(h.page, 'en');
  assert.equal(await h.page.locator('#alias').textContent(), 'Home');
});

test('invalid or inaccessible storage safely falls back to English', async t => {
  for (const storage of [{stored: 'xx'}, {deny: true}]) {
    const h = await pageFor(t, {storage}); if (!h) return;
    assert.equal(await h.page.evaluate(() => MI_I18N.language), 'en');
    assert.equal(await h.page.locator('#heading').textContent(), 'Fixture heading');
    await select(h.page, 'zh');
    assert.equal(await h.page.locator('#heading').textContent(), '测试标题');
    assert.equal(await h.page.evaluate(() => MI_I18N.setLanguage('invalid')), 'en');
    assert.equal(await h.page.locator('#mi-language').inputValue(), 'en');
  }
});

test('original news, evidence, source values, identifiers and formulas survive every switch', async t => {
  const h = await pageFor(t); if (!h) return; const {page} = h;
  const ids = ['question', 'note', 'original-news', 'evidence', 'news-title', 'news-line', 'answer', 'company', 'formula', 'literal', 'data-text', 'number', 'identifier', 'source-svg'];
  const snapshot = () => page.evaluate(names => names.map(id => { const e = document.getElementById(id); return {id, text: e.textContent, value: e.value, title: e.getAttribute('title')}; }), ids);
  const before = await snapshot();
  for (const code of ['zh', 'ja', 'en', 'ja', 'zh', 'en']) { await select(page, code); assert.deepEqual(await snapshot(), before); }
  assert.equal(await page.locator('#nav a').getAttribute('href'), '/portfolio');
});

test('new labels and asynchronously replaced states translate without stale source records', async t => {
  const h = await pageFor(t); if (!h) return; const {page} = h;
  await select(page, 'ja');
  await page.evaluate(() => {
    const button = document.createElement('button'); button.id = 'late'; button.textContent = 'Fixture loading'; button.title = 'Fixture help'; document.getElementById('dynamic').append(button);
  });
  await page.waitForFunction(() => document.getElementById('late').textContent === '読み込み中');
  assert.equal(await page.locator('#late').getAttribute('title'), 'テストの説明');
  await page.evaluate(() => { document.getElementById('late').textContent = 'Home'; });
  await page.waitForFunction(() => document.getElementById('late').textContent === 'ホーム');
  await select(page, 'zh'); assert.equal(await page.locator('#late').textContent(), '首页');
  await select(page, 'en'); assert.equal(await page.locator('#late').textContent(), 'Home');
});

test('chart label redraw preserves original traces and all numerical/source arrays', async t => {
  const h = await pageFor(t); if (!h) return; const {page} = h;
  await page.evaluate(() => { window.chartCalls = []; window.Plotly = {react: async (el, traces, layout) => { chartCalls.push({traces: structuredClone(traces), layout: structuredClone(layout)}); }}; });
  await page.addScriptTag({content: read('base.js')});
  await page.evaluate(async () => {
    window.sourceTrace = [{name: 'Home', x: ['2026-09-29', '2026-09-30'], y: [0.0123456789, -0.9], customdata: ['NVDA', 'Home']}];
    window.sourceLayout = {title: 'Fixture heading', xaxis: {title: 'Fixture x'}, yaxis: {title: {text: 'Fixture y'}}, annotations: [{text: 'Fixture annotation', x: 0.0123456789, y: -0.9}]};
    window.sourceBefore = JSON.stringify({traces: sourceTrace, layout: sourceLayout});
    await plot('chart', sourceTrace, sourceLayout);
  });
  for (const [code, home, heading, x, y, annotation] of [
    ['zh', '首页', '测试标题', '日期', '收益', '说明'],
    ['ja', 'ホーム', 'テスト見出し', '日付', 'リターン', '注記'],
    ['en', 'Home', 'Fixture heading', 'Fixture x', 'Fixture y', 'Fixture annotation']]) {
    const count = await page.evaluate(() => chartCalls.length);
    await select(page, code);
    await page.waitForFunction(n => chartCalls.length > n, count);
    const last = await page.evaluate(() => chartCalls.at(-1));
    assert.equal(last.traces[0].name, home);
    assert.equal(typeof last.layout.title === 'string' ? last.layout.title : last.layout.title.text, heading);
    assert.equal(typeof last.layout.xaxis.title === 'string' ? last.layout.xaxis.title : last.layout.xaxis.title.text, x);
    assert.equal(typeof last.layout.yaxis.title === 'string' ? last.layout.yaxis.title : last.layout.yaxis.title.text, y);
    assert.equal(last.layout.annotations[0].text, annotation);
    assert.deepEqual(last.traces[0].x, ['2026-09-29', '2026-09-30']);
    assert.deepEqual(last.traces[0].y, [0.0123456789, -0.9]);
    assert.deepEqual(last.traces[0].customdata, ['NVDA', 'Home']);
    assert.equal(await page.evaluate(() => JSON.stringify({traces: sourceTrace, layout: sourceLayout}) === sourceBefore), true);
  }
});

test('selector remains visible within the mobile header and login form', async t => {
  for (const html of [FIXTURE, '<!doctype html><html><body><form class="box"><h1>Market Intel</h1><input type="password"><button>Sign in</button></form></body></html>']) {
    const h = await pageFor(t, {html, viewport: {width: 390, height: 844}}); if (!h) return;
    await h.page.addStyleTag({content: read('base.css')});
    for (const code of ['zh', 'ja', 'en']) {
      await select(h.page, code);
      const bounds = await h.page.locator('#mi-language').boundingBox();
      assert.ok(bounds && bounds.width > 0 && bounds.x >= 0 && bounds.x + bounds.width <= 390, JSON.stringify(bounds));
    }
  }
});
