#!/usr/bin/env node
// The card's tab layouts (config.mode), the day-1 rule and the payment pill. Every code and figure is synthetic.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { Card, clock, findNode, collectTextContents } = require('./card-test-harness.js');

function findAll(node, predicate) {
  const matches = [];
  if (predicate(node)) matches.push(node);
  node.children.forEach((child) => matches.push(...findAll(child, predicate)));
  return matches;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const classes = (node) => String(node.className || '').split(/\s+/);
const texts = (card) => collectTextContents(card.shadowRoot);
const joined = (card) => texts(card).join(' | ');
const tileOf = (card, labelStart) => findNode(
  card.shadowRoot,
  (n) => classes(n).includes('metric-card') && n.children[0] && n.children[0].textContent.startsWith(labelStart),
);
const tileTexts = (card, labelStart) => {
  const tile = tileOf(card, labelStart);
  assert.ok(tile, `the "${labelStart}" tile must render`);
  return tile.children.map((c) => c.textContent);
};
const deepText = (node) => collectTextContents(node).join('');
const rowsOf = (card) => findAll(card.shadowRoot, (n) => n.tagName === 'tr').slice(1).map((tr) => tr.children.map(deepText));
const tileValues = (card) => findAll(card.shadowRoot, (n) => classes(n).includes('metric-value')).map((n) => n.textContent);
const isBareZero = (text) => /^0( kWh| ₫)?$/.test(String(text).trim());
const headersOf = (card) => findAll(card.shadowRoot, (n) => n.tagName === 'th').map((n) => n.textContent);

const PAD = (n) => String(n).padStart(2, '0');
const CODE_A = 'PB00000000001';
const CODE_B = 'PB00000000002';
const CODE_C = 'PB00000000003';

function days(first, last, kwh) {
  const rows = [];
  for (let d = Date.parse(`${first}T00:00:00Z`); d <= Date.parse(`${last}T00:00:00Z`); d += 86400000) {
    const date = new Date(d);
    rows.push({ date: `${date.getUTCFullYear()}-${PAD(date.getUTCMonth() + 1)}-${PAD(date.getUTCDate())}`, consumption: kwh, kwh });
  }
  return rows;
}

function bill(over = {}) {
  return {
    period: 'Tháng 9/2026', year: 2026, month: 9, ky: 1, KY: 1, THANG: 9, NAM: 2026,
    total_kwh: 100, total_amount: 250000, payment_status: 'paid', is_paid: true, payment_checked: true,
    paid_on: '2026-10-01', due_date: '', amount_owed: null,
    period_start: '2026-09-01', period_end: '2026-09-30',
    collected_kwh: 100.4, diff_kwh: 0.4, missing_days: 0, reconcile_status: 'match', paired_with: null,
    index_start: 1200, index_end: 1300,
    ...over,
  };
}

function codeState(code, over = {}) {
  return {
    state: '120.5',
    last_updated: '2026-10-01T02:00:00+00:00',
    attributes: {
      customer_code: code, customer_alias: '', daily_history: days('2026-09-02', '2026-09-30', 3),
      monthly_history: [bill()], latest_reading: 1300.5, latest_reading_date: '30/09/2026',
      unpaid_count: 0, unpaid_amount: 0, next_due_date: null, unpaid_fresh: true,
      projection: null, next_planned_outage: null,
      statistics_id: `evn_vietnam:${code.toLowerCase()}_daily_energy`, cost_statistics_id: `evn_vietnam:${code.toLowerCase()}_daily_cost`,
      ...over,
    },
  };
}

function aggregateState(over = {}) {
  return {
    state: '300', last_updated: '2026-10-01T02:00:00+00:00',
    attributes: {
      customer_code: '__aggregate__', selected_customer_codes: [CODE_A, CODE_B], successful_customer_codes: [CODE_A, CODE_B],
      daily_history: days('2026-09-02', '2026-09-30', 6), monthly_history: [bill()],
      statistics_id: 'evn_vietnam:total_daily_energy', cost_statistics_id: 'evn_vietnam:total_daily_cost',
      ...over,
    },
  };
}

function viewsFor(...entries) {
  return [{ id: 'aggregate', label: 'Tổng', entity: 'sensor.total' }, ...entries.map(([id, label]) => ({
    id, label, entity: `sensor.${id}`, cost_entity: `sensor.${id}_cost`,
  }))];
}

function mount(config, states, extra = {}) {
  const card = new Card();
  card.setConfig({ type: 'custom:evn-vietnam-energy-card', entity: 'sensor.total', ...config });
  card.hass = { states, config: { time_zone: 'Asia/Ho_Chi_Minh' }, ...extra };
  return card;
}

(async () => {
  clock.ms = Date.parse('2026-10-01T03:00:00Z'); // 10:00 on 1 October, Asia/Ho_Chi_Minh

  // ------------------------------------------------------------ payment pill, default layout
  {
    const rows = [
      bill({ period: 'Tháng 9/2026', month: 9, payment_status: 'paid', paid_on: '2026-10-01' }),
      bill({ period: 'Tháng 8/2026', month: 8, payment_status: 'unpaid', is_paid: false, due_date: '2026-10-15' }),
      bill({ period: 'Tháng 7/2026', month: 7, payment_status: 'unknown', is_paid: null }),
      bill({ period: 'Tháng 6/2026', month: 6, payment_status: 'unpaid', is_paid: false, due_date: '2026-10-20', payment_checked: false }),
      { period: 'Tháng 5/2026', total_kwh: 80, total_amount: 200000 },
    ];
    const card = mount({}, { 'sensor.total': { ...aggregateState(), attributes: { ...aggregateState().attributes, monthly_history: rows } } });
    const pills = findAll(card.shadowRoot, (n) => classes(n).includes('pill-paid') || classes(n).includes('pill-unpaid') || classes(n).includes('pill-unknown'))
      .map((n) => n.textContent);
    assert.deepEqual(pills, [
      'Đã thanh toán', 'Chưa thanh toán · hạn 15/10', 'Không rõ', 'Chưa thanh toán · hạn 20/10 · chưa kiểm lại', 'Không rõ',
    ], 'an unknown payment state is never shown as paid, and a row without any state is unknown too');
  }

  // ------------------------------------------------------------ default layout, day 1 of a month
  {
    const card = mount({}, { 'sensor.total': { ...aggregateState({ daily_history: days('2026-09-02', '2026-09-30', 6).map((r) => ({ ...r, customer_codes: [CODE_A, CODE_B] })) }), state: '0' } });
    const labels = findAll(card.shadowRoot, (n) => classes(n).includes('metric-label')).map((n) => n.textContent);
    assert.deepEqual(labels.slice(0, 4), ['Ngày mới nhất (30/09)', 'Hôm qua', 'Tháng này', 'Chi phí ước tính']);
    assert.deepEqual(tileTexts(card, 'Ngày mới nhất').slice(0, 2), ['Ngày mới nhất (30/09)', '6 kWh']);
    assert.deepEqual(tileTexts(card, 'Tháng này').slice(1, 2), ['Chưa có số']);
    assert.ok(joined(card).includes('EVN đăng trễ ~1 ngày'));
    assert.ok(!tileValues(card).some(isBareZero), 'no tile shows a fabricated zero');
  }
  {
    const partialDays = days('2026-09-02', '2026-09-29', 6).map((r) => ({ ...r, customer_codes: [CODE_A, CODE_B] }))
      .concat([{ date: '2026-09-30', consumption: 3, kwh: 3, customer_codes: [CODE_A] }]);
    const card = mount({}, { 'sensor.total': aggregateState({ daily_history: partialDays }) });
    assert.deepEqual(tileTexts(card, 'Ngày mới nhất'), ['Ngày mới nhất (30/09)', '3 kWh', 'thiếu 1/2 mã, EVN chưa đăng']);
  }
  {
    // a positive month figure is never hidden by the rule
    const card = mount({}, { 'sensor.total': { ...aggregateState({ daily_history: [] }), state: '75' } });
    assert.deepEqual(tileTexts(card, 'Tháng này').slice(1, 2), ['75 kWh']);
  }

  // ------------------------------------------------------------ the default layout keeps its structure
  {
    clock.ms = Date.parse('2026-08-30T05:00:00Z');
    const daily = Array.from({ length: 29 }, (_, i) => ({ date: `2026-08-${PAD(i + 1)}`, consumption: 3 + (i % 4), kwh: 3 + (i % 4) }));
    const bills = [
      { period: 'Tháng 7/2026', total_kwh: 100, total_amount: 250000, is_paid: true, collected_kwh: 100, diff_kwh: 0, reconcile_status: 'match', year: 2026, month: 7 },
      { period: 'Tháng 6/2026', total_kwh: 90, total_amount: 200000, is_paid: false },
    ];
    const card = new Card();
    card.setConfig({
      type: 'x', entity: 'sensor.total', cost_entity: 'sensor.cost',
      customer_views: [{ id: 'aggregate', label: 'Tổng', entity: 'sensor.total', cost_entity: 'sensor.cost' }, { id: 'a', label: 'Nhà A', entity: 'sensor.a' }],
    });
    card.hass = {
      states: {
        'sensor.total': { state: '120', last_updated: 'x', attributes: { customer_code: '__aggregate__', selected_customer_codes: ['a', 'b'], daily_history: daily, monthly_history: bills } },
        'sensor.cost': { state: '300000', attributes: { customer_code: '__aggregate__', selected_customer_codes: ['a', 'b'], bills } },
        'sensor.a': { state: '60', attributes: { customer_code: 'a', daily_history: daily } },
      },
      config: { time_zone: 'Asia/Ho_Chi_Minh' },
    };
    const signature = [];
    const walk = (node, depth) => {
      if (node.tagName === 'style') return;
      signature.push(`${'  '.repeat(depth)}${node.tagName}${node.className ? `.${String(node.className).split(/\s+/).join('.')}` : ''}`);
      node.children.forEach((child) => walk(child, depth + 1));
    };
    card.shadowRoot.children.forEach((child) => walk(child, 0));
    const expected = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'card-default-layout.json'), 'utf8'));
    assert.deepEqual(signature, expected, 'without a mode, mid-month, the card renders the structure public users already have');
    clock.ms = Date.parse('2026-10-01T03:00:00Z');
  }

  // ------------------------------------------------------------ unknown mode = default layout
  {
    const states = { 'sensor.total': aggregateState() };
    const plain = mount({}, states);
    const weird = mount({ mode: 'nonsense' }, states);
    assert.deepEqual(texts(weird), texts(plain), 'an unknown mode renders exactly the default layout');
    assert.ok(findNode(plain.shadowRoot, (n) => n.tagName === 'rect'), 'the default layout still draws the chart');
  }

  // ------------------------------------------------------------ usage, rolling, day 1
  {
    const aggDays = days('2026-09-02', '2026-09-30', 6).map((r) => ({ ...r, customer_codes: [CODE_A, CODE_B] }));
    const states = {
      'sensor.total': { ...aggregateState({ daily_history: aggDays }), state: '0' },
      'sensor.a': { ...codeState(CODE_A, { daily_history: days('2026-09-02', '2026-09-30', 3) }), state: '0' },
      'sensor.b': { ...codeState(CODE_B, { daily_history: days('2026-09-02', '2026-09-29', 3) }), state: '0' },
      'sensor.a_cost': { state: '0', attributes: { customer_code: CODE_A, bills: [] } },
      'sensor.b_cost': { state: '0', attributes: { customer_code: CODE_B, bills: [] } },
    };
    const card = mount({ mode: 'usage', customer_views: viewsFor(['a', 'Nhà A'], ['b', 'Nhà B']) }, states);
    assert.deepEqual(tileTexts(card, 'Ngày mới nhất'), ['Ngày mới nhất (30/09)', '3 kWh', 'thiếu 1/2 mã, EVN chưa đăng']);
    assert.deepEqual(tileTexts(card, 'Ngày trước đó'), ['Ngày trước đó (29/09)', '6 kWh']);
    assert.deepEqual(tileTexts(card, 'Tháng 10'), ['Tháng 10', 'Chưa có số', 'EVN đăng trễ ~1 ngày']);
    assert.deepEqual(tileTexts(card, 'Chi phí ước tính T10'), ['Chi phí ước tính T10', 'Chưa có số', 'EVN đăng trễ ~1 ngày']);
    assert.ok(!tileValues(card).some(isBareZero), 'day 1 never shows a zero');
    assert.ok(findNode(card.shadowRoot, (n) => n.tagName === 'rect'), 'the 0.4.0 chart stays');
  }

  // ------------------------------------------------------------ usage, past month on 01/01 (December picked)
  {
    clock.ms = Date.parse('2026-01-01T03:00:00Z');
    const calls = [];
    const decRows = (skip) => days('2025-12-01', '2025-12-31', 4)
      .filter((r) => !skip.includes(r.date))
      .map((r) => ({ start: Date.parse(`${r.date}T00:00:00+07:00`), change: r.consumption }));
    const decCost = days('2025-12-01', '2025-12-31', 8000)
      .map((r) => ({ start: Date.parse(`${r.date}T00:00:00+07:00`), change: r.consumption }));
    const callWS = async (message) => {
      calls.push(message);
      const id = message.statistic_ids[0];
      return id.endsWith('_cost') ? { [id]: decCost } : { [id]: decRows(['2025-12-10']) };
    };
    const states = {
      'sensor.total': { ...aggregateState({ daily_history: days('2025-12-02', '2025-12-31', 6), monthly_history: [] }), state: '0' },
      'sensor.a': { ...codeState(CODE_A, { daily_history: days('2025-12-02', '2025-12-31', 3), monthly_history: [bill({ year: 2025, month: 12, NAM: 2025, THANG: 12, total_kwh: 90, total_amount: 200000 })] }), state: '0' },
      'sensor.b': { ...codeState(CODE_B, { daily_history: days('2025-12-02', '2025-12-31', 3), monthly_history: [] }), state: '0' },
    };
    const card = mount({ mode: 'usage', customer_views: viewsFor(['a', 'Nhà A'], ['b', 'Nhà B']) }, states, { callWS });
    const select = findNode(card.shadowRoot, (n) => classes(n).includes('month-selector'));
    assert.ok(select, 'the 0.4.0 picker is reused');
    assert.deepEqual(tileTexts(card, 'Tháng 01'), ['Tháng 01', 'Chưa có số', 'EVN đăng trễ ~1 ngày'], 'rolling on 01/01: no fabricated zero');
    select.value = '2025-12';
    select.dispatchEvent({ type: 'change', target: { value: '2025-12' } });
    await flush();
    await flush();
    assert.deepEqual(tileTexts(card, 'Tổng T12'), ['Tổng T12', '120 kWh', '30/31 ngày']);
    assert.deepEqual(tileTexts(card, 'Tiền điện T12'), ['Tiền điện T12', '200.000 ₫', 'theo hoá đơn kỳ 12 · 90 kWh · thiếu 1/2 hoá đơn']);
    assert.deepEqual(tileTexts(card, 'TB/ngày'), ['TB/ngày', '4 kWh']);
    assert.deepEqual(tileTexts(card, 'Ngày cao nhất'), ['Ngày cao nhất', '4 kWh', '01/12']);
    assert.ok(!tileValues(card).some(isBareZero));
    assert.equal(calls.filter((m) => m.statistic_ids[0].endsWith('_cost')).length, 0, 'the cost series is asked for only when no bill exists');

    // no bill that month: an approximate amount from the cost statistics
    states['sensor.a'].attributes.monthly_history = [];
    const card2 = mount({ mode: 'usage', customer_views: viewsFor(['a', 'Nhà A'], ['b', 'Nhà B']) }, states, { callWS });
    const select2 = findNode(card2.shadowRoot, (n) => classes(n).includes('month-selector'));
    select2.value = '2025-12';
    select2.dispatchEvent({ type: 'change', target: { value: '2025-12' } });
    await flush();
    await flush();
    await flush();
    assert.deepEqual(tileTexts(card2, 'Tiền điện T12'), ['Tiền điện T12', '≈ 248.000 ₫', 'chưa có hoá đơn']);
    clock.ms = Date.parse('2026-10-01T03:00:00Z');
  }

  // ------------------------------------------------------------ bills mode
  {
    const paid = bill({ payment_status: 'paid', paid_on: '2026-10-01' });
    const states = {
      'sensor.total': aggregateState({ partial_errors: { [CODE_B]: 'unpaid_bills' }, is_partial: true }),
      'sensor.a': codeState(CODE_A, { monthly_history: [paid] }),
      'sensor.b': codeState(CODE_B, { monthly_history: [bill({ total_kwh: 80, collected_kwh: 70, diff_kwh: -10, reconcile_status: 'mismatch', payment_status: 'unpaid', is_paid: false, due_date: '2026-10-15', total_amount: 180000 })] }),
      'sensor.c': codeState(CODE_C, { monthly_history: [bill({ payment_status: 'unpaid', is_paid: false, due_date: '2026-10-16', payment_checked: false, total_amount: 100000, total_kwh: 40, collected_kwh: 40, diff_kwh: 0 })] }),
      'sensor.d': codeState('PB00000000004', { monthly_history: [bill({ payment_status: 'unknown', is_paid: null, reconcile_status: 'incomplete', collected_kwh: 60, diff_kwh: -30, missing_days: 10, total_kwh: 90, total_amount: 150000 })] }),
      'sensor.e': codeState('PB00000000005', { monthly_history: [bill({ total_kwh: null, collected_kwh: 50, diff_kwh: null, reconcile_status: 'no_kwh', total_amount: 90000 })] }),
      'sensor.f': codeState('PB00000000006', { monthly_history: [bill({ KY: null, ky: null, reconcile_status: 'no_period', collected_kwh: null, diff_kwh: null, total_amount: 70000, total_kwh: null })] }),
      'sensor.g': codeState('PB00000000007', { monthly_history: [bill({ month: 8, THANG: 8, period: 'Tháng 8/2026' })] }),
      'sensor.h': { state: 'unavailable', attributes: {} },
    };
    const names = [['a', 'Nhà A'], ['b', 'Nhà B'], ['c', 'Nhà C'], ['d', 'Nhà D'], ['e', 'Nhà E'], ['f', 'Nhà F'], ['g', 'Nhà G'], ['h', 'Nhà H']];
    const card = mount({ mode: 'bills', customer_views: viewsFor(...names) }, states);

    const option = findAll(card.shadowRoot, (n) => n.tagName === 'option').map((n) => n.textContent);
    assert.deepEqual(option, ['Tháng 09/2026', 'Tháng 08/2026'], 'the periods present in the bill lists, newest first');

    // complete = Khớp/Lệch/Lệch ranh giới with finite figures and no fetch error: only A and C
    assert.deepEqual(tileTexts(card, 'Tổng tiền'), ['Tổng tiền', '840.000 ₫', '2 mã chưa thanh toán · 2 mã chưa có hoá đơn']);
    assert.deepEqual(tileTexts(card, 'kWh hoá đơn'), ['kWh hoá đơn', '140 kWh', '2/8 mã đủ dữ liệu']);
    assert.deepEqual(tileTexts(card, 'kWh thu thập'), ['kWh thu thập', '140,4 kWh', '2/8 mã đủ dữ liệu']);
    assert.deepEqual(tileTexts(card, 'Chênh lệch'), ['Chênh lệch', '+0,4 kWh', '2/8 mã đủ dữ liệu']);
    assert.deepEqual(headersOf(card), ['Khách hàng', 'Kỳ', 'Hoá đơn', 'Thu thập', 'Chênh lệch', 'Kết quả', 'Số tiền', 'Thanh toán']);

    const excluded = findNode(card.shadowRoot, (n) => classes(n).includes('excluded-note'));
    assert.ok(excluded, 'codes left out of the totals are named');
    const note = excluded.textContent;
    ['Nhà D (chưa đủ ngày (20/30))', 'Nhà E (chưa có kWh hoá đơn)', 'Nhà F (hoá đơn không có kỳ rõ ràng)', 'Nhà G (chưa có hoá đơn kỳ này)', 'Nhà H (không kiểm được)']
      .forEach((part) => assert.ok(note.includes(part), `${part} must be named, got: ${note}`));
    assert.ok(!note.includes('Nhà A (') && !note.includes('Nhà C ('), 'complete codes are not listed');
    assert.ok(note.includes('Nhà B (không kiểm được)') === true, 'a code with a fetch error in the total is named even if its bill is complete');

    const rows = rowsOf(card);
    assert.equal(rows.length, 9, 'one row per code and a total row');
    const byName = (name) => rows.find((r) => r[0].startsWith(name));
    assert.deepEqual(byName('Nhà A').slice(1), ['01/09–30/09', '100 kWh', '100,4 kWh', '+0,4 kWh', 'Khớp', '250.000 ₫', 'Đã TT · 01/10']);
    assert.equal(byName('Nhà B')[5], 'Không kiểm được', 'a partial error is named and wins over the reconciliation');
    assert.equal(byName('Nhà B')[7], 'Chưa TT · hạn 15/10', 'the payment state is independent of completeness');
    assert.equal(byName('Nhà C')[7], 'Chưa TT · hạn 16/10 · chưa kiểm lại');
    assert.equal(byName('Nhà D')[5], 'Chưa đủ ngày (20/30)');
    assert.equal(byName('Nhà D')[7], 'Không rõ');
    assert.equal(byName('Nhà E')[5], 'Chưa có kWh hoá đơn');
    assert.equal(byName('Nhà F')[5], 'Hoá đơn không có kỳ rõ ràng');
    assert.equal(byName('Nhà G')[7], '—', 'no bill for the period');
    assert.equal(byName('Nhà H')[5], 'Không kiểm được');
    assert.deepEqual(rows[rows.length - 1].slice(0, 1), ['Tổng']);

    const cells = findAll(card.shadowRoot, (n) => n.tagName === 'td');
    assert.ok(cells.every((td) => td.attributes['data-label']), 'every cell carries its column name, so a narrow row can stack as label/value');
    assert.ok(findAll(card.shadowRoot, (n) => n.tagName === 'th').every((th) => th.attributes.scope === 'col'));
    assert.ok(findAll(card.shadowRoot, (n) => n.tagName === 'details').length >= 1, 'each billed code has an expandable detail line');
    const pillTexts = findAll(card.shadowRoot, (n) => classes(n).some((c) => c === 'pill' || c === 'pill-paid' || c === 'pill-unpaid')).map((n) => n.textContent);
    assert.ok(pillTexts.every((t) => t.length > 0), 'pills carry text, not only colour');
    const fullText = joined(card);
    [CODE_A, CODE_B, CODE_C].forEach((code) => assert.ok(!fullText.includes(code), 'the tabs never print a customer code'));

    // choosing another period
    const select = findNode(card.shadowRoot, (n) => classes(n).includes('period-selector'));
    select.value = '2026-08';
    select.dispatchEvent({ type: 'change', target: { value: '2026-08' } });
    assert.deepEqual(tileTexts(card, 'Tổng tiền'), ['Tổng tiền', '250.000 ₫', '0 mã chưa thanh toán · 7 mã chưa có hoá đơn']);
  }

  // ------------------------------------------------------------ overview mode
  {
    const zero = (entity) => ({ ...entity, state: '0' });
    const states = {
      'sensor.total': aggregateState(),
      'sensor.a': zero(codeState(CODE_A, {
        unpaid_count: 1, unpaid_amount: 250000, next_due_date: '2026-10-15',
        monthly_history: [bill({ payment_status: 'unpaid', is_paid: false, due_date: '2026-10-15' })],
        projection: { projected_amount: 200000, tier: 1, kwh_to_next_tier: 17, next_tier_price: 2050 },
        next_planned_outage: '2026-10-05T08:00:00+07:00', outage_end: '2026-10-05T11:30:00+07:00', upcoming_outage_count: 1,
      })),
      'sensor.b': zero(codeState(CODE_B, {
        unpaid_count: 2, unpaid_amount: 180000, next_due_date: '2026-10-09',
        monthly_history: [bill({ reconcile_status: 'mismatch', payment_status: 'unpaid', is_paid: false, due_date: '2026-10-09' })],
        projection: { projected_amount: 100000, tier: 2, kwh_to_next_tier: 4.5 },
        next_planned_outage: '2026-10-20T08:00:00+07:00', outage_end: '2026-10-20T09:00:00+07:00', upcoming_outage_count: 1,
      })),
    };
    const views = viewsFor(['a', 'Nhà A'], ['b', 'Nhà B']);
    const card = mount({ mode: 'overview', bills_path: '/evn-energy/hoa-don', customer_views: views }, states);

    const banners = findAll(card.shadowRoot, (n) => classes(n).includes('mode-banner')).map((n) => n.children[0].textContent);
    assert.deepEqual(banners, [
      '3 hoá đơn chưa thanh toán · còn nợ 430.000 ₫ · hạn gần nhất 09/10',
      'Dự kiến ngừng cấp điện: Nhà A · 05/10 08:00–11:30',
    ]);
    const link = findNode(card.shadowRoot, (n) => n.tagName === 'a');
    assert.equal(link.attributes.href, '/evn-energy/hoa-don');

    assert.deepEqual(tileTexts(card, 'Ngày mới nhất'), ['Ngày mới nhất (30/09)', '6 kWh']);
    assert.deepEqual(tileTexts(card, 'Tháng hiện tại'), ['Tháng hiện tại', 'Chưa có số', 'EVN đăng trễ ~1 ngày']);
    assert.deepEqual(tileTexts(card, 'Dự kiến kỳ này'), ['Dự kiến kỳ này', '≈ 300.000 ₫', 'dự kiến, không phải hoá đơn · Nhà B: còn 4,5 kWh nữa sang bậc 3']);
    assert.deepEqual(tileTexts(card, 'Đối chiếu kỳ gần nhất'), ['Đối chiếu kỳ gần nhất', '1/2 khớp']);

    assert.deepEqual(headersOf(card), ['Khách hàng', 'Ngày mới nhất', 'Dữ liệu đến', 'Hoá đơn gần nhất', 'Thanh toán', 'Đối chiếu', 'Ngừng cấp điện kế tiếp']);
    const rows = rowsOf(card);
    assert.deepEqual(rows[0], ['Nhà A', '3 kWh', '30/09', '250.000 ₫', 'Chưa TT · hạn 15/10', 'Khớp', '05/10 08:00–11:30']);
    assert.equal(rows[1][5], 'Lệch');

    // no link unless the path is an in-app path; no banners when nothing is unpaid or near
    const unsafe = mount({ mode: 'overview', bills_path: 'javascript:alert(1)', customer_views: views }, states);
    assert.equal(findNode(unsafe.shadowRoot, (n) => n.tagName === 'a'), null, 'a scheme is never linked');
    const quiet = mount({ mode: 'overview', customer_views: views }, {
      ...states,
      'sensor.a': codeState(CODE_A, { next_planned_outage: '2026-10-20T08:00:00+07:00', outage_end: '2026-10-20T09:00:00+07:00' }),
      'sensor.b': codeState(CODE_B),
    });
    assert.equal(findAll(quiet.shadowRoot, (n) => classes(n).includes('mode-banner')).length, 0);
    assert.deepEqual(tileTexts(quiet, 'Dự kiến kỳ này').slice(0, 2), ['Dự kiến kỳ này', '—']);
  }

  // ------------------------------------------------------------ meter mode
  {
    const states = {
      'sensor.total': aggregateState(),
      'sensor.a': codeState(CODE_A),
      'sensor.b': codeState(CODE_B, { monthly_history: [bill({ index_start: null, index_end: null })], latest_reading: null, latest_reading_date: '' }),
    };
    const card = mount({ mode: 'meter', customer_views: viewsFor(['a', 'Nhà A'], ['b', 'Nhà B']) }, states);
    assert.deepEqual(headersOf(card), ['Khách hàng', 'Chỉ số đầu kỳ', 'Chỉ số cuối kỳ', 'Ngày chốt', 'Sản lượng kỳ', 'Chỉ số mới nhất']);
    assert.deepEqual(rowsOf(card), [
      ['Nhà A', '1.200', '1.300', '30/09', '100 kWh', '1.300,5 (30/09)'],
      ['Nhà B', '—', '—', '30/09', '100 kWh', '—'],
    ]);
  }

  // ------------------------------------------------------------ labels never print a code
  {
    const states = {
      'sensor.total': aggregateState(),
      'sensor.a': codeState(CODE_A, { customer_alias: 'Kho' }),
      'sensor.b': codeState(CODE_B),
    };
    const card = mount({ mode: 'meter', customer_views: viewsFor(['a', 'Mã KH 1'], ['b', 'Mã KH 2']) }, states);
    assert.deepEqual(rowsOf(card).map((r) => r[0]), ['Kho', 'customer_2']);
  }

  // ------------------------------------------------------------ modes do not break on missing data
  {
    const card = mount({ mode: 'bills', customer_views: viewsFor(['a', 'Nhà A']) }, { 'sensor.total': aggregateState(), 'sensor.a': { state: 'unknown', attributes: {} } });
    assert.ok(joined(card).includes('Chưa có thông tin hoá đơn'));
    const noHass = new Card();
    noHass.setConfig({ type: 'custom:evn-vietnam-energy-card', entity: 'sensor.total', mode: 'usage' });
    assert.doesNotThrow(() => { noHass.hass = { states: {} }; });
  }

  console.log('card modes: ok');
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
