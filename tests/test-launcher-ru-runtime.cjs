'use strict';
// Тест launcher-ru-runtime.cjs на фиктивных electron и DOM (реальный лаунчер не запускается).
// Запуск: node tests/test-launcher-ru-runtime.cjs
// RED на сохранённом runtime: --runtime-source <путь к cjs>.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const {EventEmitter} = require('node:events');
const {pathToFileURL} = require('node:url');

const project = path.resolve(__dirname, '..');
const runtimeArgument = process.argv.indexOf('--runtime-source');
if (runtimeArgument >= 0 && !process.argv[runtimeArgument + 1]) throw new Error('--runtime-source требует путь');
const runtimeSource = runtimeArgument < 0 ? path.join(project, 'scripts/launcher-ru-runtime.cjs') : path.resolve(process.argv[runtimeArgument + 1]);
const sha256 = data => crypto.createHash('sha256').update(data).digest('hex');
const runtimeSourceSha256 = sha256(fs.readFileSync(runtimeSource));
// Собственный прозрачный PNG 1x1: public export не содержит закрытый арт Tencent.
const fixtureBrandPng = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII=', 'base64');
const fixtureBrandSha256 = sha256(fixtureBrandPng);
const read = rel => JSON.parse(fs.readFileSync(path.join(project, rel), 'utf8'));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

// ---------------------------------------------------------------- фиктивный DOM
function makeDom(location) {
  const observers = new Set();
  const queue = record => {
    for (const observer of observers) {
      const o = observer.options;
      if (!observer.root || !contains(observer.root, record.target)) continue;
      if (record.type === 'childList' && !o.childList) continue;
      if (record.type === 'characterData' && !o.characterData) continue;
      if (record.type === 'attributes' && (!o.attributes || (o.attributeFilter && !o.attributeFilter.includes(record.attributeName)))) continue;
      observer.records.push(record);
      if (!observer.scheduled) {
        observer.scheduled = true;
        queueMicrotask(() => { observer.scheduled = false; const batch = observer.records.splice(0); if (batch.length && observers.has(observer)) observer.callback(batch); });
      }
    }
  };
  const contains = (root, node) => { for (let n = node; n; n = n.parentNode) if (n === root) return true; return false; };
  class Node_ {
    constructor(type) { this.nodeType = type; this.parentNode = null; this.childNodes = []; }
    get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n === doc; }
    get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
    appendChild(child) {
      if (child.parentNode) child.parentNode.removeChild(child);
      child.parentNode = this; this.childNodes.push(child);
      queue({type: 'childList', target: this, addedNodes: [child], removedNodes: []}); return child;
    }
    removeChild(child) {
      const index = this.childNodes.indexOf(child); if (index < 0) return child;
      this.childNodes.splice(index, 1); child.parentNode = null;
      queue({type: 'childList', target: this, addedNodes: [], removedNodes: [child]}); return child;
    }
    remove() { this.parentNode?.removeChild(this); }
  }
  class Text extends Node_ {
    constructor(value) { super(3); this._v = String(value); }
    get nodeValue() { return this._v; }
    set nodeValue(value) { this._v = String(value); queue({type: 'characterData', target: this}); }
    get textContent() { return this._v; }
  }
  const collect = (root, predicate, out = []) => { for (const child of root.childNodes) { if (child.nodeType === 1) { if (predicate(child)) out.push(child); collect(child, predicate, out); } } return out; };
  class Element extends Node_ {
    constructor(tag) {
      super(1); this.tagName = tag.toUpperCase(); this.attrs = new Map(); this.pseudo = {}; this.bg = 'none';
      const props = new Map();
      this.style = {setProperty: (n, v) => props.set(n, v), getPropertyValue: n => props.get(n) || '', removeProperty: n => props.delete(n), cssText: ''};
    }
    getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
    setAttribute(name, value) { this.attrs.set(name, String(value)); queue({type: 'attributes', target: this, attributeName: name}); }
    get className() { return this.getAttribute('class') || ''; }
    set textContent(value) { for (const child of [...this.childNodes]) this.removeChild(child); this.appendChild(new Text(value)); }
    get textContent() { return this.childNodes.map(c => c.nodeType === 3 ? c.nodeValue : c.textContent).join(''); }
    get src() { return this.getAttribute('src'); }
    getElementsByTagName(tag) { return collect(this, e => e.tagName === tag.toUpperCase()); }
    querySelectorAll(selector) {
      const attrs = selector.split(',').map(part => part.trim().replace(/^\[|\]$/g, ''));
      return collect(this, e => attrs.some(a => e.attrs.has(a)));
    }
    querySelector(selector) {
      const parts = selector.split(/\s+/).map(p => p.replace(/^\./, ''));
      const hasClass = (e, c) => e.className.split(/\s+/).includes(c);
      return collect(this, e => hasClass(e, parts.at(-1)) && parts.slice(0, -1).every(c => { for (let n = e.parentNode; n; n = n.parentNode) if (n.nodeType === 1 && hasClass(n, c)) return true; return false; }))[0] || null;
    }
  }
  const doc = new Node_(9);
  doc.createElement = tag => new Element(tag);
  doc.createTextNode = value => new Text(value);
  const html = new Element('html'), head = new Element('head'), body = new Element('body');
  doc.appendChild(html); html.appendChild(head); html.appendChild(body);
  doc.documentElement = html; doc.head = head; doc.body = body;
  doc.getElementsByTagName = tag => collect(html, e => e.tagName === tag.toUpperCase());
  doc.getElementById = id => collect(html, e => e.getAttribute('id') === id)[0] || null;
  doc.querySelector = selector => html.querySelector(selector);
  doc.createTreeWalker = root => {
    const texts = []; const walk = n => { for (const c of n.childNodes) { if (c.nodeType === 3) texts.push(c); else walk(c); } }; walk(root);
    let index = 0; return {nextNode: () => texts[index++] || null};
  };
  class MutationObserver {
    constructor(callback) { this.callback = callback; this.records = []; this.root = null; this.scheduled = false; }
    observe(root, options) { this.root = root; this.options = options; observers.add(this); }
    disconnect() { observers.delete(this); this.records = []; }
  }
  const getComputedStyle = (element, pseudo) => pseudo ? {content: element.pseudo[pseudo] ?? 'none'} : {backgroundImage: element.bg};
  const context = {document: doc, MutationObserver, getComputedStyle, location, queueMicrotask, console};
  context.window = context; context.top = context;
  vm.createContext(context);
  const el = (tag, attrs = {}, ...children) => {
    const node = new Element(tag);
    for (const [k, v] of Object.entries(attrs)) node.attrs.set(k, v);
    for (const child of children) node.appendChild(typeof child === 'string' ? new Text(child) : child);
    return node;
  };
  return {context, doc, el, Text, observersCount: () => observers.size};
}
const texts = root => { const out = []; const walk = n => { for (const c of n.childNodes) c.nodeType === 3 ? out.push(c.nodeValue) : walk(c); }; walk(root); return out; };

// ---------------------------------------------------------------- фиктивный electron
const resourcesDir = '%USERPROFILE% юзер/Program Files/WXQLauncher/26083.0.7.30004/resources';
const mainURL = pathToFileURL(resourcesDir + '/app.asar/dist/renderer/index.html').href;
class Contents extends EventEmitter {
  constructor(id, type, url, host) {
    super(); this.id = id; this.type = type; this.url = url; this.hostWebContents = host; this.calls = 0; this.failures = 0; this.destroyed = false;
    this.newDocument();
  }
  newDocument() {
    let origin = 'null', pathname = '/';
    try { const u = new URL(this.url); origin = u.origin; pathname = u.pathname; } catch { /* about:blank */ }
    this.dom = makeDom({origin, pathname, href: this.url, protocol: new URL(this.url).protocol});
  }
  navigate(url = this.url) {
    this.url = url; this.newDocument();
    this.emit('did-start-navigation', {isMainFrame: true, isSameDocument: false}, url);
    this.emit('dom-ready'); this.emit('did-finish-load');
    return this.dom;
  }
  getType() { return this.type; }
  getURL() { return this.url; }
  isDestroyed() { return this.destroyed; }
  isLoadingMainFrame() { return false; }
  async executeJavaScript(source) {
    this.calls++;
    if (this.failures > 0) { this.failures--; throw new Error('renderer unavailable'); }
    return vm.runInContext(source, this.dom.context);
  }
}
function makePayload(root, {dictionary} = {}) {
  const relative = path.relative(os.tmpdir(), path.resolve(root));
  assert.ok(relative && relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative), 'тестовый payload создаётся только внутри Temp');
  fs.mkdirSync(root, {recursive: true});
  const strings = Object.assign({}, read('translations/launcher.render.ru.json'), read('translations/launcher-ui-supplement.ru.json'), read('source/launcher-ru/launcher-ru-additions.ru.json'));
  const news = Object.fromEntries(read('source/launcher-ru/launcher-ru-news.json').keys.map(key => [key, strings[key]]));
  const guest = read('source/launcher-ru/launcher-ru-guest.ru.json');
  const menu = read('source/launcher-ru/launcher-ru-menu.ru.json');
  const payload = dictionary || {schema: 1, strings, news, guest, menu};
  fs.writeFileSync(path.join(root, 'launcher-ru.json'), JSON.stringify(payload));
  let substitutions = 0;
  // Меняем ожидаемый SHA собственного PNG только в Temp-копии; production guard сохраняется.
  const fixtureRuntime = fs.readFileSync(runtimeSource, 'utf8').replace(/(^const BRAND_PNG_SHA256 = ')[a-f0-9]{64}(';)/gm, (match, before, after) => {
    substitutions++; return before + fixtureBrandSha256 + after;
  });
  assert.equal(substitutions, 1, 'в Temp-копии runtime должна быть ровно одна подстановка BRAND_PNG_SHA256');
  fs.writeFileSync(path.join(root, 'launcher-ru-runtime.cjs'), fixtureRuntime);
  fs.copyFileSync(path.join(project, 'localization/examples/launcher-entry-public-ru.draft.json'), path.join(root, 'launcher-entry-public-ru.draft.json'));
  fs.copyFileSync(path.join(project, 'scripts/launcher-layout.css'), path.join(root, 'launcher-layout.css'));
  fs.writeFileSync(path.join(root, 'main-logo-title.a73b14e6.png'), fixtureBrandPng);
  return {strings, news, file: path.join(root, 'launcher-ru-runtime.cjs')};
}
function makeElectron(list) {
  const app = new EventEmitter();
  return {app, webContents: {getAllWebContents: () => list}};
}
const fast = {retryDelayMs: 5, operationTimeoutMs: 500};
const runtimeOf = () => globalThis.__wxqLauncherRuRuntime;
const settle = async () => { await runtimeOf().lifecycle.idle(2000); await sleep(10); };

const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wxq-ru-'));
fast.menuLogPath = path.join(tmp, 'menu.log');
const payloadRoot = path.join(tmp, 'Папка с пробелом и кириллицей', 'payload');
const payload = makePayload(payloadRoot);
const moduleUnderTest = require(payload.file);
const after = () => { runtimeOf()?.dispose(); };

// Сборка экрана «главная + вход» для проверки слоя DOM.
function buildScreen(dom) {
  const {el, doc} = dom;
  const login = el('div', {class: 'login-box'},
    el('div', {id: 'login-title', class: 'login-title'}),
    el('div', {class: 'tab'}, '微信登录'), el('div', {class: 'tab'}, 'QQ登录'),
    el('div', {class: 'login-agreement'}, el('div', {class: 'content'}, '登录即代表你已阅读并同意：',
      el('div', {}, el('span', {class: 'link-button-component'}, '游戏许可及服务协议'), '、', el('span', {class: 'link-button-component'}, '王者万象棋隐私保护指引'), '、',
        el('span', {class: 'link-button-component'}, '儿童隐私保护指引'), '、', el('span', {class: 'link-button-component'}, '王者万象棋第三方信息共享清单'), '、',
        el('span', {class: 'link-button-component'}, '王者万象棋已收集个人信息清单')))));
  login.childNodes[0].bg = 'url(main-logo-icon-text.8d617972.png)';
  const home = el('div', {class: 'home'},
    el('div', {class: 'left-menu'}, ...['首页', '生涯', '战绩', '攻略'].map(t => el('div', {class: 'left-menu-title'}, t))),
    el('div', {class: 'logo'}, el('img', {src: 'static/img/main-logo-title.a73b14e6.png'})),
    el('div', {class: 'notice-tabs'}, ...['最新', '公告', '活动', '资讯', '全部'].map(t => el('div', {class: 'notice-tab-item'}, t))),
    el('div', {class: 'play-time'}, '游玩时长: 12h'),
    el('div', {class: 'game-download-button-content'}, el('div', {class: 'download-title'}, '启动游戏')),
    el('div', {class: 'icons'}, el('i', {title: '设置'}), el('i', {title: '最小化'}), el('i', {title: '关闭'}), el('i', {title: '消息'}), el('i', {title: '反馈'}), el('i', {'aria-label': '客服'})),
    el('div', {class: 'user-nickname'}, '设置'),
    el('div', {class: 'account-name'}, '关闭'),
    el('textarea', {}, '设置'),
    el('input', {placeholder: '设置', value: '设置'}),
    el('div', {contenteditable: 'true'}, '反馈'),
    el('div', {class: 'home-notice-item-title'}, '首套珍品传说皮肤现已上架'),
    el('div', {class: 'home-notice-item-title'}, '设置'),
    el('div', {class: 'home-notice-item-desc'}, '首页'),
    el('div', {class: 'extra'}, '请点击设置按钮'), el('div', {class: 'extra'}, '未知的新文本'), el('div', {class: 'extra'}, '游玩时长: 中文h'), el('div', {class: 'padded'}, ' 首页 '));
  doc.body.appendChild(login); doc.body.appendChild(home);
  return {login, home};
}
const hostSetup = async (electron, host, options = {}) => {
  const result = moduleUnderTest.install({payloadRoot, electron, ...fast, ...options});
  assert.equal(result.ok, true, JSON.stringify(result));
  return result;
};

// Отдельный VM меняет только часы тестового runtime, без production-опций и Electron.
class FrameClock {
  constructor() { this.now = Date.now(); this.timers = new Map(); }
  setTimeout(callback, delay = 0) {
    const timer = {unref() { return this; }};
    this.timers.set(timer, {at: this.now + Math.max(0, Number(delay) || 0), callback});
    return timer;
  }
  clearTimeout(timer) { this.timers.delete(timer); }
  async advance(milliseconds) {
    const target = this.now + milliseconds;
    for (let steps = 0; ; steps++) {
      assert.ok(steps < 10000, 'виртуальные таймеры должны завершаться');
      await new Promise(resolve => setImmediate(resolve));
      let next;
      for (const entry of this.timers) if (entry[1].at <= target && (!next || entry[1].at < next[1].at)) next = entry;
      if (!next) { if (this.now === target) return; this.now = target; continue; }
      this.now = Math.max(this.now, next[1].at);
      this.timers.delete(next[0]); next[1].callback();
    }
  }
}
const frameCampURL = 'https://camp.qq.com/launcher/web/game-strategy/index.html';
const campDocument = () => {
  const dom = makeDom({origin: 'https://camp.qq.com', pathname: '/launcher/web/game-strategy/index.html', href: frameCampURL, protocol: 'https:'});
  dom.doc.body.appendChild(dom.el('span', {}, '推荐阵容'));
  return dom;
};
const campTranslated = frame => {
  assert.ok(frame.dom.context.__wxqLauncherRU, 'rendererInstall действительно установлен в документе frame');
  assert.deepEqual(texts(frame.dom.doc.body), ['Рекомендуемые составы']);
};
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
};
async function withCampFrame(fn, {present = true, initialURL = mainURL, created = false} = {}) {
  const clock = new FrameClock();
  class FrameDate extends Date {
    constructor(...args) { super(...(args.length ? args : [clock.now])); }
    static now() { return clock.now; }
  }
  const module = {exports: {}};
  const sandbox = {module, exports: module.exports, __filename: payload.file, __dirname: payloadRoot,
    URL, Buffer, console, queueMicrotask, Date: FrameDate,
    setTimeout: clock.setTimeout.bind(clock), clearTimeout: clock.clearTimeout.bind(clock),
    require(name) {
      assert.ok(['node:fs', 'node:path', 'node:crypto', 'node:os'].includes(name), 'VM не загружает Electron: ' + name);
      return require(name);
    }};
  vm.runInNewContext(fs.readFileSync(payload.file, 'utf8'), sandbox, {filename: payload.file});
  const host = new Contents(1, 'window', initialURL || 'about:blank'); host.url = initialURL;
  const frame = {url: frameCampURL, dom: campDocument(), calls: 0,
    executeJavaScript(source) {
      this.calls++;
      return this.apply ? this.apply(source, this.dom) : vm.runInContext(source, this.dom.context);
    }};
  const main = {url: initialURL}; main.framesInSubtree = present ? [main, frame] : [main]; host.mainFrame = main;
  const runtime = () => sandbox.__wxqLauncherRuRuntime;
  const install = () => {
    const electron = makeElectron(created ? [] : [host]);
    const result = module.exports.install({payloadRoot, electron, ...fast, menuHooks: false});
    assert.equal(result.ok, true, JSON.stringify(result));
    if (created) electron.app.emit('web-contents-created', {}, host);
  };
  try { await fn({clock, host, frame, main, install, runtime}); }
  finally { runtime()?.dispose(); }
}

test('install синхронный; sha и count совпадают со словарём; payloadRoot с пробелами и кириллицей', async () => {
  const host = new Contents(1, 'window', mainURL);
  const electron = makeElectron([host]);
  const statusPath = path.join(tmp, 'статус', 'status.json');
  const result = moduleUnderTest.install({payloadRoot, statusPath, electron, ...fast});
  assert.equal(typeof result.then, 'undefined');
  assert.deepEqual(Object.keys(result).sort(), ['applied', 'count', 'ok', 'sha']);
  assert.equal(result.ok, true); assert.equal(result.applied, true);
  assert.equal(result.sha, sha256(fs.readFileSync(path.join(payloadRoot, 'launcher-ru.json'))));
  assert.ok(result.count >= Object.keys(payload.strings).length);
  const status = JSON.parse(fs.readFileSync(statusPath, 'utf8'));
  assert.equal(status.moduleSha256, sha256(fs.readFileSync(payload.file)));
  assert.equal(JSON.stringify(status).includes('首页'), false, 'status не содержит текстов');
  // повторная установка с тем же payload — тот же результат, один слушатель
  assert.deepEqual(moduleUnderTest.install({payloadRoot, electron, ...fast}), result);
  assert.equal(electron.app.listenerCount('web-contents-created'), 1);
  after();
});

test('payloadRoot по умолчанию — папка модуля; без electron и без файлов ok=false без исключения', async () => {
  const host = new Contents(1, 'window', mainURL);
  const result = require(payload.file).install({electron: makeElectron([host]), ...fast});
  assert.equal(result.ok, true); after();
  assert.equal(require(payload.file).install({payloadRoot}).ok, false, 'require("electron") вне Electron недоступен');
  const broken = path.join(tmp, 'broken'); makePayload(broken); fs.unlinkSync(path.join(broken, 'launcher-ru.json'));
  assert.equal(moduleUnderTest.install({payloadRoot: broken, electron: makeElectron([])}).ok, false);
  const bad = path.join(tmp, 'bad'); makePayload(bad, {dictionary: {schema: 2, strings: {}}});
  assert.equal(moduleUnderTest.install({payloadRoot: bad, electron: makeElectron([])}).ok, false);
  assert.equal(runtimeOf(), undefined);
});

test('слой DOM: известные строки, атрибуты, шаблон, пробелы', async () => {
  const host = new Contents(1, 'window', mainURL);
  await hostSetup(makeElectron([host]));
  const {home, login} = buildScreen(host.dom);

  host.emit('dom-ready'); await settle();
  const t = texts(host.dom.doc.body);
  for (const s of ['Главная', 'Карьера', 'Матчи', 'Гайды', 'Новое', 'Объявления', 'События', 'Новости', 'Все', 'Сыграно: 12 ч', 'Запустить игру']) assert.ok(t.includes(s), s);
  assert.ok(t.includes(' Главная '), 'пробелы по краям сохраняются');
  const titles = [...home.querySelectorAll('[title],[aria-label]')].map(e => e.getAttribute('title') || e.getAttribute('aria-label'));
  assert.deepEqual(titles, ['Настройки', 'Свернуть', 'Закрыть', 'Сообщения', 'Обратная связь', 'Поддержка']);
  assert.ok(t.includes('Вход через WeChat') && t.includes('Вход через QQ'));
  assert.ok(t.includes('Вход означает, что вы прочитали и принимаете:'));
  assert.ok(t.includes('Лицензия и условия игры') && t.includes('Защита данных детей') && t.includes('Передача данных третьим лицам') && t.includes('Собранные персональные данные'));
  assert.equal(t.filter(s => s === ', ').length, 4, 'разделители ссылок');
  assert.ok(host.dom.doc.head.childNodes.some(n => n.getAttribute('data-wxq-ru-css')), 'layout css вставлен');
  void login; after();
});

test('слой DOM: негативные случаи не трогаются', async () => {
  const host = new Contents(1, 'window', mainURL);
  await hostSetup(makeElectron([host]));
  const {home} = buildScreen(host.dom);
  host.emit('dom-ready'); await settle();
  const by = cls => home.childNodes.filter(n => n.nodeType === 1 && n.className === cls);
  assert.equal(texts(by('user-nickname')[0])[0], '设置', 'ник');
  assert.equal(texts(by('account-name')[0])[0], '关闭', 'аккаунт');
  assert.equal(texts(home.getElementsByTagName('textarea')[0])[0], '设置', 'textarea');
  assert.equal(home.getElementsByTagName('input')[0].getAttribute('value'), '设置', 'value input');
  assert.equal(home.getElementsByTagName('input')[0].getAttribute('placeholder'), 'Настройки', 'placeholder допустим');
  assert.equal(texts(home.childNodes.find(n => n.getAttribute('contenteditable')))[0], '反馈', 'contenteditable');
  const notice = home.childNodes.filter(n => /home-notice-item/.test(n.className)).map(n => texts(n)[0]);
  assert.deepEqual(notice, ['Первый редкий легендарный облик уже доступен', '设置', '首页'], 'только известные заголовки новостей');
  const extra = by('extra').map(n => texts(n)[0]);
  assert.deepEqual(extra, ['请点击设置按钮', '未知的新文本', '游玩时长: 中文h'], 'частичная и неизвестная строки, чужой слот');
  after();
});

test('повторная отрисовка: MutationObserver переводит заново', async () => {
  const host = new Contents(1, 'window', mainURL);
  await hostSetup(makeElectron([host]));
  const {home} = buildScreen(host.dom);
  host.emit('dom-ready'); await settle();
  const {el, Text} = host.dom;
  const menu = home.childNodes[0];
  // React вернул исходный текст в существующий узел
  menu.childNodes[0].childNodes[0].nodeValue = '首页'; await sleep(10);
  assert.equal(menu.childNodes[0].textContent, 'Главная');
  // элемент пересоздан
  menu.removeChild(menu.childNodes[1]); menu.appendChild(el('div', {class: 'left-menu-title'}, '生涯')); await sleep(10);
  assert.equal(menu.childNodes.at(-1).textContent, 'Карьера');
  // атрибут сброшен
  const icon = home.querySelectorAll('[title]')[0]; icon.setAttribute('title', '设置'); await sleep(10);
  assert.equal(icon.getAttribute('title'), 'Настройки');
  // выход и вход в том же документе: экран входа удалён и создан заново
  host.dom.doc.body.removeChild(host.dom.doc.body.childNodes[0]);
  host.dom.doc.body.appendChild(el('div', {class: 'login-box'}, el('div', {class: 'tab'}, '微信登录'), el('div', {class: 'tab'}, new Text('QQ登录').nodeValue))); await sleep(10);
  assert.deepEqual(texts(host.dom.doc.body.childNodes.at(-1)), ['Вход через WeChat', 'Вход через QQ']);
  void Text; after();
});

test('бренд: логотип заголовка и экран входа', async () => {
  assert.equal(moduleUnderTest.loadPayload(payloadRoot).pngOk, true, 'синтетический PNG проходит штатную проверку SHA');
  const host = new Contents(1, 'window', mainURL);
  await hostSetup(makeElectron([host]));
  const {home, login} = buildScreen(host.dom);
  host.emit('dom-ready'); await settle();
  const img = home.getElementsByTagName('img')[0];
  assert.ok(img.getAttribute('src').startsWith('data:image/svg+xml;base64,'));
  assert.ok(img.parentNode.childNodes.some(n => n.nodeType === 1 && n.getAttribute('data-wxq-brand') && n.textContent === 'Honor of Kings Ace'));
  assert.equal(img.parentNode.childNodes.filter(n => n.nodeType === 1 && n.getAttribute('data-wxq-brand')).length, 1);
  const title = login.childNodes[0];
  assert.equal(title.getAttribute('data-wxq-brand'), 'login');
  assert.match(title.style.getPropertyValue('background-image'), /^url\("data:image\/svg\+xml/);
  host.emit('dom-ready'); await settle(); host.emit('did-finish-load'); await settle();
  assert.equal(img.parentNode.childNodes.filter(n => n.nodeType === 1 && n.getAttribute('data-wxq-brand')).length, 1, 'идемпотентно');
  after();
});

test('новый документ после выхода/входа: счётчик попыток сбрасывается', async () => {
  const host = new Contents(1, 'window', mainURL);
  await hostSetup(makeElectron([host]), host, {maxAttempts: 3});
  await settle();
  host.failures = 99;
  host.navigate(); await settle();
  let row = runtimeOf().lifecycle.snapshot()[0];
  assert.equal(row.attempts, 3, 'попытки исчерпаны'); assert.notEqual(row.status, 'ready');
  const callsAtExhaustion = host.calls; await sleep(40);
  assert.equal(host.calls, callsAtExhaustion, 'после исчерпания новых попыток нет');
  // выход → новый документ (экран входа)
  host.failures = 0;
  let dom = host.navigate(); buildScreen(dom); host.emit('did-finish-load'); await settle();
  row = runtimeOf().lifecycle.snapshot()[0];
  assert.equal(row.status, 'ready'); assert.ok(row.attempts >= 1 && row.attempts <= 2);
  assert.ok(texts(dom.doc.body).includes('Вход через WeChat'));
  // вход → снова новый документ (главная)
  dom = host.navigate(); buildScreen(dom); host.emit('did-finish-load'); await settle();
  assert.ok(texts(dom.doc.body).includes('Главная'));
  // и ещё раз: выход после успешного входа
  dom = host.navigate(); buildScreen(dom); host.emit('did-finish-load'); await settle();
  assert.ok(texts(dom.doc.body).includes('Вход через QQ'));
  assert.ok(runtimeOf().lifecycle.snapshot()[0].epoch >= 4);
  after();
});

test('SPA без новой навигации: did-navigate-in-page повторно применяет перевод', async () => {
  const host = new Contents(1, 'window', mainURL);
  await hostSetup(makeElectron([host]));
  const dom = host.dom; await settle();
  const {el} = dom;
  dom.doc.body.appendChild(el('div', {class: 'left-menu-title'}, '首页'));
  host.emit('did-navigate-in-page', {isMainFrame: true}); await settle();
  assert.equal(dom.doc.body.childNodes[0].textContent, 'Главная');
  after();
});

test('гости: WeChat QR и QQ ptlogin по точному адресу; чужие адреса не получают скрипт', async () => {
  const host = new Contents(1, 'window', mainURL);
  const wechat = new Contents(2, 'webview', 'https://open.weixin.qq.com/connect/qrconnect?appid=not-read', host);
  const qq = new Contents(3, 'webview', 'https://xui.ptlogin2.qq.com/cgi-bin/xlogin?private=1', host);
  const wrong = [
    new Contents(4, 'webview', 'https://open.weixin.qq.com/private', host),
    new Contents(5, 'webview', 'http://open.weixin.qq.com/connect/qrconnect', host),
    new Contents(6, 'webview', 'https://open.weixin.qq.com.evil.invalid/connect/qrconnect', host),
    new Contents(7, 'webview', 'https://xui.ptlogin2.qq.com/cgi-bin/other', host),
    new Contents(8, 'webview', 'https://user:pass@xui.ptlogin2.qq.com/cgi-bin/xlogin', host),
    new Contents(9, 'webview', 'https://open.weixin.qq.com/connect/qrconnect', new Contents(10, 'window', 'https://example.org/')),
    new Contents(11, 'window', 'https://open.weixin.qq.com/connect/qrconnect'),
  ];
  const {el} = wechat.dom;
  const title = el('div', {class: 'title'}); title.pseudo['::after'] = '"微信扫码登录"';
  wechat.dom.doc.body.appendChild(el('div', {class: 'loginPanel'}, title));
  qq.dom.doc.body.appendChild(el('div', {}, el('span', {class: 'tip'}, '登录'), el('span', {class: 'nickname'}, '登录'), el('span', {}, '不认识的字')));
  await hostSetup(makeElectron([host, wechat, qq, ...wrong]));
  await settle();
  const style = wechat.dom.doc.head.childNodes.find(n => n.getAttribute('data-wxq-ru-caption'));
  assert.ok(style && style.textContent.includes('"Вход по QR-коду WeChat"'));
  assert.deepEqual(texts(qq.dom.doc.body), ['Войти', '登录', '不认识的字']);
  for (const guest of wrong) assert.equal(guest.calls, 0, guest.url);
  // дрейф источника: подпись другая — ничего не применяется
  title.pseudo['::after'] = '"другое"';
  const drift = new Contents(12, 'webview', 'https://open.weixin.qq.com/connect/qrconnect?x', host);
  const t2 = drift.dom.el('div', {class: 'title'}); t2.pseudo['::after'] = '"другое"';
  drift.dom.doc.body.appendChild(drift.dom.el('div', {class: 'loginPanel'}, t2));
  runtimeOf().lifecycle.track(drift); await settle();
  assert.equal(drift.dom.doc.head.childNodes.length, 0);
  after();
});

const campURL = 'https://camp.qq.com/launcher/web/os-launcher-tool/index.html?tab=career';
// Экран удалённой страницы camp.qq.com: вкладки, шапка таблицы, данные пользователя, числа и даты.
function buildCamp(dom) {
  const {el, doc} = dom;
  const tabs = el('div', {class: 'guide-tabs'}, el('div', {class: 'tab-item'}, '热门'), el('div', {class: 'tab-item'}, '阵容'), el('div', {class: 'tab-item'}, '直播'), el('div', {class: 'tab-item'}, '新手'));
  const header = el('div', {class: 'table-head'}, el('span', {}, '排名'), el('span', {}, '棋手'), el('span', {class: 'col'}, '阵容'), el('span', {}, '天赋'));
  const row = el('div', {class: 'row'}, el('span', {class: 'meta'}, '排位 万象大赛 09-12 14:14'), el('span', {class: 'streak'}, '10连胜'), el('span', {class: 'score'}, '57人评分'),
    el('span', {class: 'rank'}, '秩序白银III'), el('span', {class: 'n'}, '22'), el('img', {alt: '更多', src: 'x.png'}));
  const user = el('div', {class: 'user-card'}, el('span', {class: 'nickname'}, '更多'), el('span', {class: 'author-name'}, '热门'), el('span', {class: 'room-title'}, '全部'),
    el('span', {translate: 'no'}, '全部'), el('span', {'data-no-translate': ''}, '全部'));
  const form = el('div', {}, el('textarea', {}, '全部'), el('input', {placeholder: '更多'}), el('script', {}, '全部'), el('style', {}, '全部'));
  const long = el('p', {class: 'desc'}, '全部' + '长'.repeat(500));
  doc.body.appendChild(el('div', {class: 'page'}, tabs, header, row, user, form, long));
  return {tabs, header, row, user, form, long};
}

test('camp.qq.com: webview переводится по точным строкам, вкладки отдельно, данные пользователя не трогаются', async () => {
  const host = new Contents(1, 'window', mainURL);
  const camp = new Contents(2, 'webview', campURL, host);
  const parts = buildCamp(camp.dom);
  const other = [
    new Contents(3, 'webview', 'https://camp.qq.com.evil.invalid/launcher/web/os-launcher-tool/index.html', host),
    new Contents(4, 'webview', 'http://camp.qq.com/launcher/web/os-launcher-tool/index.html', host),
    new Contents(5, 'webview', 'https://user:pass@camp.qq.com/launcher/web/os-launcher-tool/index.html', host),
    new Contents(6, 'webview', campURL, new Contents(7, 'window', 'https://example.org/')),
  ];
  await hostSetup(makeElectron([host, camp, ...other]));
  await settle();
  assert.deepEqual(texts(parts.tabs), ['Популярные', 'Составы', 'Трансляции', 'Новичкам']);
  assert.deepEqual(texts(parts.header), ['Место', 'Шахматист', 'Состав', 'Таланты']);
  assert.deepEqual(texts(parts.row), ['Рейтинг — Турнир ACE 09-12 14:14', 'Серия побед: 10', 'Оценок: 57', 'Серебро III', '22']);
  assert.equal(parts.row.childNodes[5].getAttribute('alt'), 'Ещё');
  assert.deepEqual(texts(parts.user), ['更多', '热门', '全部', '全部', '全部']);
  assert.deepEqual(texts(parts.form), ['全部', '全部', '全部']);
  assert.equal(parts.form.childNodes[1].getAttribute('placeholder'), 'Ещё', 'placeholder переводится, value не трогается');
  assert.equal(texts(parts.long)[0].startsWith('全部'), true, 'слишком длинный текст не переводится');
  assert.equal(parts.tabs.childNodes[1].getAttribute('data-wxq-ru'), 'tab');
  assert.equal(parts.header.childNodes[2].getAttribute('data-wxq-ru'), 'text');
  assert.ok(camp.dom.doc.head.childNodes.some(n => n.textContent.includes('data-wxq-ru')), 'CSS гостя добавлен');
  for (const guest of other) assert.equal(guest.calls, 0, guest.url);
  after();
});

test('camp.qq.com: React дорисовывает узлы — MutationObserver переводит; число и ник остаются', async () => {
  const host = new Contents(1, 'window', mainURL);
  const camp = new Contents(2, 'webview', campURL, host);
  const parts = buildCamp(camp.dom);
  await hostSetup(makeElectron([host, camp]));
  await settle();
  const {el} = camp.dom;
  const late = el('div', {class: 'late'}, el('span', {}, '平均排名'), el('span', {}, '2.6'), el('span', {class: 'nickname'}, '平均排名'));
  parts.row.appendChild(late);
  await sleep(20);
  assert.deepEqual(texts(late), ['Среднее место', '2.6', '平均排名']);
  after();
});

test('camp.qq.com: словарь guest не попадает в host-слой и в чужих гостей', async () => {
  const host = new Contents(1, 'window', mainURL);
  const qq = new Contents(3, 'webview', 'https://xui.ptlogin2.qq.com/cgi-bin/xlogin', host);
  host.dom.doc.body.appendChild(host.dom.el('div', {}, host.dom.el('span', {}, '热门'), host.dom.el('span', {}, '平均排名')));
  qq.dom.doc.body.appendChild(qq.dom.el('div', {}, qq.dom.el('span', {}, '平均排名')));
  await hostSetup(makeElectron([host, qq]));
  await settle();
  assert.deepEqual(texts(host.dom.doc.body), ['热门', '平均排名']);
  assert.deepEqual(texts(qq.dom.doc.body), ['平均排名']);
  after();
});

test('camp.qq.com: страница во фрейме хоста получает перевод; чужой фрейм нет; dispose гасит таймеры', async () => {
  const host = new Contents(1, 'window', mainURL);
  const frameDom = makeDom({origin: 'https://camp.qq.com', pathname: '/launcher/web/game-strategy/index.html', href: 'https://camp.qq.com/launcher/web/game-strategy/index.html', protocol: 'https:'});
  const alienDom = makeDom({origin: 'https://example.org', pathname: '/', href: 'https://example.org/', protocol: 'https:'});
  const mkFrame = (url, dom) => ({url, calls: 0, executeJavaScript(source) { this.calls++; return vm.runInContext(source, dom.context); }});
  const frame = mkFrame('https://camp.qq.com/launcher/web/game-strategy/index.html', frameDom);
  const alien = mkFrame('https://example.org/', alienDom);
  const main = {url: mainURL};
  main.framesInSubtree = [main, frame, alien];
  host.mainFrame = main;
  frameDom.doc.body.appendChild(frameDom.el('div', {}, frameDom.el('span', {}, '推荐阵容'), frameDom.el('span', {}, '导入阵容')));
  alienDom.doc.body.appendChild(alienDom.el('span', {}, '推荐阵容'));
  await hostSetup(makeElectron([host]));
  await settle();
  assert.equal(frame.calls, 1);
  assert.deepEqual(texts(frameDom.doc.body), ['Рекомендуемые составы', 'Импортировать состав']);
  assert.equal(alien.calls, 0);
  assert.deepEqual(texts(alienDom.doc.body), ['推荐阵容']);
  const before = frame.calls;
  runtimeOf().dispose();
  host.emit('did-frame-finish-load'); host.emit('did-frame-navigate');
  await sleep(450);
  assert.equal(frame.calls, before, 'после dispose повторных вставок нет');
});

test('camp.qq.com: события фреймов не плодят вставки (один таймер, без повтора в тот же frame), dispose снимает обработчики', async () => {
  const host = new Contents(1, 'window', mainURL);
  const frameDom = makeDom({origin: 'https://camp.qq.com', pathname: '/launcher/web/game-strategy/index.html', href: 'https://camp.qq.com/launcher/web/game-strategy/index.html', protocol: 'https:'});
  const frame = {url: 'https://camp.qq.com/launcher/web/game-strategy/index.html', calls: 0, executeJavaScript(source) { this.calls++; return vm.runInContext(source, frameDom.context); }};
  const main = {url: mainURL}; main.framesInSubtree = [main, frame]; host.mainFrame = main;
  frameDom.doc.body.appendChild(frameDom.el('span', {}, '更多'));
  await hostSetup(makeElectron([host]));
  await settle();
  assert.equal(frame.calls, 1);
  const baseline = {finish: host.listenerCount('did-frame-finish-load'), navigate: host.listenerCount('did-frame-navigate')};
  assert.deepEqual(baseline, {finish: 1, navigate: 1});
  for (let i = 0; i < 5; i++) host.emit('did-frame-finish-load');
  await sleep(450);
  assert.equal(frame.calls, 1, 'тот же frame с тем же url повторно не получает скрипт');
  for (let i = 0; i < 5; i++) host.emit('did-frame-navigate');
  await sleep(450);
  assert.equal(frame.calls, 2, 'после навигации одна вставка, а не пять');
  frame.url = 'https://camp.qq.com/launcher/web/game-strategy/other.html';
  host.emit('did-frame-finish-load'); await sleep(450);
  assert.equal(frame.calls, 3, 'url изменился — вставка');
  runtimeOf().dispose();
  assert.equal(host.listenerCount('did-frame-finish-load'), 0);
  assert.equal(host.listenerCount('did-frame-navigate'), 0);
  host.emit('did-frame-navigate'); await sleep(450);
  assert.equal(frame.calls, 3);
});

for (const event of ['frame-created', 'did-stop-loading']) test('frame regression: поздний iframe получает перевод только по ' + event, async () => {
  await withCampFrame(async ({clock, host, frame, main, install}) => {
    install(); await clock.advance(0);
    assert.equal(frame.calls, 0, 'при install iframe ещё нет');
    main.framesInSubtree.push(frame);
    host.emit(event, {}, {frame});
    await clock.advance(400);
    assert.equal(frame.calls, 1);
    campTranslated(frame);
  }, {present: false});
});

test('frame regression: contents создан с пустым URL, затем host URL и frame-finish-load переводят iframe', async () => {
  await withCampFrame(async ({clock, host, frame, main, install}) => {
    install(); await clock.advance(0);
    assert.equal(frame.calls, 0, 'пустой URL ещё не разрешает перевод');
    host.url = mainURL; host.newDocument(); main.url = mainURL;
    host.emit('did-frame-finish-load'); await clock.advance(400);
    assert.equal(frame.calls, 1, 'слушатели должны существовать со времени web-contents-created');
    campTranslated(frame);
  }, {initialURL: '', created: true});
});

test('frame regression: camp iframe чужого window не получает перевод', async () => {
  await withCampFrame(async ({clock, host, frame, install}) => {
    install(); await clock.advance(0);
    for (const event of ['frame-created', 'did-frame-finish-load', 'did-stop-loading']) host.emit(event);
    await clock.advance(1500);
    assert.equal(frame.calls, 0);
    assert.equal(frame.dom.context.__wxqLauncherRU, undefined);
    assert.deepEqual(texts(frame.dom.doc.body), ['推荐阵容']);
  }, {initialURL: 'https://example.org/', created: true});
});

test('frame regression: structured ok:false повторяется на том же URL без внешних событий', async () => {
  await withCampFrame(async ({clock, frame, install}) => {
    frame.apply = (source, dom) => frame.calls === 1 ? {ok: false, status: 'not-ready'} : vm.runInContext(source, dom.context);
    install(); await clock.advance(0);
    assert.equal(frame.calls, 1);
    assert.equal(frame.dom.context.__wxqLauncherRU, undefined);
    await clock.advance(1500);
    assert.equal(frame.calls, 2, 'неуспешная вставка не должна попадать в success cache');
    campTranslated(frame);
    await clock.advance(5000);
    assert.equal(frame.calls, 2, 'после ok:true вставки прекращаются');
  });
});

test('frame regression: rejection повторяется без внешнего события и затем переводит DOM', async () => {
  await withCampFrame(async ({clock, frame, install}) => {
    frame.apply = (source, dom) => frame.calls === 1 ? Promise.reject(new Error('frame temporarily unavailable')) : vm.runInContext(source, dom.context);
    install(); await clock.advance(0);
    assert.equal(frame.calls, 1);
    await clock.advance(1500);
    assert.equal(frame.calls, 2);
    campTranslated(frame);
  });
});

test('frame regression: hung promise имеет 10s deadline, не дублируется до него и ограничен окном 120s', async () => {
  await withCampFrame(async ({clock, host, frame, install}) => {
    frame.apply = () => new Promise(() => {});
    install(); await clock.advance(0);
    for (let i = 0; i < 8; i++) { host.emit('frame-created'); host.emit('did-frame-finish-load'); }
    await clock.advance(9999);
    assert.equal(frame.calls, 1, 'до deadline одна операция на frame даже при шквале событий');
    await clock.advance(1001);
    assert.equal(frame.calls, 2, 'timeout освобождает операцию и запускает повтор');
    await clock.advance(120000);
    const exhausted = frame.calls;
    assert.ok(exhausted > 1 && exhausted <= 60);
    await clock.advance(120000);
    assert.equal(frame.calls, exhausted, 'после окна 120s зависший renderer больше не вызывается');
    assert.equal(clock.timers.size, 0);
  });
});

test('frame regression: постоянный ok:false исчерпывает не более 60 попыток', async () => {
  await withCampFrame(async ({clock, frame, install}) => {
    frame.apply = () => ({ok: false});
    install(); await clock.advance(120000);
    assert.equal(frame.calls, 60);
    await clock.advance(120000);
    assert.equal(frame.calls, 60);
    assert.equal(clock.timers.size, 0);
  });
});

test('frame regression: stale ok:true после навигации не помечает новый документ готовым', async () => {
  await withCampFrame(async ({clock, host, frame, install}) => {
    const oldDocument = deferred(), newDocument = deferred();
    frame.apply = (source, dom) => frame.calls === 1 ? oldDocument.promise : frame.calls === 2 ? newDocument.promise : vm.runInContext(source, dom.context);
    install(); await clock.advance(0);
    assert.equal(frame.calls, 1);
    frame.dom = campDocument();
    host.emit('did-frame-navigate'); await clock.advance(400);
    assert.equal(frame.calls, 2);
    oldDocument.resolve({ok: true}); await clock.advance(0);
    assert.equal(frame.dom.context.__wxqLauncherRU, undefined);
    newDocument.resolve({ok: false}); await clock.advance(1500);
    assert.equal(frame.calls, 3, 'старый success не должен подавлять retry нового документа на том же URL');
    campTranslated(frame);
  });
});

test('frame regression: dispose отменяет уже поставленный retry и снимает новые listeners', async () => {
  await withCampFrame(async ({clock, host, frame, install, runtime}) => {
    frame.apply = () => ({ok: false});
    install(); await clock.advance(0);
    assert.equal(frame.calls, 1);
    assert.ok(clock.timers.size > 0, 'retry действительно поставлен до dispose');
    runtime().dispose();
    assert.equal(clock.timers.size, 0, 'dispose очищает retry и operation guards');
    for (const event of ['frame-created', 'did-stop-loading', 'did-frame-finish-load', 'did-frame-navigate']) {
      assert.equal(host.listenerCount(event), 0, event);
      host.emit(event);
    }
    await clock.advance(240000);
    assert.equal(frame.calls, 1);
  });
});

test('camp.qq.com: при несовпадении location.origin скрипт ничего не делает', async () => {
  const host = new Contents(1, 'window', mainURL);
  const evilDom = makeDom({origin: 'https://evil.invalid', pathname: '/', href: 'https://evil.invalid/', protocol: 'https:'});
  const frame = {url: 'https://camp.qq.com/launcher/web/game-strategy/index.html', calls: 0, result: null,
    executeJavaScript(source) { this.calls++; this.result = vm.runInContext(source, evilDom.context); return this.result; }};
  const main = {url: mainURL}; main.framesInSubtree = [main, frame]; host.mainFrame = main;
  evilDom.doc.body.appendChild(evilDom.el('span', {}, '更多'));
  await hostSetup(makeElectron([host]));
  await settle();
  assert.equal(frame.calls, 1);
  assert.equal(frame.result.ok, false);
  assert.equal(frame.result.status, 'wrong-origin');
  assert.deepEqual(texts(evilDom.doc.body), ['更多']);
  assert.equal(evilDom.context.__wxqLauncherRU, undefined);
  after();
});

test('camp.qq.com: гостю не попадают шаблоны лаунчера со свободными слотами, только числовые', async () => {
  const dictionary = {schema: 1, strings: {'{{slot1}}中': '{{slot1}}', '退出{{slot1}}': 'Выйти из {{slot1}}', 'CPU: {{cpu}}': 'ЦП: {{cpu}}', '{{n}}分钟': '{{n}} мин', '设置': 'Настройки'},
    news: {}, guest: {'更多': 'Ещё'}};
  const root = path.join(tmp, 'guest-templates'); makePayload(root, {dictionary});
  const loaded = moduleUnderTest.loadPayload(root);
  assert.deepEqual(Object.keys(loaded.guestStrings).sort(), ['{{n}}分钟', '更多'].sort());
  assert.equal(loaded.guestStrings['设置'], undefined);
});

test('camp.qq.com: tab-маркер только у вкладок; table/nav/toggle/switch не считаются вкладкой', async () => {
  const host = new Contents(1, 'window', mainURL);
  const camp = new Contents(2, 'webview', campURL, host);
  const {el, doc} = camp.dom;
  const mk = cls => { const cell = el('span', {}, '阵容'); doc.body.appendChild(el('div', {class: cls}, cell)); return cell; };
  const cells = {table: mk('table-head'), nav: mk('nav-box'), toggle: mk('toggle'), sw: mk('switch-wrap'), tab: mk('guide-tabs'), tabItem: mk('x_tab__y')};
  await hostSetup(makeElectron([host, camp]));
  await settle();
  const mark = cell => cell.getAttribute('data-wxq-ru');
  assert.equal(mark(cells.table), 'text'); assert.equal(mark(cells.nav), 'text'); assert.equal(mark(cells.toggle), 'text'); assert.equal(mark(cells.sw), 'text');
  assert.equal(mark(cells.tab), 'tab'); assert.equal(mark(cells.tabItem), 'tab');
  assert.deepEqual([cells.table, cells.nav, cells.tab].map(c => texts(c)[0]), ['Состав', 'Состав', 'Составы']);
  const css = camp.dom.doc.head.childNodes.map(n => n.textContent).join('');
  assert.ok(css.includes('[data-wxq-ru="tab"]{white-space:nowrap}'));
  const commonRule = /\[data-wxq-ru\]\{([^}]*)\}/.exec(css);
  assert.ok(commonRule);
  assert.equal(commonRule[1].includes('flex-shrink'), false, 'общий переводимый текст не получает flex-shrink');
  after();
});

function buildAuthorCard(dom, {roleTag = 'div', roleText = '万象棋大神', rootClass = 'author-M1MiN', rootAttrs = {}} = {}) {
  const {el, doc} = dom;
  const role = el(roleTag, {class: 'authorLabel-pjKj2'}, roleText);
  const authorName = el('div', {class: 'authorName-actual'}, '万象棋大神');
  const userName = el('div', {class: 'userName-actual'}, '万象棋大神');
  const info = el('div', {class: 'authorInfo-R6s3o'}, role, authorName, userName);
  const root = el('div', {...rootAttrs, class: rootClass}, info);
  doc.body.appendChild(root);
  return {root, role, authorName, userName};
}

test('DOM regression: camelCase subTabText/subTabItem берут короткий |tab, table/stable остаются обычным текстом', async () => {
  await withCampFrame(async ({clock, frame, install}) => {
    const {el, doc} = frame.dom;
    const labels = ['推荐阵容', '热门阵容'].map(text => el('span', {class: 'subTabText-ksnvZ'}, text));
    doc.body.appendChild(el('div', {class: 'subTabContainer-wLHot'}, ...labels.map(label => el('div', {class: 'subTabItem-MOxhz'}, label))));
    const negatives = ['tableHead-hash', 'stableText-hash', 'dataTable-hash'].map(cls => {
      const label = el('span', {}, '推荐阵容'); doc.body.appendChild(el('div', {class: cls}, label)); return label;
    });
    install(); await clock.advance(0);
    assert.deepEqual(labels.map(node => node.textContent), ['Рекомендуемые', 'Популярные']);
    for (const label of labels) assert.equal(label.getAttribute('data-wxq-ru'), 'tab');
    for (const label of negatives) {
      assert.equal(label.textContent, 'Рекомендуемые составы');
      assert.equal(label.getAttribute('data-wxq-ru'), 'text');
    }
  });
});

test('DOM regression: известный DIV authorLabel переводится, тот же CN в authorName/userName сохраняется', async () => {
  await withCampFrame(async ({clock, frame, install}) => {
    const card = buildAuthorCard(frame.dom);
    install(); await clock.advance(0);
    assert.equal(card.role.textContent, 'Эксперт ACE');
    assert.ok(card.role.getAttribute('data-wxq-ru'), 'переведённый служебный role-label помечен для scoped CSS');
    for (const name of [card.authorName, card.userName]) {
      assert.equal(name.textContent, '万象棋大神');
      assert.equal(name.getAttribute('data-wxq-ru'), null);
    }
  });
});

test('DOM regression: author role exception не расширяется на SPAN, другие строки, authorName/UGC или raw protected', async () => {
  await withCampFrame(async ({clock, frame, install}) => {
    const cards = [
      buildAuthorCard(frame.dom, {roleTag: 'span'}),
      buildAuthorCard(frame.dom, {roleText: '推荐阵容'}),
      buildAuthorCard(frame.dom, {rootClass: 'author-M1MiN authorName-protected'}),
      buildAuthorCard(frame.dom, {rootClass: 'author-M1MiN ugc-card'}),
      buildAuthorCard(frame.dom, {rootClass: 'author-M1MiN userName-protected'}),
    ];
    const originals = cards.map(card => card.role.textContent);
    install(); await clock.advance(0);
    assert.deepEqual(cards.map(card => card.role.textContent), originals);
    for (const card of cards) assert.equal(card.role.getAttribute('data-wxq-ru'), null);
  });
});

test('DOM regression: author role exception сохраняет translate=no, data-no-translate и contenteditable у предка', async () => {
  await withCampFrame(async ({clock, frame, install}) => {
    const cards = [{translate: 'no'}, {'data-no-translate': ''}, {contenteditable: 'true'}]
      .map(rootAttrs => buildAuthorCard(frame.dom, {rootAttrs}));
    install(); await clock.advance(0);
    for (const card of cards) {
      assert.equal(card.role.textContent, '万象棋大神');
      assert.equal(card.role.getAttribute('data-wxq-ru'), null);
    }
  });
});

test('DOM regression: известные emoji badges переводят точный суффикс, чужие префиксы и пользовательские данные сохраняются', async () => {
  await withCampFrame(async ({clock, frame, install}) => {
    const {el, doc} = frame.dom;
    const badge = text => el('div', {class: 'tag-AnzbE'}, text);
    const positives = ['🚀近期飙升', '🚀 近期飙升', '🔥赛季热门', '🔥 赛季热门'].map(badge);
    const negativeTexts = ['TOP 近期飙升', '⭐赛季热门', '🚀未知徽章', '🚀推荐阵容', 'TOP 🚀近期飙升'];
    const negatives = negativeTexts.map(badge);
    doc.body.appendChild(el('div', {class: 'title-DnnGX'}, ...positives, ...negatives));
    const protectedBadges = ['authorName-actual', 'ugc-card'].map(cls => {
      const node = badge('🚀近期飙升'); doc.body.appendChild(el('div', {class: cls}, node)); return node;
    });
    install(); await clock.advance(0);
    assert.deepEqual(positives.map(node => node.textContent), ['🚀 Быстро растёт', '🚀 Быстро растёт', '🔥 Хит сезона', '🔥 Хит сезона']);
    assert.deepEqual(negatives.map(node => node.textContent), negativeTexts);
    for (const node of protectedBadges) {
      assert.equal(node.textContent, '🚀近期飙升');
      assert.equal(node.getAttribute('data-wxq-ru'), null);
    }
  });
});

test('DOM regression: role не переводится в host, не-camp госте и вне game-strategy route', async () => {
  const host = new Contents(1, 'window', mainURL);
  const qq = new Contents(2, 'webview', 'https://xui.ptlogin2.qq.com/cgi-bin/xlogin', host);
  const otherRoute = new Contents(3, 'webview', campURL, host);
  const cards = [host, qq, otherRoute].map(contents => buildAuthorCard(contents.dom));
  await hostSetup(makeElectron([host, qq, otherRoute])); await settle();
  for (const contents of [host, qq, otherRoute]) assert.ok(contents.dom.context.__wxqLauncherRU);
  for (const card of cards) {
    assert.equal(card.role.textContent, '万象棋大神');
    assert.equal(card.role.getAttribute('data-wxq-ru'), null);
  }
  after();
});

test('окно лаунчера: тексты длиннее 4000 символов не обрабатываются и не считаются; translate=no пропускается', async () => {
  const loading = new Contents(1, 'window', loadingURL);
  await hostSetup(makeElectron([loading]), loading);
  const {el, doc} = loading.dom;
  const huge = el('div', {class: 'game-loading-window__text'}, '玩'.repeat(4001));
  const unknown = el('div', {class: 'game-loading-window__subtext'}, '玩家小明正在加入');
  const known = el('div', {translate: 'no'}, el('span', {}, '请稍候...'));
  const nested = el('div', {translate: 'no'}, el('p', {}, el('b', {}, '另一个未知文本')));
  doc.body.appendChild(el('div', {class: 'game-loading-window'}, huge, unknown, known, nested));
  loading.emit('dom-ready'); await settle();
  assert.equal(texts(known)[0], '请稍候...', 'translate=no не переводится');
  assert.equal(texts(huge)[0].length, 4001);
  const row = runtimeOf().lifecycle.snapshot()[0];
  assert.equal(row.unmatchedCount, 1, 'считается только короткий неизвестный текст вне translate=no');
  after();
});

test('словарь guest: нет цепочек A→B→A (значение не является ключом)', async () => {
  const guest = read('source/launcher-ru/launcher-ru-guest.ru.json');
  const keys = new Set(Object.keys(guest).map(key => key.replace(/\|tab$/, '')));
  for (const [key, value] of Object.entries(guest)) {
    assert.equal(keys.has(value), false, 'значение совпало с ключом: ' + key);
    // шаблоны без слотов вида «<число> <существительное>» не допускаем: форма «Слово: {{n}}»
    if (/^\{\{n\d*\}\}/.test(key)) assert.match(value, /^[^{}]+: \{\{n\d*\}\}$/, key);
  }
});

test('словарь guest: нет «Ваньсян»/«тратег», все значения строки, ключи-шаблоны корректны', async () => {
  const guest = read('source/launcher-ru/launcher-ru-guest.ru.json');
  for (const [key, value] of Object.entries(guest)) {
    assert.equal(typeof value, 'string', key);
    assert.ok(!/Ваньсян|тратег/i.test(value), key);
    if (key.includes('{{')) for (const slot of key.matchAll(/\{\{([^{}]+)\}\}/g)) assert.match(slot[1], /^(?:n\d*|date|rn)$/, key);
  }
  assert.equal(guest['阵容'], 'Состав');
  assert.equal(guest['阵容|tab'], 'Составы');
  assert.equal(guest['万象大赛'], 'Турнир ACE');
  assert.equal(guest['棋手'], 'Шахматист');
});

const loadingURL = pathToFileURL(resourcesDir + '/app.asar/dist/renderer/game-loading.html').href;
const buildLoading = (dom, title) => {
  const {el, doc} = dom;
  const text = el('div', {class: 'game-loading-window__text'}, title);
  doc.body.appendChild(el('div', {class: 'game-loading-window'}, text, el('div', {class: 'game-loading-window__subtext'}, '请稍候...')));
  return text;
};

test('окно подготовки запуска: распознаётся, заголовок и подзаголовок по-русски, CSS и шрифт', async () => {
  assert.equal(moduleUnderTest.isLoadingURL(loadingURL + '?a=1#b'), true);
  assert.equal(moduleUnderTest.isLoadingURL(pathToFileURL(resourcesDir + '/dist/renderer/game-loading.html').href), false);
  assert.equal(moduleUnderTest.isLoadingURL('https://example.org/app.asar/dist/renderer/game-loading.html'), false);
  assert.equal(moduleUnderTest.isMainURL(loadingURL), false);
  const loading = new Contents(1, 'window', loadingURL + '?scene=1');
  await hostSetup(makeElectron([loading]));
  const title = buildLoading(loading.dom, '正在开启ACE安全模块......');
  loading.emit('dom-ready'); await settle();
  assert.equal(runtimeOf().lifecycle.snapshot()[0].kind, 'loading');
  assert.equal(runtimeOf().lifecycle.snapshot()[0].status, 'ready');
  assert.deepEqual(texts(loading.dom.doc.body), ['Запуск модуля защиты ACE…', 'Подождите…']);
  const css = loading.dom.doc.head.childNodes.find(n => n.getAttribute('data-wxq-ru-css'));
  assert.ok(css && css.textContent.includes('"WXQ Loading RU"') && css.textContent.includes('static/font/HYQiHei_65S.2c6fe0a6.ttf'));
  assert.ok(!loading.dom.doc.documentElement.childNodes.some(n => n.nodeType === 1 && n.getAttribute('data-wxq-brand')), 'бренд не вставляется');
  // смена заголовка по IPC: React меняет текстовый узел на месте
  title.childNodes[0].nodeValue = '正在准备进入赛场'; await sleep(10);
  assert.equal(title.textContent, 'Подготовка к матчу…');
  title.childNodes[0].nodeValue = '正在初始化图形模块'; await sleep(10);
  assert.equal(title.textContent, 'Инициализация графического модуля');
  title.childNodes[0].nodeValue = '正在下载'; await sleep(10);
  assert.equal(title.textContent, 'Загрузка…');
  after();
});

test('окно подготовки запуска: неизвестный китайский остаётся и только считается, содержимое не пишется', async () => {
  const loading = new Contents(1, 'window', loadingURL);
  const statusPath = path.join(tmp, 'loading-status', 'status.json');
  await hostSetup(makeElectron([loading]), loading, {statusPath});
  const title = buildLoading(loading.dom, '玩家小明正在加入');
  loading.emit('dom-ready'); await settle();
  assert.equal(title.textContent, '玩家小明正在加入');
  const row = runtimeOf().lifecycle.snapshot()[0];
  assert.equal(row.unmatchedCount, 1);
  const raw = fs.readFileSync(statusPath, 'utf8');
  assert.equal(raw.includes('小明') || raw.includes('玩家'), false, 'статус без содержимого окна');
  assert.equal(JSON.parse(raw).rows[0].unmatchedCount, 1);
  const report = await loading.executeJavaScript('window.__wxqLauncherRU.report(false)');
  assert.equal(report.unmatched, undefined, 'список текстов не отдаётся');
  after();
});

test('окно подготовки запуска не задевает index и гостей', async () => {
  const host = new Contents(1, 'window', mainURL);
  const loading = new Contents(2, 'window', loadingURL);
  const guest = new Contents(3, 'webview', loadingURL, host);
  const foreign = [new Contents(4, 'window', pathToFileURL('C:/Other/dist/renderer/game-loading.html').href), new Contents(5, 'window', 'https://example.org/app.asar/dist/renderer/game-loading.html')];
  const qq = new Contents(6, 'webview', 'https://xui.ptlogin2.qq.com/cgi-bin/xlogin', host);
  qq.dom.doc.body.appendChild(qq.dom.el('span', {class: 'tip'}, '登录'));
  await hostSetup(makeElectron([host, loading, guest, qq, ...foreign]));
  const {home} = buildScreen(host.dom);
  buildLoading(loading.dom, '加载中');
  host.emit('dom-ready'); loading.emit('dom-ready'); await settle();
  const kinds = Object.fromEntries(runtimeOf().lifecycle.snapshot().map(r => [r.id, r.kind]));
  assert.deepEqual(kinds, {1: 'host', 2: 'loading', 3: null, 4: null, 5: null, 6: 'guest'});
  assert.equal(guest.calls, 0, 'webview с адресом loading не обрабатывается');
  for (const other of foreign) assert.equal(other.calls, 0, other.url);
  // host: русский и без CSS окна подготовки; в окне подготовки нет брендинга index
  assert.ok(texts(host.dom.doc.body).includes('Главная') && texts(home).includes('Запустить игру'));
  const hostCss = host.dom.doc.head.childNodes.find(n => n.getAttribute('data-wxq-ru-css'));
  assert.ok(hostCss && !hostCss.textContent.includes('WXQ Loading RU'));
  assert.equal(host.dom.doc.getElementsByTagName('img')[0].getAttribute('src').startsWith('data:image/svg+xml'), true);
  assert.deepEqual(texts(loading.dom.doc.body), ['Загрузка…', 'Подождите…']);
  assert.deepEqual(texts(qq.dom.doc.body), ['Войти']);
  // негативы index остаются: ник не переводится
  assert.equal(texts(home.childNodes.find(n => n.className === 'user-nickname'))[0], '设置');
  after();
});

test('будущее окно и уничтожение contents', async () => {
  const host = new Contents(1, 'window', mainURL);
  const electron = makeElectron([]);
  await hostSetup(electron);
  const {el} = host.dom; host.dom.doc.body.appendChild(el('div', {}, '首页'));
  electron.app.emit('web-contents-created', {}, host); await settle();
  assert.equal(host.dom.doc.body.childNodes[0].textContent, 'Главная');
  const other = new Contents(2, 'window', 'https://example.org/');
  electron.app.emit('web-contents-created', {}, other); await settle();
  assert.equal(other.calls, 0);
  host.destroyed = true; host.emit('destroyed');
  assert.equal(runtimeOf().lifecycle.snapshot().some(r => r.id === 1), false);
  assert.equal(host.listenerCount('dom-ready'), 0);
  after();
  assert.equal(electron.app.listenerCount('web-contents-created'), 0);
});

// ---------------------------------------------------------------- окно трея и нативные меню главного процесса
const trayURL = pathToFileURL(resourcesDir + '/app.asar/dist/renderer/tray.html').href;

test('окно трея: пункты по-русски, ник не трогается, словарь меню не попадает в главное окно', async () => {
  assert.equal(moduleUnderTest.isTrayURL(trayURL + '?x=1'), true);
  assert.equal(moduleUnderTest.isTrayURL(pathToFileURL(resourcesDir + '/app.asar/dist/renderer/crash.html').href), true);
  assert.equal(moduleUnderTest.isTrayURL('https://example.org/app.asar/dist/renderer/tray.html'), false);
  assert.equal(moduleUnderTest.isTrayURL(pathToFileURL(resourcesDir + '/dist/renderer/tray.html').href), false);
  const host = new Contents(1, 'window', mainURL);
  const tray = new Contents(2, 'window', trayURL);
  await hostSetup(makeElectron([host, tray]));
  const {el} = tray.dom;
  tray.dom.doc.body.appendChild(el('div', {class: 'tray-menu'},
    el('img', {src: 'avatar.png'}), el('span', {class: 'user-nickname'}, '设置'), el('span', {}, '***'),
    el('div', {class: 'item'}, '显示主窗口'), el('div', {class: 'item'}, '切换账号'), el('div', {class: 'item'}, '退出'), el('div', {class: 'item'}, '打开'), el('div', {class: 'item'}, '未知的新项')));
  host.dom.doc.body.appendChild(el('div', {}, '打开'));
  tray.emit('dom-ready'); host.emit('dom-ready'); await settle();
  assert.equal(runtimeOf().lifecycle.snapshot().find(r => r.id === 2).kind, 'tray');
  assert.deepEqual(texts(tray.dom.doc.body), ['设置', '***', 'Показать главное окно', 'Сменить аккаунт', 'Выйти', 'Открыть', '未知的新项']);
  assert.deepEqual(texts(host.dom.doc.body), ['打开'], 'раздел menu только для трея и главного процесса');
  after();
});

// Фиктивные классы electron: Menu, Tray, dialog.
function makeMenuElectron() {
  const calls = [];
  class MenuItem { constructor(o) { Object.assign(this, o); } }
  class Menu {
    constructor() { this.items = []; }
    static buildFromTemplate(template) { calls.push(['buildFromTemplate', template]); const m = new Menu(); m.items = template.map(t => new MenuItem(Object.assign({}, t, t.submenu ? {submenu: Menu.buildFromTemplate(t.submenu)} : {}))); return m; }
    static setApplicationMenu(menu) { calls.push(['setApplicationMenu', menu]); }
    append(item) { calls.push(['append', item]); this.items.push(item); }
    insert(pos, item) { calls.push(['insert', item]); this.items.splice(pos, 0, item); }
    popup() { calls.push(['popup', this]); }
  }
  class Tray {
    setContextMenu(menu) { calls.push(['setContextMenu', menu]); }
    popUpContextMenu(menu) { calls.push(['popUpContextMenu', menu]); }
    setToolTip(text) { calls.push(['setToolTip', text]); }
    setTitle(text) { calls.push(['setTitle', text]); }
  }
  const dialog = {};
  for (const name of ['showMessageBox', 'showMessageBoxSync', 'showOpenDialog', 'showSaveDialog']) dialog[name] = function (...args) { calls.push([name, args]); return 0; };
  dialog.showErrorBox = (title, content) => { calls.push(['showErrorBox', [title, content]]); };
  return {calls, Menu, MenuItem, Tray, dialog};
}
const lastCall = (calls, name) => calls.filter(c => c[0] === name).at(-1)[1];

test('меню главного процесса: buildFromTemplate, мнемоника, подменю, шаблон с числом, исходный шаблон не мутируется', async () => {
  const fake = makeMenuElectron();
  const electron = Object.assign(makeElectron([]), {Menu: fake.Menu, Tray: fake.Tray, dialog: fake.dialog});
  const originalBuild = fake.Menu.buildFromTemplate;
  await hostSetup(electron);
  assert.notEqual(fake.Menu.buildFromTemplate, originalBuild);
  const hooks = runtimeOf().menuHooks;
  assert.ok(hooks.hooked.includes('Menu.buildFromTemplate') && hooks.hooked.includes('Tray.setContextMenu') && hooks.hooked.includes('dialog.showMessageBox'));
  const numeric = Object.entries(payload.strings).map(([key, value]) => [key, value, /^[^{]*(\{\{slot\d*\}\})[^{]*$/.exec(key)]).filter(([key, value, m]) => m && /[一-鿿]/.test(key) && value.includes(m[1]))
    .map(([key, value, m]) => [key.replace(m[1], '5'), value.replace(m[1], '5')])[0];
  assert.ok(numeric, 'в словаре есть числовой шаблон');
  const template = [
    {label: '显示主窗口'}, {label: '切换账号', sublabel: '退出'}, {type: 'separator'}, {label: '退出(&Q)', click() {}},
    {label: '更多', submenu: [{label: '打开设置'}, {label: '未知的新项'}]},
    {label: numeric[0]}, {label: 'Plain Latin'}, {label: '  退出  '}];
  const snapshot = JSON.stringify(template);
  const menu = fake.Menu.buildFromTemplate(template);
  assert.equal(JSON.stringify(template), snapshot, 'исходный шаблон не изменён');
  const passed = fake.calls.find(c => c[0] === 'buildFromTemplate')[1]; // первый вызов — внешний (подменю фиктивный Menu строит рекурсивно)
  assert.deepEqual(passed.slice(0, 4).map(i => i.label), ['Показать главное окно', 'Сменить аккаунт', undefined, 'Выйти']);
  assert.equal(passed[1].sublabel, 'Выйти');
  assert.equal(passed[3].click, template[3].click, 'click сохранён');
  assert.deepEqual(passed[4].submenu.map(i => i.label), ['Открыть настройки', '未知的新项']);
  assert.equal(passed[5].label, numeric[1]);
  assert.equal(passed[6].label, 'Plain Latin');
  assert.equal(passed[7].label, '  Выйти  ', 'пробелы по краям сохраняются');
  assert.equal(menu.items.length, template.length);
  after();
  assert.equal(fake.Menu.buildFromTemplate, originalBuild, 'dispose возвращает исходную функцию');
});

test('меню главного процесса: готовые Menu/MenuItem, Tray и диалоги', async () => {
  const fake = makeMenuElectron();
  const electron = Object.assign(makeElectron([]), {Menu: fake.Menu, Tray: fake.Tray, dialog: fake.dialog});
  await hostSetup(electron);
  const built = new fake.Menu(); built.items.push(new fake.MenuItem({label: '切换账号'}), new fake.MenuItem({label: '更多', submenu: Object.assign(new fake.Menu(), {items: [new fake.MenuItem({label: '退出'})]})}));
  const tray = new fake.Tray();
  tray.setContextMenu(built);
  assert.equal(built.items[0].label, 'Сменить аккаунт');
  assert.equal(built.items[1].submenu.items[0].label, 'Выйти');
  const extra = new fake.MenuItem({label: '显示主窗口'}); built.append(extra); assert.equal(extra.label, 'Показать главное окно');
  const inserted = new fake.MenuItem({label: '退出'}); built.insert(0, inserted); assert.equal(inserted.label, 'Выйти');
  built.items[0].label = '检查更新'; built.popup(); assert.equal(built.items[0].label, 'Проверить обновления');
  tray.setToolTip('检查更新'); assert.equal(lastCall(fake.calls, 'setToolTip'), 'Проверить обновления');
  tray.setToolTip('Honor of Kings'); assert.equal(lastCall(fake.calls, 'setToolTip'), 'Honor of Kings');
  const options = {message: '退出', detail: '未知的新项', buttons: ['确定', '取消', 'OK'], title: '提示', filters: [{name: '设置', extensions: ['x']}]};
  const copy = JSON.stringify(options);
  fake.dialog.showMessageBox({id: 'window'}, options);
  const [win, passed] = lastCall(fake.calls, 'showMessageBox');
  assert.equal(win.id, 'window'); assert.equal(JSON.stringify(options), copy, 'options не мутируется');
  assert.equal(passed.message, 'Выйти'); assert.equal(passed.detail, '未知的新项'); assert.deepEqual(passed.buttons, ['Подтвердить', 'Отменить', 'OK']); assert.equal(passed.title, 'Уведомление');
  assert.equal(passed.filters[0].name, 'Настройки');
  fake.dialog.showMessageBoxSync({message: '关闭'}); assert.equal(lastCall(fake.calls, 'showMessageBoxSync')[0].message, 'Закрыть');
  fake.dialog.showErrorBox('提示', '退出'); assert.deepEqual(lastCall(fake.calls, 'showErrorBox'), ['Уведомление', 'Выйти']);
  const log = fs.existsSync(fast.menuLogPath) ? fs.readFileSync(fast.menuLogPath, 'utf8') : '';
  assert.equal(/[一-鿿]/.test(log), false, 'журнал не содержит текстов меню без collectUnmatched');
  assert.ok(runtimeOf().menuHooks.summary().translated > 5);
  after();
});

test('меню главного процесса: fail-closed — исключение при переводе не мешает исходному вызову; нет Menu/заморожен — install жив', async () => {
  const fake = makeMenuElectron();
  const electron = Object.assign(makeElectron([]), {Menu: fake.Menu, Tray: fake.Tray, dialog: fake.dialog});
  await hostSetup(electron);
  const broken = {type: 'normal'}; Object.defineProperty(broken, 'label', {get() { throw new Error('boom'); }, enumerable: true});
  const template = [broken, {label: '退出'}];
  assert.throws(() => fake.Menu.buildFromTemplate(template), /boom/, 'исходная функция вызвана с исходными аргументами и сама упала на кривом пункте');
  assert.equal(lastCall(fake.calls, 'buildFromTemplate'), template, 'исходные аргументы переданы без изменений');
  assert.ok(runtimeOf().menuHooks.summary().errors >= 1);
  assert.ok(fs.readFileSync(fast.menuLogPath, 'utf8').includes('hook-error:Menu.buildFromTemplate'));
  after();
  // замороженные классы: перехват недоступен, но install успешен и лаунчер не страдает
  const frozen = Object.assign(makeElectron([]), {Menu: Object.freeze({buildFromTemplate() { return 1; }}), dialog: Object.freeze({showMessageBox() { return 2; }})});
  const result = moduleUnderTest.install({payloadRoot, electron: frozen, ...fast});
  assert.equal(result.ok, true);
  assert.deepEqual(runtimeOf().menuHooks.hooked, []);
  assert.equal(frozen.Menu.buildFromTemplate(), 1);
  after();
  // перехват отключён опцией
  const off = Object.assign(makeElectron([]), {Menu: makeMenuElectron().Menu});
  const originalBuild = off.Menu.buildFromTemplate;
  assert.equal(moduleUnderTest.install({payloadRoot, electron: off, menuHooks: false, ...fast}).ok, true);
  assert.equal(off.Menu.buildFromTemplate, originalBuild); after();
});

test('переводчик главного процесса: точное совпадение, шаблоны, чужой китайский не меняется', async () => {
  const translator = moduleUnderTest.createTranslator({'退出': 'Выйти', '已下载{{n}}个': 'Скачано: {{n}}', '玩家{{name}}加入': 'Игрок {{name}} вошёл', 'bad': '', '打开': 'Открыть'});
  assert.equal(translator.translate('退出'), 'Выйти');
  assert.equal(translator.translate('已下载12个'), 'Скачано: 12');
  assert.equal(translator.translate('玩家Tom加入'), 'Игрок Tom вошёл');
  assert.equal(translator.translate('玩家小明加入'), undefined, 'непереведённый китайский в слоте — не шаблон');
  assert.equal(translator.translate('退出了'), undefined);
  assert.equal(translator.translate('Exit'), undefined);
  assert.equal(translator.translate(undefined), undefined);
  assert.equal(translator.translate('打开(&O)'), 'Открыть');
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      assert.equal(sha256(fs.readFileSync(runtimeSource)), runtimeSourceSha256, 'исходный runtime не меняется при создании тестовых payload');
      console.log('ok   ' + name);
    }
    catch (error) { failed++; console.log('FAIL ' + name + '\n' + (error.stack || error)); try { after(); } catch { /* ignore */ } }
  }
  fs.rmSync(tmp, {recursive: true, force: true});
  console.log(JSON.stringify({passed: tests.length - failed, failed, total: tests.length, realLauncherTested: false}));
  process.exitCode = failed ? 1 : 0;
})();
