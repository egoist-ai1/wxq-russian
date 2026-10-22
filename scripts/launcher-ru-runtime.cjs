'use strict';
// Переносимый модуль русификации лаунчера WXQ.
// Грузится из главного процесса Electron: require('<payloadRoot>/launcher-ru-runtime.cjs').install({payloadRoot, statusPath}).
// Читает рядом: launcher-ru.json, launcher-entry-public-ru.draft.json, launcher-layout.css, main-logo-title.a73b14e6.png.
// Слой DOM: только полное совпадение строки (после trim) или шаблон; неизвестный текст не трогается.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const VERSION = 1;
const MAIN_SUFFIX = '/app.asar/dist/renderer/index.html';
const LOADING_SUFFIX = '/app.asar/dist/renderer/game-loading.html';
const TRAY_SUFFIX = '/app.asar/dist/renderer/tray.html';
const CRASH_SUFFIX = '/app.asar/dist/renderer/crash.html';
// Окно подготовки запуска (game-loading): раскладка и шрифт из game-loading-localization.cjs; шрифт берётся из app.asar рядом с документом.
const LOADING_CSS = '.game-loading-window__text{font-size:16px;position:relative;top:auto;margin-top:15px;width:224px;line-height:20px;max-height:40px;white-space:normal;word-break:normal;overflow-wrap:normal;hyphens:none;font-family:"WXQ Loading RU","Segoe UI",sans-serif}'
  + '.game-loading-window__subtext{font-size:12px;position:relative;top:auto;margin-top:6px;width:224px;line-height:16px;white-space:normal;word-break:normal;overflow-wrap:normal;hyphens:none;font-family:"WXQ Loading RU","Segoe UI",sans-serif}'
  + '@font-face{font-family:"WXQ Loading RU";src:url("static/font/HYQiHei_65S.2c6fe0a6.ttf") format("truetype");font-style:normal;font-weight:400;font-display:block}\n';
const BRAND_PNG = 'main-logo-title.a73b14e6.png';
const BRAND_PNG_SHA256 = '05b03187f58e645904314f6c9c2c1eb4bfeea2ad3fa907c5ebd61a13e642bb06';
const ENTRY_PREFIX_TARGET = 'Вход означает, что вы прочитали и принимаете:';
// Гости (webview) и их кадры (iframe внутри index) переводятся только по точному origin; pathname проверяется, если он указан в правиле.
// Правило camp-qq (вкладки «Карьера», «Матчи», «Гайды») задано одним origin: пути страниц Tencent меняются, а перевод идёт только по точному
// совпадению текстового узла со словарём гостя (launcher-ru.json, ключ guest), без innerHTML и без чужого кода.
const GUEST_RULES = [
  {id: 'wechat-qr', origin: 'https://open.weixin.qq.com', pathname: '/connect/qrconnect', kind: 'caption', source: '微信扫码登录'},
  {id: 'qq-ptlogin', origin: 'https://xui.ptlogin2.qq.com', pathname: '/cgi-bin/xlogin', kind: 'dom'},
  {id: 'camp-qq', origin: 'https://camp.qq.com', kind: 'dom', dict: 'camp'},
];
// Дополнительные защищённые классы гостевых страниц: авторы, ведущие, названия пользовательского контента.
const GUEST_PROTECTED = 'author|anchor|streamer|creator|up[-_]?name|(?:lineup|room|video|live)[-_]?(?:name|title)|ugc|(?:^|\\s)user-info-content_name(?:$|\\s)';
// Только безопасные свойства: русский текст длиннее китайского, поэтому слова не рвём, а вкладки не переносим. Размер шрифта не меняется.
const GUEST_CSS = '[data-wxq-ru]{word-break:normal;overflow-wrap:normal;hyphens:none}'
  + '[data-wxq-ru="tab"]{white-space:nowrap}\n';
const FRAME_DEBOUNCE_MS = 300;
const MAX_GUEST_TEXT = 400;
// Числовые шаблоны лаунчера, допустимые на странице гостя (как NUMERIC_UNIT в rendererInstall).
const GUEST_NUMERIC_TEMPLATE = /^\{\{[^{}]+\}\}(?:s|min|万|0K|分钟|小时|秒)(?:前|后|或更长)?$/;

const sha256 = data => crypto.createHash('sha256').update(data).digest('hex');

// ---------------------------------------------------------------------------
// Код ниже сериализуется и выполняется в renderer/guest. Внешних идентификаторов не использует.
// ---------------------------------------------------------------------------
function rendererInstall(cfg) {
  // Страница могла уйти на другой origin (редирект): чужому сайту словарь не применяем.
  if (cfg.origin && location.origin !== cfg.origin) return {ok: false, status: 'wrong-origin'};
  const KEY = '__wxqLauncherRU';
  const prior = window[KEY];
  if (prior && prior.fingerprint === cfg.fingerprint && prior.live()) { prior.sweep(document.documentElement); return prior.report(false); }
  if (prior) prior.dispose();

  const CJK = /[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]/;
  const OPS = {'下载': 'Загрузка', '安装': 'Установка', '更新': 'Обновление', '修复': 'Восстановление', '预下载': 'Предзагрузка', '预更新': 'Предварительное обновление', '检查': 'Проверка',
    'Download': 'Загрузка', 'Install': 'Установка', 'Update': 'Обновление', 'Repair': 'Восстановление', 'Pre-download': 'Предзагрузка', 'Pre-update': 'Предварительное обновление', 'Verify': 'Проверка'};
  const STATIC = {'正在计算...': 'Расчёт…', '体验服': 'Тестовый сервер', '正式服': 'Основной сервер', '进行中': 'В игре', '启动中': 'Запускается', '你已经启动': 'Вы уже запустили', '正在启动': 'Запускается'};
  const OP_TEMPLATES = new Set(['{{slot1}}已暂停', '{{slot1}}中', '继续{{slot1}}', '暂停{{slot1}}', '准备{{slot1}}', '切换会自动暂停{{slot1}}']);
  const NUMERIC_UNIT = /^\{\{[^{}]+\}\}(?:s|min|万|0K|分钟|小时|秒)(?:前|后|或更长)?$/;
  const TEN_THOUSAND = /^\{\{[^{}]+\}\}(?:万|0K)$/;
  const BLOCK_TAGS = {INPUT: 1, TEXTAREA: 1, SELECT: 1, SCRIPT: 1, STYLE: 1, NOSCRIPT: 1};
  const PROTECTED = /nick|user[-_]?name|player[-_]?name|friend[-_]?name|role[-_]?name|account[-_]?(?:name|id|nick)|user[-_]?id|(?:^|[\s_-])uid(?:$|[\s_-])|chat[-_](?:text|content|message|body)|comment[-_](?:content|body|text)|no-translate/i;
  const NEWS_ITEM = /(?:^|\s)home-notice-item/;
  const AGREEMENT = /(?:^|\s)login-agreement(?:$|\s)/;
  const ATTRS = ['title', 'placeholder', 'aria-label'].concat(Array.isArray(cfg.attrs) ? cfg.attrs : []);
  const ATTR_SELECTOR = ATTRS.map(name => '[' + name + ']').join(',');
  const PROTECTED_EXTRA = cfg.protectedExtra ? new RegExp(cfg.protectedExtra, 'i') : null;
  // Класс вкладки — целым словом (tab, tabs, tab-item, x_tab__y), чтобы не цеплять table/stable.
  // Только сами вкладки (не nav/switch/toggle): nowrap и вариант «|tab» нужны именно им.
  const TABISH = /(?:^|[\s_-])(?:tabs?|tabbar|segment|segmented|pill)(?:$|[\s_-])/i;
  const MAX_TEXT = cfg.maxText || 4000;
  const SWEEP_LIMIT = cfg.sweepLimit || 200000;
  // Слоты шаблона по имени: n, n1… — целое число; date — дата или время; rn — разряд ранга (римская цифра или цифра).
  const SLOT_CHECK = {int: /^\d+(?:[.,]\d+)?$/, date: /^[\d\-/.: ]{3,24}$/, rank: /^(?:[IVX]{1,3}|[Ⅰ-Ⅴ]|\d)$/};
  const slotKind = name => /^n\d*$/.test(name) ? 'int' : name === 'date' ? 'date' : name === 'rn' ? 'rank' : '';

  const exact = Object.create(null);
  const exactTab = Object.create(null);
  for (const key of Object.keys(cfg.strings)) {
    if (key.endsWith('|tab')) exactTab[key.slice(0, -4)] = cfg.strings[key];
    else exact[key] = cfg.strings[key];
  }
  const news = Object.create(null);
  for (const key of Object.keys(cfg.news || {})) news[key] = cfg.news[key];
  const esc = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const templates = [];
  for (const key of Object.keys(exact)) {
    if (!key.includes('{{')) continue;
    const names = [], literals = [];
    let offset = 0, pattern = '^';
    for (const match of key.matchAll(/\{\{([^{}]+)\}\}/g)) {
      const literal = key.slice(offset, match.index);
      literals.push(literal); pattern += esc(literal) + '([^]{0,80}?)'; names.push(match[1]); offset = match.index + match[0].length;
    }
    const tail = key.slice(offset); literals.push(tail); pattern += esc(tail) + '$';
    const strength = literals.join('').replace(/[\s\p{P}\p{S}\d]/gu, '').length;
    if (!strength) continue;
    templates.push({key, value: exact[key], names, expression: new RegExp(pattern), strength, kinds: names.map(slotKind),
      numeric: NUMERIC_UNIT.test(key), thousands: TEN_THOUSAND.test(key), operation: OP_TEMPLATES.has(key),
      probe: literals.reduce((a, b) => b.length > a.length ? b : a, '')});
  }
  templates.sort((a, b) => b.strength - a.strength);

  const state = {live: true, mapped: 0, unmatched: new Set(), observer: null, queued: false, pending: new Set(), brandTitle: cfg.brandTitle || 'Honor of Kings Ace'};

  const slotValue = raw => exact[raw] !== undefined && !raw.includes('{{') ? exact[raw] : OPS[raw] ?? STATIC[raw] ?? raw;
  const translateTemplate = clean => {
    if (clean.length > 240) return undefined;
    for (const template of templates) {
      if (!clean.includes(template.probe)) continue;
      const match = template.expression.exec(clean); if (!match) continue;
      const values = {}; let applicable = true;
      template.names.forEach((name, index) => {
        const raw = match[index + 1];
        if (template.numeric && !/^\d+(?:\.\d+)?$/.test(raw)) applicable = false;
        const kind = template.kinds[index];
        if (kind && !SLOT_CHECK[kind].test(raw)) applicable = false;
        if (template.operation && !OPS[raw]) { applicable = false; return; }
        const value = template.operation ? OPS[raw] : slotValue(raw);
        // Непереведённый китайский внутри слота — значит это чужой текст, шаблон не применяем.
        if (CJK.test(value)) applicable = false;
        values[name] = value;
      });
      if (!applicable) continue;
      if (template.thousands) return new Intl.NumberFormat('ru-RU').format(Number(match[1]) * 10000).replace(/[\u00a0\u202f]/g, ' ');
      return template.value.replace(/\{\{([^{}]+)\}\}/g, (token, name) => values[name] ?? token);
    }
    return undefined;
  };
  const classOf = element => {
    const value = element.className;
    if (typeof value === 'string') return value;
    return typeof element.getAttribute === 'function' ? element.getAttribute('class') || '' : '';
  };
  // 0 — можно, 1 — защищённый блок, 2 — внутри .home-notice-item-* (только известные заголовки), 3 — соглашение входа.
  const zone = (start, forAttribute) => {
    let mode = 0;
    for (let element = start; element && element.nodeType === 1; element = element.parentNode) {
      const tag = String(element.tagName || '').toUpperCase();
      if (!forAttribute && BLOCK_TAGS[tag]) return 1;
      if (forAttribute && (tag === 'SCRIPT' || tag === 'STYLE')) return 1;
      if (!forAttribute && typeof element.getAttribute === 'function') {
        const editable = element.getAttribute('contenteditable');
        if (editable !== null && editable !== undefined && editable !== 'false') return 1;
      }
      if (typeof element.getAttribute === 'function') {
        // Явная пометка «не переводить»: translate="no" и data-no-translate (в том числе пользовательские данные страницы).
        if (element.getAttribute('translate') === 'no' || (element.getAttribute('data-no-translate') ?? null) !== null) return 1;
      }
      const cls = classOf(element);
      if (cls) {
        if (PROTECTED.test(cls) || (PROTECTED_EXTRA && PROTECTED_EXTRA.test(cls))) return 1;
        if (NEWS_ITEM.test(cls)) mode = 2;
        else if (!mode && AGREEMENT.test(cls)) mode = 3;
      }
    }
    return mode;
  };
  const tabContext = start => {
    let depth = 0;
    for (let element = start; element && element.nodeType === 1 && depth < 3; element = element.parentNode, depth++) {
      const cls = classOf(element);
      if (cls && TABISH.test(cls)) return true;
    }
    return false;
  };
  const translateValue = (value, mode, tab) => {
    const clean = value.trim();
    if (!clean || clean.length > MAX_TEXT) return undefined;
    if (mode === 2) return Object.hasOwn(news, clean) ? news[clean] : undefined;
    let russian = tab && Object.hasOwn(exactTab, clean) ? exactTab[clean] : exact[clean];
    if (russian === undefined && mode === 3 && clean === '、') russian = ', ';
    if (russian === undefined) russian = translateTemplate(clean);
    if (russian === undefined) {
      // countOnly: считаем непереведённый китайский, содержимое наружу не отдаём (в окне могут быть ники).
      if ((cfg.collect || cfg.countOnly) && CJK.test(clean) && state.unmatched.size < 500) state.unmatched.add(clean);
      return undefined;
    }
    return russian;
  };
  // Точные React-потребители battle-detail: меняем только соседние литералы,
  // оставляя числовой text node React на месте и с исходным значением.
  const formatBattleField = node => {
    if (cfg.origin !== 'https://camp.qq.com' || location.pathname !== '/launcher/web/os-launcher-tool/index.html') return false;
    const parent = node.parentNode;
    if (!parent || parent.nodeType !== 1 || String(parent.tagName).toUpperCase() !== 'SPAN') return false;
    const children = [...parent.childNodes];
    const textOnly = children.every(child => child.nodeType === 3);
    const setLiteral = (child, value) => {
      if (child.nodeValue !== value) { child.nodeValue = value; state.mapped++; }
    };
    if (/(?:^|\s)my-data-header_title_rank(?:$|\s)/.test(classOf(parent))) {
      if (textOnly && children.length === 3 && /^(?:第|Место: )$/.test(children[0].nodeValue)
          && /^[1-9]\d{0,8}$/.test(children[1].nodeValue) && /^(?:名|)$/.test(children[2].nodeValue)) {
        setLiteral(children[0], 'Место: '); setLiteral(children[2], '');
      }
      return true;
    }
    const extra = parent.parentNode;
    if (!extra || extra.nodeType !== 1 || !/(?:^|\s)user-info-content_extra(?:$|\s)/.test(classOf(extra))) return false;
    if (!textOnly || children.length !== 2 || !/^(?:回合|Раунд| раунд(?:а|ов)?)$/.test(children[1].nodeValue)) return false;
    if (/^\d{1,9}$/.test(children[0].nodeValue)) {
      const count = Number(children[0].nodeValue), last = count % 10, hundred = count % 100;
      const unit = hundred >= 11 && hundred <= 14 ? 'раундов' : last === 1 ? 'раунд' : last >= 2 && last <= 4 ? 'раунда' : 'раундов';
      setLiteral(children[1], ' ' + unit);
    }
    return true;
  };
  const processText = node => {
    const original = node.nodeValue;
    if (typeof original !== 'string' || !original.trim()) return;
    const mode = zone(node.parentNode, false);
    if (mode === 1) return;
    if (mode === 0 && formatBattleField(node)) return;
    const tab = cfg.mark ? tabContext(node.parentNode) : false;
    const russian = translateValue(original, mode, tab);
    if (russian === undefined) return;
    const leading = original.slice(0, original.length - original.trimStart().length);
    const trailing = original.slice(original.trimEnd().length);
    const next = leading + russian + trailing;
    if (next !== original) {
      node.nodeValue = next; state.mapped++;
      // Пометка для CSS гостя: только атрибут data-wxq-ru на родителе, содержимое не трогаем.
      const parent = node.parentNode;
      if (cfg.mark && parent && parent.nodeType === 1 && typeof parent.setAttribute === 'function') parent.setAttribute('data-wxq-ru', tab ? 'tab' : 'text');
    }
  };
  const processAttributes = element => {
    if (element.nodeType !== 1) return;
    const mode = zone(element, true);
    if (mode === 1) return;
    for (const name of ATTRS) {
      const value = element.getAttribute(name);
      if (typeof value !== 'string' || !value.trim()) continue;
      const russian = translateValue(value, mode === 2 ? 2 : 0);
      if (russian !== undefined && russian !== value) { element.setAttribute(name, russian); state.mapped++; }
    }
  };
  const sweep = root => {
    if (!root || !state.live) return;
    if (root.nodeType === 3) { processText(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 9) return;
    const walker = document.createTreeWalker(root, 4);
    let node, budget = SWEEP_LIMIT;
    while ((node = walker.nextNode())) { processText(node); if (--budget <= 0) break; }
    if (root.nodeType === 1) processAttributes(root);
    if (typeof root.querySelectorAll === 'function') for (const element of root.querySelectorAll(ATTR_SELECTOR)) { processAttributes(element); if (--budget <= 0) break; }
  };

  const brand = cfg.brand || null;
  const applyBrand = () => {
    if (!brand) return;
    for (const image of document.getElementsByTagName('img')) {
      const source = image.getAttribute('src');
      if (!source || !source.split(/[?#]/, 1)[0].endsWith('/main-logo-title.a73b14e6.png')) continue;
      const parent = image.parentNode; if (!parent) continue;
      if ([...parent.childNodes].some(child => child.nodeType === 1 && child.getAttribute('data-wxq-brand'))) continue;
      image.setAttribute('src', brand.header);
      const title = document.createElement('span'); title.setAttribute('data-wxq-brand', 'header'); title.textContent = state.brandTitle;
      title.style.cssText = 'position:absolute;left:71px;top:15px;width:154px;color:white;font-family:HYQiHei;font-size:24px;line-height:30px;white-space:normal;word-break:normal;overflow-wrap:normal;hyphens:none;pointer-events:none';
      parent.appendChild(title);
    }
    const login = document.getElementById('login-title');
    if (login && !login.childNodes.length && !login.getAttribute('data-wxq-brand') && String(getComputedStyle(login).backgroundImage).includes('main-logo-icon-text.8d617972.png')) {
      login.style.setProperty('background-image', 'url("' + brand.login + '")', 'important');
      login.setAttribute('data-wxq-brand', 'login');
      const title = document.createElement('span'); title.textContent = state.brandTitle;
      title.style.cssText = 'display:block;padding-top:172px;text-align:center;font-family:HYQiHei;font-size:24px;line-height:32px;color:white;white-space:normal;word-break:normal;overflow-wrap:normal;hyphens:none';
      login.appendChild(title);
    }
  };
  const ensureCss = () => {
    if (!cfg.css) return;
    const parent = document.head || document.documentElement;
    if (!parent || [...parent.childNodes].some(child => child.nodeType === 1 && child.getAttribute('data-wxq-ru-css'))) return;
    const style = document.createElement('style'); style.setAttribute('data-wxq-ru-css', '1'); style.textContent = cfg.css; parent.appendChild(style);
  };
  const flush = () => {
    state.queued = false;
    if (!state.live) return;
    const roots = [...state.pending]; state.pending.clear();
    for (const root of roots) if (root.isConnected !== false) sweep(root);
    applyBrand(); ensureCss();
  };
  const schedule = () => { if (!state.queued) { state.queued = true; queueMicrotask(flush); } };
  state.observer = new MutationObserver(records => {
    if (!state.live) return;
    for (const record of records) {
      if (record.type === 'childList') for (const node of record.addedNodes) { if (node.nodeType === 1 || node.nodeType === 3) state.pending.add(node); }
      else if (record.target) state.pending.add(record.target);
    }
    if (state.pending.size) schedule();
  });
  const api = {
    fingerprint: cfg.fingerprint, sweep, live: () => state.live,
    report: fresh => ({ok: true, fresh, version: cfg.version, mapped: state.mapped, templates: templates.length, unmatched: cfg.collect ? [...state.unmatched] : undefined,
      unmatchedCount: cfg.countOnly ? state.unmatched.size : undefined}),
    dispose: () => { state.live = false; state.observer.disconnect(); if (window[KEY] === api) delete window[KEY]; },
  };
  window[KEY] = api;
  state.observer.observe(document.documentElement, {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS});
  sweep(document.documentElement); applyBrand(); ensureCss();
  return api.report(true);
}

function guestCaption(cfg) {
  if (window.top !== window || location.origin !== cfg.origin || location.pathname !== cfg.pathname) return {ok: false, status: 'wrong-route'};
  const marker = '__wxqLauncherRU_caption';
  const existing = window[marker];
  const title = document.querySelector('.loginPanel .title');
  if (existing && existing.target === cfg.target && existing.style.isConnected) return {ok: true, fresh: false};
  if (!title) return {ok: false, status: 'title-not-present'};
  const content = getComputedStyle(title, '::after').content;
  if (content !== JSON.stringify(cfg.source) && content !== JSON.stringify(cfg.target)) return {ok: false, status: 'public-source-drift'};
  if (existing) existing.style.remove();
  const style = document.createElement('style'); style.setAttribute('data-wxq-ru-caption', '1');
  style.textContent = '.loginPanel .title::after{content:' + JSON.stringify(cfg.target) + '!important;white-space:normal!important;word-break:normal!important;overflow-wrap:normal!important;hyphens:none!important}';
  (document.head || document.documentElement).appendChild(style);
  window[marker] = {target: cfg.target, style};
  return {ok: true, fresh: true};
}

// ---------------------------------------------------------------------------
// Главный процесс.
// ---------------------------------------------------------------------------
function isMainURL(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'file:' && decodeURIComponent(parsed.pathname).toLowerCase().endsWith(MAIN_SUFFIX);
  } catch { return false; }
}
function isLoadingURL(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'file:' && decodeURIComponent(parsed.pathname).toLowerCase().endsWith(LOADING_SUFFIX);
  } catch { return false; }
}
// Окно трея (аватар, «Показать главное окно», «Сменить аккаунт», «Выйти») и окно сбоя: отдельные страницы лаунчера без своего словаря.
function isTrayURL(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'file:' && [TRAY_SUFFIX, CRASH_SUFFIX].some(suffix => decodeURIComponent(parsed.pathname).toLowerCase().endsWith(suffix));
  } catch { return false; }
}

// ---------------------------------------------------------------------------
// Главный процесс: нативные меню, трей, диалоги. Перехват точный по словарю; любой сбой оставляет исходный вызов нетронутым.
// ---------------------------------------------------------------------------
const CJK_TEXT = /[　-〿㐀-鿿＀-￯]/;
const MENU_FIELDS = ['label', 'sublabel', 'toolTip'];
const DIALOG_FIELDS = ['title', 'message', 'detail', 'checkboxLabel', 'buttonLabel'];
const LOG_LIMIT_BYTES = 200 * 1024;

// Переводчик строк главного процесса: точное совпадение (trim), мнемоника «(&X)» отбрасывается, шаблоны «{{слот}}» с числами и латиницей в слотах.
function createTranslator(strings) {
  const esc = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const exact = new Map(), templates = [];
  for (const key of Object.keys(strings)) {
    const value = strings[key];
    if (typeof value !== 'string' || !value.trim()) continue;
    if (!key.includes('{{')) { exact.set(key, value); continue; }
    const names = [], literals = [];
    let offset = 0, pattern = '^';
    for (const match of key.matchAll(/\{\{([^{}]+)\}\}/g)) {
      const literal = key.slice(offset, match.index);
      literals.push(literal); pattern += esc(literal) + '([^]{0,80}?)'; names.push(match[1]); offset = match.index + match[0].length;
    }
    const tail = key.slice(offset); literals.push(tail); pattern += esc(tail) + '$';
    const strength = literals.join('').replace(/[\s\p{P}\p{S}\d]/gu, '').length;
    if (!strength) continue;
    templates.push({value, names, expression: new RegExp(pattern), strength, probe: literals.reduce((a, b) => b.length > a.length ? b : a, '')});
  }
  templates.sort((a, b) => b.strength - a.strength);
  const translateTemplate = clean => {
    for (const template of templates) {
      if (!clean.includes(template.probe)) continue;
      const match = template.expression.exec(clean); if (!match) continue;
      const values = {}; let applicable = true;
      template.names.forEach((name, index) => {
        const raw = match[index + 1];
        const value = exact.has(raw) ? exact.get(raw) : raw;
        if (CJK_TEXT.test(value)) applicable = false;
        values[name] = value;
      });
      if (applicable) return template.value.replace(/\{\{([^{}]+)\}\}/g, (token, name) => values[name] ?? token);
    }
    return undefined;
  };
  // undefined — перевода нет (строка остаётся как есть).
  const translate = text => {
    if (typeof text !== 'string' || text.length > 240 || !CJK_TEXT.test(text)) return undefined;
    const core = text.trim(); if (!core) return undefined;
    const lead = text.slice(0, text.length - text.trimStart().length), trail = text.slice(text.trimEnd().length);
    const mnemonic = /^(.*?)\s*\(&[A-Za-z0-9]\)$/.exec(core);
    const base = mnemonic ? mnemonic[1] : core;
    const russian = exact.has(base) ? exact.get(base) : translateTemplate(base);
    return russian === undefined ? undefined : lead + russian + trail;
  };
  return {translate, size: exact.size + templates.length};
}

function createMenuLog(options) {
  const file = options.menuLogPath || path.join(require('node:os').tmpdir(), 'wxq-launcher-ru-menu.log');
  // Только коды и счётчики; тексты (в меню могут быть ники) пишутся лишь при collectUnmatched.
  return (code, detail) => {
    try {
      let size = 0; try { size = fs.statSync(file).size; } catch { /* файла ещё нет */ }
      if (size > LOG_LIMIT_BYTES) return;
      fs.appendFileSync(file, new Date().toISOString() + ' ' + code + (detail === undefined ? '' : ' ' + String(detail).slice(0, 160)) + '\n');
    } catch { /* журнал вторичен */ }
  };
}

function installMenuHooks({electron, strings, collect, log}) {
  const translator = createTranslator(strings);
  const stats = {translated: 0, unmatched: 0, errors: 0};
  const hooked = [], restores = [];
  const unmatchedSeen = new Set();
  const tr = text => {
    if (typeof text !== 'string' || !CJK_TEXT.test(text)) return undefined;
    const russian = translator.translate(text);
    if (russian === undefined || russian === text) {
      stats.unmatched++;
      if (collect && !unmatchedSeen.has(text) && unmatchedSeen.size < 200) { unmatchedSeen.add(text); log('unmatched', text); }
      return undefined;
    }
    stats.translated++; return russian;
  };
  const guard = (name, fn) => { try { return fn(); } catch (error) { stats.errors++; log('hook-error:' + name, error?.code || error?.message || error); return undefined; } };
  // Пункты, ещё не ставшие Menu: копии с переведёнными полями (исходные объекты приложения не мутируются).
  const translateTemplate = (items, depth = 0) => {
    if (!Array.isArray(items) || depth > 8) return items;
    let changed = false;
    const result = items.map(item => {
      if (!item || typeof item !== 'object') return item;
      let copy = null;
      for (const field of MENU_FIELDS) {
        const russian = tr(item[field]);
        if (russian !== undefined) { copy = copy || Object.assign({}, item); copy[field] = russian; }
      }
      if (Array.isArray(item.submenu)) {
        const sub = translateTemplate(item.submenu, depth + 1);
        if (sub !== item.submenu) { copy = copy || Object.assign({}, item); copy.submenu = sub; }
      } else if (item.submenu && typeof item.submenu === 'object') translateMenu(item.submenu, new WeakSet(), depth + 1);
      if (copy) changed = true;
      return copy || item;
    });
    return changed ? result : items;
  };
  // Готовые Menu/MenuItem: поля меняются на месте (Electron допускает динамическую смену label).
  const translateMenu = (menu, seen = new WeakSet(), depth = 0) => {
    if (!menu || typeof menu !== 'object' || seen.has(menu) || depth > 8) return;
    seen.add(menu);
    if (Array.isArray(menu.items)) { for (const item of menu.items) translateMenu(item, seen, depth + 1); return; }
    for (const field of MENU_FIELDS) {
      const russian = tr(menu[field]);
      if (russian !== undefined) guard('menu-item-set', () => { menu[field] = russian; });
    }
    if (menu.submenu) translateMenu(menu.submenu, seen, depth + 1);
  };
  const translateOptions = options => {
    if (!options || typeof options !== 'object' || Array.isArray(options)) return options;
    let copy = null;
    const set = (key, value) => { copy = copy || Object.assign({}, options); copy[key] = value; };
    for (const field of DIALOG_FIELDS) { const russian = tr(options[field]); if (russian !== undefined) set(field, russian); }
    if (Array.isArray(options.buttons)) {
      const buttons = options.buttons.map(button => { const russian = tr(button); return russian === undefined ? button : russian; });
      if (buttons.some((button, index) => button !== options.buttons[index])) set('buttons', buttons);
    }
    if (Array.isArray(options.filters)) {
      const filters = options.filters.map(filter => { const russian = filter && tr(filter.name); return russian === undefined || !filter ? filter : Object.assign({}, filter, {name: russian}); });
      if (filters.some((filter, index) => filter !== options.filters[index])) set('filters', filters);
    }
    return copy || options;
  };
  // wrap: подмена свойства obj[name]; prepare(args) возвращает новые аргументы, исключение там — вызов с исходными.
  const wrap = (label, obj, name, prepare) => {
    if (!obj || typeof obj[name] !== 'function') return false;
    const original = obj[name];
    const wrapped = function wxqRuWrapped(...args) {
      let next = args;
      const self = this;
      const prepared = guard(label, () => prepare(args, self));
      if (Array.isArray(prepared)) next = prepared;
      return original.apply(this, next);
    };
    try { Object.defineProperty(wrapped, 'name', {value: original.name, configurable: true}); } catch { /* имя вторично */ }
    try { obj[name] = wrapped; } catch (error) { log('hook-unavailable:' + label, error?.code || error?.message || error); return false; }
    if (obj[name] !== wrapped) { log('hook-unavailable:' + label, 'not-assignable'); return false; }
    hooked.push(label);
    restores.push(() => { if (obj[name] === wrapped) obj[name] = original; });
    return true;
  };
  const argsWith = (args, index, value) => { const next = args.slice(); next[index] = value; return next; };
  const lastObjectIndex = args => { for (let i = args.length - 1; i >= 0; i--) if (args[i] && typeof args[i] === 'object' && !Array.isArray(args[i])) return i; return -1; };
  const {Menu, Tray, dialog} = electron;
  if (Menu) {
    wrap('Menu.buildFromTemplate', Menu, 'buildFromTemplate', args => argsWith(args, 0, translateTemplate(args[0])));
    wrap('Menu.setApplicationMenu', Menu, 'setApplicationMenu', args => { translateMenu(args[0]); return args; });
    const proto = Menu.prototype;
    wrap('Menu.append', proto, 'append', args => { translateMenu(args[0]); return args; });
    wrap('Menu.insert', proto, 'insert', args => { translateMenu(args[1]); return args; });
    wrap('Menu.popup', proto, 'popup', (args, self) => { translateMenu(self); return args; });
    guard('app-menu', () => translateMenu(typeof Menu.getApplicationMenu === 'function' ? Menu.getApplicationMenu() : null));
  }
  if (Tray && Tray.prototype) {
    const proto = Tray.prototype;
    wrap('Tray.setContextMenu', proto, 'setContextMenu', args => { translateMenu(args[0]); return args; });
    wrap('Tray.popUpContextMenu', proto, 'popUpContextMenu', args => { translateMenu(args[0]); return args; });
    wrap('Tray.setToolTip', proto, 'setToolTip', args => { const russian = tr(args[0]); return russian === undefined ? args : argsWith(args, 0, russian); });
    wrap('Tray.setTitle', proto, 'setTitle', args => { const russian = tr(args[0]); return russian === undefined ? args : argsWith(args, 0, russian); });
  }
  if (dialog) {
    for (const name of ['showMessageBox', 'showMessageBoxSync', 'showOpenDialog', 'showOpenDialogSync', 'showSaveDialog', 'showSaveDialogSync']) {
      wrap('dialog.' + name, dialog, name, args => { const index = lastObjectIndex(args); return index < 0 ? args : argsWith(args, index, translateOptions(args[index])); });
    }
    wrap('dialog.showErrorBox', dialog, 'showErrorBox', args => args.map(value => { const russian = tr(value); return russian === undefined ? value : russian; }));
  }
  const dispose = () => { while (restores.length) guard('restore', restores.pop()); };
  return {hooked, stats, dispose, size: translator.size, translator, translateTemplate, translateMenu, translateOptions,
    summary: () => ({hooked: hooked.slice(), translated: stats.translated, unmatchedCount: stats.unmatched, errors: stats.errors})};
}
function guestRuleFor(url) {
  try {
    const parsed = new URL(url);
    if (parsed.username || parsed.password) return null;
    return GUEST_RULES.find(rule => rule.origin === parsed.origin && (rule.pathname === undefined || rule.pathname === parsed.pathname)) || null;
  } catch { return null; }
}

function iconAssets(png) {
  if (sha256(png) !== BRAND_PNG_SHA256) return null;
  const image = '<image href="data:image/png;base64,' + png.toString('base64') + '" width="464" height="186"/>';
  const uri = value => 'data:image/svg+xml;base64,' + Buffer.from(value).toString('base64');
  return {
    header: uri('<svg xmlns="http://www.w3.org/2000/svg" width="464" height="186"><svg x="40" y="25" width="100" height="136" viewBox="40 25 100 136">' + image + '</svg></svg>'),
    login: uri('<svg xmlns="http://www.w3.org/2000/svg" width="285" height="230"><svg x="77" width="130" height="140" viewBox="40 25 100 113">' + image + '</svg></svg>'),
  };
}

// Задачи привязаны к webContents и его документу; счётчик попыток сбрасывается на каждом новом документе.
function createLifecycle(options) {
  const retryDelayMs = options.retryDelayMs ?? 250, operationTimeoutMs = options.operationTimeoutMs ?? 4000, maxAttempts = options.maxAttempts ?? 8;
  const setT = options.setTimeout || setTimeout, clearT = options.clearTimeout || clearTimeout;
  const rows = new Map();
  let active = true;
  const safe = (fn, fallback = false) => { try { return fn(); } catch { return fallback; } };
  const snapshot = row => ({id: row.id, kind: row.kind, epoch: row.epoch, attempts: row.attempts, status: row.status, mapped: row.mapped, unmatchedCount: row.unmatchedCount});
  const notify = row => { if (active) safe(() => options.changed(snapshot(row))); };
  const clearTimer = row => { if (row.timer) clearT(row.timer); row.timer = null; };
  const newDocument = row => { row.epoch++; row.attempts = 0; row.status = 'pending'; row.mapped = 0; row.requested = false; row.opId++; row.running = false; clearTimer(row); notify(row); };
  const schedule = row => {
    if (!active || !row.documentReady || safe(() => row.contents.isDestroyed(), true)) return;
    if (row.running) { row.requested = true; return; }
    if (row.timer || row.attempts >= maxAttempts) return;
    row.timer = setT(() => run(row), row.attempts === 0 ? 0 : retryDelayMs);
  };
  const run = row => {
    row.timer = null;
    if (!active || row.running || !row.documentReady || safe(() => row.contents.isDestroyed(), true)) return;
    if (row.attempts >= maxAttempts) { row.status = 'exhausted'; notify(row); return; }
    const kind = safe(() => options.classify(row.contents), null);
    if (!kind) { row.kind = null; row.status = 'ignored'; notify(row); return; }
    row.kind = kind; row.attempts++; row.status = 'applying'; row.running = true;
    const epoch = row.epoch, opId = ++row.opId;
    const finish = (status, result) => {
      if (row.opId !== opId) return;
      clearT(guard); row.running = false;
      if (row.epoch === epoch) {
        row.status = status;
        if (result && Number.isFinite(result.mapped)) row.mapped = result.mapped;
        if (result && Number.isFinite(result.unmatchedCount)) row.unmatchedCount = result.unmatchedCount;
        notify(row);
      }
      if (status !== 'ready' || row.requested || row.epoch !== epoch) { row.requested = false; schedule(row); }
    };
    const guard = setT(() => finish('operation-expired'), operationTimeoutMs);
    Promise.resolve().then(() => options.apply(row.contents, kind)).then(
      result => finish(result && result.ok === true ? 'ready' : 'pending', result),
      () => finish('retry'));
  };
  const track = contents => {
    if (!active || !contents || rows.has(contents.id) || safe(() => contents.isDestroyed(), true)) return false;
    const row = {contents, id: contents.id, kind: null, epoch: 0, attempts: 0, status: 'pending', mapped: 0, timer: null, running: false, requested: false, opId: 0, listeners: [],
      documentReady: safe(() => typeof contents.isLoadingMainFrame !== 'function' || !contents.isLoadingMainFrame(), true)};
    rows.set(row.id, row);
    const on = (name, listener) => { contents.on(name, listener); row.listeners.push([name, listener]); };
    on('did-start-navigation', (event, ...rest) => {
      const flags = typeof event?.isMainFrame === 'boolean' ? {main: event.isMainFrame, same: event.isSameDocument === true}
        : rest[0] && typeof rest[0] === 'object' ? {main: rest[0].isMainFrame, same: rest[0].isSameDocument === true} : {main: rest[2], same: rest[1] === true};
      if (flags.main === true && !flags.same) { row.documentReady = false; newDocument(row); }
    });
    on('did-navigate-in-page', () => { row.documentReady = true; row.attempts = 0; row.status = 'pending'; schedule(row); });
    for (const name of ['dom-ready', 'did-finish-load']) on(name, () => { row.documentReady = true; row.attempts = 0; if (row.status !== 'applying') row.status = 'pending'; schedule(row); });
    on('render-process-gone', () => { row.documentReady = false; newDocument(row); });
    on('destroyed', () => { clearTimer(row); detach(row); rows.delete(row.id); });
    schedule(row); return true;
  };
  const detach = row => { for (const [name, listener] of row.listeners) safe(() => row.contents.removeListener(name, listener)); row.listeners = []; };
  const dispose = () => { active = false; for (const row of rows.values()) { clearTimer(row); detach(row); } rows.clear(); };
  const idle = async (timeoutMs = 3000) => {
    const deadline = Date.now() + timeoutMs;
    do {
      if ([...rows.values()].every(row => !row.running && !row.timer)) return true;
      await new Promise(resolve => setT(resolve, 5));
    } while (Date.now() < deadline);
    return false;
  };
  return {track, dispose, idle, request: contents => { const row = rows.get(contents?.id); if (row) schedule(row); }, snapshot: () => [...rows.values()].map(snapshot)};
}

function loadPayload(payloadRoot) {
  const read = name => fs.readFileSync(path.join(payloadRoot, name));
  const dictionaryBytes = read('launcher-ru.json');
  const dictionary = JSON.parse(dictionaryBytes.toString('utf8'));
  if (!dictionary || dictionary.schema !== 1 || !dictionary.strings || typeof dictionary.strings !== 'object') throw Error('launcher-ru.json: unexpected schema');
  const strings = Object.assign(Object.create(null), dictionary.strings);
  for (const [key, value] of Object.entries(strings)) if (typeof value !== 'string') throw Error('launcher-ru.json: non-string value');
  const news = dictionary.news && typeof dictionary.news === 'object' ? dictionary.news : {};
  // Словарь удалённых страниц camp.qq.com: только строки; ключ с суффиксом «|tab» — вариант для вкладок.
  const guestExtra = dictionary.guest === undefined ? {} : dictionary.guest;
  if (!guestExtra || typeof guestExtra !== 'object' || Array.isArray(guestExtra)) throw Error('launcher-ru.json: guest must be an object');
  for (const value of Object.values(guestExtra)) if (typeof value !== 'string') throw Error('launcher-ru.json: non-string guest value');
  // Гостю camp.qq.com: свой словарь и только числовые шаблоны лаунчера («{{n}}万», «{{n}}分钟»); шаблоны со свободными слотами не берём.
  const guestStrings = Object.assign(Object.create(null), guestExtra);
  for (const [key, value] of Object.entries(strings)) if (GUEST_NUMERIC_TEMPLATE.test(key) && !Object.hasOwn(guestStrings, key)) guestStrings[key] = value;
  const draftBytes = read('launcher-entry-public-ru.draft.json');
  const draft = JSON.parse(draftBytes.toString('utf8'));
  if (!Array.isArray(draft.records)) throw Error('draft: records missing');
  // Подписи входа из draft перекрывают общий словарь (принятые вживую значения).
  let brandTitle = 'Honor of Kings Ace';
  for (const record of draft.records) {
    if (record.id === 'entry.brand' && typeof record.target === 'string') brandTitle = record.target;
    else if (/^entry\.agreement\./.test(record.id) && record.id !== 'entry.agreement.prefix' && record.source && record.target) strings[record.source] = record.target;
  }
  strings['登录即代表你已阅读并同意：'] = ENTRY_PREFIX_TARGET;
  // Раздел "menu": подписи нативных меню, трея и диалогов; в DOM главного окна не попадает, только в главный процесс и окно трея.
  const menuExtra = dictionary.menu === undefined ? {} : dictionary.menu;
  if (!menuExtra || typeof menuExtra !== 'object' || Array.isArray(menuExtra)) throw Error('launcher-ru.json: menu must be an object');
  for (const value of Object.values(menuExtra)) if (typeof value !== 'string') throw Error('launcher-ru.json: non-string menu value');
  const menuStrings = Object.assign(Object.create(null), strings, menuExtra);
  const css = read('launcher-layout.css').toString('utf8');
  const png = read(BRAND_PNG);
  const brand = iconAssets(png);
  return {strings, menuStrings, guestStrings, news, css, brand, brandTitle, count: Object.keys(strings).length, dictionarySha: sha256(dictionaryBytes), draftSha: sha256(draftBytes), pngOk: Boolean(brand)};
}

function install(options = {}) {
  const fail = error => ({ok: false, applied: false, count: 0, sha: '', error: String(error?.code || error?.message || error).slice(0, 160)});
  try {
    const payloadRoot = path.resolve(options.payloadRoot || __dirname);
    const payload = loadPayload(payloadRoot);
    const moduleSha = sha256(fs.readFileSync(__filename));
    const electron = options.electron || require('electron');
    if (!electron?.app || !electron.webContents) throw Error('electron api unavailable');
    const existing = globalThis.__wxqLauncherRuRuntime;
    if (existing) {
      if (existing.dictionarySha === payload.dictionarySha && existing.moduleSha === moduleSha && existing.alive()) return existing.result;
      existing.dispose();
    }
    const collect = options.collectUnmatched === true;
    const fingerprint = sha256(payload.dictionarySha + payload.draftSha + moduleSha + (collect ? 'c' : '')).slice(0, 16);
    const hostConfig = {version: VERSION, fingerprint, strings: payload.strings, news: payload.news, css: payload.css, collect,
      brand: payload.brand, brandTitle: payload.brandTitle};
    const hostSource = '(' + rendererInstall.toString() + ')(' + JSON.stringify(hostConfig) + ')';
    const guestDomSource = '(' + rendererInstall.toString() + ')(' + JSON.stringify({version: VERSION, fingerprint: fingerprint + 'g', strings: payload.strings, news: {}, collect}) + ')';
    const campSource = '(' + rendererInstall.toString() + ')(' + JSON.stringify({version: VERSION, fingerprint: fingerprint + 'c', origin: 'https://camp.qq.com', strings: payload.guestStrings, news: {}, collect,
      css: GUEST_CSS, protectedExtra: GUEST_PROTECTED, attrs: ['alt'], maxText: MAX_GUEST_TEXT, mark: true}) + ')';
    const loadingSource ='(' + rendererInstall.toString() + ')(' + JSON.stringify({version: VERSION, fingerprint: fingerprint + 'l', strings: payload.strings, news: {}, collect: false, countOnly: true, css: LOADING_CSS}) + ')';
    const traySource = '(' + rendererInstall.toString() + ')(' + JSON.stringify({version: VERSION, fingerprint: fingerprint + 't', strings: payload.menuStrings, news: {}, collect: false, countOnly: true}) + ')';
    const startedAt = new Date().toISOString();
    const statusPath = options.statusPath ? path.resolve(options.statusPath) : null;
    // Перехват меню/трея/диалогов главного процесса: отдельная защита, сбой не мешает остальным слоям.
    let menuHooks = null;
    const menuLog = createMenuLog(options);
    if (options.menuHooks !== false) {
      try { menuHooks = installMenuHooks({electron, strings: payload.menuStrings, collect, log: menuLog}); menuLog('menu-hooks', menuHooks.hooked.length); }
      catch (error) { menuLog('menu-install-failed', error?.code || error?.message || error); menuHooks = null; }
    }
    const writeStatus = rows => {
      if (!statusPath) return;
      try {
        fs.mkdirSync(path.dirname(statusPath), {recursive: true});
        fs.writeFileSync(statusPath, JSON.stringify({schema: 1, version: VERSION, moduleSha256: moduleSha, dictionarySha256: payload.dictionarySha, draftSha256: payload.draftSha,
          count: payload.count, brand: payload.pngOk, startedAt, updatedAt: new Date().toISOString(), menu: menuHooks ? menuHooks.summary() : null, rows}, null, 2));
      } catch { /* статус вторичен */ }
    };
    const isHost = contents => safe(() => contents.getType() === 'window' && isMainURL(contents.getURL()));
    const safe = fn => { try { return fn(); } catch { return false; } };
    const classify = contents => {
      if (isHost(contents)) return 'host';
      if (safe(() => contents.getType() === 'window' && isLoadingURL(contents.getURL()))) return 'loading';
      if (safe(() => contents.getType() === 'window' && isTrayURL(contents.getURL()))) return 'tray';
      if (safe(() => contents.getType() === 'webview' && guestRuleFor(contents.getURL()) && isMainURL(contents.hostWebContents?.getURL()))) return 'guest';
      return null;
    };
    const apply = async (contents, kind) => {
      if (kind === 'host') return contents.executeJavaScript(hostSource);
      if (kind === 'loading') return classify(contents) === 'loading' ? contents.executeJavaScript(loadingSource) : {ok: false};
      if (kind === 'tray') return classify(contents) === 'tray' ? contents.executeJavaScript(traySource) : {ok: false};
      const rule = guestRuleFor(contents.getURL());
      if (!rule || classify(contents) !== 'guest') return {ok: false};
      if (rule.kind === 'caption') {
        const target = payload.strings[rule.source];
        if (typeof target !== 'string') return {ok: false};
        return contents.executeJavaScript('(' + guestCaption.toString() + ')(' + JSON.stringify({origin: rule.origin, pathname: rule.pathname, source: rule.source, target}) + ')');
      }
      return contents.executeJavaScript(rule.dict === 'camp' ? campSource : guestDomSource);
    };
    // Удалённые страницы camp.qq.com могут жить во фрейме хоста: вставляем скрипт в каждый подходящий фрейм, с повторами.
    const campFrames = contents => safe(() => {
      const main = contents.mainFrame;
      const list = main?.framesInSubtree || [];
      // Закрытый соседний iframe может бросить исключение даже при чтении url.
      // Он не должен отменять перевод остальных живых и разрешённых кадров.
      return list.filter(frame => {
        if (frame === main) return false;
        const url = safe(() => frame.url);
        const rule = typeof url === 'string' ? guestRuleFor(url) : null;
        return rule && rule.dict === 'camp';
      });
    }) || [];
    // На каждый contents: один отложенный таймер (повторные события не плодят вставок) и память «frame -> url», куда уже вставили.
    let alive = true;
    const frameWatches = new Map();
    const injectFrames = contents => {
      const watch = frameWatches.get(contents);
      if (!watch || !alive) return;
      for (const frame of campFrames(contents)) {
        const url = safe(() => frame.url);
        const rule = typeof url === 'string' ? guestRuleFor(url) : null;
        if (!rule || rule.dict !== 'camp') continue;
        if (watch.injected.get(frame) === url) continue;
        watch.injected.set(frame, url);
        // Включаем синхронный отказ Electron в ту же ветку повтора, что и Promise rejection.
        Promise.resolve().then(() => frame.executeJavaScript(campSource)).catch(() => { if (watch.injected.get(frame) === url) watch.injected.delete(frame); });
      }
    };
    const unwatchFrames = watch => {
      if (watch.timer) clearTimeout(watch.timer);
      watch.timer = null;
      for (const [name, listener] of watch.listeners) safe(() => watch.contents.removeListener(name, listener));
      watch.listeners = [];
    };
    const watchFrames = contents => {
      if (frameWatches.has(contents) || (!isHost(contents) && !safe(() => contents.getType() === 'webview'))) return;
      const watch = {contents, timer: null, injected: new Map(), listeners: []};
      const schedule = () => {
        if (watch.timer || !alive) return;
        watch.timer = setTimeout(() => { watch.timer = null; injectFrames(contents); }, FRAME_DEBOUNCE_MS);
      };
      // Новая навигация фрейма даёт новый документ: прежняя отметка недействительна. Простая загрузка отметку не сбрасывает.
      const onNavigate = () => { watch.injected.clear(); schedule(); };
      const on = (name, listener) => { if (safe(() => { contents.on(name, listener); return true; })) watch.listeners.push([name, listener]); };
      on('did-frame-finish-load', schedule);
      on('did-frame-navigate', onNavigate);
      on('destroyed', () => { unwatchFrames(watch); frameWatches.delete(contents); });
      frameWatches.set(contents, watch);
    };
    const lifecycle = createLifecycle({classify, apply, retryDelayMs: options.retryDelayMs, operationTimeoutMs: options.operationTimeoutMs, maxAttempts: options.maxAttempts,
      changed: () => writeStatus(lifecycle.snapshot())});
    const onCreated = (event, contents) => { lifecycle.track(contents); watchFrames(contents); };
    electron.app.on('web-contents-created', onCreated);
    for (const contents of electron.webContents.getAllWebContents()) { lifecycle.track(contents); watchFrames(contents); injectFrames(contents); }
    const result = {ok: true, applied: true, count: payload.count, sha: payload.dictionarySha};
    const runtime = {result, dictionarySha: payload.dictionarySha, moduleSha, lifecycle, menuHooks, alive: () => alive,
      dispose: () => { alive = false; if (menuHooks) menuHooks.dispose(); for (const watch of frameWatches.values()) unwatchFrames(watch); frameWatches.clear(); electron.app.removeListener('web-contents-created', onCreated); lifecycle.dispose(); if (globalThis.__wxqLauncherRuRuntime === runtime) delete globalThis.__wxqLauncherRuRuntime; }};
    globalThis.__wxqLauncherRuRuntime = runtime;
    writeStatus(lifecycle.snapshot());
    return result;
  } catch (error) { return fail(error); }
}

module.exports = {install, rendererInstall, guestCaption, createLifecycle, createTranslator, installMenuHooks, isTrayURL, isMainURL, isLoadingURL, guestRuleFor, iconAssets, loadPayload, GUEST_RULES, VERSION};
