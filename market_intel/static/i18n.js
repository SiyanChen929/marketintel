/* MarketIntel UI localization. Catalogs contain authored interface text only.
 * No network translation, source-data mutation, or numerical/locale reformatting.
 * English is the fallback. Add a catalog entry or t('Label {count}', {count})
 * for new UI; use translate="no" for original research/news/user content.
 */
(function (global) {
  'use strict';
  const codes = ['en', 'zh', 'ja'];
  const names = ['English', '中文', '日本語'];
  const langTags = {en: 'en', zh: 'zh-CN', ja: 'ja'};
  const storageKey = 'mi-language';
  const catalog = new Map(), aliases = new Map(), rendered = new Map();
  const records = new WeakMap();
  const attrs = ['title', 'aria-label', 'placeholder', 'alt'];
  let language = 'en', started = false, observer = null, queued = false;
  const normalize = s => String(s).replace(/\s+/g, ' ').trim();
  try { const saved = global.localStorage.getItem(storageKey); if (codes.includes(saved)) language = saved; } catch (_) {}
  document.documentElement.lang = langTags[language];
  document.documentElement.setAttribute('data-language', language);

  function register(entries) {
    Object.entries(entries).forEach(([source, values]) => {
      const row = Array.isArray(values) ? values : [values.en, values.zh, values.ja];
      catalog.set(source, row);
      // Existing screens have both Chinese and English UI source strings.
      // Canonical keys win collisions; aliases only support reversible UI text.
      [source, ...row.filter(v => typeof v === 'string' && v)].forEach(v => {
        const key = normalize(v); if (!aliases.has(key) || v === source) aliases.set(key, source);
      });
    });
  }
  const interpolate = (s, vars) => String(s).replace(/\{([a-zA-Z][\w]*)\}/g, (all, key) =>
    Object.prototype.hasOwnProperty.call(vars, key) ? String(vars[key] ?? '') : all);
  function resolve(source, code, vars) {
    const canonical = catalog.has(source) ? source : aliases.get(normalize(source));
    const row = catalog.get(canonical);
    return interpolate(row ? (row[codes.indexOf(code)] || row[0] || source) : source, vars || {});
  }
  function t(source, vars) {
    source = String(source ?? '');
    const result = resolve(source, language, vars);
    if (vars && Object.keys(vars).length && (catalog.has(source) || aliases.has(normalize(source)))) {
      // Remember only explicitly authored UI templates. Do not guess patterns in data.
      codes.forEach(code => rendered.set(normalize(resolve(source, code, vars)), {source, vars: {...vars}}));
      if (rendered.size > 10000) rendered.clear();
    }
    return result;
  }
  function known(value) {
    const key = normalize(value), concrete = rendered.get(key);
    return concrete || (aliases.has(key) ? {source: aliases.get(key), vars: {}} : null);
  }
  function translated(value) {
    const found = known(value); if (!found) return value;
    const leading = /^\s*/.exec(value)[0], trailing = /\s*$/.exec(value)[0];
    return leading + resolve(found.source, language, found.vars) + trailing;
  }
  const excluded = 'script,style,code,pre,textarea,[translate="no"],[data-no-i18n],.js-plotly-plot,.plot svg,' +
    '.ev .title,.ev .line,.ar .title,.ar .line,.cn,.nm,.cs';
  const sourceRegions = '.answer,.evid,.brief,.hl-list,.cm span';
  const uiLabels = '[data-i18n],[data-i18n-label],.empty,.panel-msg,.badge,.lvl,.na,.pstate,.rmx-pending,button,label';
  function eligible(el, attribute) {
    if (!el || !el.closest || el.closest(excluded)) return false;
    if (el.closest(sourceRegions) && !el.closest(uiLabels)) return false;
    // Table body is data by default. Authors opt display labels in explicitly.
    const cell = el.closest('tbody td,tbody th');
    if (cell && !el.closest(uiLabels)) return false;
    // Inputs retain their values, including entered questions and identifiers.
    if (!attribute && el.closest('input,select option[data-no-i18n]')) return false;
    return true;
  }
  function update(node, key, read, write) {
    const value = read(); if (value == null || !normalize(value)) return;
    let item = records.get(node); if (!item) { item = {}; records.set(node, item); }
    let record = item[key];
    if (!record || (value !== record.output && value !== record.source)) record = {source: value, output: value, binding: known(value)};
    // Keep a template binding on its node even when the bounded rendered cache expires.
    if (!record.binding) record.binding = known(record.source);
    const output = record.binding
      ? /^\s*/.exec(record.source)[0] + resolve(record.binding.source, language, record.binding.vars) + /\s*$/.exec(record.source)[0]
      : translated(record.source);
    record.output = output; item[key] = record;
    if (output !== value) write(output);
  }
  function translate(root) {
    root = root || document.body; if (!root) return;
    const visit = node => {
      if (node.nodeType === 3) {
        if (eligible(node.parentElement, false)) update(node, 'text', () => node.nodeValue, v => { node.nodeValue = v; });
        return;
      }
      if (node.nodeType !== 1 && node.nodeType !== 9 && node.nodeType !== 11) return;
      if (node.nodeType === 1 && eligible(node, true)) {
        attrs.forEach(a => { if (node.hasAttribute(a)) update(node, a, () => node.getAttribute(a), v => node.setAttribute(a, v)); });
      }
      if (node.nodeType === 1 && node.matches && node.matches(excluded)) return;
      Array.from(node.childNodes || []).forEach(visit);
    };
    visit(root);
    if (document.querySelector('title')) visit(document.querySelector('title'));
  }
  function syncSelector() {
    const selector = document.getElementById('mi-language'); if (selector) selector.value = language;
  }
  function setLanguage(code, persist = true) {
    if (!codes.includes(code)) code = 'en';
    const changed = code !== language; language = code;
    if (persist) { try { global.localStorage.setItem(storageKey, code); } catch (_) {} }
    document.documentElement.lang = langTags[code];
    document.documentElement.setAttribute('data-language', code);
    syncSelector(); translate();
    if (changed) {
      document.dispatchEvent(new CustomEvent('languagechange', {detail: {language: code}}));
      // Cached page renderers can append new UI while responding to the event.
      translate();
    }
    return code;
  }
  function init() {
    if (started || !document.body) return; started = true;
    let selector = document.getElementById('mi-language');
    if (!selector) {
      const group = document.createElement('label'); group.className = 'language-control'; group.setAttribute('translate', 'no');
      const caption = document.createElement('span'); caption.className = 'language-caption'; caption.textContent = 'English / 中文 / 日本語';
      selector = document.createElement('select'); selector.id = 'mi-language'; selector.setAttribute('aria-label', 'English / 中文 / 日本語');
      codes.forEach((code, i) => { const option = document.createElement('option'); option.value = code; option.lang = langTags[code]; option.textContent = names[i]; selector.appendChild(option); });
      group.appendChild(caption); group.appendChild(selector);
      const host = document.querySelector('header.top') || document.querySelector('form.box') || document.body;
      if (host.matches && host.matches('form.box')) { group.classList.add('language-login'); host.insertBefore(group, host.firstChild); }
      else host.appendChild(group);
    }
    selector.addEventListener('change', () => setLanguage(selector.value)); syncSelector(); translate();
    if (typeof MutationObserver !== 'undefined') {
      observer = new MutationObserver(() => {
        if (queued) return; queued = true;
        Promise.resolve().then(() => { queued = false; translate(); });
      });
      observer.observe(document.body, {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: attrs});
    }
  }
  global.MI_I18N = {register, t, translate, setLanguage, init, get language() { return language; }, languages: codes.slice()};
  global.t = t;
  document.addEventListener('DOMContentLoaded', init, {once: true});
  if (document.readyState !== 'loading') init();
  global.addEventListener('storage', e => { if (e.key === storageKey) setLanguage(e.newValue, false); });
})(window);
