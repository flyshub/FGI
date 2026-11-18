// =============================================
// FGI Web Frontend — Main Entry Point
// =============================================
const DATA_PATH = 'data/';

const FGI_ZONES = [
  { max: 20, label: '极度恐惧', color: '#d32f2f', bgColor: '#ffebee' },
  { max: 40, label: '恐惧',    color: '#ef5350', bgColor: '#fff3e0' },
  { max: 60, label: '中性',    color: '#9e9e9e', bgColor: '#f5f5f5' },
  { max: 80, label: '贪婪',    color: '#66bb6a', bgColor: '#e8f5e9' },
  { max: 100,label: '极度贪婪',color: '#2e7d32', bgColor: '#e8f5e9' },
];

function getZone(fgi) {
  if (fgi == null) return FGI_ZONES[2];
  for (const z of FGI_ZONES) {
    if (fgi < z.max) return z;
  }
  return FGI_ZONES[4];
}

function zoneColor(fgi) { return getZone(fgi).color; }

// Global state
let state = {
  allDates: [],        // pre-computed all dates data (fgi_all_dates.json)
  allDatesIndex: {},   // date string -> record
  history: [],         // fgi_history.json (for charts)
  signalReport: null,  // fgi_signal_report.json
  indicatorsHistory: null, // fgi_indicators_history.json
  latest: null,        // fgi_latest.json (版本号 / 数据日期，供页脚使用)
  optionalFailed: [],  // 可选数据文件加载失败的清单
};
let chartInstances = {};
let currentDate = null; // YYYY-MM-DD

function registerChart(id, chart) {
  chartInstances[id] = chart;
  return chart;
}
function unregisterChart(id) {
  delete chartInstances[id];
}
function getChart(id) { return chartInstances[id]; }

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${url}`);
  return res.json();
}

async function loadAllData() {
  try {
    const [allDates, history, signalReport, indicatorsHistory, latest] = await Promise.all([
      fetchJSON(DATA_PATH + 'fgi_all_dates.json'),
      fetchJSON(DATA_PATH + 'fgi_history.json'),
      // 可选文件：缺失不代表站点坏了，但要记下是哪个
      fetchJSON(DATA_PATH + 'fgi_signal_report.json').catch(() => {
        state.optionalFailed.push('信号报告');
        return null;
      }),
      fetchJSON(DATA_PATH + 'fgi_indicators_history.json').catch(() => {
        state.optionalFailed.push('指标历史');
        return null;
      }),
      fetchJSON(DATA_PATH + 'fgi_latest.json').catch(() => null),
    ]);
    state.allDates = allDates;
    state.history = history;
    state.signalReport = signalReport;
    state.indicatorsHistory = indicatorsHistory;
    state.latest = latest;

    // Build lookup index
    allDates.forEach(d => { state.allDatesIndex[d.date] = d; });
  } catch (err) {
    // 细节留 console，页面只说人话——不要把 HTTP 404 与文件路径摊给读者
    console.error('Data load error:', err);
    document.getElementById('loading-overlay').style.display = 'none';
    const el = document.getElementById('error-overlay');
    el.style.display = 'flex';
    document.getElementById('error-message').textContent =
      '无法读取站点数据文件，可能是部署尚未完成或网络中断。请稍后重试。';
    throw err;
  }
}

function renderFooterMeta() {
  const el = document.getElementById('footer-meta');
  if (!el) return;
  const parts = [];
  const version = state.latest && state.latest.app_version;
  if (version && version !== 'unknown') parts.push(`v${version}`);
  const date = state.latest && state.latest.date;
  if (date) parts.push(`数据日期 ${date}`);
  if (state.optionalFailed.length) parts.push(`部分数据缺失：${state.optionalFailed.join('、')}`);
  el.textContent = parts.join(' · ');
}

function getDataForDate(dateStr) {
  return state.allDatesIndex[dateStr] || null;
}

// ── Bootstrap ──────────────────────────────────────
function init() {
  document.getElementById('loading-overlay').style.display = 'none';

  if (!state.allDates.length) {
    document.querySelector('#app').innerHTML = `
      <div class="card" style="text-align:center;padding:40px">
        <h2>暂无数据</h2>
        <p>数据文件未找到</p>
      </div>`;
    return;
  }

  const latestDate = state.allDates[state.allDates.length - 1].date;

  // Initialize once-only charts (history, trend)
  initHistoryChart(state.history);
  initTrendChart(state.indicatorsHistory);
  enableHistoryChartClick();

  // Setup date picker
  setupDatePicker(latestDate);

  // Render initial — triggers markLine via _updateHistoryMarkLine
  switchToDate(latestDate);
  renderFooterMeta();
}

// ── Date switching (debounced) ──
let _switchTimer = null;

function switchToDate(dateStr) {
  // Debounce: only process the last request within 200ms
  if (_switchTimer) clearTimeout(_switchTimer);
  _switchTimer = setTimeout(() => _doSwitchDate(dateStr), 200);
}

function _doSwitchDate(dateStr) {
  if (dateStr === currentDate) return;
  const data = getDataForDate(dateStr);
  if (!data) {
    // 静默 return 会让用户以为点击没生效
    showDateHint(dateStr);
    return;
  }
  clearDateHint();
  currentDate = dateStr;

  // Update header label
  document.getElementById('last-update').textContent = `数据截止 ${dateStr}`;
  document.getElementById('current-date-label').textContent = data.zone || '';

  // Update quality badge
  const badge = document.getElementById('quality-badge');
  const hs = data.health_score;
  badge.textContent = `健康度 ${hs != null ? Math.round(hs) : '?'}`;
  badge.className = 'quality-badge ' + (hs >= 80 ? 'ok' : hs >= 60 ? 'warn' : 'bad');

  // Update date picker
  document.getElementById('date-picker').value = dateStr;

  // Render all sections with this date's data
  renderDashboard(data);
  renderDecisionMatrix(data);
  renderSignalRef(data);
  renderAnchor(data);
  renderExtremeSignals(data);
  initRadarChart(data.dimensions);
  initIndicatorChart(data.scores, data.statuses, data.extreme_signals);
  initDistributionChart(state.history, data.fgi_final, dateStr);
  _updateHistoryMarkLine(dateStr);
  renderSignalReport(state.signalReport);
}

// ── History chart mark line ──────────────────────────
function updateHistoryMarkLine(dateStr) {
  // Delegated to charts.js
  _updateHistoryMarkLine(dateStr);
}

// ── History chart init (called once) ────────────────

// Responsive
function resizeAll() {
  Object.values(chartInstances).forEach(ch => { if (ch) ch.resize(); });
}
let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(resizeAll, 200);
});

// Bootstrap
document.addEventListener('DOMContentLoaded', async () => {
  try {
    await loadAllData();
    init();
  } catch (_) { /* error overlay shown */ }
});
