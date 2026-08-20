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
    this._selectedViewId = null;
    this._selectedRangeDays = 30;
  }

  setConfig(config) {
    const providedConfig = config && typeof config === 'object' ? config : {};
    const entity = typeof providedConfig.entity === 'string' ? providedConfig.entity.trim() : '';

    let views = [];
    if (Array.isArray(providedConfig.customer_views)) {
      views = providedConfig.customer_views
        .filter((v) => v && typeof v === 'object')
        .map((v, index) => ({
          id: typeof v.id === 'string' && v.id.trim() ? v.id.trim() : (index === 0 ? 'aggregate' : `view_${index}`),
          label: typeof v.label === 'string' && v.label.trim() ? v.label.trim() : (index === 0 ? 'Tổng' : `Khách hàng ${index}`),
          entity: typeof v.entity === 'string' ? v.entity.trim() : '',
          cost_entity: typeof v.cost_entity === 'string' ? v.cost_entity.trim() : '',
          today_entity: typeof v.today_entity === 'string' ? v.today_entity.trim() : '',
          yesterday_entity: typeof v.yesterday_entity === 'string' ? v.yesterday_entity.trim() : '',
        }))
        .filter((v) => Boolean(v.entity));
    }

    this._config = {
      title: 'Điện năng EVN',
      color_scheme: 'auto',
      ...providedConfig,
      customer_views: views.length > 0 ? views : providedConfig.customer_views,
    };

    const hasEntity = entity || (views.length > 0 && views.some((v) => Boolean(v.entity)));
    this._configError = hasEntity
      ? null
      : 'Chọn entity sản lượng tháng của EVN để hiển thị thẻ này.';

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

  get hass() {
    return this._hass;
  }

  get config() {
    return this._config;
  }

  _shouldUpdate(oldHass, newHass) {
    if (!this._config || !oldHass || !oldHass.states || !newHass || !newHass.states) return true;
    const entities = new Set();
    [
      this._config.entity,
      this._config.cost_entity,
      this._config.today_entity,
      this._config.yesterday_entity,
    ].forEach((id) => {
      if (typeof id === 'string' && id.trim() !== '') entities.add(id.trim());
    });

    if (Array.isArray(this._config.customer_views)) {
      this._config.customer_views.forEach((v) => {
        if (v && typeof v === 'object') {
          [v.entity, v.cost_entity, v.today_entity, v.yesterday_entity].forEach((id) => {
            if (typeof id === 'string' && id.trim() !== '') entities.add(id.trim());
          });
        }
      });
    }

    for (const entityId of entities) {
      if (oldHass.states[entityId] !== newHass.states[entityId]) {
        return true;
      }
    }
    return false;
  }

  getCardSize() {
    return 8;
  }

  _chartValue(item) {
    if (!item || typeof item !== 'object') return 0;
    const raw = item.consumption !== undefined && item.consumption !== null ? item.consumption : item.kwh;
    if (raw === null || raw === undefined || raw === '') return 0;
    const num = Number(raw);
    if (!Number.isFinite(num) || num < 0) return 0;
    return num;
  }

  _isoDate(value) {
    const raw = String(value || '').slice(0, 10);
    return /^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw : '';
  }

  _shiftIsoDate(iso, days) {
    const [year, month, day] = iso.split('-').map(Number);
    const date = new Date(year, month - 1, day);
    date.setDate(date.getDate() + days);
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  }

  _calendarBars(dailyHistory, rangeDays) {
    const byDate = new Map();
    const rows = Array.isArray(dailyHistory) ? dailyHistory : [];
    for (const item of rows) {
      const iso = this._isoDate(item && item.date);
      if (!iso) continue;
      byDate.set(iso, item);
    }
    const known = [...byDate.keys()].sort();
    if (known.length === 0) return [];
    const days = Math.max(1, Number(rangeDays) || 30);
    const end = known[known.length - 1];
    const start = this._shiftIsoDate(end, -(days - 1));
    const series = [];
    for (let iso = start; iso <= end; iso = this._shiftIsoDate(iso, 1)) {
      const source = byDate.get(iso);
      const consumption = source ? this._chartValue(source) : 0;
      series.push({
        date: iso,
        consumption,
        kwh: consumption,
        missing: !source,
      });
    }
    return series;
  }

  _formatAxisDateLabel(series, idx) {
    const item = series[idx];
    const fullLabel = this._formatDateLabel(item && item.date);
    if (series.length <= 7 || idx === 0 || idx === series.length - 1) return fullLabel;
    const previous = series[idx - 1];
    if (!previous || previous.date.slice(5, 7) !== item.date.slice(5, 7)) return fullLabel;
    return fullLabel.slice(0, 2);
  }

  // --- Formatting Helpers ---
  _formatNumber(val, decimals = 1) {
    if (val === null || val === undefined || val === '') return '—';
    const num = Number(val);
    if (!Number.isFinite(num)) return '—';
    try {
      return new Intl.NumberFormat('vi-VN', {
        minimumFractionDigits: 0,
        maximumFractionDigits: decimals,
      }).format(num);
    } catch (e) {
      return String(num);
    }
  }

  _formatKwh(val) {
    if (val === null || val === undefined || val === '') return '—';
    const num = Number(val);
    if (!Number.isFinite(num)) return '—';
    return `${this._formatNumber(num, 1)} kWh`;
  }

  _formatVnd(val) {
    if (val === null || val === undefined || val === '') return '—';
    const num = Number(val);
    if (!Number.isFinite(num)) return '—';
    return `${this._formatNumber(num, 0)} ₫`;
  }

  _formatDateLabel(dateStr) {
    if (!dateStr || typeof dateStr !== 'string') return '';
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

    // Normalize customer_views
    const rawViews = Array.isArray(this._config.customer_views) ? this._config.customer_views : [];
    const views = rawViews
      .filter((v) => v && typeof v === 'object')
      .map((v, index) => ({
        id: typeof v.id === 'string' && v.id.trim() ? v.id.trim() : (index === 0 ? 'aggregate' : `view_${index}`),
        label: typeof v.label === 'string' && v.label.trim() ? v.label.trim() : (index === 0 ? 'Tổng' : `Mã KH ${index}`),
        entity: typeof v.entity === 'string' ? v.entity.trim() : '',
        cost_entity: typeof v.cost_entity === 'string' ? v.cost_entity.trim() : '',
        today_entity: typeof v.today_entity === 'string' ? v.today_entity.trim() : '',
        yesterday_entity: typeof v.yesterday_entity === 'string' ? v.yesterday_entity.trim() : '',
      }))
      .filter((v) => Boolean(v.entity));

    let activeView = null;
    if (views.length > 0) {
      if (this._selectedViewId) {
        activeView = views.find((v) => v.id === this._selectedViewId) || views[0];
      } else {
        activeView = views[0];
      }
    } else {
      activeView = {
        id: 'aggregate',
        label: 'Tổng',
        entity: typeof this._config.entity === 'string' ? this._config.entity.trim() : '',
        cost_entity: typeof this._config.cost_entity === 'string' ? this._config.cost_entity.trim() : '',
        today_entity: typeof this._config.today_entity === 'string' ? this._config.today_entity.trim() : '',
        yesterday_entity: typeof this._config.yesterday_entity === 'string' ? this._config.yesterday_entity.trim() : '',
      };
    }

    const currentViewId = activeView ? activeView.id : 'aggregate';

    if (this._configError || !activeView || !activeView.entity) {
      cardEl.appendChild(this._createErrorBox(this._configError || 'Chọn entity sản lượng tháng của EVN để hiển thị thẻ này.'));
      this.shadowRoot.appendChild(cardEl);
      return;
    }

    if (!this._hass || !this._hass.states) {
      cardEl.appendChild(this._createStateBox('Đang kết nối với Home Assistant...'));
      this.shadowRoot.appendChild(cardEl);
      return;
    }

    const activeEntityId = activeView.entity;
    const mainEntity = this._hass.states[activeEntityId];
    if (!mainEntity) {
      if (views.length > 1) {
        cardContent.appendChild(this._renderHeader('—', [], [], null, false, views, currentViewId));
      }
      cardContent.appendChild(
        this._createErrorBox(`Không tìm thấy entity: ${activeEntityId}`)
      );
      cardEl.appendChild(cardContent);
      this.shadowRoot.appendChild(cardEl);
      return;
    }

    if (mainEntity.state === 'unavailable' || mainEntity.state === 'unknown') {
      if (views.length > 1) {
        cardContent.appendChild(this._renderHeader('—', [], [], null, false, views, currentViewId));
      }
      cardContent.appendChild(
        this._createStateBox(`Thực thể ${activeEntityId} đang ở trạng thái: ${mainEntity.state}`)
      );
      cardEl.appendChild(cardContent);
      this.shadowRoot.appendChild(cardEl);
      return;
    }

    // Extract Attributes safely
    const attrs = (mainEntity && typeof mainEntity.attributes === 'object' && mainEntity.attributes !== null)
      ? mainEntity.attributes
      : {};
    const customerCode = attrs.customer_code || '—';
    const selectedCodes = Array.isArray(attrs.selected_customer_codes) ? attrs.selected_customer_codes : [];
    const successfulCodes = Array.isArray(attrs.successful_customer_codes) ? attrs.successful_customer_codes : [];
    const latestReading = attrs.latest_reading !== undefined && attrs.latest_reading !== null ? attrs.latest_reading : null;
    const isPartial = Boolean(attrs.is_partial);
    const partialErrors = (attrs.partial_errors && typeof attrs.partial_errors === 'object') ? attrs.partial_errors : null;
    const dailyHistory = Array.isArray(attrs.daily_history) ? attrs.daily_history : [];

    // Safely process optional Cost Entity with proven scope equality
    let costStateValue = null;
    let costBills = [];
    const activeCostEntity = activeView.cost_entity;
    if (
      activeCostEntity &&
      this._hass &&
      this._hass.states &&
      this._hass.states[activeCostEntity]
    ) {
      const costEntity = this._hass.states[activeCostEntity];
      if (
        costEntity &&
        costEntity.state !== 'unavailable' &&
        costEntity.state !== 'unknown'
      ) {
        const costNum = Number(costEntity.state);
        const costAttrs = (typeof costEntity.attributes === 'object' && costEntity.attributes !== null)
          ? costEntity.attributes
          : {};
        if (this._sameCustomerScope(attrs, costAttrs)) {
          if (Number.isFinite(costNum)) {
            costStateValue = costNum;
          }
          if (Array.isArray(costAttrs.bills)) {
            costBills = costAttrs.bills;
          }
        }
      }
    }

    const monthlyHistory = Array.isArray(attrs.monthly_history) && attrs.monthly_history.length > 0
      ? attrs.monthly_history
      : costBills;

    // 1. Header Section
    cardContent.appendChild(this._renderHeader(customerCode, selectedCodes, successfulCodes, latestReading, isPartial, views, currentViewId));

    // 2. Partial Warning & Error Banners
    if (isPartial) {
      cardContent.appendChild(this._renderPartialWarning());
    }

    if (partialErrors && Object.keys(partialErrors).length > 0) {
      cardContent.appendChild(this._renderErrorBanner(partialErrors));
    }

    // 3. Summary Grid (4 Summary Values)
    cardContent.appendChild(
      this._renderSummaryGrid(
        mainEntity.state,
        costStateValue,
        dailyHistory,
        activeView.today_entity,
        activeView.yesterday_entity
      )
    );

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
    if (
      !mainAttrs ||
      typeof mainAttrs !== 'object' ||
      !costAttrs ||
      typeof costAttrs !== 'object'
    ) {
      return false;
    }

    const mainCode = typeof mainAttrs.customer_code === 'string' ? mainAttrs.customer_code.trim() : '';
    const costCode = typeof costAttrs.customer_code === 'string' ? costAttrs.customer_code.trim() : '';

    // Scope equality must be proven: both must have a non-empty customer_code and they must match exactly
    if (!mainCode || !costCode || mainCode !== costCode) {
      return false;
    }

    // For __aggregate__, require exact non-empty selected_customer_codes set match
    if (mainCode === '__aggregate__') {
      if (!Array.isArray(mainAttrs.selected_customer_codes) || !Array.isArray(costAttrs.selected_customer_codes)) {
        return false;
      }

      const mainCodes = mainAttrs.selected_customer_codes
        .map((c) => (c !== null && c !== undefined ? String(c).trim() : ''))
        .filter((c) => c.length > 0);
      const costCodes = costAttrs.selected_customer_codes
        .map((c) => (c !== null && c !== undefined ? String(c).trim() : ''))
        .filter((c) => c.length > 0);

      if (mainCodes.length === 0 || costCodes.length === 0) {
        return false;
      }

      const mainSorted = Array.from(new Set(mainCodes)).sort();
      const costSorted = Array.from(new Set(costCodes)).sort();

      if (mainSorted.length !== costSorted.length) {
        return false;
      }

      return mainSorted.every((code, idx) => code === costSorted[idx]);
    }

    return true;
  }

  _renderHeader(customerCode, selectedCodes, successfulCodes, latestReading, isPartial, views, activeViewId) {
    const header = document.createElement('div');
    header.className = 'card-header';

    const titleBox = document.createElement('div');
    titleBox.className = 'title-box';

    const titleEl = document.createElement('div');
    titleEl.className = 'card-title';
    titleEl.textContent = (this._config && this._config.title) || 'Điện năng EVN';
    titleBox.appendChild(titleEl);

    // Customer Code / Aggregate badge
    const badgeEl = document.createElement('span');
    badgeEl.className = 'customer-badge';

    if (customerCode === '__aggregate__' || (Array.isArray(selectedCodes) && selectedCodes.length > 1)) {
      let countStr = '';
      if (successfulCodes.length > 0 && selectedCodes.length > 0 && successfulCodes.length !== selectedCodes.length) {
        countStr = `${successfulCodes.length}/${selectedCodes.length}`;
      } else {
        countStr = String(selectedCodes.length || 'nhiều');
      }
      badgeEl.textContent = `Tổng hợp (${countStr} mã KH)`;
    } else {
      badgeEl.textContent = customerCode !== '—' ? `Mã KH: ${customerCode}` : 'EVN Vietnam';
    }
    titleBox.appendChild(badgeEl);

    // Latest Reading badge
    if (latestReading !== null && latestReading !== undefined && latestReading !== '') {
      const numRead = Number(latestReading);
      if (Number.isFinite(numRead)) {
        const readingBadge = document.createElement('span');
        readingBadge.className = 'reading-badge';
        readingBadge.textContent = `Chỉ số: ${this._formatKwh(numRead)}`;
        titleBox.appendChild(readingBadge);
      }
    }

    header.appendChild(titleBox);

    const controlsBox = document.createElement('div');
    controlsBox.className = 'header-controls';

    if (Array.isArray(views) && views.length > 1) {
      const select = document.createElement('select');
      select.className = 'view-selector';
      select.setAttribute('aria-label', 'Chọn chế độ xem điện năng');

      views.forEach((v) => {
        const option = document.createElement('option');
        option.value = v.id;
        option.textContent = v.label || v.id;
        if (v.id === activeViewId) {
          option.selected = true;
        }
        select.appendChild(option);
      });

      select.addEventListener('change', (e) => {
        const val = e && e.target ? e.target.value : select.value;
        this._selectedViewId = val;
        this.render();
      });

      controlsBox.appendChild(select);
    }

    if (isPartial) {
      const warningPill = document.createElement('span');
      warningPill.className = 'status-warning';
      warningPill.textContent = '⚠️ Một phần';
      controlsBox.appendChild(warningPill);
    }

    if (controlsBox.children.length > 0) {
      header.appendChild(controlsBox);
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

  _renderSummaryGrid(currentMonthState, costStateValue, dailyHistory, todayEntityId, yesterdayEntityId) {
    const grid = document.createElement('div');
    grid.className = 'metrics-grid';

    // 1. Today Value
    let todayVal = null;
    const targetTodayEntity = todayEntityId || (this._config && this._config.today_entity);
    if (
      targetTodayEntity &&
      this._hass &&
      this._hass.states &&
      this._hass.states[targetTodayEntity]
    ) {
      const st = this._hass.states[targetTodayEntity].state;
      if (st !== 'unavailable' && st !== 'unknown') {
        const num = Number(st);
        if (Number.isFinite(num)) {
          todayVal = num;
        }
      }
    }
    if (todayVal === null) {
      const now = new Date();
      const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
      const found = Array.isArray(dailyHistory)
        ? dailyHistory.find((item) => item && typeof item === 'object' && item.date === todayStr)
        : null;
      if (found) {
        const raw = found.consumption !== undefined && found.consumption !== null ? found.consumption : found.kwh;
        if (raw !== undefined && raw !== null && raw !== '') {
          const num = Number(raw);
          if (Number.isFinite(num)) {
            todayVal = num;
          }
        }
      }
    }

    // 2. Yesterday Value
    let yesterdayVal = null;
    const targetYesterdayEntity = yesterdayEntityId || (this._config && this._config.yesterday_entity);
    if (
      targetYesterdayEntity &&
      this._hass &&
      this._hass.states &&
      this._hass.states[targetYesterdayEntity]
    ) {
      const st = this._hass.states[targetYesterdayEntity].state;
      if (st !== 'unavailable' && st !== 'unknown') {
        const num = Number(st);
        if (Number.isFinite(num)) {
          yesterdayVal = num;
        }
      }
    }
    if (yesterdayVal === null) {
      const now = new Date();
      const yest = new Date(now);
      yest.setDate(yest.getDate() - 1);
      const yestStr = `${yest.getFullYear()}-${String(yest.getMonth() + 1).padStart(2, '0')}-${String(yest.getDate()).padStart(2, '0')}`;
      const found = Array.isArray(dailyHistory)
        ? dailyHistory.find((item) => item && typeof item === 'object' && item.date === yestStr)
        : null;
      if (found) {
        const raw = found.consumption !== undefined && found.consumption !== null ? found.consumption : found.kwh;
        if (raw !== undefined && raw !== null && raw !== '') {
          const num = Number(raw);
          if (Number.isFinite(num)) {
            yesterdayVal = num;
          }
        }
      }
    }

    // 3. Current Month Value
    const rawMonth = Number(currentMonthState);
    const monthVal = Number.isFinite(rawMonth) ? rawMonth : null;

    // 4. Estimated Cost Value (VND)
    const rawCost = Number(costStateValue);
    const costVal = Number.isFinite(rawCost) ? rawCost : null;

    grid.appendChild(this._createMetricTile('Hôm nay', this._formatKwh(todayVal)));
    grid.appendChild(this._createMetricTile('Hôm qua', this._formatKwh(yesterdayVal)));
    grid.appendChild(this._createMetricTile('Tháng này', this._formatKwh(monthVal), true));
    grid.appendChild(this._createMetricTile('Chi phí ước tính', this._formatVnd(costVal), true));

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

    const titleBox = document.createElement('div');
    titleBox.className = 'chart-title-box';

    const titleEl = document.createElement('span');
    titleEl.className = 'chart-title';
    titleEl.textContent = 'Sản lượng theo ngày (kWh)';
    titleBox.appendChild(titleEl);

    // Range segmented controls (7, 14, 30 days)
    const rangeControls = document.createElement('div');
    rangeControls.className = 'range-controls';

    const currentRange = this._selectedRangeDays || 30;
    [7, 14, 30].forEach((days) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `range-btn${days === currentRange ? ' active' : ''}`;
      btn.textContent = `${days} ngày`;
      btn.setAttribute('aria-label', `Xem ${days} ngày`);
      btn.addEventListener('click', (e) => {
        if (e && typeof e.stopPropagation === 'function') {
          e.stopPropagation();
        }
        this._selectedRangeDays = days;
        this.render();
      });
      rangeControls.appendChild(btn);
    });
    titleBox.appendChild(rangeControls);
    header.appendChild(titleBox);

    const tooltipEl = document.createElement('span');
    tooltipEl.className = 'chart-tooltip';
    tooltipEl.textContent = 'Chạm/Rê chuột để xem';
    header.appendChild(tooltipEl);

    section.appendChild(header);

    const rangeDays = this._selectedRangeDays || 30;
    const filteredData = this._calendarBars(dailyHistory, rangeDays);

    if (filteredData.length === 0) {
      const emptyMsg = document.createElement('div');
      emptyMsg.className = 'empty-state';
      emptyMsg.textContent = 'Không có dữ liệu sản lượng hàng ngày';
      section.appendChild(emptyMsg);
      return section;
    }

    const svgNS = 'http://www.w3.org/2000/svg';
    const width = 720;
    const height = 236;
    const paddingLeft = 40;
    const paddingRight = 12;
    const paddingTop = 16;
    const paddingBottom = 52;

    const chartW = width - paddingLeft - paddingRight;
    const chartH = height - paddingTop - paddingBottom;
    const vals = filteredData.map((item) => this._chartValue(item));
    const rawMax = vals.length > 0 ? Math.max(...vals) : 0;
    const maxVal = Number.isFinite(rawMax) && rawMax > 0 ? rawMax : 1.0;
    const yMax = maxVal * 1.15;

    const svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('class', 'chart-svg');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'Biểu đồ sản lượng điện hàng ngày');

    // Horizontal Grid lines (y=0, y=50%, y=100%)
    const gridLevels = [0, yMax / 2, yMax];
    gridLevels.forEach((level) => {
      const safeLevel = Number.isFinite(level) ? Math.max(0, level) : 0;
      const yPos = paddingTop + chartH - (safeLevel / yMax) * chartH;
      if (!Number.isFinite(yPos)) return;

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
        text.textContent = this._formatNumber(safeLevel, 0);
        svg.appendChild(text);
      }
    });

    // Amber dashed average line across visible range
    const totalVal = vals.reduce((sum, v) => sum + v, 0);
    const avgVal = vals.length > 0 ? totalVal / vals.length : 0;
    if (avgVal > 0 && Number.isFinite(avgVal)) {
      const yAvg = paddingTop + chartH - (avgVal / yMax) * chartH;
      if (Number.isFinite(yAvg)) {
        const avgLine = document.createElementNS(svgNS, 'line');
        avgLine.setAttribute('class', 'avg-line');
        avgLine.setAttribute('x1', paddingLeft);
        avgLine.setAttribute('x2', width - paddingRight);
        avgLine.setAttribute('y1', yAvg);
        avgLine.setAttribute('y2', yAvg);
        avgLine.setAttribute('stroke', '#f59e0b');
        avgLine.setAttribute('stroke-width', '1.5');
        avgLine.setAttribute('stroke-dasharray', '4,4');

        const avgTitle = document.createElementNS(svgNS, 'title');
        avgTitle.textContent = `Trung bình: ${this._formatKwh(avgVal)}`;
        avgLine.appendChild(avgTitle);

        svg.appendChild(avgLine);
      }
    }

    // One calendar day per column, including days EVN has not reported yet.
    const itemCount = filteredData.length;
    if (itemCount === 0) return section;

    const step = chartW / itemCount;
    const barWidth = Math.max(Math.min(step * 0.72, 22), 3);

    filteredData.forEach((item, idx) => {
      const val = this._chartValue(item);
      const barH = val > 0 ? Math.max((val / yMax) * chartH, 2) : Math.max(chartH * 0.015, 1);
      const xPos = paddingLeft + idx * step + (step - barWidth) / 2;
      const yPos = paddingTop + chartH - barH;

      if (!Number.isFinite(xPos) || !Number.isFinite(yPos) || !Number.isFinite(barWidth) || !Number.isFinite(barH)) {
        return;
      }

      const rect = document.createElementNS(svgNS, 'rect');
      rect.setAttribute('class', item.missing || val === 0 ? 'bar bar-empty' : 'bar');
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

      // Every bar is one calendar day, so every slot receives its own label.
      const textX = paddingLeft + idx * step + step / 2;
      const textY = height - 14;
      if (Number.isFinite(textX) && Number.isFinite(textY)) {
        const text = document.createElementNS(svgNS, 'text');
        text.setAttribute('class', 'axis-label');
        text.setAttribute('x', textX);
        text.setAttribute('y', textY);
        text.setAttribute('text-anchor', 'middle');
        text.setAttribute('font-size', itemCount > 14 ? '8' : '10');
        text.setAttribute('fill', 'var(--secondary-text-color, #6b7280)');
        text.textContent = this._formatAxisDateLabel(filteredData, idx);
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

    const validBills = Array.isArray(monthlyHistory)
      ? monthlyHistory.filter((b) => b && typeof b === 'object')
      : [];

    if (validBills.length === 0) {
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
    validBills.forEach((bill) => {
      const tr = document.createElement('tr');

      // Period
      const tdPeriod = document.createElement('td');
      tdPeriod.textContent = bill.period || bill.month || '—';
      tr.appendChild(tdPeriod);

      // kWh
      const rawKwh = bill.totalKwh !== undefined ? bill.totalKwh : (bill.total_kwh !== undefined ? bill.total_kwh : bill.kwh);
      const numKwh = (rawKwh !== undefined && rawKwh !== null && rawKwh !== '') ? Number(rawKwh) : null;
      const kwhVal = (numKwh !== null && Number.isFinite(numKwh)) ? numKwh : null;
      const tdKwh = document.createElement('td');
      tdKwh.textContent = this._formatKwh(kwhVal);
      tr.appendChild(tdKwh);

      // VND
      const rawAmount = bill.totalAmount !== undefined ? bill.totalAmount : (bill.total_amount !== undefined ? bill.total_amount : bill.amount);
      const numAmount = (rawAmount !== undefined && rawAmount !== null && rawAmount !== '') ? Number(rawAmount) : null;
      const vndVal = (numAmount !== null && Number.isFinite(numAmount)) ? numAmount : null;
      const tdVnd = document.createElement('td');
      tdVnd.textContent = this._formatVnd(vndVal);
      tr.appendChild(tdVnd);

      // Status
      let isPaid = false;
      if (bill.isPaid !== undefined) {
        isPaid = Boolean(bill.isPaid);
      } else if (bill.is_paid !== undefined) {
        isPaid = Boolean(bill.is_paid);
      } else if (typeof bill.status === 'string') {
        const s = bill.status.toLowerCase();
        isPaid = s === 'paid' || s.includes('đã thanh toán');
      } else if (typeof bill.payment_status === 'string') {
        const s = bill.payment_status.toLowerCase();
        isPaid = s === 'paid' || s.includes('đã thanh toán');
      }

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
    const scheme = (this._config && this._config.color_scheme) || 'auto';
    const [accent, accentStrong] = palettes[scheme] || palettes.auto;
    return `
      :host {
        display: block;
        width: 100%;
        --evn-accent: ${accent};
        --evn-accent-strong: ${accentStrong};
      }
      ha-card {
        width: 100%;
        max-width: 100%;
        padding: 20px;
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
      .reading-badge {
        font-size: 11px;
        background: var(--secondary-background-color, rgba(0, 0, 0, 0.05));
        color: var(--secondary-text-color, #6b7280);
        padding: 2px 6px;
        border-radius: 4px;
        border: 1px solid var(--divider-color, rgba(0, 0, 0, 0.08));
        font-variant-numeric: tabular-nums;
      }
      .header-controls {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
      }
      .view-selector {
        font-family: inherit;
        font-size: 12px;
        font-weight: 500;
        color: var(--primary-text-color, #111827);
        background: var(--card-background-color, var(--ha-card-background, #ffffff));
        border: 1px solid var(--divider-color, rgba(0, 0, 0, 0.15));
        border-radius: 6px;
        padding: 3px 8px;
        cursor: pointer;
        outline: none;
      }
      .view-selector:focus {
        border-color: var(--evn-accent, #1976d2);
        box-shadow: 0 0 0 1px var(--evn-accent, #1976d2);
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
        gap: 12px;
      }
      @media (max-width: 720px) {
        .metrics-grid {
          grid-template-columns: repeat(2, 1fr);
        }
      }
      .metric-card {
        background: var(--secondary-background-color, rgba(0, 0, 0, 0.03));
        border: 1px solid var(--divider-color, rgba(0, 0, 0, 0.08));
        border-radius: 8px;
        padding: 12px 14px;
        display: flex;
        flex-direction: column;
      }
      .metric-label {
        font-size: 12px;
        color: var(--secondary-text-color, #6b7280);
        margin-bottom: 6px;
        white-space: nowrap;
      }
      .metric-value {
        font-size: 18px;
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
        border-radius: 8px;
        padding: 14px 16px;
      }
      .chart-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 8px;
        font-size: 12px;
        font-weight: 500;
        color: var(--secondary-text-color, #4b5563);
        margin-bottom: 8px;
      }
      .chart-title-box {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
      }
      .chart-title {
        font-weight: 600;
      }
      .range-controls {
        display: inline-flex;
        background: var(--secondary-background-color, rgba(0, 0, 0, 0.05));
        border: 1px solid var(--divider-color, rgba(0, 0, 0, 0.08));
        border-radius: 6px;
        padding: 2px;
        gap: 2px;
      }
      .range-btn {
        font-family: inherit;
        font-size: 11px;
        font-weight: 500;
        border: none;
        background: transparent;
        color: var(--secondary-text-color, #6b7280);
        padding: 2px 6px;
        border-radius: 4px;
        cursor: pointer;
        outline: none;
        transition: background 0.15s ease, color 0.15s ease;
      }
      .range-btn:hover {
        color: var(--primary-text-color, #111827);
      }
      .range-btn.active {
        background: var(--card-background-color, var(--ha-card-background, #ffffff));
        color: var(--evn-accent, #1976d2);
        font-weight: 600;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
      }
      .range-btn:focus-visible {
        outline: 1px solid var(--evn-accent, #1976d2);
      }
      .chart-tooltip {
        font-variant-numeric: tabular-nums;
        font-size: 11px;
        color: var(--primary-text-color, #111827);
        font-weight: 600;
      }
      .chart-svg {
        width: 100%;
        min-height: 220px;
        height: auto;
        display: block;
        overflow: visible;
      }
      .avg-line {
        stroke: var(--warning-color, #f59e0b);
        stroke-width: 1.5;
        stroke-dasharray: 4,4;
        opacity: 0.9;
      }
      .bar {
        fill: var(--evn-accent);
        opacity: 0.9;
        transition: opacity 0.15s ease, fill 0.15s ease;
        cursor: pointer;
      }
      .bar-empty {
        opacity: 0.28;
      }
      .bar:hover, .bar:focus {
        opacity: 1;
        fill: var(--evn-accent-strong);
        outline: none;
      }
      @media (min-width: 900px) {
        ha-card {
          padding: 24px 28px;
        }
        .card-title {
          font-size: 18px;
        }
        .metric-value {
          font-size: 22px;
        }
        .chart-svg {
          min-height: 260px;
        }
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
if (!window.customCards.some((card) => card.type === 'evn-vietnam-energy-card')) {
  window.customCards.push({
    type: 'evn-vietnam-energy-card',
    name: 'EVN Vietnam Energy Card',
    description: 'Thẻ theo dõi sản lượng và tiền điện EVN Việt Nam dành cho Home Assistant.',
  });
}
