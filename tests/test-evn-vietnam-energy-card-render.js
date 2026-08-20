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

// 1. Incomplete configuration drafts
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

// 2. Malformed customer_views variations must never throw in setConfig
assert.doesNotThrow(() => {
  const c1 = new Card();
  c1.setConfig({ type: 'custom:evn-vietnam-energy-card', customer_views: null });
  c1.setConfig({ type: 'custom:evn-vietnam-energy-card', customer_views: 'invalid' });
  c1.setConfig({ type: 'custom:evn-vietnam-energy-card', customer_views: [null, undefined, {}, { entity: '' }] });
}, 'malformed customer_views in setConfig must be safely ignored without throwing');

// 3. Valid aggregate configuration
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
assert.ok(containsTag(validCard.shadowRoot, 'rect'), 'a non-empty daily history must render chart bars');

const partialCard = new Card();
partialCard.setConfig({ type: 'custom:evn-vietnam-energy-card', entity: 'sensor.partial_month' });
partialCard.hass = {
  states: {
    'sensor.partial_month': {
      state: '4.2',
      attributes: {
        customer_code: '__aggregate__',
        selected_customer_codes: ['PB000001', 'PB000002'],
        successful_customer_codes: ['PB000001'],
        is_partial: true,
        partial_errors: { PB000002: 'api_error' },
        daily_history: [],
      },
    },
  },
};
assert.ok(
  collectTextContents(partialCard.shadowRoot).join(' ').includes('Dữ liệu tổng hợp chưa đầy đủ'),
  'a partial aggregate must expose its safe warning state',
);

const unavailableCard = new Card();
unavailableCard.setConfig({ type: 'custom:evn-vietnam-energy-card', entity: 'sensor.unavailable_month' });
unavailableCard.hass = { states: { 'sensor.unavailable_month': { state: 'unavailable', attributes: {} } } };
assert.ok(
  collectTextContents(unavailableCard.shadowRoot).join(' ').includes('đang ở trạng thái: unavailable'),
  'an unavailable entity must render a safe local state',
);

// 4. Multi-view selector and view switching
const multiViewCard = new Card();
const multiHass = {
  states: {
    'sensor.aggregate_month': {
      state: '100.0',
      attributes: {
        customer_code: '__aggregate__',
        selected_customer_codes: ['KH01', 'KH02'],
        daily_history: [{ date: '2026-08-20', consumption: 100.0 }],
        monthly_history: [{ period: '08/2026', totalKwh: 100.0, totalAmount: 250000, isPaid: true }],
      },
    },
    'sensor.kh01_month': {
      state: '45.5',
      attributes: {
        customer_code: 'KH01',
        daily_history: [{ date: '2026-08-20', consumption: 45.5 }],
        monthly_history: [{ period: '08/2026', totalKwh: 45.5, totalAmount: 110000, isPaid: false }],
      },
    },
    'sensor.kh02_month': {
      state: '54.5',
      attributes: {
        customer_code: 'KH02',
        daily_history: [{ date: '2026-08-20', consumption: 54.5 }],
      },
    },
  },
};

multiViewCard.setConfig({
  type: 'custom:evn-vietnam-energy-card',
  entity: 'sensor.aggregate_month',
  customer_views: [
    { id: 'aggregate', label: 'Tổng', entity: 'sensor.aggregate_month' },
    { id: 'kh01', label: 'Mã KH 1', entity: 'sensor.kh01_month' },
    { id: 'kh02', label: 'Mã KH 2', entity: 'sensor.kh02_month' },
    { id: 'kh_missing', label: 'Mã KH lỗi', entity: 'sensor.kh_missing' },
  ],
});
multiViewCard.hass = multiHass;

const select = findNode(multiViewCard.shadowRoot, (n) => n.tagName === 'select');
assert.ok(select, 'card header must render a <select> view selector when customer_views has multiple items');
assert.equal(select.children.length, 4, 'selector must contain options for all configured views');

// Initial view is aggregate (100 kWh, paid bill)
const initialTexts = collectTextContents(multiViewCard.shadowRoot).join(' ');
assert.ok(initialTexts.includes('100') || initialTexts.includes('100,0 kWh'), 'initial view must show aggregate data');
assert.ok(initialTexts.includes('Đã thanh toán'), 'initial view must show aggregate bill status');

// Switch view to kh01
select.value = 'kh01';
select.dispatchEvent({ type: 'change', target: { value: 'kh01' } });

const switchedTexts = collectTextContents(multiViewCard.shadowRoot).join(' ');
assert.ok(switchedTexts.includes('45,5 kWh') || switchedTexts.includes('45.5'), 'switched view must display kh01 consumption');
assert.ok(switchedTexts.includes('Chưa thanh toán'), 'switched view must display kh01 unpaid bill status');

function findAllNodes(node, predicate) {
  const matches = [];
  if (predicate(node)) matches.push(node);
  for (const child of node.children) {
    matches.push(...findAllNodes(child, predicate));
  }
  return matches;
}

// Switch to missing entity view (should show local error, keep select, not throw)
select.value = 'kh_missing';
select.dispatchEvent({ type: 'change', target: { value: 'kh_missing' } });
const missingTexts = collectTextContents(multiViewCard.shadowRoot).join(' ');
assert.ok(missingTexts.includes('Không tìm thấy entity: sensor.kh_missing'), 'missing view entity must show explanatory message');
const selectAfterMissing = findNode(multiViewCard.shadowRoot, (n) => n.tagName === 'select');
assert.ok(selectAfterMissing, 'view selector must remain accessible even when an entity is missing');

// 5. Daily chart range controls (7/14/30 days) and average line
const historyCard = new Card();
const thirtyDaysHistory = Array.from({ length: 30 }, (_, i) => ({
  date: `2026-08-${String(i + 1).padStart(2, '0')}`,
  consumption: 1.0 + (i % 5),
}));

const historyHass = {
  states: {
    'sensor.history_month': {
      state: '75.0',
      attributes: {
        customer_code: 'TEST01',
        daily_history: thirtyDaysHistory,
      },
    },
  },
};

historyCard.setConfig({
  type: 'custom:evn-vietnam-energy-card',
  entity: 'sensor.history_month',
});
historyCard.hass = historyHass;

const rangeButtons = findAllNodes(historyCard.shadowRoot, (n) => n.tagName === 'button' && n.className && n.className.includes('range-btn'));
assert.equal(rangeButtons.length, 3, 'chart header must render 3 range control buttons (7, 14, 30 days)');

// Initial default range is 30 days -> 30 bars
const isChartBar = (n) => n.tagName === 'rect' && String(n.className || '').split(/\s+/).includes('bar');
const axisLabelsFor = (card) => findAllNodes(
  card.shadowRoot,
  (n) => n.tagName === 'text' && n.className === 'axis-label',
);
const expectedAxisLabel = (card, series, idx) => {
  const full = card._formatDateLabel(series[idx].date);
  if (series.length <= 7 || idx === 0 || idx === series.length - 1) return full;
  const previousMonth = series[idx - 1].date.slice(5, 7);
  return previousMonth !== series[idx].date.slice(5, 7) ? full : full.slice(0, 2);
};
const assertChartColumnsAndLabels = (card, days) => {
  const buttons = findAllNodes(card.shadowRoot, (n) => n.tagName === 'button' && n.className && n.className.includes('range-btn'));
  const button = buttons.find((b) => b.textContent && b.textContent.includes(String(days)));
  assert.ok(button, `${days}-day range control must exist`);
  button.dispatchEvent({ type: 'click' });

  const series = card._calendarBars(thirtyDaysHistory, days);
  const rangeBars = findAllNodes(card.shadowRoot, isChartBar);
  const labels = axisLabelsFor(card);
  assert.equal(rangeBars.length, days, `${days}-day range must render one bar per calendar day`);
  assert.equal(labels.length, rangeBars.length, `${days}-day range must render one label per calendar day`);
  labels.forEach((label, idx) => {
    assert.equal(label.textContent, expectedAxisLabel(card, series, idx), `label ${idx} must describe bar ${idx}'s date`);
    const expectedX = 40 + idx * ((720 - 40 - 12) / days) + ((720 - 40 - 12) / days) / 2;
    assert.equal(Number(label.attributes.x), expectedX, `label ${idx} must share bar ${idx}'s slot center`);
    assert.equal(label.attributes.transform, undefined, `${days}-day labels must stay upright on the bar center`);
    const bar = rangeBars[idx];
    const barCenter = Number(bar.attributes.x) + Number(bar.attributes.width) / 2;
    assert.ok(Math.abs(barCenter - expectedX) < 0.01, `bar ${idx} center must equal label ${idx} x`);
  });
};
let bars = findAllNodes(historyCard.shadowRoot, isChartBar);
assert.equal(bars.length, 30, 'initial 30-day view must render 30 chart bars');
assert.equal(axisLabelsFor(historyCard).length, bars.length, 'initial 30-day view must render 30 date labels');

// Average line must be rendered
const avgLine = findNode(historyCard.shadowRoot, (n) => n.tagName === 'line' && (n.className === 'avg-line' || n.attributes['class'] === 'avg-line'));
assert.ok(avgLine, 'chart must render an amber dashed average line for non-empty history');

// Click 7-day range button
const btn7 = rangeButtons.find((b) => b.textContent && b.textContent.includes('7'));
assert.ok(btn7, '7-day range button must exist');
btn7.dispatchEvent({ type: 'click' });

bars = findAllNodes(historyCard.shadowRoot, isChartBar);
assert.equal(bars.length, 7, 'clicking 7-day range button must filter visible chart bars to 7');
assert.equal(axisLabelsFor(historyCard).length, bars.length, '7-day range must label each calendar column');

assertChartColumnsAndLabels(historyCard, 14);
assertChartColumnsAndLabels(historyCard, 30);

// Check KPI label for Chi phí ước tính
const allTexts = collectTextContents(historyCard.shadowRoot).join(' ');
assert.ok(allTexts.includes('Chi phí ước tính'), 'card must render Chi phí ước tính KPI label');

// 6. Sparse daily history still occupies one calendar column per selected day
const sparseCard = new Card();
const sparseDayIndexes = [0, 1, 3, 4, 6, 7, 9, 10, 12, 13, 15, 16, 18, 19, 21, 22, 24, 27, 29];
const sparseHistory = sparseDayIndexes.map((i) => ({
  date: `2026-08-${String(i + 1).padStart(2, '0')}`,
  consumption: i === 9 || i === 10 ? 20 : 12,
}));
sparseCard.setConfig({
  type: 'custom:evn-vietnam-energy-card',
  entity: 'sensor.sparse_month',
});
sparseCard.hass = {
  states: {
    'sensor.sparse_month': {
      state: '200',
      attributes: {
        customer_code: 'TEST01',
        daily_history: sparseHistory,
      },
    },
  },
};
const sparseBars = findAllNodes(sparseCard.shadowRoot, isChartBar);
assert.equal(sparseBars.length, 30, '30-day range must render one column per calendar day, filling missing days');
assert.equal(
  sparseBars.filter((bar) => String(bar.className || '').split(/\s+/).includes('bar-empty')).length,
  11,
  'missing days inside the observed calendar span must retain empty columns',
);

const sparseAxisLabels = axisLabelsFor(sparseCard);
assert.equal(sparseAxisLabels.length, sparseBars.length, '19 sparse source days must still render 30 date labels');
const sparseSeries = sparseCard._calendarBars(sparseHistory, 30);
sparseAxisLabels.forEach((label, idx) => {
  assert.equal(label.textContent, expectedAxisLabel(sparseCard, sparseSeries, idx), `sparse label ${idx} must describe its own calendar column`);
});
