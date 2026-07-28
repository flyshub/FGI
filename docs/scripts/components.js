// =============================================
// FGI Web Frontend — DOM Components
// =============================================

// HTML 转义，防止后端数据污染前端 DOM
function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// 前瞻窗口（与 backend signal_report.py 保持一致）
const HORIZON_DAYS = [5, 20, 60];

// ── Date picker setup ──
function setupDatePicker(defaultDate) {
  const picker = document.getElementById('date-picker');
  const goBtn = document.getElementById('date-go-btn');

  // Build date list for datalist
  const dates = state.allDates.map(d => d.date);
  picker.value = defaultDate;
  picker.max = dates[dates.length - 1];
  picker.min = dates[0];

  function doSwitch() {
    const dateStr = picker.value;
    if (dateStr && state.allDatesIndex[dateStr]) {
      switchToDate(dateStr);
    }
  }

  picker.addEventListener('change', doSwitch);
  goBtn.addEventListener('click', doSwitch);
}

// ── Dashboard ──
function renderDashboard(data) {
  if (!data) return;
  const fgi = data.fgi_final;
  const zone = getZone(fgi);

  document.getElementById('fgi-value').textContent = fgi != null ? fgi.toFixed(1) : '--';
  document.getElementById('fgi-value').style.color = zone.color;
  const zoneEl = document.getElementById('fgi-zone');
  zoneEl.textContent = zone.label;
  zoneEl.style.color = zone.color;

  const trendEl = document.getElementById('stat-trend');
  trendEl.textContent = data.trend === '→' ? '持平' : `${data.trend} ${data.delta != null ? Math.abs(data.delta).toFixed(1) : '--'}`;
  trendEl.style.color = data.trend === '↑' ? '#d32f2f' : data.trend === '↓' ? '#2e7d32' : '#9e9e9e';

  const hs = data.health_score;
  const healthFill = document.getElementById('health-fill');
  if (hs != null) {
    healthFill.style.width = hs + '%';
    healthFill.style.background = hs >= 80 ? '#2e7d32' : hs >= 60 ? '#e65100' : '#d32f2f';
  } else {
    healthFill.style.width = '0%';
  }

  document.getElementById('stat-percentile').textContent = data.percentile_label || `分位 ${data.percentile}%`;

  const noteEl = document.getElementById('extreme-note');
  if (data.extreme_note) {
    noteEl.textContent = data.extreme_note;
    noteEl.style.display = 'block';
  } else {
    noteEl.style.display = 'none';
  }
}

// ── Decision Matrix ──
function renderDecisionMatrix(data) {
  const dm = data.decision_matrix;
  if (!dm) return;

  const container = document.getElementById('matrix-container');
  const rows = ['恐惧', '中性', '贪婪'];
  const cols = ['低估', '合理', '高估'];
  const QUADRANT_LABELS = {
    '恐惧_低估': '强烈关注', '恐惧_合理': '关注', '恐惧_高估': '观望',
    '中性_低估': '关注', '中性_合理': '中性', '中性_高估': '谨慎',
    '贪婪_低估': '观望', '贪婪_合理': '谨慎', '贪婪_高估': '强烈谨慎',
  };

  const curSent = dm.sentiment_tier;
  const curVal = dm.valuation_tier;

  let html = `<div class="matrix-cell matrix-header">情绪\\估值<sup>沪深300</sup></div>`;
  html += cols.map(v => `<div class="matrix-cell matrix-header">${v}</div>`).join('');

  rows.forEach(s => {
    html += `<div class="matrix-cell matrix-row-header">${escapeHtml(s)}</div>`;
    cols.forEach(v => {
      const key = `${s}_${v}`;
      const label = QUADRANT_LABELS[key] || '?';
      const hl = s === curSent && v === curVal;
      html += `<div class="matrix-cell${hl ? ' highlight' : ''}">${escapeHtml(label)}</div>`;
    });
  });
  container.innerHTML = html;

  const info = document.getElementById('matrix-info');
  info.innerHTML = `
    <p>象限：<strong>${escapeHtml(dm.quadrant)}</strong>（${escapeHtml(dm.sentiment_tier)} · ${escapeHtml(dm.valuation_tier)}）</p>
    <p style="color:#888;font-size:0.85rem">${escapeHtml(dm.advice)}</p>
  `;
}

// ── Signal Reference ──
function renderSignalRef(data) {
  const zc = data.zone_context;
  const el = document.getElementById('signal-ref');

  if (!zc) {
    el.innerHTML = '';
    return;
  }

  el.innerHTML = `
    <p style="margin-bottom:8px;font-size:0.9rem">
      FGI <strong>${escapeHtml(zc.zone)}</strong> 区间，历史上 <strong>${escapeHtml(zc.n)}</strong> 次（${escapeHtml(zc.pct)}%）
    </p>
    ${_signalTable(zc.horizons)}
  `;
}

function _signalTable(horizons) {
  if (!horizons || !Object.keys(horizons).length) return '';
  const rows = HORIZON_DAYS
    .map(h => {
      const d = horizons[String(h)];
      if (!d) return '';
      const meanStr = d.mean != null ? `${(d.mean * 100).toFixed(2)}%` : '--';
      const wrStr = d.win_rate != null ? `${(d.win_rate * 100).toFixed(0)}%` : '--';
      return `<tr><td>${escapeHtml(h)} 日</td><td>${meanStr}</td><td>${wrStr}</td></tr>`;
    })
    .join('');

  return `
    <table class="signal-table">
      <thead><tr><th>前瞻</th><th>平均涨跌</th><th>胜率</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

// ── Anchor ──
function renderAnchor(data) {
  const anchor = data.anchor;
  const el = document.getElementById('time-anchor');

  if (!anchor) {
    el.innerHTML = '';
    return;
  }

  let html = `<div class="anchor-card">`;
  html += `<p>📎 上次同向接近此水平（${escapeHtml(anchor.closest_fgi)}）是 <strong>${escapeHtml(anchor.closest_date)}</strong>`;
  if (anchor.forward_20d_return != null) {
    const arrow = anchor.forward_20d_return > 0 ? '📈' : '📉';
    const sign = anchor.forward_20d_return > 0 ? '+' : '';
    html += `，之后 20 日 ${arrow} <strong>${sign}${escapeHtml(anchor.forward_20d_return)}%</strong>`;
  }
  html += `</p>`;

  const dt = anchor.detail_table;
  if (dt && dt.length >= 2) {
    const vals = dt.map(r => r.forward_20);
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    html += `<div class="anchor-table-wrap"><table class="signal-table">
      <thead><tr><th>日期</th><th>FGI</th><th>后市20日</th></tr></thead>
      <tbody>`;
    dt.forEach(r => {
      const isClosest = r.date === anchor.closest_date;
      const arrow = r.forward_20 > 0 ? '📈' : '📉';
      const sign = r.forward_20 > 0 ? '+' : '';
      html += `<tr${isClosest ? ' style="font-weight:700;background:#fff3e0"' : ''}>
        <td>${escapeHtml(r.date)}</td><td>${escapeHtml(r.fgi)}</td>
        <td>${arrow} ${sign}${escapeHtml(r.forward_20)}%</td>
      </tr>`;
    });
    html += `<tr style="background:#ececec"><td colspan="2"><strong>区间</strong></td><td><strong>${min > 0 ? '+' : ''}${escapeHtml(min)}% ~ ${max > 0 ? '+' : ''}${escapeHtml(max)}%</strong></td></tr>`;
    html += `</tbody></table>`;
    html += `<p class="footnote" style="margin-top:4px">最近似日期以加粗标注</p></div>`;
  }
  html += `</div>`;
  el.innerHTML = html;
}

// ── Extreme Signals ──
function renderExtremeSignals(data) {
  const el = document.getElementById('extreme-signals');
  const ext = data.extreme_signals;
  if (!ext || (!ext.high.length && !ext.low.length)) {
    el.innerHTML = '<span style="color:#888;font-size:0.85rem">无极端信号</span>';
    return;
  }
  let html = '';
  if (ext.high.length) {
    html += ext.high.map(([, label, score]) =>
      `<span class="extreme-tag high">🔴 ${escapeHtml(label)} ${escapeHtml(score)}</span>`
    ).join(' ');
  }
  if (ext.low.length) {
    html += ext.low.map(([, label, score]) =>
      `<span class="extreme-tag low">🟢 ${escapeHtml(label)} ${escapeHtml(score)}</span>`
    ).join(' ');
  }
  el.innerHTML = html;
}

// ── Signal Report ──
function renderSignalReport(sr) {
  if (!sr || sr.error) {
    document.getElementById('signal-report-section').style.display = 'none';
    return;
  }
  document.getElementById('signal-report-section').style.display = '';

  // Rank IC
  const ic = sr.rank_ic;
  if (ic && ic.full) {
    const d = ic.full;
    document.getElementById('signal-report-ic').innerHTML = `
      <p><strong>Rank IC 分析</strong>（FGI vs 上证综指20日前瞻收益）</p>
      <p style="margin:6px 0;font-size:0.85rem;color:#666;border-left:3px solid #e0e0e0;padding-left:8px">💡 大白话：Rank IC 衡量「FGI 分数和未来市场涨跌之间的关联有多强」。IC 为负数说明 FGI 越高（市场越贪婪），未来越容易跌——这正是我们希望看到的反向预测能力。IR 衡量这种关联是否稳定。IC 胜率低于 50% 说明 FGI 大多数时候方向正确。</p>
      <table class="signal-table">
        <thead><tr><th>指标</th><th>值</th></tr></thead>
        <tbody>
          <tr><td>全样本 IC</td><td>${d.ic.toFixed(4)} (n=${d.n})</td></tr>
          <tr><td>IC 均值（20日滚动）</td><td>${d.mean.toFixed(4)}</td></tr>
          <tr><td>IR</td><td>${d.ir.toFixed(4)}</td></tr>
          <tr><td>IC 胜率</td><td>${(d.win_rate * 100).toFixed(1)}%</td></tr>
        </tbody>
      </table>
      ${d.rolling && d.rolling.length ? `
      <p style="margin-top:8px;font-size:0.85rem;color:#888">滚动 IC（每半年，3年回顾窗，最近5个）</p>
      <table class="signal-table">
        <thead><tr><th>日期</th><th>IC</th><th>IR</th><th>胜率</th></tr></thead>
        <tbody>${d.rolling.slice(-5).map(r => `
          <tr><td>${escapeHtml(r.date)}</td><td>${r.ic.toFixed(4)}</td><td>${r.ir != null ? r.ir.toFixed(3) : '—'}</td><td>${(r.win_rate * 100).toFixed(0)}%</td></tr>`).join('')}
        </tbody>
      </table>` : ''}
    `;
  }

  // Layer backtest
  const layers = sr.layer_backtest;
  if (layers && layers.full) {
    let html = '<p style="margin-top:12px"><strong>10档分层回测</strong>（FGI分档 × 前瞻收益）</p>';
    html += '<p style="margin:6px 0;font-size:0.85rem;color:#666;border-left:3px solid #e0e0e0;padding-left:8px">💡 大白话：把历史上所有交易日按 FGI 分数从低到高排成 10 档，第 1 档最恐慌，第 10 档最贪婪。理想情况下应该是「第 1 档涨最多、第 10 档跌最多」——像下楼梯一样严格递减。如果中段收益交叉，说明 FGI 在中间区域的区分度不够精细。</p>';
    HORIZON_DAYS.forEach(h => {
      const data = layers.full[String(h)];
      if (!data || !data.length) return;
      html += `<table class="signal-table" style="margin-top:6px">
        <caption style="caption-side:top;font-size:0.85rem;text-align:left;padding:4px 0">${escapeHtml(h)}日前瞻</caption>
        <thead><tr><th>分档</th><th>N</th><th>平均收益</th><th>胜率</th></tr></thead>
        <tbody>${data.map(d => `
          <tr><td>${escapeHtml(d.layer)}</td><td>${escapeHtml(d.n)}</td><td>${(d.mean_return * 100).toFixed(2)}%</td><td>${(d.win_rate * 100).toFixed(0)}%</td></tr>`).join('')}
        </tbody>
      </table>`;
    });
    document.getElementById('signal-report-layer').innerHTML = html;
  }

  // DCA
  const dca = sr.dca;
  if (dca && !dca.error) {
    const _pct = v => `${(v * 100).toFixed(2)}%`;
    document.getElementById('signal-report-dca').innerHTML = `
      <p style="margin-top:12px"><strong>逆情绪 DCA vs 等额定投</strong>（${escapeHtml(dca.n_months)}个月）</p>
      <p style="margin:6px 0;font-size:0.85rem;color:#666;border-left:3px solid #e0e0e0;padding-left:8px">💡 大白话：模拟两种每月定投策略。等额定投：每月固定投入 1 万元。逆情绪 DCA：市场越恐慌（FGI 低）投入越多（最多 2 万），越贪婪（FGI 高）投入越少。核心逻辑是「别人恐惧我加仓，别人贪婪我减仓」。对比两者的收益和风险，看 FGI 择时定投是否有价值。</p>
      <table class="signal-table">
        <thead><tr><th>策略</th><th>总收益</th><th>年化收益</th><th>最大回撤</th><th>夏普</th></tr></thead>
        <tbody>
          <tr><td>逆情绪 DCA</td><td>${_pct(dca.dca_total_return)}</td><td>${_pct(dca.dca_annualized)}</td><td>${_pct(dca.dca_max_drawdown)}</td><td>${dca.dca_sharpe.toFixed(2)}</td></tr>
          <tr><td>等额定投</td><td>${_pct(dca.benchmark_total_return)}</td><td>${_pct(dca.benchmark_annualized)}</td><td>${_pct(dca.benchmark_max_drawdown)}</td><td>${dca.benchmark_sharpe.toFixed(2)}</td></tr>
        </tbody>
      </table>
    `;
  }
}