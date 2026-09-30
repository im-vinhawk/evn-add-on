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

// 5. Dropdown labels: manual label > nickname (code) > full customer code
function optionLabels(card) {
  const select = findNode(card.shadowRoot, (n) => n.className === 'view-selector');
  assert.ok(select, 'a multi-view card must render the selector');
  return select.children.map((o) => o.textContent);
}
function codeState(code, alias) {
  const attributes = { customer_code: code, daily_history: [] };
  if (alias !== undefined) attributes.customer_alias = alias;
  return { state: '1', attributes };
}
const labelCard = new Card();
labelCard.setConfig({
  type: 'custom:evn-vietnam-energy-card',
  entity: 'sensor.agg',
  customer_views: [
    { id: 'aggregate', entity: 'sensor.agg' },
    { id: 'a', label: 'Mã KH 1', entity: 'sensor.a' },
    { id: 'b', label: 'Khách hàng 2', entity: 'sensor.b' },
    { id: 'c', label: 'Nhà kho', entity: 'sensor.c' },
    { id: 'd', label: 'Mã KH 4', entity: 'sensor.missing' },
  ],
});
labelCard.hass = {
  states: {
    'sensor.agg': { state: '1', attributes: { customer_code: '__aggregate__', selected_customer_codes: ['PB000001'], daily_history: [] } },
    'sensor.a': codeState('PB000001', 'Nhà chính'),
    'sensor.b': codeState('PB000002', ''),
    'sensor.c': codeState('PB000003', 'Bị ghi đè'),
  },
};
assert.deepEqual(
  optionLabels(labelCard),
  ['Tổng', 'Nhà chính (PB000001)', 'PB000002', 'Nhà kho', 'Mã KH 4'],
  'generic label + nickname -> "nickname (code)"; no nickname -> full code; manual label wins; missing entity keeps the old fallback',
);

// Labels follow later hass updates (alias edited in Options)
labelCard.hass = {
  states: {
    ...labelCard.hass.states,
    'sensor.a': codeState('PB000001', 'Nhà phụ'),
  },
};
assert.equal(optionLabels(labelCard)[1], 'Nhà phụ (PB000001)', 'labels must be recomputed on each hass update');

// Header badge shows nickname + full code
const badgeCard = new Card();
badgeCard.setConfig({ type: 'custom:evn-vietnam-energy-card', entity: 'sensor.a' });
badgeCard.hass = { states: { 'sensor.a': codeState('PB000001', 'Nhà chính') } };
assert.ok(
  collectTextContents(badgeCard.shadowRoot).includes('Nhà chính (PB000001)'),
  'header badge must show nickname with the full customer code',
);
const plainBadgeCard = new Card();
plainBadgeCard.setConfig({ type: 'custom:evn-vietnam-energy-card', entity: 'sensor.a' });
plainBadgeCard.hass = { states: { 'sensor.a': codeState('PB000001') } };
assert.ok(
  collectTextContents(plainBadgeCard.shadowRoot).includes('Mã KH: PB000001'),
  'header badge without nickname keeps the code-only form',
);

// Bill table: missing kWh renders the placeholder, not 0
const billCard = new Card();
billCard.setConfig({ type: 'custom:evn-vietnam-energy-card', entity: 'sensor.a' });
billCard.hass = {
  states: {
    'sensor.a': {
      state: '1',
      attributes: {
        customer_code: 'PB000001',
        daily_history: [],
        monthly_history: [{ period: 'Tháng 8/2026', total_kwh: null, total_amount: 123000 }],
      },
    },
  },
};
assert.ok(
  !collectTextContents(billCard.shadowRoot).includes('0,0 kWh') && collectTextContents(billCard.shadowRoot).includes('—'),
  'a bill without kWh must show the placeholder',
);

// Bill table: rows with known and unknown kWh side by side, extra period keys ignored
const mixedBillCard = new Card();
mixedBillCard.setConfig({ type: 'custom:evn-vietnam-energy-card', entity: 'sensor.a' });
mixedBillCard.hass = {
  states: {
    'sensor.a': {
      state: '1',
      attributes: {
        customer_code: 'PB000001',
        daily_history: [],
        monthly_history: [
          {
            period: 'Tháng 9/2026', total_kwh: 120, total_amount: 300000, calculated_amount: 300000,
            period_start: '2026-09-01', period_end: '2026-09-30', KY: 1, THANG: 9, NAM: 2026,
          },
          { period: 'Tháng 8/2025', total_kwh: null, total_amount: 123000, calculated_amount: null },
        ],
      },
    },
  },
};
const mixedTexts = collectTextContents(mixedBillCard.shadowRoot);
assert.ok(mixedTexts.includes('120 kWh'), 'a bill with known kWh must show it');
assert.ok(mixedTexts.includes('—'), 'the bill without kWh must still show the placeholder');
assert.ok(!mixedTexts.includes('0,0 kWh'), 'unknown kWh must never render as zero');

// 7. Day-vs-last-month comparison tile (statistics via hass.callWS)
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const texts = (card) => collectTextContents(card.shadowRoot);
const ENERGY_ID = 'evn_vietnam:pb000001_daily_energy';
const COST_ID = 'evn_vietnam:pb000001_daily_cost';
const HOUR = 3600 * 1000;

// Statistics rows as the recorder returns them: one row per local day, `start` in epoch ms.
function statRow(iso, change) {
  const [y, m, d] = iso.split('-').map(Number);
  const start = Date.UTC(y, m - 1, d) - 7 * HOUR; // local midnight at +07
  return { start, end: start + 24 * HOUR, change };
}
function monthRows(year, month, days, valueOf, skip = []) {
  const rows = [];
  for (let d = 1; d <= days; d += 1) {
    if (skip.includes(d)) continue;
    rows.push(statRow(`${year}-${String(month).padStart(2, '0')}-${String(d).padStart(2, '0')}`, valueOf(d)));
  }
  return rows;
}
function compareHass(callWS, attrs = {}, lastUpdated = '2026-08-21T03:00:00+00:00') {
  return {
    config: { time_zone: 'Asia/Ho_Chi_Minh' },
    callWS,
    states: {
      'sensor.cur': {
        state: '100',
        last_updated: lastUpdated,
        attributes: {
          customer_code: 'PB000001',
          statistics_id: ENERGY_ID,
          cost_statistics_id: COST_ID,
          daily_history: [
            { date: '2026-08-19', consumption: 11 },
            { date: '2026-08-20', consumption: 12.5 },
          ],
          ...attrs,
        },
      },
    },
  };
}
function augustStats() {
  // July: 10 kWh every day except the 5th; August 20th: 12.5 kWh. Cost: 25000 (July) / 30000 (Aug 20).
  return {
    [ENERGY_ID]: [
      ...monthRows(2026, 7, 31, () => 10, [5]),
      ...monthRows(2026, 8, 20, (d) => (d === 20 ? 12.5 : 11)),
    ],
    [COST_ID]: [
      ...monthRows(2026, 7, 31, () => 25000, [5]),
      ...monthRows(2026, 8, 20, (d) => (d === 20 ? 30000 : 27000)),
    ],
  };
}
function newCompareCard(hass) {
  const card = new Card();
  card.setConfig({ type: 'custom:evn-vietnam-energy-card', entity: 'sensor.cur' });
  card.hass = hass;
  return card;
}
function hasOwnInnerHtml(node) {
  return Object.prototype.hasOwnProperty.call(node, 'innerHTML') || node.children.some(hasOwnInnerHtml);
}

(async () => {
  // 7a. Pure comparison helper
  const probe = new Card();
  const julyAug = [
    { date: '2026-07-20', value: 10 }, { date: '2026-07-21', value: 14 }, { date: '2026-08-20', value: 12.5 },
  ];
  let cmp = probe._compareDay(julyAug, '2026-08-20');
  assert.equal(cmp.day, 12.5);
  assert.equal(cmp.lastMonthDay, 10);
  assert.equal(cmp.avgPrevMonth, 12, 'previous-month average is the sum over the days that have data');

  cmp = probe._compareDay([{ date: '2026-07-21', value: 14 }, { date: '2026-08-20', value: 12.5 }], '2026-08-20');
  assert.equal(cmp.lastMonthDay, null, 'a missing day in the previous month is unknown, never 0');
  assert.equal(cmp.avgPrevMonth, 14);

  cmp = probe._compareDay([{ date: '2026-02-28', value: 5 }, { date: '2026-03-31', value: 8 }], '2026-03-31');
  assert.equal(cmp.day, 8);
  assert.equal(cmp.lastMonthDay, null, 'the 31st has no counterpart in February');
  assert.equal(cmp.avgPrevMonth, 5);

  cmp = probe._compareDay([{ date: '2025-12-15', value: 7 }, { date: '2026-01-15', value: 9 }], '2026-01-15');
  assert.equal(cmp.lastMonthDay, 7, 'January compares with December of the previous year');

  cmp = probe._compareDay([], '2026-08-20');
  assert.deepEqual([cmp.day, cmp.lastMonthDay, cmp.avgPrevMonth], [null, null, null]);

  cmp = probe._compareDay([{ date: '2026-07-01', value: 0 }, { date: '2026-07-02', value: 10 }, { date: '2026-08-20', value: 1 }], '2026-08-20');
  assert.equal(cmp.avgPrevMonth, 5, 'a recorded zero is data and counts as a day');
  cmp = probe._compareDay([{ date: '2026-07-01', value: null }, { date: '2026-07-02', value: NaN }, { date: '2026-08-20', value: 1 }], '2026-08-20');
  assert.equal(cmp.avgPrevMonth, null, 'unusable values are not data');

  // 7b. Rendering with a fake recorder websocket
  const calls = [];
  const callWS = async (message) => { calls.push(message); return augustStats(); };
  const tileCard = newCompareCard(compareHass(callWS));
  await flush();
  assert.equal(calls.length, 1, 'one request per view and month pair');
  assert.equal(calls[0].type, 'recorder/statistics_during_period');
  assert.equal(calls[0].period, 'day');
  assert.equal(JSON.stringify(calls[0].types), JSON.stringify(['change']));
  assert.equal(JSON.stringify(calls[0].statistic_ids), JSON.stringify([ENERGY_ID, COST_ID]));
  assert.equal(calls[0].start_time, '2026-06-30T17:00:00.000Z', 'window starts at local midnight of the 1st of the previous month (+07)');
  assert.equal(calls[0].end_time, '2026-08-31T17:00:00.000Z', 'window ends at local midnight of the 1st of the next month (+07)');

  let shown = texts(tileCard);
  const joined = shown.join(' | ');
  assert.ok(shown.includes('So sánh ngày 20/08'), 'the tile names the selected day (latest day with data)');
  assert.ok(shown.includes('12,5 kWh'), 'selected day kWh');
  assert.ok(shown.includes('10 kWh'), 'same day of the previous month and its average');
  assert.ok(shown.includes('+2,5 kWh (+25%)'), `delta against the same day last month: ${joined}`);
  assert.ok(shown.includes('30.000 ₫'), 'cost of the selected day');
  assert.ok(shown.includes('+5.000 ₫ (+20%)'), 'cost delta');
  assert.ok(!shown.includes('Chưa có lịch sử'));
  assert.ok(!hasOwnInnerHtml(tileCard.shadowRoot), 'the tile must only use textContent');

  // The tile sits between the summary grid and the chart.
  const content = findNode(tileCard.shadowRoot, (n) => n.className === 'card-content');
  const order = content.children.map((c) => c.className);
  assert.ok(order.indexOf('metrics-grid') < order.indexOf('compare-tile') && order.indexOf('compare-tile') < order.indexOf('chart-container'),
    `tile must sit below the summary grid and above the chart, got ${order.join(',')}`);

  // Re-rendering, even from a new hass object with the same data, asks nothing more.
  tileCard.render();
  tileCard.hass = compareHass(callWS);
  await flush();
  assert.equal(calls.length, 1, 'cached until the day or the entity update changes');

  // Clicking a bar selects that day; the same month pair is served from the cache.
  const bar18 = findAllNodes(tileCard.shadowRoot, isChartBar).find((b) => (b.attributes['aria-label'] || '').startsWith('2026-08-18'));
  assert.ok(bar18, 'the chart has a bar for the 18th');
  bar18.dispatchEvent({ type: 'click' });
  await flush();
  shown = texts(tileCard);
  assert.ok(shown.includes('So sánh ngày 18/08'), 'clicking a bar selects its day');
  assert.ok(shown.includes('11 kWh'), 'the 18th has 11 kWh in the statistics');
  assert.equal(calls.length, 1, 'selecting another day of the same month needs no request');
  assert.ok(
    findAllNodes(tileCard.shadowRoot, (n) => n.tagName === 'rect' && String(n.className).includes('bar-selected')).length === 1,
    'exactly the selected bar is highlighted',
  );

  // A new entity update (last_updated) refetches.
  tileCard.hass = compareHass(callWS, {}, '2026-08-21T03:30:00+00:00');
  await flush();
  assert.equal(calls.length, 2, 'a changed last_updated refetches the statistics');

  // 7c. Missing points are dashes, never zero
  const gapCalls = [];
  const gapCard = newCompareCard(compareHass(async (m) => {
    gapCalls.push(m);
    const stats = augustStats();
    stats[ENERGY_ID] = stats[ENERGY_ID].filter((r) => r.start !== statRow('2026-07-20', 0).start);
    delete stats[COST_ID];
    return stats;
  }, { cost_statistics_id: undefined }));
  await flush();
  shown = texts(gapCard);
  assert.ok(shown.includes('—'), 'a missing day last month shows a dash');
  assert.ok(!shown.includes('0 kWh') && !shown.includes('0,0 kWh'), 'a missing point must never render as 0');
  assert.ok(shown.includes('12,5 kWh'), 'the selected day is still shown');
  assert.equal(JSON.stringify(gapCalls[0].statistic_ids), JSON.stringify([ENERGY_ID]), 'no cost id attribute, no cost request');

  // The 31st against a February with 28 days
  const marCard = newCompareCard(compareHass(async () => ({
    [ENERGY_ID]: [statRow('2026-02-28', 5), statRow('2026-03-31', 8)],
  }), { cost_statistics_id: undefined, daily_history: [{ date: '2026-03-31', consumption: 8 }] }));
  await flush();
  shown = texts(marCard);
  assert.ok(shown.includes('So sánh ngày 31/03'));
  assert.ok(shown.includes('8 kWh') && shown.includes('5 kWh'));
  assert.ok(shown.filter((t) => t === '—').length >= 2, 'no same-day counterpart: value and delta are dashes');

  // Default selection: latest day with data, not a trailing zero
  const zeroCard = newCompareCard(compareHass(callWS, {
    daily_history: [{ date: '2026-08-19', consumption: 11 }, { date: '2026-08-20', consumption: 12.5 }, { date: '2026-08-21', consumption: 0 }],
  }));
  await flush();
  assert.ok(texts(zeroCard).includes('So sánh ngày 20/08'), 'an unreported zero day is not the default selection');

  // 7d. Unavailable statistics hide the tile behind a muted note
  const failCalls = [];
  const failCard = newCompareCard(compareHass(async (m) => { failCalls.push(m); throw new Error('no recorder'); }));
  await flush();
  shown = texts(failCard);
  assert.ok(shown.includes('Chưa có lịch sử'), 'a failed request shows the muted note');
  assert.ok(!shown.some((t) => t.startsWith('So sánh ngày')), 'and hides the tile');
  failCard.render();
  await flush();
  assert.equal(failCalls.length, 1, 'a failure is not retried on every render');

  const emptyCard = newCompareCard(compareHass(async () => ({})));
  await flush();
  assert.ok(texts(emptyCard).includes('Chưa có lịch sử'), 'statistics without any row show the muted note');

  let asked = 0;
  const noIdCard = newCompareCard(compareHass(async () => { asked += 1; return {}; }, { statistics_id: undefined }));
  await flush();
  assert.equal(asked, 0, 'an entity without statistics_id asks nothing');
  assert.ok(texts(noIdCard).includes('Chưa có lịch sử'));

  const noWsHass = compareHass(undefined);
  assert.doesNotThrow(() => newCompareCard(noWsHass), 'a hass without callWS must not break the card');
  await flush();

  // Without a configured time zone the browser's own calendar is used; it must still ask.
  const plainCalls = [];
  const plainHass = compareHass(async (m) => { plainCalls.push(m); return augustStats(); });
  delete plainHass.config;
  newCompareCard(plainHass);
  await flush();
  assert.equal(plainCalls.length, 1);
  assert.ok(Number.isFinite(Date.parse(plainCalls[0].start_time)) && Number.isFinite(Date.parse(plainCalls[0].end_time)));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
