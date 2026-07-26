// =============================================
// FGI Web Frontend — DOM Components
// =============================================

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
    html += `<div class="matrix-cell matrix-row-header">${s}</div>`;
    cols.forEach(v => {
      const key = `${s}_${v}`;
      const label = QUADRANT_LABELS[key] || '?';
      const hl = s === curSent && v === curVal;
      html += `<div class="matrix-cell${hl ? ' highlight' : ''}">${label}</div>`;
    });
  });
  container.innerHTML = html;

  const info = document.getElementById('matrix-info');
  info.innerHTML = `
    <p>象限：<strong>${dm.quadrant}</strong>（${dm.sentiment_tier} · ${dm.valuation_tier}）</p>
    <p style="color:#888;font-size:0.85rem">${dm.advice}</p>
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
      FGI <strong>${zc.zone}</strong> 区间，历史上 <strong>${zc.n}</strong> 次（${zc.pct}%）
    </p>
    ${_signalTable(zc.horizons)}
  `;
}

function _signalTable(horizons) {
  if (!horizons || !Object.keys(horizons).length) return '';
  const rows = [5, 20, 60]
    .map(h => {
      const d = horizons[String(h)];
      if (!d) return '';
      const meanStr = d.mean != null ? `${(d.mean * 100).toFixed(2)}%` : '--';
      const wrStr = d.win_rate != null ? `${(d.win_rate * 100).toFixed(0)}%` : '--';
      return `<tr><td>${h} 日</td><td>${meanStr}</td><td>${wrStr}</td></tr>`;
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
  html += `<p>📎 上次同向接近此水平（${anchor.closest_fgi}）是 <strong>${anchor.closest_date}</strong>`;
  if (anchor.forward_20d_return != null) {
    const arrow = anchor.forward_20d_return > 0 ? '📈' : '📉';
    html += `，之后 20 日 ${arrow} <strong>${anchor.forward_20d_return > 0 ? '+' : ''}${anchor.forward_20d_return}%</strong>`;
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
      html += `<tr${isClosest ? ' style="font-weight:700;background:#fff3e0"' : ''}>
        <td>${r.date}</td><td>${r.fgi}</td>
        <td>${r.forward_20 > 0 ? '📈' : '📉'} ${r.forward_20 > 0 ? '+' : ''}${r.forward_20}%</td>
      </tr>`;
    });
    html += `<tr style="background:#ececec"><td colspan="2"><strong>区间</strong></td><td><strong>${min > 0 ? '+' : ''}${min}% ~ ${max > 0 ? '+' : ''}${max}%</strong></td></tr>`;
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
      `<span class="extreme-tag high">🔴 ${label} ${score}</span>`
    ).join(' ');
  }
  if (ext.low.length) {
    html += ext.low.map(([, label, score]) =>
      `<span class="extreme-tag low">🟢 ${label} ${score}</span>`
    ).join(' ');
  }
  el.innerHTML = html;
}
