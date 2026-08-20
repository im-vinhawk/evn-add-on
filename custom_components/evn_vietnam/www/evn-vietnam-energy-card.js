/**
 * EVN Vietnam Home Assistant Energy Card
 * Custom Lovelace Card for EVN Vietnam HACS Integration
 */

class EvnVietnamEnergyCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = null;
    this._hass = null;
  }

  static get properties() {
    return {
      hass: {},
      config: {},
    };
  }

  setConfig(config) {
    if (!config) {
      throw new Error('Cấu hình card không hợp lệ.');
    }
    if (!config.entity) {
      throw new Error('Bạn cần khai báo "entity" (sensor sản lượng tháng) trong cấu hình card.');
    }
    this._config = {
      title: 'Điện năng EVN',
      ...config,
    };
    if (this._hass) {
      this.render();
    }
  }

  set hass(hass) {
    const oldHass = this._hass;
    this._hass = hass;

    if (!oldHass || this._shouldUpdate(oldHass, hass)) {
      this.render();
    }
  }

  _shouldUpdate(oldHass, newHass) {
    if (!this._config) return true;
    const entities = [
      this._config.entity,
      this._config.cost_entity,
      this._config.today_entity,
      this._config.yesterday_entity,
    ].filter(Boolean);

    return entities.some(
      (entityId) => oldHass.states[entityId] !== newHass.states[entityId]
    );
  }

  getCardSize() {
    return 4;
  }

  // --- Formatting Helpers ---
  _formatNumber(val, decimals = 1) {
    if (val === null || val === undefined || isNaN(Number(val))) return '—';
    const num = Number(val);
    return new Intl.NumberFormat('vi-VN', {
      minimumFractionDigits: 0,
      maximumFractionDigits: decimals,
    }).format(num);
  }

  _formatKwh(val) {
    if (val === null || val === undefined || isNaN(Number(val))) return '—';
    return `${this._formatNumber(val, 1)} kWh`;
  }

  _formatVnd(val) {
    if (val === null || val === undefined || isNaN(Number(val))) return '—';
    const num = Number(val);
    return `${new Intl.NumberFormat('vi-VN').format(num)} ₫`;
  }

  _formatDateLabel(dateStr) {
    if (!dateStr) return '';
    const parts = dateStr.split('-');
    if (parts.length === 3) {
      return `${parts[2]}/${parts[1]}`;
    }
    return dateStr;
  }

  // --- Rendering Entry Point ---
  render() {
    if (!this.shadowRoot || !this._config) return;

    // Clear previous elements
    while (this.shadowRoot.firstChild) {
      this.shadowRoot.removeChild(this.shadowRoot.firstChild);
    }

    // Inject Scoped Styles
    const styleEl = document.createElement('style');
    styleEl.textContent = this._getStyles();
    this.shadowRoot.appendChild(styleEl);

    // Root Container
    const cardEl = document.createElement('ha-card');
    const cardContent = document.createElement('div');
    cardContent.className = 'card-content';

    if (!this._hass) {
      cardEl.appendChild(this._createStateBox('Đang kết nối với Home Assistant...'));
      this.shadowRoot.appendChild(cardEl);
      return;
    }

    const mainEntity = this._hass.states[this._config.entity];
    if (!mainEntity) {
      cardEl.appendChild(
        this._createErrorBox(`Không tìm thấy entity: ${this._config.entity}`)
      );
      this.shadowRoot.appendChild(cardEl);
      return;
    }

    if (mainEntity.state === 'unavailable' || mainEntity.state === 'unknown') {
      cardEl.appendChild(
        this._createStateBox(`Thực thể ${this._config.entity} đang ở trạng thái: ${mainEntity.state}`)
      );
      this.shadowRoot.appendChild(cardEl);
      return;
    }

    // Extract Attributes
    const attrs = mainEntity.attributes || {};
    const customerCode = attrs.customer_code || '—';
    const selectedCodes = attrs.selected_customer_codes || [];
    const isPartial = Boolean(attrs.is_partial);
    const partialErrors = attrs.partial_errors || null;
    const dailyHistory = Array.isArray(attrs.daily_history) ? attrs.daily_history : [];
    
    // Cost Entity
    const costEntity = this._config.cost_entity ? this._hass.states[this._config.cost_entity] : null;
    if (costEntity && !this._sameCustomerScope(attrs, costEntity.attributes || {})) {
      cardContent.appendChild(this._createErrorBox('Entity tiền tạm tính không cùng mã KH hoặc tập mã tổng với entity sản lượng.'));
      cardEl.appendChild(cardContent);
      this.shadowRoot.appendChild(cardEl);
      return;
    }
    const costStateValue = costEntity && !isNaN(Number(costEntity.state)) ? Number(costEntity.state) : null;
    const monthlyHistory = Array.isArray(attrs.monthly_history) && attrs.monthly_history.length > 0
      ? attrs.monthly_history
      : (costEntity && costEntity.attributes && Array.isArray(costEntity.attributes.bills) ? costEntity.attributes.bills : []);

    // 1. Header Section
    cardContent.appendChild(this._renderHeader(customerCode, selectedCodes, isPartial));

    // 2. Partial Warning & Error Banners
    if (isPartial) {
      cardContent.appendChild(this._renderPartialWarning());
    }

    if (partialErrors && Object.keys(partialErrors).length > 0) {
      cardContent.appendChild(this._renderErrorBanner(partialErrors));
    }

    // 3. Summary Grid (4 Dense Summary Values)
    cardContent.appendChild(this._renderSummaryGrid(mainEntity.state, costStateValue, dailyHistory));

    // 4. Daily Energy Chart
    cardContent.appendChild(this._renderChartSection(dailyHistory));

    // 5. Official Bill History Table
    cardContent.appendChild(this._renderBillTableSection(monthlyHistory));

    cardEl.appendChild(cardContent);
    this.shadowRoot.appendChild(cardEl);
  }

  // --- Helper UI Builders ---
  _createStateBox(message) {
    const box = document.createElement('div');
    box.className = 'empty-state';
    box.textContent = message;
    return box;
  }

  _createErrorBox(message) {
    const box = document.createElement('div');
    box.className = 'error-banner';
    box.textContent = message;
    return box;
  }

  _sameCustomerScope(mainAttrs, costAttrs) {
    if (!costAttrs.customer_code || mainAttrs.customer_code !== costAttrs.customer_code) {
      return false;
    }
    const mainCodes = [...(mainAttrs.selected_customer_codes || [])].map(String).sort();
    const costCodes = [...(costAttrs.selected_customer_codes || [])].map(String).sort();
    return mainCodes.length === costCodes.length && mainCodes.every((code, index) => code === costCodes[index]);
  }

  _renderHeader(customerCode, selectedCodes, isPartial) {
    const header = document.createElement('div');
    header.className = 'card-header';

    const titleBox = document.createElement('div');
    titleBox.className = 'title-box';

    const titleEl = document.createElement('div');
    titleEl.className = 'card-title';
    titleEl.textContent = this._config.title || 'Điện năng EVN';
    titleBox.appendChild(titleEl);

    // Customer Code / Aggregate badge
    const badgeEl = document.createElement('span');
    badgeEl.className = 'customer-badge';
    
    if (customerCode === '__aggregate__' || selectedCodes.length > 1) {
      const count = selectedCodes.length || 'nhiều';
      badgeEl.textContent = `Tổng hợp (${count} mã KH)`;
    } else {
      badgeEl.textContent = customerCode !== '—' ? `Mã KH: ${customerCode}` : 'EVN Vietnam';
    }
    titleBox.appendChild(badgeEl);

    header.appendChild(titleBox);

    if (isPartial) {
      const warningPill = document.createElement('span');
      warningPill.className = 'status-warning';
      warningPill.textContent = '⚠️ Một phần';
      header.appendChild(warningPill);
    }

    return header;
  }

  _renderPartialWarning() {
    const warningEl = document.createElement('div');
    warningEl.className = 'warning-banner';
    warningEl.textContent = '⚠️ Dữ liệu tổng hợp chưa đầy đủ. Một số mã khách hàng gặp lỗi kết nối hoặc chưa đồng bộ.';
    return warningEl;
  }

  _renderErrorBanner(partialErrors) {
    const banner = document.createElement('div');
    banner.className = 'error-banner';
    
    const title = document.createElement('strong');
    title.textContent = 'Lỗi kết nối mã KH: ';
    banner.appendChild(title);

    const errStrings = Object.entries(partialErrors)
      .map(([code, err]) => `${code}: ${err}`)
      .join(' | ');

    const errText = document.createTextNode(errStrings);
    banner.appendChild(errText);

    return banner;
  }

  _renderSummaryGrid(currentMonthState, costStateValue, dailyHistory) {
    const grid = document.createElement('div');
    grid.className = 'metrics-grid';

    // 1. Today Value
    let todayVal = null;
    if (this._config.today_entity && this._hass.states[this._config.today_entity]) {
      const st = this._hass.states[this._config.today_entity].state;
      if (st !== 'unavailable' && st !== 'unknown' && !isNaN(Number(st))) {
        todayVal = Number(st);
      }
    } else {
      const now = new Date();
      const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
      const found = dailyHistory.find((item) => item.date === todayStr);
      if (found) {
        todayVal = found.consumption !== undefined ? found.consumption : found.kwh;
      }
    }

    // 2. Yesterday Value
    let yesterdayVal = null;
    if (this._config.yesterday_entity && this._hass.states[this._config.yesterday_entity]) {
      const st = this._hass.states[this._config.yesterday_entity].state;
      if (st !== 'unavailable' && st !== 'unknown' && !isNaN(Number(st))) {
        yesterdayVal = Number(st);
      }
    } else {
      const now = new Date();
      const yest = new Date(now);
      yest.setDate(yest.getDate() - 1);
      const yestStr = `${yest.getFullYear()}-${String(yest.getMonth() + 1).padStart(2, '0')}-${String(yest.getDate()).padStart(2, '0')}`;
      const found = dailyHistory.find((item) => item.date === yestStr);
      if (found) {
        yesterdayVal = found.consumption !== undefined ? found.consumption : found.kwh;
      }
    }

    // 3. Current Month Value
    const monthVal = !isNaN(Number(currentMonthState)) ? Number(currentMonthState) : null;

    // 4. Estimated Cost Value (VND)
    const costVal = costStateValue;

    grid.appendChild(this._createMetricTile('Hôm nay', this._formatKwh(todayVal)));
    grid.appendChild(this._createMetricTile('Hôm qua', this._formatKwh(yesterdayVal)));
    grid.appendChild(this._createMetricTile('Tháng này', this._formatKwh(monthVal), true));
    grid.appendChild(this._createMetricTile('Tạm tính', this._formatVnd(costVal), true));

    return grid;
  }

  _createMetricTile(label, valueText, isAccent = false) {
    const tile = document.createElement('div');
    tile.className = 'metric-card';

    const labelEl = document.createElement('div');
    labelEl.className = 'metric-label';
    labelEl.textContent = label;

    const valueEl = document.createElement('div');
    valueEl.className = `metric-value${isAccent ? ' accent' : ''}`;
    valueEl.textContent = valueText;

    tile.appendChild(labelEl);
    tile.appendChild(valueEl);
    return tile;
  }

  // --- SVG Chart Renderer ---
  _renderChartSection(dailyHistory) {
    const section = document.createElement('div');
    section.className = 'chart-container';

    const header = document.createElement('div');
    header.className = 'chart-header';

    const titleEl = document.createElement('span');
    titleEl.textContent = 'Sản lượng theo ngày (kWh)';

    const tooltipEl = document.createElement('span');
    tooltipEl.className = 'chart-tooltip';
    tooltipEl.textContent = 'Chạm/Rê chuột để xem';

    header.appendChild(titleEl);
    header.appendChild(tooltipEl);
    section.appendChild(header);

    if (!dailyHistory || dailyHistory.length === 0) {
      const emptyMsg = document.createElement('div');
      emptyMsg.className = 'empty-state';
      emptyMsg.textContent = 'Không có dữ liệu sản lượng hàng ngày';
      section.appendChild(emptyMsg);
      return section;
    }

    // Sort history by date ascending if needed
    const sortedData = [...dailyHistory].sort((a, b) => (a.date > b.date ? 1 : -1));

    const svgNS = 'http://www.w3.org/2000/svg';
    const width = 500;
    const height = 150;
    const paddingLeft = 35;
    const paddingRight = 10;
    const paddingTop = 15;
    const paddingBottom = 25;

    const chartW = width - paddingLeft - paddingRight;
    const chartH = height - paddingTop - paddingBottom;

    // Find max value
    const maxVal = Math.max(
      ...sortedData.map((d) => (d.consumption !== undefined ? Number(d.consumption) : Number(d.kwh || 0))),
      1.0
    );
    const yMax = maxVal * 1.15;

    const svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('class', 'chart-svg');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'Biểu đồ sản lượng điện hàng ngày');

    // Horizontal Grid lines (y=0, y=50%, y=100%)
    const gridLevels = [0, yMax / 2, yMax];
    gridLevels.forEach((level) => {
      const yPos = paddingTop + chartH - (level / yMax) * chartH;
      const line = document.createElementNS(svgNS, 'line');
      line.setAttribute('x1', paddingLeft);
      line.setAttribute('x2', width - paddingRight);
      line.setAttribute('y1', yPos);
      line.setAttribute('y2', yPos);
      line.setAttribute('stroke', 'var(--divider-color, rgba(0,0,0,0.08))');
      line.setAttribute('stroke-dasharray', level > 0 && level < yMax ? '3,3' : 'none');
      svg.appendChild(line);

      // Y-axis label
      if (level > 0) {
        const text = document.createElementNS(svgNS, 'text');
        text.setAttribute('x', paddingLeft - 4);
        text.setAttribute('y', yPos + 3);
        text.setAttribute('text-anchor', 'end');
        text.setAttribute('font-size', '9');
        text.setAttribute('fill', 'var(--secondary-text-color, #6b7280)');
        text.textContent = this._formatNumber(level, 0);
        svg.appendChild(text);
      }
    });

    // Bars
    const itemCount = sortedData.length;
    const step = chartW / itemCount;
    const barWidth = Math.max(step * 0.65, 2);

    sortedData.forEach((item, idx) => {
      const val = item.consumption !== undefined ? Number(item.consumption) : Number(item.kwh || 0);
      const barH = Math.max((val / yMax) * chartH, 1);
      const xPos = paddingLeft + idx * step + (step - barWidth) / 2;
      const yPos = paddingTop + chartH - barH;

      const rect = document.createElementNS(svgNS, 'rect');
      rect.setAttribute('class', 'bar');
      rect.setAttribute('x', xPos);
      rect.setAttribute('y', yPos);
      rect.setAttribute('width', barWidth);
      rect.setAttribute('height', barH);
      rect.setAttribute('rx', '2');
      rect.setAttribute('ry', '2');
      rect.setAttribute('tabindex', '0');
      rect.setAttribute('role', 'img');
      rect.setAttribute('aria-label', `${item.date}: ${this._formatKwh(val)}`);

      // Accessible title tooltips
      const title = document.createElementNS(svgNS, 'title');
      title.textContent = `${item.date}: ${this._formatKwh(val)}`;
      rect.appendChild(title);

      // Interaction listeners for inline tooltip text (no innerHTML)
      const updateTooltip = () => {
        tooltipEl.textContent = `${this._formatDateLabel(item.date)}: ${this._formatKwh(val)}`;
      };
      const resetTooltip = () => {
        tooltipEl.textContent = 'Chạm/Rê chuột để xem';
      };

      rect.addEventListener('mouseenter', updateTooltip);
      rect.addEventListener('focus', updateTooltip);
      rect.addEventListener('mouseleave', resetTooltip);
      rect.addEventListener('blur', resetTooltip);

      svg.appendChild(rect);

      // X-axis date labels (show for first, middle, last, or every 5th)
      const shouldShowLabel =
        itemCount <= 10 ||
        idx === 0 ||
        idx === itemCount - 1 ||
        idx === Math.floor(itemCount / 2) ||
        idx % 5 === 0;

      if (shouldShowLabel) {
        const text = document.createElementNS(svgNS, 'text');
        text.setAttribute('x', xPos + barWidth / 2);
        text.setAttribute('y', height - 6);
        text.setAttribute('text-anchor', 'middle');
        text.setAttribute('font-size', '9');
        text.setAttribute('fill', 'var(--secondary-text-color, #6b7280)');
        text.textContent = this._formatDateLabel(item.date);
        svg.appendChild(text);
      }
    });

    section.appendChild(svg);
    return section;
  }

  // --- Official Bill History Table ---
  _renderBillTableSection(monthlyHistory) {
    const container = document.createElement('div');
    container.className = 'table-container';

    const titleEl = document.createElement('div');
    titleEl.className = 'section-title';
    titleEl.textContent = 'Lịch sử hóa đơn';
    container.appendChild(titleEl);

    if (!monthlyHistory || monthlyHistory.length === 0) {
      const emptyMsg = document.createElement('div');
      emptyMsg.className = 'empty-state';
      emptyMsg.textContent = 'Chưa có thông tin hóa đơn';
      container.appendChild(emptyMsg);
      return container;
    }

    const table = document.createElement('table');
    table.className = 'bill-table';

    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');

    ['Kỳ thanh toán', 'Sản lượng', 'Số tiền', 'Trạng thái'].forEach((text) => {
      const th = document.createElement('th');
      th.textContent = text;
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    monthlyHistory.forEach((bill) => {
      const tr = document.createElement('tr');

      // Period
      const tdPeriod = document.createElement('td');
      tdPeriod.textContent = bill.period || bill.month || '—';
      tr.appendChild(tdPeriod);

      // kWh
      const kwhVal = bill.totalKwh !== undefined ? bill.totalKwh : (bill.total_kwh !== undefined ? bill.total_kwh : bill.kwh);
      const tdKwh = document.createElement('td');
      tdKwh.textContent = this._formatKwh(kwhVal);
      tr.appendChild(tdKwh);

      // VND
      const vndVal = bill.totalAmount !== undefined ? bill.totalAmount : (bill.total_amount !== undefined ? bill.total_amount : bill.amount);
      const tdVnd = document.createElement('td');
      tdVnd.textContent = this._formatVnd(vndVal);
      tr.appendChild(tdVnd);

      // Status
      const isPaid = bill.isPaid !== undefined ? bill.isPaid : bill.is_paid;
      const tdStatus = document.createElement('td');
      const pill = document.createElement('span');
      pill.className = isPaid ? 'pill-paid' : 'pill-unpaid';
      pill.textContent = isPaid ? 'Đã thanh toán' : 'Chưa thanh toán';
      tdStatus.appendChild(pill);
      tr.appendChild(tdStatus);

      tbody.appendChild(tr);
    });

    table.appendChild(tbody);
    container.appendChild(table);
    return container;
  }

  // --- Scoped CSS Styles ---
  _getStyles() {
    const palettes = {
      auto: ['var(--primary-color, #1976d2)', 'var(--primary-color, #1565c0)'],
      slate: ['#1976d2', '#125ea8'],
      forest: ['#16803c', '#0f6530'],
      amber: ['#b76b00', '#8d5200'],
      high_contrast: ['#005fcc', '#003f8a'],
    };
    const [accent, accentStrong] = palettes[this._config.color_scheme] || palettes.auto;
    return `
      :host {
        display: block;
        --evn-accent: ${accent};
        --evn-accent-strong: ${accentStrong};
      }
      ha-card {
        padding: 16px;
        background: var(--card-background-color, var(--ha-card-background, #ffffff));
        color: var(--primary-text-color, #212121);
        border-radius: var(--ha-card-border-radius, 12px);
        border: 1px solid var(--divider-color, rgba(0, 0, 0, 0.12));
        box-shadow: var(--ha-card-box-shadow, none);
        font-family: var(--primary-font-family, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif);
        box-sizing: border-box;
      }
      .card-content {
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      .card-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 8px;
      }
      .title-box {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
      }
      .card-title {
        font-size: 16px;
        font-weight: 600;
        color: var(--primary-text-color, #111827);
        margin: 0;
      }
      .customer-badge {
        font-family: var(--code-font-family, monospace);
        font-size: 11px;
        background: var(--secondary-background-color, rgba(0, 0, 0, 0.05));
        color: var(--secondary-text-color, #6b7280);
        padding: 2px 6px;
        border-radius: 4px;
        border: 1px solid var(--divider-color, rgba(0, 0, 0, 0.08));
      }
      .status-warning {
        font-size: 11px;
        font-weight: 500;
        background: #fef3c7;
        color: #92400e;
        padding: 2px 8px;
        border-radius: 12px;
        border: 1px solid #fde68a;
      }
      .warning-banner {
        background: #fffbeb;
        color: #b45309;
        border: 1px solid #fcd34d;
        border-radius: 6px;
        padding: 8px 12px;
        font-size: 12px;
      }
      .error-banner {
        background: #fee2e2;
        color: #991b1b;
        border: 1px solid #fca5a5;
        border-radius: 6px;
        padding: 8px 12px;
        font-size: 12px;
      }
      .metrics-grid {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 8px;
      }
      @media (max-width: 520px) {
        .metrics-grid {
          grid-template-columns: repeat(2, 1fr);
        }
      }
      .metric-card {
        background: var(--secondary-background-color, rgba(0, 0, 0, 0.03));
        border: 1px solid var(--divider-color, rgba(0, 0, 0, 0.08));
        border-radius: 6px;
        padding: 8px 10px;
        display: flex;
        flex-direction: column;
      }
      .metric-label {
        font-size: 11px;
        color: var(--secondary-text-color, #6b7280);
        margin-bottom: 4px;
        white-space: nowrap;
      }
      .metric-value {
        font-size: 15px;
        font-weight: 600;
        font-variant-numeric: tabular-nums;
        color: var(--primary-text-color, #111827);
      }
      .metric-value.accent {
        color: var(--evn-accent);
      }
      .chart-container {
        background: var(--secondary-background-color, rgba(0, 0, 0, 0.02));
        border: 1px solid var(--divider-color, rgba(0, 0, 0, 0.08));
        border-radius: 6px;
        padding: 10px 12px;
      }
      .chart-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 12px;
        font-weight: 500;
        color: var(--secondary-text-color, #4b5563);
        margin-bottom: 8px;
      }
      .chart-tooltip {
        font-variant-numeric: tabular-nums;
        font-size: 11px;
        color: var(--primary-text-color, #111827);
        font-weight: 600;
      }
      .chart-svg {
        width: 100%;
        height: auto;
        display: block;
        overflow: visible;
      }
      .bar {
        fill: var(--evn-accent);
        opacity: 0.85;
        transition: opacity 0.15s ease, fill 0.15s ease;
        cursor: pointer;
      }
      .bar:hover, .bar:focus {
        opacity: 1;
        fill: var(--evn-accent-strong);
        outline: none;
      }
      .table-container {
        overflow-x: auto;
      }
      .section-title {
        font-size: 12px;
        font-weight: 600;
        color: var(--secondary-text-color, #4b5563);
        margin-bottom: 6px;
      }
      .bill-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 12px;
        text-align: left;
      }
      .bill-table th {
        color: var(--secondary-text-color, #6b7280);
        font-weight: 500;
        border-bottom: 1px solid var(--divider-color, rgba(0, 0, 0, 0.12));
        padding: 6px 8px;
      }
      .bill-table td {
        padding: 8px;
        border-bottom: 1px solid var(--divider-color, rgba(0, 0, 0, 0.05));
        font-variant-numeric: tabular-nums;
        color: var(--primary-text-color, #111827);
      }
      .bill-table tr:last-child td {
        border-bottom: none;
      }
      .pill-paid {
        display: inline-block;
        padding: 2px 6px;
        border-radius: 4px;
        font-size: 11px;
        font-weight: 500;
        background: rgba(16, 185, 129, 0.15);
        color: #047857;
      }
      .pill-unpaid {
        display: inline-block;
        padding: 2px 6px;
        border-radius: 4px;
        font-size: 11px;
        font-weight: 500;
        background: rgba(239, 68, 68, 0.15);
        color: #b91c1c;
      }
      .empty-state {
        text-align: center;
        padding: 16px;
        color: var(--secondary-text-color, #6b7280);
        font-size: 12px;
      }
    `;
  }
}

// Register Custom Element
if (!customElements.get('evn-vietnam-energy-card')) {
  customElements.define('evn-vietnam-energy-card', EvnVietnamEnergyCard);
}

// Register with Home Assistant Card Picker
window.customCards = window.customCards || [];
window.customCards.push({
  type: 'evn-vietnam-energy-card',
  name: 'EVN Vietnam Energy Card',
  description: 'Thẻ theo dõi sản lượng và tiền điện EVN Việt Nam dành cho Home Assistant.',
});
