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
    this._selectedDay = '';
    this._selectedMonth = '';
    this._compareCache = new Map();
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
    if (byDate.size === 0) return [];
    const days = Math.max(1, Number(rangeDays) || 30);
    // The window ends today (Home Assistant's calendar), so a day EVN has not published yet is a gap, not the end.
    const end = this._zonedIsoDate(Date.now());
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

  _codeLabel(alias, code) {
    // Full customer code is the identity; a nickname is a prefix, never a replacement.
    const nick = typeof alias === 'string' ? alias.trim() : '';
    const id = typeof code === 'string' ? code.trim() : '';
    if (nick && id) return `${nick} (${id})`;
    return nick || id;
  }

  _viewLabel(view) {
    // Priority: hand-written label (not a generic "Mã KH n") > nickname (code) > full code.
    const generic = /^\s*(Mã\s*KH|Khách\s*hàng)\s*\d*\s*$/i;
    if (view.label && !generic.test(view.label)) return view.label;
    const state = this._hass && this._hass.states ? this._hass.states[view.entity] : null;
    const attrs = state && typeof state.attributes === 'object' && state.attributes !== null ? state.attributes : null;
    if (!attrs || typeof attrs.customer_code !== 'string' || !attrs.customer_code.trim()) return view.label;
    if (attrs.customer_code === '__aggregate__') return 'Tổng';
    return this._codeLabel(attrs.customer_alias, attrs.customer_code) || view.label;
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

    const mode = this._mode();
    if (mode) {
      cardContent.appendChild(this._renderTabs(mode, views.length > 0 ? views : [activeView]));
      cardEl.appendChild(cardContent);
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
    const customerAlias = typeof attrs.customer_alias === 'string' ? attrs.customer_alias : '';
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
    cardContent.appendChild(this._renderHeader(customerCode, selectedCodes, successfulCodes, latestReading, isPartial, views, currentViewId, customerAlias));

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
        activeView.yesterday_entity,
        attrs
      )
    );

    // 4. Selected day against the same day last month (long-term statistics)
    cardContent.appendChild(
      this._renderCompareSection(currentViewId, attrs, mainEntity.last_updated, dailyHistory)
    );

    // 5. Daily Energy Chart
    cardContent.appendChild(this._renderChartSection(dailyHistory, attrs, mainEntity.last_updated, monthlyHistory));

    // 6. Official Bill History Table
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

  _renderHeader(customerCode, selectedCodes, successfulCodes, latestReading, isPartial, views, activeViewId, customerAlias = '') {
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
      badgeEl.textContent = customerCode === '—'
        ? 'EVN Vietnam'
        : (customerAlias.trim() ? this._codeLabel(customerAlias, customerCode) : `Mã KH: ${customerCode}`);
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
        option.textContent = this._viewLabel(v) || v.id;
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

  _renderSummaryGrid(currentMonthState, costStateValue, dailyHistory, todayEntityId, yesterdayEntityId, attrs = null) {
    const grid = document.createElement('div');
    grid.className = 'metrics-grid';

    // 1. Newest day with data. "Today" is never published, so this replaces the always-empty today tile.
    const newest = this._newestDay(dailyHistory);
    const todayVal = newest ? newest.value : null;
    const selected = attrs && Array.isArray(attrs.selected_customer_codes) ? attrs.selected_customer_codes.length : 0;
    const reported = newest && Array.isArray(newest.row.customer_codes) ? newest.row.customer_codes.length : selected;
    const newestNote = newest ? this._missingCodesNote(selected - reported, selected) : '';

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

    // A month with no day of data yet is "no figure", not 0: EVN publishes about a day late.
    const monthHasData = this._hasMonthDays(dailyHistory, this._monthPrefix());
    const lateNote = 'EVN đăng trễ ~1 ngày';
    const monthShown = monthHasData || (monthVal !== null && monthVal > 0);
    const costShown = monthHasData || (costVal !== null && costVal > 0);

    grid.appendChild(this._noteTile(
      newest ? `Ngày mới nhất (${this._formatDateLabel(newest.date)})` : 'Ngày mới nhất', this._formatKwh(todayVal), newestNote,
    ));
    grid.appendChild(this._createMetricTile('Hôm qua', this._formatKwh(yesterdayVal)));
    grid.appendChild(monthShown
      ? this._createMetricTile('Tháng này', this._formatKwh(monthVal), true)
      : this._noteTile('Tháng này', 'Chưa có số', lateNote, true));
    grid.appendChild(costShown
      ? this._createMetricTile('Chi phí ước tính', this._formatVnd(costVal), true)
      : this._noteTile('Chi phí ước tính', 'Chưa có số', lateNote, true));

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

  // --- Day vs same day last month (recorder statistics) ---
  _statisticsIds(attrs) {
    const clean = (value) => (typeof value === 'string' && value.trim() ? value.trim() : '');
    return { energy: clean(attrs && attrs.statistics_id), cost: clean(attrs && attrs.cost_statistics_id) };
  }

  _timeZone() {
    const zone = this._hass && this._hass.config && this._hass.config.time_zone;
    return typeof zone === 'string' ? zone : '';
  }

  _zonedParts(ms, timeZone) {
    const formatter = new Intl.DateTimeFormat('en-US', {
      timeZone,
      hourCycle: 'h23',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    const parts = {};
    formatter.formatToParts(new Date(ms)).forEach((part) => {
      parts[part.type] = Number(part.value);
    });
    return parts;
  }

  _zonedOffsetMs(ms, timeZone) {
    const p = this._zonedParts(ms, timeZone);
    return Date.UTC(p.year, p.month - 1, p.day, p.hour % 24, p.minute, p.second) - Math.floor(ms / 1000) * 1000;
  }

  // Calendar date (YYYY-MM-DD) of an instant in Home Assistant's time zone.
  _zonedIsoDate(ms) {
    const pad = (n) => String(n).padStart(2, '0');
    const zone = this._timeZone();
    if (zone) {
      try {
        const p = this._zonedParts(ms, zone);
        return `${p.year}-${pad(p.month)}-${pad(p.day)}`;
      } catch (e) {
        // unknown zone name: fall back to the browser's calendar
      }
    }
    const date = new Date(ms);
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  // The instant a calendar day starts in Home Assistant's time zone.
  _zonedMidnight(year, month, day) {
    const zone = this._timeZone();
    if (zone) {
      try {
        const guess = Date.UTC(year, month - 1, day);
        const first = guess - this._zonedOffsetMs(guess, zone);
        return new Date(guess - this._zonedOffsetMs(first, zone));
      } catch (e) {
        // unknown zone name: fall back to the browser's calendar
      }
    }
    return new Date(year, month - 1, day);
  }

  _compareWindow(dayIso) {
    const [year, month] = dayIso.split('-').map(Number);
    const prevYear = month === 1 ? year - 1 : year;
    const prevMonth = month === 1 ? 12 : month - 1;
    const nextYear = month === 12 ? year + 1 : year;
    const nextMonth = month === 12 ? 1 : month + 1;
    return {
      start: this._zonedMidnight(prevYear, prevMonth, 1).toISOString(),
      end: this._zonedMidnight(nextYear, nextMonth, 1).toISOString(),
    };
  }

  // rows: [{ date: 'YYYY-MM-DD', value }]. A missing or unusable value is null, never 0.
  _compareDay(rows, dayIso) {
    const [year, month, day] = String(dayIso).split('-').map(Number);
    const byDate = new Map();
    (Array.isArray(rows) ? rows : []).forEach((row) => {
      if (!row || row.value === null || row.value === undefined || row.value === '') return;
      const num = Number(row.value);
      if (Number.isFinite(num)) byDate.set(String(row.date), num);
    });
    const pad = (n) => String(n).padStart(2, '0');
    const prevYear = month === 1 ? year - 1 : year;
    const prevMonth = month === 1 ? 12 : month - 1;
    const prefix = `${prevYear}-${pad(prevMonth)}-`;
    const sameDay = `${prefix}${pad(day)}`;
    const previous = [...byDate.entries()].filter(([date]) => date.startsWith(prefix)).map(([, value]) => value);
    return {
      day: byDate.has(dayIso) ? byDate.get(dayIso) : null,
      // The 29th-31st have no row in a shorter previous month, so they stay unknown.
      lastMonthDay: byDate.has(sameDay) ? byDate.get(sameDay) : null,
      avgPrevMonth: previous.length > 0 ? previous.reduce((sum, v) => sum + v, 0) / previous.length : null,
    };
  }

  _statRowsToDays(rows) {
    if (!Array.isArray(rows)) return [];
    return rows
      .filter((row) => row && Number.isFinite(Number(row.start)))
      .map((row) => ({ date: this._zonedIsoDate(Number(row.start)), value: row.change }));
  }

  // The selected day: a clicked bar while it is still on the chart, else the latest day with data.
  _selectedCompareDay(dailyHistory) {
    const rows = Array.isArray(dailyHistory) ? dailyHistory : [];
    if (this._selectedDay && (
      (this._selectedMonth && this._selectedDay.startsWith(this._selectedMonth))
      || this._calendarBars(rows, 30).some((bar) => bar.date === this._selectedDay)
    )) {
      return this._selectedDay;
    }
    const dated = rows
      .map((item) => ({ iso: this._isoDate(item && item.date), value: this._chartValue(item) }))
      .filter((item) => item.iso)
      .sort((a, b) => (a.iso < b.iso ? -1 : 1));
    const withData = dated.filter((item) => item.value > 0);
    const latest = withData.length > 0 ? withData[withData.length - 1] : dated[dated.length - 1];
    return latest ? latest.iso : '';
  }

  _formatDelta(value, reference, format) {
    if (value === null || reference === null) return '—';
    const delta = value - reference;
    const sign = delta > 0 ? '+' : delta < 0 ? '-' : '';
    const percent = reference !== 0 ? ` (${sign}${this._formatNumber(Math.abs(delta / reference) * 100, 1)}%)` : '';
    return `${sign}${format(Math.abs(delta))}${percent}`;
  }

  _compareNote(message) {
    const note = document.createElement('div');
    note.className = 'compare-muted';
    note.textContent = message;
    return note;
  }

  _renderCompareSection(viewId, attrs, lastUpdated, dailyHistory) {
    const ids = this._statisticsIds(attrs);
    const day = this._selectedCompareDay(dailyHistory);
    if (!ids.energy || !day || !this._hass || typeof this._hass.callWS !== 'function') {
      return this._compareNote('Chưa có lịch sử');
    }
    const now = new Date();
    const today = `${now.getFullYear()}-${now.getMonth() + 1}-${now.getDate()}`;
    const key = [viewId, ids.energy, ids.cost, day.slice(0, 7), lastUpdated || '', today].join('|');
    let entry = this._compareCache.get(key);
    if (!entry) {
      entry = { status: 'loading' };
      this._compareCache.set(key, entry);
      while (this._compareCache.size > 16) {
        this._compareCache.delete(this._compareCache.keys().next().value);
      }
      this._loadCompare(key, ids, day);
    }
    if (entry.status === 'loading') return this._compareNote('Đang tải so sánh…');
    if (entry.status !== 'ready') return this._compareNote('Chưa có lịch sử');
    return this._renderCompareTile(entry.data, day, ids);
  }

  _loadCompare(key, ids, day) {
    const range = this._compareWindow(day);
    const statisticIds = ids.cost ? [ids.energy, ids.cost] : [ids.energy];
    Promise.resolve()
      .then(() => this._hass.callWS({
        type: 'recorder/statistics_during_period',
        start_time: range.start,
        end_time: range.end,
        statistic_ids: statisticIds,
        period: 'day',
        types: ['change'],
      }))
      .then((result) => {
        const energy = this._statRowsToDays(result && result[ids.energy]);
        const cost = ids.cost ? this._statRowsToDays(result && result[ids.cost]) : [];
        this._compareCache.set(key, energy.length > 0 ? { status: 'ready', data: { energy, cost } } : { status: 'error' });
      })
      .catch(() => {
        this._compareCache.set(key, { status: 'error' });
      })
      .then(() => this.render());
  }

  _renderCompareTile(data, day, ids) {
    const section = document.createElement('div');
    section.className = 'compare-tile';

    const title = document.createElement('div');
    title.className = 'section-title';
    title.textContent = `So sánh ngày ${this._formatDateLabel(day)}`;
    section.appendChild(title);

    const addGroup = (rows, format, prefix) => {
      const cmp = this._compareDay(rows, day);
      const grid = document.createElement('div');
      grid.className = 'compare-grid';
      const tile = (label, value, reference) => {
        const card = this._createMetricTile(label, value === null ? '—' : format(value), false);
        if (reference !== undefined) {
          const delta = document.createElement('div');
          delta.className = 'compare-delta';
          delta.textContent = this._formatDelta(cmp.day, reference, format);
          card.appendChild(delta);
        }
        return card;
      };
      grid.appendChild(tile(`${prefix}ngày đã chọn`, cmp.day));
      grid.appendChild(tile(`${prefix}cùng ngày tháng trước`, cmp.lastMonthDay, cmp.lastMonthDay));
      grid.appendChild(tile(`${prefix}TB ngày tháng trước`, cmp.avgPrevMonth, cmp.avgPrevMonth));
      section.appendChild(grid);
    };
    addGroup(data.energy, (v) => this._formatKwh(v), 'Sản lượng ');
    if (ids.cost && data.cost.length > 0) {
      addGroup(data.cost, (v) => this._formatVnd(v), 'Chi phí ');
    }

    const hint = document.createElement('div');
    hint.className = 'compare-hint';
    hint.textContent = 'Chọn một cột trên biểu đồ để đổi ngày';
    section.appendChild(hint);
    return section;
  }

  // --- Month picker (recorder statistics) ---
  // The current month, then the 12 completed months before it, newest first.
  _monthOptions(todayIso) {
    const [year, month] = String(todayIso).split('-').map(Number);
    const options = [];
    for (let back = 0; back < 13; back += 1) {
      const index = year * 12 + (month - 1) - back;
      const optYear = Math.floor(index / 12);
      const optMonth = (index % 12) + 1;
      const mm = String(optMonth).padStart(2, '0');
      options.push({ value: `${optYear}-${mm}`, year: optYear, month: optMonth, label: `Tháng ${mm}/${optYear}` });
    }
    return options;
  }

  _loadMonth(key, ids, year, month) {
    const nextYear = month === 12 ? year + 1 : year;
    const nextMonth = month === 12 ? 1 : month + 1;
    Promise.resolve()
      .then(() => this._hass.callWS({
        type: 'recorder/statistics_during_period',
        start_time: this._zonedMidnight(year, month, 1).toISOString(),
        end_time: this._zonedMidnight(nextYear, nextMonth, 1).toISOString(),
        statistic_ids: [ids.energy],
        period: 'day',
        types: ['change'],
      }))
      .then((result) => {
        const rows = this._statRowsToDays(result && result[ids.energy]);
        this._compareCache.set(key, rows.length > 0 ? { status: 'ready', data: rows } : { status: 'error' });
      })
      .catch(() => {
        this._compareCache.set(key, { status: 'error' });
      })
      .then(() => this.render());
  }

  // One bar per calendar day of the month; a day the statistics lack is a gap, never 0.
  _renderMonthBars(rows, year, month) {
    const byDate = new Map();
    (Array.isArray(rows) ? rows : []).forEach((row) => {
      if (!row || row.value === null || row.value === undefined || row.value === '') return;
      const num = Number(row.value);
      if (Number.isFinite(num)) byDate.set(String(row.date), num);
    });
    const mm = String(month).padStart(2, '0');
    const lastDay = new Date(year, month, 0).getDate();
    const series = [];
    for (let day = 1; day <= lastDay; day += 1) {
      const iso = `${year}-${mm}-${String(day).padStart(2, '0')}`;
      const known = byDate.has(iso);
      const consumption = known ? this._chartValue({ consumption: byDate.get(iso) }) : 0;
      series.push({ date: iso, consumption, kwh: consumption, missing: !known });
    }
    return series;
  }

  _formatSignedKwh(val) {
    if (val === null || val === undefined || val === '' || !Number.isFinite(Number(val))) return '—';
    const num = Number(val);
    const sign = num > 0 ? '+' : num < 0 ? '-' : '';
    return `${sign}${this._formatNumber(Math.abs(num), 1)} kWh`;
  }

  _renderReconcileLine(bill) {
    const words = {
      match: 'Khớp',
      boundary: 'Lệch ranh giới kỳ, đã bù với kỳ liền kề',
      incomplete: 'Thiếu dữ liệu ngày',
      mismatch: 'Lệch',
      no_kwh: 'Chưa có kWh hoá đơn',
    };
    const status = bill && typeof bill.reconcile_status === 'string' ? bill.reconcile_status : '';
    if (!Object.prototype.hasOwnProperty.call(words, status)) return null;
    const line = document.createElement('div');
    line.className = status === 'mismatch' ? 'recon-line recon-warn' : 'recon-line';
    line.textContent = [
      `Hoá đơn ${this._formatKwh(bill.total_kwh)}`,
      `Thu thập ${this._formatKwh(bill.collected_kwh)}`,
      `Lệch ${this._formatSignedKwh(bill.diff_kwh)}`,
      words[status],
    ].join(' · ');
    return line;
  }

  // --- SVG Chart Renderer ---
  _renderChartSection(dailyHistory, attrs, lastUpdated, monthlyHistory) {
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

    // Rolling window or one calendar month (kept per card instance)
    const today = this._zonedIsoDate(Date.now());
    const monthChoices = this._monthOptions(today);
    const picked = monthChoices.find((o) => o.value === this._selectedMonth) || null;
    const monthSelect = document.createElement('select');
    monthSelect.className = 'month-selector';
    monthSelect.setAttribute('aria-label', 'Chọn khoảng thời gian của biểu đồ');
    [{ value: '', label: '30 ngày gần nhất' }, ...monthChoices].forEach((choice) => {
      const option = document.createElement('option');
      option.value = choice.value;
      option.textContent = choice.label;
      if (choice.value === (picked ? picked.value : '')) option.selected = true;
      monthSelect.appendChild(option);
    });
    monthSelect.addEventListener('change', (e) => {
      this._selectedMonth = e && e.target ? e.target.value : monthSelect.value;
      this.render();
    });
    titleBox.appendChild(monthSelect);

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
    if (!picked) titleBox.appendChild(rangeControls);
    header.appendChild(titleBox);

    const tooltipEl = document.createElement('span');
    tooltipEl.className = 'chart-tooltip';
    tooltipEl.textContent = 'Chạm/Rê chuột để xem';
    header.appendChild(tooltipEl);

    section.appendChild(header);

    let filteredData;
    let reconLines = [];
    if (picked) {
      reconLines = (Array.isArray(monthlyHistory) ? monthlyHistory : [])
        .filter((bill) => bill && Number(bill.year) === picked.year && Number(bill.month) === picked.month)
        .map((bill) => this._renderReconcileLine(bill))
        .filter(Boolean);
      const finishWithNote = (message) => {
        section.appendChild(this._compareNote(message));
        reconLines.forEach((line) => section.appendChild(line));
        return section;
      };
      const ids = this._statisticsIds(attrs);
      if (!ids.energy || !this._hass || typeof this._hass.callWS !== 'function') {
        return finishWithNote('Chưa có lịch sử');
      }
      // Only the running month still changes, so only it is keyed to the entity update.
      const key = ['month', ids.energy, picked.value, today, picked.value === today.slice(0, 7) ? (lastUpdated || '') : ''].join('|');
      let entry = this._compareCache.get(key);
      if (!entry) {
        entry = { status: 'loading' };
        this._compareCache.set(key, entry);
        while (this._compareCache.size > 16) {
          this._compareCache.delete(this._compareCache.keys().next().value);
        }
        this._loadMonth(key, ids, picked.year, picked.month);
      }
      if (entry.status === 'loading') return finishWithNote('Đang tải…');
      if (entry.status !== 'ready') return finishWithNote('Chưa có lịch sử');
      filteredData = this._renderMonthBars(entry.data, picked.year, picked.month);
      const monthTotal = document.createElement('div');
      monthTotal.className = 'month-total';
      monthTotal.textContent = `Tổng tháng: ${this._formatKwh(filteredData.reduce((sum, item) => sum + item.consumption, 0))}`;
      section.appendChild(monthTotal);
    } else {
      filteredData = this._calendarBars(dailyHistory, this._selectedRangeDays || 30);
    }
    const selectedDay = this._selectedCompareDay(dailyHistory);

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
      const barClass = item.missing || val === 0 ? 'bar bar-empty' : 'bar';
      rect.setAttribute('class', item.date === selectedDay ? `${barClass} bar-selected` : barClass);
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

      const selectDay = () => {
        this._selectedDay = item.date;
        this.render();
      };
      rect.addEventListener('click', selectDay);
      rect.addEventListener('keydown', (event) => {
        if (event && event.key === 'Enter') selectDay();
      });
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
    reconLines.forEach((line) => section.appendChild(line));
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

    ['Kỳ thanh toán', 'Sản lượng', 'Số tiền', 'Trạng thái', 'Thu thập', 'Lệch'].forEach((text) => {
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

      // Status: a payment state EVN did not give is "Không rõ", never "paid".
      const tdStatus = document.createElement('td');
      tdStatus.appendChild(this._paymentPill(bill, false));
      tr.appendChild(tdStatus);

      // Reconciliation against the collected daily data; a row without a status has none.
      const reconciled = typeof bill.reconcile_status === 'string' && bill.reconcile_status !== '';
      const tdCollected = document.createElement('td');
      const collectedText = reconciled ? this._formatKwh(bill.collected_kwh) : '—';
      tdCollected.textContent = collectedText === '—' ? '-' : collectedText;
      tr.appendChild(tdCollected);
      const tdDiff = document.createElement('td');
      const diffText = reconciled ? this._formatSignedKwh(bill.diff_kwh) : '—';
      tdDiff.textContent = diffText === '—' ? '-' : diffText;
      if (bill.reconcile_status === 'mismatch') tdDiff.className = 'recon-warn';
      tr.appendChild(tdDiff);

      tbody.appendChild(tr);
    });

    table.appendChild(tbody);
    container.appendChild(table);
    return container;
  }

  // --- Payment state, shared by every layout ---
  // kind: paid | unpaid | unknown. An unknown state is never shown as paid.
  _paymentState(bill) {
    const source = bill && typeof bill === 'object' ? bill : {};
    const status = typeof source.payment_status === 'string' ? source.payment_status.toLowerCase() : '';
    let kind = status === 'paid' || status === 'unpaid' ? status : '';
    if (!kind) {
      const legacy = source.isPaid !== undefined ? source.isPaid : source.is_paid;
      if (typeof legacy === 'boolean') {
        kind = legacy ? 'paid' : 'unpaid';
      } else if (typeof source.status === 'string') {
        const s = source.status.toLowerCase();
        kind = s === 'paid' || s.includes('đã thanh toán') ? 'paid' : 'unknown';
      } else {
        kind = 'unknown';
      }
    }
    const paidOn = this._isoDate(source.paid_on);
    const due = this._isoDate(source.due_date);
    return { kind, paidOn, due, unchecked: kind === 'unpaid' && source.payment_checked === false };
  }

  _paymentText(state, compact) {
    if (state.kind === 'paid') {
      const word = compact ? 'Đã TT' : 'Đã thanh toán';
      return state.paidOn && compact ? `${word} · ${this._formatDateLabel(state.paidOn)}` : word;
    }
    if (state.kind === 'unpaid') {
      const word = compact ? 'Chưa TT' : 'Chưa thanh toán';
      const due = state.due ? ` · hạn ${this._formatDateLabel(state.due)}` : '';
      return `${word}${due}${state.unchecked ? ' · chưa kiểm lại' : ''}`;
    }
    return 'Không rõ';
  }

  _paymentPill(bill, compact) {
    const state = this._paymentState(bill);
    const pill = document.createElement('span');
    pill.className = state.kind === 'paid' ? 'pill-paid' : state.kind === 'unpaid' ? 'pill-unpaid' : 'pill pill-unknown';
    pill.textContent = this._paymentText(state, compact);
    return pill;
  }

  // --- Day-1 rule and missing codes ---
  _finiteKwh(item) {
    if (!item || typeof item !== 'object') return null;
    const raw = item.consumption !== undefined && item.consumption !== null ? item.consumption : item.kwh;
    if (raw === null || raw === undefined || raw === '') return null;
    const num = Number(raw);
    return Number.isFinite(num) && num >= 0 ? num : null;
  }

  // Newest dated row with a usable value: { date, value, row } or null.
  _newestDay(dailyHistory) {
    let best = null;
    (Array.isArray(dailyHistory) ? dailyHistory : []).forEach((row) => {
      const date = this._isoDate(row && row.date);
      const value = this._finiteKwh(row);
      if (date && value !== null && (!best || date > best.date)) best = { date, value, row };
    });
    return best;
  }

  _hasMonthDays(dailyHistory, monthPrefix) {
    return (Array.isArray(dailyHistory) ? dailyHistory : []).some((row) => {
      const date = this._isoDate(row && row.date);
      return date && date.startsWith(monthPrefix) && this._finiteKwh(row) !== null;
    });
  }

  _monthPrefix() {
    return this._zonedIsoDate(Date.now()).slice(0, 7);
  }

  _missingCodesNote(missing, total) {
    return missing > 0 && total > 0 ? `thiếu ${missing}/${total} mã, EVN chưa đăng` : '';
  }

  _noteTile(label, valueText, note, isAccent = false) {
    const tile = this._createMetricTile(label, valueText, isAccent);
    if (note) {
      const noteEl = document.createElement('div');
      noteEl.className = 'metric-note';
      noteEl.textContent = note;
      tile.appendChild(noteEl);
    }
    return tile;
  }

  // --- Tab layouts (config.mode): overview | usage | bills | meter ---
  _mode() {
    const mode = this._config && this._config.mode;
    return mode === 'overview' || mode === 'usage' || mode === 'bills' || mode === 'meter' ? mode : '';
  }

  _isGenericLabel(label) {
    return /^\s*(Mã\s*KH|Khách\s*hàng)\s*\d*\s*$/i.test(label || '');
  }

  // A name for a code in the tabs: its own label, else its nickname, else customer_N. Never the code.
  _rowLabel(row, position) {
    const label = row.view && typeof row.view.label === 'string' ? row.view.label.trim() : '';
    if (label && !this._isGenericLabel(label)) return label;
    const alias = typeof row.attrs.customer_alias === 'string' ? row.attrs.customer_alias.trim() : '';
    return alias || `customer_${position}`;
  }

  _codeRows(views) {
    const states = (this._hass && this._hass.states) || {};
    const all = views.map((view) => {
      const state = states[view.entity];
      const attrs = state && typeof state.attributes === 'object' && state.attributes !== null ? state.attributes : {};
      const usable = Boolean(state) && state.state !== 'unavailable' && state.state !== 'unknown';
      return {
        view, state, attrs, usable, isAggregate: attrs.customer_code === '__aggregate__',
        daily: Array.isArray(attrs.daily_history) ? attrs.daily_history : [],
        bills: (Array.isArray(attrs.monthly_history) ? attrs.monthly_history : []).filter((bill) => bill && typeof bill === 'object'),
      };
    });
    const aggregate = all.find((row) => row.isAggregate) || null;
    let codes = all.filter((row) => !row.isAggregate);
    if (codes.length === 0 && aggregate) codes = [aggregate];
    const partial = aggregate && aggregate.attrs.partial_errors && typeof aggregate.attrs.partial_errors === 'object'
      ? aggregate.attrs.partial_errors
      : {};
    codes.forEach((row, index) => {
      row.label = this._rowLabel(row, index + 1);
      const code = typeof row.attrs.customer_code === 'string' ? row.attrs.customer_code : '';
      row.error = !row.usable ? 'unavailable' : (code && typeof partial[code] === 'string' ? partial[code] : '');
    });
    return { codes, aggregate };
  }

  _renderTabs(mode, views) {
    const wrap = document.createElement('div');
    wrap.className = `tab-layout tab-${mode}`;
    const titles = { overview: 'Tổng quan', usage: 'Sản lượng', bills: 'Hoá đơn & đối chiếu', meter: 'Chỉ số công tơ' };
    const header = document.createElement('div');
    header.className = 'card-header';
    const title = document.createElement('div');
    title.className = 'card-title';
    title.textContent = (this._config && this._config.title) || 'Điện năng EVN';
    const sub = document.createElement('span');
    sub.className = 'customer-badge';
    sub.textContent = titles[mode];
    const box = document.createElement('div');
    box.className = 'title-box';
    box.appendChild(title);
    box.appendChild(sub);
    header.appendChild(box);
    wrap.appendChild(header);

    const { codes, aggregate } = this._codeRows(views);
    if (codes.length === 0) {
      wrap.appendChild(this._createStateBox('Chưa có dữ liệu khách hàng.'));
      return wrap;
    }
    const render = { overview: '_renderOverviewTab', usage: '_renderUsageTab', bills: '_renderBillsTab', meter: '_renderMeterTab' }[mode];
    this[render](wrap, codes, aggregate);
    return wrap;
  }

  _section(title) {
    const container = document.createElement('div');
    container.className = 'table-container';
    if (title) {
      const titleEl = document.createElement('div');
      titleEl.className = 'section-title';
      titleEl.textContent = title;
      container.appendChild(titleEl);
    }
    return container;
  }

  // A table whose rows stack as label/value cards on a narrow container. columns: header texts.
  _stackTable(columns) {
    const table = document.createElement('table');
    table.className = 'bill-table stack-table';
    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');
    columns.forEach((text) => {
      const th = document.createElement('th');
      th.setAttribute('scope', 'col');
      th.textContent = text;
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    table.appendChild(tbody);
    table.addRow = (cells, className = '') => {
      const tr = document.createElement('tr');
      if (className) tr.className = className;
      cells.forEach((cell, index) => {
        const td = document.createElement('td');
        td.setAttribute('data-label', columns[index] || '');
        if (cell && typeof cell === 'object' && cell.tagName) {
          td.appendChild(cell);
        } else {
          td.textContent = cell === null || cell === undefined ? '—' : String(cell);
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
      return tr;
    };
    return table;
  }

  _banner(kind, text, link) {
    const banner = document.createElement('div');
    banner.className = `mode-banner banner-${kind}`;
    banner.setAttribute('role', 'status');
    const span = document.createElement('span');
    span.textContent = text;
    banner.appendChild(span);
    const path = this._config && typeof this._config.bills_path === 'string' ? this._config.bills_path.trim() : '';
    // Only an in-app path is linked: never a scheme such as javascript:.
    if (link && /^\/[A-Za-z0-9_\-./]*$/.test(path)) {
      const anchor = document.createElement('a');
      anchor.className = 'banner-link';
      anchor.setAttribute('href', path);
      anchor.textContent = link;
      banner.appendChild(anchor);
    }
    return banner;
  }

  _num(value) {
    if (value === null || value === undefined || value === '') return null;
    const num = Number(value);
    return Number.isFinite(num) ? num : null;
  }

  // The newest day any code has, the sum over the codes that have it, and how many lack it.
  _newestDayAcross(codes) {
    let date = '';
    codes.forEach((row) => {
      const found = this._newestDay(row.daily);
      if (found && found.date > date) date = found.date;
    });
    return date ? { date, ...this._dayAcross(codes, date) } : null;
  }

  _dayAcross(codes, date) {
    let sum = 0;
    let have = 0;
    codes.forEach((row) => {
      const match = row.daily.find((item) => this._isoDate(item && item.date) === date && this._finiteKwh(item) !== null);
      if (match) {
        sum += this._finiteKwh(match);
        have += 1;
      }
    });
    return { sum: have > 0 ? sum : null, missing: codes.length - have, total: codes.length };
  }

  _latestDayTiles(codes) {
    const newest = this._newestDayAcross(codes);
    if (!newest) {
      return [
        this._noteTile('Ngày mới nhất', '—', 'Chưa có dữ liệu ngày'),
        this._noteTile('Ngày trước đó', '—', ''),
      ];
    }
    const before = this._shiftIsoDate(newest.date, -1);
    const prior = this._dayAcross(codes, before);
    return [
      this._noteTile(`Ngày mới nhất (${this._formatDateLabel(newest.date)})`, this._formatKwh(newest.sum), this._missingCodesNote(newest.missing, newest.total)),
      this._noteTile(
        `Ngày trước đó (${this._formatDateLabel(before)})`, this._formatKwh(prior.sum),
        prior.sum === null ? '' : this._missingCodesNote(prior.missing, prior.total),
      ),
    ];
  }

  // Sum of the codes' states of one entity key; null while none is a number.
  _sumStates(codes, pick) {
    let sum = 0;
    let have = 0;
    codes.forEach((row) => {
      const value = this._num(pick(row));
      if (value !== null) {
        sum += value;
        have += 1;
      }
    });
    return have > 0 ? sum : null;
  }

  _costState(row) {
    const id = row.view.cost_entity;
    const entity = id && this._hass.states[id];
    if (!entity || entity.state === 'unavailable' || entity.state === 'unknown') return null;
    const costAttrs = typeof entity.attributes === 'object' && entity.attributes !== null ? entity.attributes : {};
    return this._sameCustomerScope(row.attrs, costAttrs) ? entity.state : null;
  }

  _rollingMonthTiles(codes) {
    const prefix = this._monthPrefix();
    const monthNo = prefix.slice(5, 7);
    const hasDays = codes.some((row) => this._hasMonthDays(row.daily, prefix));
    const kwh = this._sumStates(codes, (row) => (row.usable ? row.state.state : null));
    const cost = this._sumStates(codes, (row) => this._costState(row));
    const kwhShown = hasDays || (kwh !== null && kwh > 0);
    return [
      kwhShown ? this._noteTile(`Tháng ${monthNo}`, this._formatKwh(kwh), '', true) : this._noteTile(`Tháng ${monthNo}`, 'Chưa có số', 'EVN đăng trễ ~1 ngày', true),
      (hasDays || (cost !== null && cost > 0))
        ? this._noteTile(`Chi phí ước tính T${monthNo}`, this._formatVnd(cost), '', true)
        : this._noteTile(`Chi phí ước tính T${monthNo}`, 'Chưa có số', 'EVN đăng trễ ~1 ngày', true),
    ];
  }

  _pickedMonth() {
    const today = this._zonedIsoDate(Date.now());
    return this._monthOptions(today).find((o) => o.value === this._selectedMonth) || null;
  }

  // --- usage ---
  _renderUsageTab(wrap, codes, aggregate) {
    const chartRow = aggregate || codes[0];
    const picked = this._pickedMonth();
    const chartMonthly = chartRow.bills;
    const chart = chartRow.usable
      ? this._renderChartSection(chartRow.daily, chartRow.attrs, chartRow.state.last_updated, chartMonthly)
      : this._createStateBox('Không có dữ liệu để vẽ biểu đồ.');
    const grid = document.createElement('div');
    grid.className = 'metrics-grid';
    const tiles = picked ? this._pastMonthTiles(codes, chartRow, picked) : [...this._latestDayTiles(codes), ...this._rollingMonthTiles(codes)];
    tiles.forEach((tile) => grid.appendChild(tile));
    wrap.appendChild(grid);
    wrap.appendChild(chart);
  }

  _monthStats(chartRow, picked) {
    const ids = this._statisticsIds(chartRow.attrs);
    const today = this._zonedIsoDate(Date.now());
    const key = ['month', ids.energy, picked.value, today, picked.value === today.slice(0, 7) ? (chartRow.state.last_updated || '') : ''].join('|');
    const entry = ids.energy ? this._compareCache.get(key) : null;
    return entry && entry.status === 'ready' ? entry.data : null;
  }

  _monthBills(codes, picked) {
    return codes.map((row) => row.bills.filter((bill) => Number(bill.year) === picked.year && Number(bill.month) === picked.month));
  }

  _costStatistics(chartRow, picked) {
    const ids = this._statisticsIds(chartRow.attrs);
    if (!ids.cost || !this._hass || typeof this._hass.callWS !== 'function') return null;
    const today = this._zonedIsoDate(Date.now());
    const key = ['monthcost', ids.cost, picked.value, today, picked.value === today.slice(0, 7) ? (chartRow.state.last_updated || '') : ''].join('|');
    let entry = this._compareCache.get(key);
    if (!entry) {
      entry = { status: 'loading' };
      this._compareCache.set(key, entry);
      while (this._compareCache.size > 16) {
        this._compareCache.delete(this._compareCache.keys().next().value);
      }
      const nextYear = picked.month === 12 ? picked.year + 1 : picked.year;
      const nextMonth = picked.month === 12 ? 1 : picked.month + 1;
      Promise.resolve()
        .then(() => this._hass.callWS({
          type: 'recorder/statistics_during_period',
          start_time: this._zonedMidnight(picked.year, picked.month, 1).toISOString(),
          end_time: this._zonedMidnight(nextYear, nextMonth, 1).toISOString(),
          statistic_ids: [ids.cost],
          period: 'day',
          types: ['change'],
        }))
        .then((result) => {
          const rows = this._statRowsToDays(result && result[ids.cost]);
          this._compareCache.set(key, rows.length > 0 ? { status: 'ready', data: rows } : { status: 'error' });
        })
        .catch(() => {
          this._compareCache.set(key, { status: 'error' });
        })
        .then(() => this.render());
    }
    return entry;
  }

  _pastMonthTiles(codes, chartRow, picked) {
    const mm = String(picked.month).padStart(2, '0');
    const rows = this._monthStats(chartRow, picked);
    const lastDay = new Date(picked.year, picked.month, 0).getDate();
    let total = null;
    let known = [];
    if (rows) {
      const byDate = new Map();
      rows.forEach((row) => {
        const num = this._num(row && row.value);
        if (num !== null && String(row.date).startsWith(`${picked.year}-${mm}-`)) byDate.set(String(row.date), num);
      });
      known = [...byDate.entries()];
      total = known.reduce((sum, [, value]) => sum + value, 0);
    }
    const peak = known.reduce((best, item) => (!best || item[1] > best[1] ? item : best), null);
    const bills = this._monthBills(codes, picked);
    const billed = bills.filter((list) => list.length > 0);
    const amounts = billed.map((list) => list.reduce((sum, bill) => sum + (this._num(bill.total_amount) || 0), 0));
    const billKwh = billed.reduce((sum, list) => sum + (this._num(list[0].total_kwh) || 0), 0);
    let moneyTile;
    if (billed.length > 0) {
      const lacking = codes.length - billed.length;
      moneyTile = this._noteTile(
        `Tiền điện T${mm}`, this._formatVnd(amounts.reduce((sum, v) => sum + v, 0)),
        `theo hoá đơn kỳ ${picked.month} · ${this._formatKwh(billKwh)}${lacking > 0 ? ` · thiếu ${lacking}/${codes.length} hoá đơn` : ''}`, true,
      );
    } else {
      const cost = this._costStatistics(chartRow, picked);
      const costRows = cost && cost.status === 'ready' ? cost.data : null;
      const estimate = costRows ? costRows.reduce((sum, row) => sum + (this._num(row && row.value) || 0), 0) : null;
      moneyTile = this._noteTile(
        `Tiền điện T${mm}`, estimate !== null && estimate > 0 ? `≈ ${this._formatVnd(estimate)}` : '—',
        cost && cost.status === 'loading' ? 'Đang tải…' : 'chưa có hoá đơn', true,
      );
    }
    return [
      this._noteTile(`Tổng T${mm}`, total === null ? '—' : this._formatKwh(total), total === null ? 'Chưa có lịch sử' : `${known.length}/${lastDay} ngày`, true),
      moneyTile,
      this._noteTile('TB/ngày', known.length > 0 ? this._formatKwh(total / known.length) : '—', ''),
      this._noteTile('Ngày cao nhất', peak ? this._formatKwh(peak[1]) : '—', peak ? this._formatDateLabel(peak[0]) : ''),
    ];
  }

  // --- bills ---
  _billPeriods(codes) {
    const found = new Map();
    codes.forEach((row) => row.bills.forEach((bill) => {
      const year = Number(bill.year);
      const month = Number(bill.month);
      if (Number.isInteger(year) && Number.isInteger(month) && month >= 1 && month <= 12) {
        found.set(`${year}-${String(month).padStart(2, '0')}`, { value: `${year}-${String(month).padStart(2, '0')}`, year, month });
      }
    }));
    return [...found.values()].sort((a, b) => (a.value < b.value ? 1 : -1))
      .map((p) => ({ ...p, label: `Tháng ${String(p.month).padStart(2, '0')}/${p.year}` }));
  }

  // One code's bill for a period: reconciliation from the row that has it, amount and payment over all rows.
  _periodBill(row, period) {
    const list = row.bills.filter((bill) => Number(bill.year) === period.year && Number(bill.month) === period.month);
    if (list.length === 0) return null;
    const main = list.find((bill) => typeof bill.reconcile_status === 'string' && bill.reconcile_status) || list[0];
    const states = list.map((bill) => this._paymentState(bill));
    const worst = states.find((s) => s.kind === 'unpaid') || states.find((s) => s.kind === 'unknown') || states[0];
    const dues = states.filter((s) => s.kind === 'unpaid' && s.due).map((s) => s.due).sort();
    return {
      main,
      amount: list.reduce((sum, bill) => sum + (this._num(bill.total_amount) || 0), 0),
      payment: { ...worst, due: dues[0] || worst.due, unchecked: states.some((s) => s.unchecked) },
      status: typeof main.reconcile_status === 'string' ? main.reconcile_status : '',
    };
  }

  _daysInclusive(startIso, endIso) {
    const start = this._isoDate(startIso);
    const end = this._isoDate(endIso);
    if (!start || !end || end < start) return null;
    return Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86400000) + 1;
  }

  _reasonText(bill, error) {
    if (error) return 'không kiểm được';
    if (!bill) return 'chưa có hoá đơn kỳ này';
    const main = bill.main;
    const total = this._daysInclusive(main.period_start, main.period_end);
    const missing = this._num(main.missing_days);
    switch (bill.status) {
      case 'incomplete':
        return total !== null && missing !== null ? `chưa đủ ngày (${total - missing}/${total})` : 'chưa đủ ngày';
      case 'no_kwh': return 'chưa có kWh hoá đơn';
      case 'no_period': return 'hoá đơn không có kỳ rõ ràng';
      case '': return 'chưa đối chiếu';
      default: return '';
    }
  }

  // Complete = a result that adds up, and finite bill, collected and difference figures.
  _isComplete(bill) {
    if (!bill) return false;
    const main = bill.main;
    return ['match', 'mismatch', 'boundary'].includes(bill.status)
      && this._num(main.total_kwh) !== null && this._num(main.collected_kwh) !== null && this._num(main.diff_kwh) !== null;
  }

  _resultPill(bill, error) {
    const pill = document.createElement('span');
    let kind = 'muted';
    let text = this._reasonText(bill, error);
    if (!error && bill) {
      if (bill.status === 'match') { kind = 'ok'; text = 'Khớp'; }
      else if (bill.status === 'boundary') { kind = 'ok'; text = 'Khớp theo cặp kỳ'; }
      else if (bill.status === 'mismatch') { kind = 'warn'; text = 'Lệch'; }
      else if (text) { text = text.charAt(0).toUpperCase() + text.slice(1); }
    } else if (text) {
      text = text.charAt(0).toUpperCase() + text.slice(1);
    }
    pill.className = `pill pill-${kind}`;
    pill.textContent = text || '—';
    return pill;
  }

  _periodText(main) {
    const start = this._isoDate(main.period_start);
    const end = this._isoDate(main.period_end);
    return start && end ? `${this._formatDateLabel(start)}–${this._formatDateLabel(end)}` : '—';
  }

  _detailCell(label, bill, error) {
    const cell = document.createElement('div');
    cell.className = 'code-cell';
    const name = document.createElement('span');
    name.className = 'code-name';
    name.textContent = label;
    cell.appendChild(name);
    if (bill && !error) {
      const details = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = 'Chi tiết';
      details.appendChild(summary);
      const main = bill.main;
      const total = this._daysInclusive(main.period_start, main.period_end);
      const missing = this._num(main.missing_days);
      const lines = [];
      if (total !== null && missing !== null) lines.push(`Đã thu thập ${total - missing}/${total} ngày`);
      if (bill.status === 'mismatch') lines.push(`Lệch ${this._formatSignedKwh(main.diff_kwh)} so với hoá đơn`);
      if (bill.status === 'boundary') lines.push('Lệch ranh giới kỳ, đã bù với kỳ liền kề');
      if (bill.status === 'incomplete') lines.push('Thiếu dữ liệu ngày nên chưa kết luận');
      if (bill.status === 'no_kwh') lines.push('Hoá đơn chưa có kWh để so');
      const note = document.createElement('div');
      note.className = 'detail-note';
      note.textContent = lines.length > 0 ? lines.join(' · ') : 'Không có thêm chi tiết';
      details.appendChild(note);
      cell.appendChild(details);
    }
    return cell;
  }

  _renderBillsTab(wrap, codes) {
    const periods = this._billPeriods(codes);
    if (periods.length === 0) {
      wrap.appendChild(this._createStateBox('Chưa có thông tin hoá đơn'));
      return;
    }
    const period = periods.find((p) => p.value === this._selectedBillPeriod) || periods[0];

    const picker = document.createElement('select');
    picker.className = 'month-selector period-selector';
    picker.setAttribute('aria-label', 'Chọn kỳ hoá đơn');
    periods.forEach((p) => {
      const option = document.createElement('option');
      option.value = p.value;
      option.textContent = p.label;
      if (p.value === period.value) option.selected = true;
      picker.appendChild(option);
    });
    picker.addEventListener('change', (e) => {
      this._selectedBillPeriod = e && e.target ? e.target.value : picker.value;
      this.render();
    });
    const controls = document.createElement('div');
    controls.className = 'header-controls';
    controls.appendChild(picker);
    wrap.appendChild(controls);

    const items = codes.map((row) => ({ row, bill: this._periodBill(row, period) }));
    const complete = items.filter((item) => this._isComplete(item.bill) && !item.row.error);
    const sum = (pick) => complete.reduce((total, item) => total + pick(item.bill.main), 0);
    const billed = items.filter((item) => item.bill);
    const totalAmount = billed.reduce((total, item) => total + item.bill.amount, 0);
    const unpaidCodes = billed.filter((item) => item.bill.payment.kind === 'unpaid').length;
    const withoutBill = items.length - billed.length;

    const grid = document.createElement('div');
    grid.className = 'metrics-grid';
    grid.appendChild(this._noteTile(
      'Tổng tiền', billed.length > 0 ? this._formatVnd(totalAmount) : '—',
      `${unpaidCodes} mã chưa thanh toán${withoutBill > 0 ? ` · ${withoutBill} mã chưa có hoá đơn` : ''}`, true,
    ));
    const completeNote = `${complete.length}/${items.length} mã đủ dữ liệu`;
    grid.appendChild(this._noteTile('kWh hoá đơn', complete.length > 0 ? this._formatKwh(sum((m) => this._num(m.total_kwh))) : '—', completeNote));
    grid.appendChild(this._noteTile('kWh thu thập', complete.length > 0 ? this._formatKwh(sum((m) => this._num(m.collected_kwh))) : '—', completeNote));
    grid.appendChild(this._noteTile('Chênh lệch', complete.length > 0 ? this._formatSignedKwh(sum((m) => this._num(m.diff_kwh))) : '—', completeNote));
    wrap.appendChild(grid);

    const excluded = items.filter((item) => item.row.error || !this._isComplete(item.bill));
    if (excluded.length > 0) {
      const note = document.createElement('div');
      note.className = 'mode-banner banner-info excluded-note';
      note.textContent = `Chưa tính vào tổng kWh: ${excluded.map((item) => `${item.row.label} (${this._reasonText(item.bill, item.row.error) || 'chưa đủ dữ liệu'})`).join('; ')}`;
      wrap.appendChild(note);
    }

    const section = this._section(`Hoá đơn ${period.label}`);
    const table = this._stackTable(['Khách hàng', 'Kỳ', 'Hoá đơn', 'Thu thập', 'Chênh lệch', 'Kết quả', 'Số tiền', 'Thanh toán']);
    items.forEach(({ row, bill }) => {
      const main = bill ? bill.main : null;
      table.addRow([
        this._detailCell(row.label, bill, row.error),
        main ? this._periodText(main) : '—',
        main ? this._formatKwh(main.total_kwh) : '—',
        main && main.collected_kwh !== null && main.collected_kwh !== undefined ? this._formatKwh(main.collected_kwh) : '—',
        main && main.diff_kwh !== null && main.diff_kwh !== undefined ? this._formatSignedKwh(main.diff_kwh) : '—',
        this._resultPill(bill, row.error),
        bill ? this._formatVnd(bill.amount) : '—',
        bill ? this._paymentPill({ payment_status: bill.payment.kind, paid_on: bill.payment.paidOn, due_date: bill.payment.due, payment_checked: bill.payment.unchecked ? false : true }, true) : '—',
      ]);
    });
    table.addRow([
      'Tổng', '', complete.length > 0 ? this._formatKwh(sum((m) => this._num(m.total_kwh))) : '—',
      complete.length > 0 ? this._formatKwh(sum((m) => this._num(m.collected_kwh))) : '—',
      complete.length > 0 ? this._formatSignedKwh(sum((m) => this._num(m.diff_kwh))) : '—', '',
      billed.length > 0 ? this._formatVnd(totalAmount) : '—', '',
    ], 'total-row');
    section.appendChild(table);
    wrap.appendChild(section);

    const help = document.createElement('div');
    help.className = 'compare-hint';
    help.textContent = 'Kỳ hoá đơn [đầu, cuối] được đối chiếu với các ngày [đầu − 1, cuối − 1] đã thu thập. Chỉ tính vào tổng những mã có kết quả Khớp, Lệch hoặc Lệch ranh giới và đủ số liệu.';
    wrap.appendChild(help);
  }

  // --- meter ---
  _renderMeterTab(wrap, codes) {
    const section = this._section('Chỉ số công tơ theo kỳ hoá đơn mới nhất');
    const table = this._stackTable(['Khách hàng', 'Chỉ số đầu kỳ', 'Chỉ số cuối kỳ', 'Ngày chốt', 'Sản lượng kỳ', 'Chỉ số mới nhất']);
    codes.forEach((row) => {
      const dated = row.bills.filter((bill) => this._isoDate(bill.period_end));
      const newest = dated.sort((a, b) => (this._isoDate(a.period_end) < this._isoDate(b.period_end) ? 1 : -1))[0] || null;
      const latest = this._num(row.attrs.latest_reading);
      const latestDate = typeof row.attrs.latest_reading_date === 'string' ? row.attrs.latest_reading_date : '';
      table.addRow([
        row.label,
        newest && this._num(newest.index_start) !== null ? this._formatNumber(newest.index_start, 1) : '—',
        newest && this._num(newest.index_end) !== null ? this._formatNumber(newest.index_end, 1) : '—',
        newest ? this._formatDateLabel(this._isoDate(newest.period_end)) : '—',
        newest ? this._formatKwh(newest.total_kwh) : '—',
        latest !== null ? `${this._formatNumber(latest, 1)}${latestDate ? ` (${latestDate.slice(0, 5)})` : ''}` : '—',
      ]);
    });
    section.appendChild(table);
    wrap.appendChild(section);
  }

  // --- overview ---
  _zonedClock(ms) {
    const date = this._zonedIsoDate(ms);
    const zone = this._timeZone();
    let hour;
    let minute;
    try {
      if (zone) {
        const p = this._zonedParts(ms, zone);
        hour = p.hour % 24;
        minute = p.minute;
      }
    } catch (e) {
      hour = undefined;
    }
    if (hour === undefined) {
      const d = new Date(ms);
      hour = d.getHours();
      minute = d.getMinutes();
    }
    const pad = (n) => String(n).padStart(2, '0');
    return { date, hm: `${pad(hour)}:${pad(minute)}` };
  }

  _outageText(row) {
    const start = Date.parse(row.attrs.next_planned_outage);
    const end = Date.parse(row.attrs.outage_end);
    if (!Number.isFinite(start)) return '';
    const from = this._zonedClock(start);
    const to = Number.isFinite(end) ? this._zonedClock(end) : null;
    const range = to ? `${from.hm}–${to.hm}` : from.hm;
    return `${this._formatDateLabel(from.date)} ${range}`;
  }

  _upcomingOutages(codes, days) {
    const now = Date.now();
    return codes
      .filter((row) => row.usable && typeof row.attrs.next_planned_outage === 'string')
      .map((row) => ({ row, start: Date.parse(row.attrs.next_planned_outage), end: Date.parse(row.attrs.outage_end) }))
      .filter((item) => Number.isFinite(item.start) && item.start - now <= days * 86400000 && (Number.isFinite(item.end) ? item.end : item.start) >= now)
      .sort((a, b) => a.start - b.start);
  }

  _newestBill(row) {
    const keyed = row.bills.filter((bill) => Number.isInteger(Number(bill.year)) && Number.isInteger(Number(bill.month)));
    return keyed.sort((a, b) => (Number(a.year) * 12 + Number(a.month) < Number(b.year) * 12 + Number(b.month) ? 1 : -1))[0] || null;
  }

  _renderOverviewTab(wrap, codes) {
    const usable = codes.filter((row) => row.usable);
    const unpaidRows = usable.filter((row) => this._num(row.attrs.unpaid_count) > 0);
    if (unpaidRows.length > 0) {
      const count = unpaidRows.reduce((sum, row) => sum + this._num(row.attrs.unpaid_count), 0);
      const owed = unpaidRows.reduce((sum, row) => sum + (this._num(row.attrs.unpaid_amount) || 0), 0);
      const dues = unpaidRows.map((row) => this._isoDate(row.attrs.next_due_date)).filter(Boolean).sort();
      wrap.appendChild(this._banner(
        'warn',
        `${count} hoá đơn chưa thanh toán · còn nợ ${this._formatVnd(owed)}${dues.length > 0 ? ` · hạn gần nhất ${this._formatDateLabel(dues[0])}` : ''}`,
        'Xem hoá đơn',
      ));
    }
    const outages = this._upcomingOutages(codes, 7);
    if (outages.length > 0) {
      const first = outages[0];
      wrap.appendChild(this._banner(
        'info',
        `Dự kiến ngừng cấp điện: ${first.row.label} · ${this._outageText(first.row)}${outages.length > 1 ? ` (+${outages.length - 1} lịch khác)` : ''}`,
        '',
      ));
    }

    const grid = document.createElement('div');
    grid.className = 'metrics-grid';
    const newest = this._latestDayTiles(codes)[0];
    grid.appendChild(newest);
    const monthTile = this._rollingMonthTiles(codes)[0];
    grid.appendChild(monthTile.children[0].textContent.startsWith('Tháng')
      ? this._renamedTile(monthTile, 'Tháng hiện tại') : monthTile);
    const projections = usable.map((row) => row.attrs.projection).filter((p) => p && typeof p === 'object');
    const amounts = projections.map((p) => this._num(p.projected_amount));
    const projectionKnown = usable.length > 0 && projections.length === usable.length && amounts.every((v) => v !== null);
    const hints = projections
      .filter((p) => this._num(p.kwh_to_next_tier) !== null && this._num(p.tier) !== null)
      .sort((a, b) => a.kwh_to_next_tier - b.kwh_to_next_tier);
    const hintRow = hints.length > 0 ? usable.find((row) => row.attrs.projection === hints[0]) : null;
    const projectionNote = `dự kiến, không phải hoá đơn${hintRow ? ` · ${hintRow.label}: còn ${this._formatNumber(hints[0].kwh_to_next_tier, 1)} kWh nữa sang bậc ${Number(hints[0].tier) + 1}` : ''}`;
    grid.appendChild(this._noteTile(
      'Dự kiến kỳ này', projectionKnown ? `≈ ${this._formatVnd(amounts.reduce((sum, v) => sum + v, 0))}` : '—',
      projectionKnown ? projectionNote : 'dự kiến: chưa đủ số liệu cho mọi mã', true,
    ));
    const newestBills = usable.map((row) => ({ row, bill: this._newestBill(row) })).filter((item) => item.bill);
    const matched = newestBills.filter((item) => item.bill.reconcile_status === 'match' || item.bill.reconcile_status === 'boundary').length;
    grid.appendChild(this._noteTile('Đối chiếu kỳ gần nhất', newestBills.length > 0 ? `${matched}/${newestBills.length} khớp` : '—', ''));
    wrap.appendChild(grid);

    const section = this._section('Từng khách hàng');
    const table = this._stackTable(['Khách hàng', 'Ngày mới nhất', 'Dữ liệu đến', 'Hoá đơn gần nhất', 'Thanh toán', 'Đối chiếu', 'Ngừng cấp điện kế tiếp']);
    codes.forEach((row) => {
      const found = this._newestDay(row.daily);
      const bill = this._newestBill(row);
      const periodBill = bill ? this._periodBill(row, { year: Number(bill.year), month: Number(bill.month) }) : null;
      table.addRow([
        row.label,
        found ? this._formatKwh(found.value) : '—',
        found ? this._formatDateLabel(found.date) : '—',
        periodBill ? this._formatVnd(periodBill.amount) : '—',
        periodBill ? this._paymentPill({ payment_status: periodBill.payment.kind, paid_on: periodBill.payment.paidOn, due_date: periodBill.payment.due, payment_checked: periodBill.payment.unchecked ? false : true }, true) : (row.error ? 'Không kiểm được' : '—'),
        this._resultPill(periodBill, row.error),
        row.usable && typeof row.attrs.next_planned_outage === 'string' ? this._outageText(row) : '—',
      ]);
    });
    section.appendChild(table);
    wrap.appendChild(section);
  }

  _renamedTile(tile, label) {
    tile.children[0].textContent = label;
    return tile;
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
      .view-selector, .month-selector {
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
      .view-selector:focus, .month-selector:focus {
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
      .bar-selected {
        stroke: var(--primary-text-color, #111827);
        stroke-width: 1.5;
        opacity: 1;
      }
      .compare-tile {
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .compare-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 12px;
      }
      @media (max-width: 720px) {
        .compare-grid {
          grid-template-columns: 1fr;
        }
      }
      .compare-delta {
        margin-top: 4px;
        font-size: 12px;
        font-variant-numeric: tabular-nums;
        color: var(--secondary-text-color, #6b7280);
      }
      .month-total, .recon-line {
        font-size: 12px;
        font-variant-numeric: tabular-nums;
        color: var(--secondary-text-color, #6b7280);
        margin-bottom: 6px;
      }
      .recon-line {
        margin: 8px 0 0;
      }
      .recon-warn {
        color: var(--warning-color, #b45309);
        font-weight: 600;
      }
      .compare-hint, .compare-muted {
        font-size: 12px;
        color: var(--secondary-text-color, #6b7280);
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
      .pill {
        display: inline-block;
        padding: 2px 6px;
        border-radius: 4px;
        font-size: 11px;
        font-weight: 500;
      }
      .pill-unknown, .pill-muted {
        background: var(--secondary-background-color, rgba(0, 0, 0, 0.06));
        color: var(--secondary-text-color, #4b5563);
      }
      .pill-ok {
        background: rgba(16, 185, 129, 0.15);
        color: #047857;
      }
      .pill-warn {
        background: rgba(245, 158, 11, 0.18);
        color: #92400e;
      }
      .metric-note {
        margin-top: 4px;
        font-size: 11px;
        color: var(--secondary-text-color, #6b7280);
      }
      .mode-banner {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 8px;
        border-radius: 6px;
        padding: 8px 12px;
        font-size: 12px;
        border: 1px solid var(--divider-color, rgba(0, 0, 0, 0.12));
      }
      .banner-warn {
        background: #fffbeb;
        color: #92400e;
        border-color: #fcd34d;
      }
      .banner-info {
        background: var(--secondary-background-color, rgba(0, 0, 0, 0.04));
        color: var(--primary-text-color, #111827);
      }
      .banner-link {
        color: var(--evn-accent, #1976d2);
        font-weight: 600;
      }
      .tab-layout {
        display: flex;
        flex-direction: column;
        gap: 12px;
        min-width: 0;
      }
      .tab-layout .table-container {
        container-type: inline-size;
        overflow-x: visible;
      }
      .code-cell details summary {
        cursor: pointer;
        font-size: 11px;
        color: var(--secondary-text-color, #6b7280);
      }
      .detail-note {
        font-size: 11px;
        color: var(--secondary-text-color, #6b7280);
        margin-top: 4px;
      }
      .code-name {
        font-weight: 600;
      }
      .total-row td {
        font-weight: 600;
        border-top: 1px solid var(--divider-color, rgba(0, 0, 0, 0.2));
      }
      @container (max-width: 560px) {
        .stack-table thead {
          display: none;
        }
        .stack-table, .stack-table tbody, .stack-table tr {
          display: block;
          width: 100%;
        }
        .stack-table tr {
          border: 1px solid var(--divider-color, rgba(0, 0, 0, 0.12));
          border-radius: 8px;
          padding: 6px 10px;
          margin-bottom: 8px;
        }
        .stack-table td {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          border-bottom: none;
          padding: 4px 0;
        }
        .stack-table td::before {
          content: attr(data-label);
          color: var(--secondary-text-color, #6b7280);
          flex: 0 0 auto;
        }
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
