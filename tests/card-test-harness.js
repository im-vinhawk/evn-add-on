// Shared fake-DOM harness for the card tests: loads the card source in a sandbox with a pinned clock and time zone.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class FakeNode {
  constructor(tagName = '') {
    this.tagName = tagName;
    this.children = [];
    this.attributes = {};
    this.className = '';
    this.textContent = '';
    this.value = '';
    this._listeners = {};
  }

  get firstChild() {
    return this.children[0] || null;
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  removeChild(child) {
    this.children.splice(this.children.indexOf(child), 1);
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === 'class') {
      this.className = String(value);
    }
  }

  addEventListener(type, handler) {
    this._listeners[type] = this._listeners[type] || [];
    this._listeners[type].push(handler);
  }

  dispatchEvent(event) {
    if (!event.stopPropagation) event.stopPropagation = () => {};
    if (!event.preventDefault) event.preventDefault = () => {};
    const handlers = this._listeners[event.type] || [];
    handlers.forEach((h) => h(event));
  }

  attachShadow() {
    this.shadowRoot = new FakeNode();
    return this.shadowRoot;
  }
}

// The card reads "today" from the clock; tests pin it and the browser calendar so
// day-edge cases are deterministic. Tests move the clock through `clock.ms`.
process.env.TZ = 'Asia/Ho_Chi_Minh';
const clock = { ms: Date.parse('2026-08-30T05:00:00Z') };
class FakeDate extends Date {
  constructor(...args) {
    if (args.length === 0) super(clock.ms);
    else super(...args);
  }

  static now() {
    return clock.ms;
  }
}

const registered = new Map();
const context = {
  Date: FakeDate,
  HTMLElement: FakeNode,
  document: {
    createElement: (tagName) => new FakeNode(tagName),
    createElementNS: (_, tagName) => new FakeNode(tagName),
    createTextNode: (text) => Object.assign(new FakeNode('#text'), { textContent: String(text) }),
  },
  customElements: {
    get: (name) => registered.get(name),
    define: (name, value) => registered.set(name, value),
  },
  window: {},
  Intl,
  Number,
  String,
  Array,
  Set,
  Math,
  Boolean,
  Object,
};

vm.runInNewContext(
  fs.readFileSync(path.join(__dirname, '..', 'custom_components', 'evn_vietnam', 'www', 'evn-vietnam-energy-card.js'), 'utf8'),
  context,
  { filename: 'evn-vietnam-energy-card.js' },
);

const Card = registered.get('evn-vietnam-energy-card');
if (!Card) throw new Error('the custom card must register itself');

function findNode(node, predicate) {
  if (predicate(node)) return node;
  for (const child of node.children) {
    const found = findNode(child, predicate);
    if (found) return found;
  }
  return null;
}

function containsTag(node, tagName) {
  return node.children.some((child) => child.tagName === tagName || containsTag(child, tagName));
}

function collectTextContents(node) {
  let texts = [];
  if (node.textContent) texts.push(node.textContent);
  for (const child of node.children) {
    texts.push(...collectTextContents(child));
  }
  return texts;
}

module.exports = { Card, clock, findNode, containsTag, collectTextContents, registered };
