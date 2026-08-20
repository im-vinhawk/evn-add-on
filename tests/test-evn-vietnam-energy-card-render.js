#!/usr/bin/env node

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class FakeNode {
  constructor(tagName = '') {
    this.tagName = tagName;
    this.children = [];
    this.attributes = {};
    this.className = '';
    this.textContent = '';
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
  }

  addEventListener() {}

  attachShadow() {
    this.shadowRoot = new FakeNode();
    return this.shadowRoot;
  }
}

const registered = new Map();
const context = {
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
  fs.readFileSync('custom_components/evn_vietnam/www/evn-vietnam-energy-card.js', 'utf8'),
  context,
  { filename: 'evn-vietnam-energy-card.js' },
);

const Card = registered.get('evn-vietnam-energy-card');
assert.ok(Card, 'the custom card must register itself');
assert.equal(
  Object.getOwnPropertyDescriptor(Card, 'properties'),
  undefined,
  'a vanilla HTMLElement card must not advertise Lit reactive properties',
);

const incompleteCard = new Card();
assert.doesNotThrow(
  () => incompleteCard.setConfig({ type: 'custom:evn-vietnam-energy-card' }),
  'an incomplete custom-card draft must render a local instruction, not throw Lovelace Configuration error',
);
incompleteCard.hass = { states: {} };
assert.ok(
  incompleteCard.shadowRoot.children.length > 0,
  'an incomplete draft must still produce a card-local state',
);

const validCard = new Card();
const validHass = {
  states: {
    'sensor.aggregate_month': {
      state: '4.2',
      attributes: {
        customer_code: '__aggregate__',
        selected_customer_codes: ['a', 'b'],
        daily_history: [{ date: '2026-08-20', consumption: 4.2 }],
      },
    },
    'sensor.aggregate_cost': {
      state: '10000',
      attributes: {
        customer_code: '__aggregate__',
        selected_customer_codes: ['a', 'b'],
        bills: [],
      },
    },
  },
};
assert.doesNotThrow(() => {
  validCard.setConfig({
    type: 'custom:evn-vietnam-energy-card',
    entity: 'sensor.aggregate_month',
    cost_entity: 'sensor.aggregate_cost',
  });
  validCard.hass = validHass;
}, 'a valid aggregate configuration must render without throwing');
assert.equal(validCard.hass, validHass, 'Home Assistant wrappers must be able to read back hass');
assert.equal(validCard.config.entity, 'sensor.aggregate_month', 'Home Assistant wrappers must be able to read back config');

function containsTag(node, tagName) {
  return node.children.some((child) => child.tagName === tagName || containsTag(child, tagName));
}

assert.ok(containsTag(validCard.shadowRoot, 'rect'), 'a non-empty daily history must render chart bars');
