// =============================================
// FGI Web Frontend — ECharts Configurations
// =============================================

// ── FGI + 上证综指 History Chart (initialized once) ──
function initHistoryChart(history) {
  const dom = document.getElementById('history-chart');
  if (!dom || getChart('history')) return;
  const chart = echarts.init(dom);
  registerChart('history', chart);

  const dates = history.map(d => d.date);
  const fgiVals = history.map(d => d.FGI_final);
  const closeVals = history.map(d => d.close);

  const closeFiltered = closeVals.filter(v => v != null);
  const closeMin = Math.min(...closeFiltered);
  const closeMax = Math.max(...closeFiltered);

  const option = {
    tooltip: {
      trigger: 'axis',
      formatter: params => {
        let html = `<strong>${params[0].axisValue}</strong>`;
        params.forEach(p => {
          if (p.seriesName === 'FGI') {
            const z = getZone(p.value);
            html += `<br/>FGI: <strong style="color:${z.color}">${p.value.toFixed(1)}</strong> (${z.label})`;
          } else if (p.seriesName === '上证综指') {
            html += `<br/>上证综指: <strong>${p.value.toFixed(0)}</strong>`;
          }
        });
        return html;
      }
    },
    legend: { data: ['FGI', '上证综指'], bottom: 0, left: 'center', icon: 'line', itemWidth: 20 },
    grid: { left: 45, right: 55, top: 10, bottom: 40 },
    xAxis: {
      type: 'category', data: dates, boundaryGap: false,
      axisLabel: { fontSize: 10, interval: 'auto', rotate: 0 },
    },
    yAxis: [
      {
        type: 'value', min: 0, max: 100,
        splitLine: { lineStyle: { type: 'dashed', color: '#e0e0e0' } },
        axisLabel: { color: '#888' },
        name: 'FGI', nameTextStyle: { color: '#888', fontSize: 10 },
      },
      {
        type: 'value',
        min: Math.floor(closeMin / 500) * 500,
        max: Math.ceil(closeMax / 500) * 500,
        splitLine: { show: false },
        axisLabel: { color: '#555' },
        name: '上证综指', nameTextStyle: { color: '#555', fontSize: 10 },
      },
    ],
    dataZoom: [
      { type: 'inside', start: 70, end: 100 },
      { type: 'slider', show: true, height: 20, bottom: 22, start: 70, end: 100 },
    ],
    series: [
      {
        name: 'FGI',
        type: 'line',
        yAxisIndex: 0,
        data: fgiVals.map(v => ({
          value: v,
          itemStyle: { color: v != null ? zoneColor(v) : '#ccc' },
        })),
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 2 },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(100,100,100,0.15)' },
            { offset: 1, color: 'rgba(100,100,100,0.02)' },
          ])
        },
        markPoint: {
          data: history
            .filter(d => d.FGI_final != null && (d.FGI_final <= 20 || d.FGI_final >= 80))
            .map(d => ({
              name: d.date,
              coord: [d.date, d.FGI_final],
              value: d.FGI_final.toFixed(1),
              itemStyle: { color: d.FGI_final <= 20 ? '#d32f2f' : '#2e7d32' },
              symbol: 'diamond',
              symbolSize: 10,
            })),
          symbolSize: 10,
          label: { show: false },
        },
        markLine: {
          silent: true,
          symbol: 'none',
          data: [{
            xAxis: history.length - 1,
            label: {
              formatter: `⬅ ${history[history.length - 1].date}`,
              position: 'start',
              color: '#ff9800',
              fontSize: 11,
              fontWeight: 'bold',
            },
            lineStyle: { color: '#ff9800', type: 'dashed', width: 2 },
          }],
        },
      },
      {
        name: '上证综指',
        type: 'line',
        yAxisIndex: 1,
        data: closeVals,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 1.5, color: '#e65100' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(230,81,0,0.10)' },
            { offset: 1, color: 'rgba(230,81,0,0.01)' },
          ])
        },
      },
    ],
  };
  chart.setOption(option);
}

// ── Update mark line on history chart ──
function updateHistoryMarkLine(dateStr) {
  const chart = getChart('history');
  if (!chart) return;
  const idx = state.history.findIndex(d => d.date === dateStr);
  if (idx < 0) return;

  chart.setOption({
    series: [{
      type: 'line',
      markLine: {
        silent: true,
        symbol: 'none',
        data: [{
          xAxis: idx,
          label: {
            formatter: `⬅ ${dateStr}`,
            position: 'start',
            color: '#ff9800',
            fontSize: 11,
            fontWeight: 'bold',
          },
          lineStyle: { color: '#ff9800', type: 'dashed', width: 2 },
        }],
      },
    }],
  });
}

// ── Radar (re-renderable) ──
function initRadarChart(dimensions) {
  const dom = document.getElementById('radar-chart');
  if (!dom) return;
  let chart = getChart('radar');
  if (!chart) {
    chart = echarts.init(dom);
    registerChart('radar', chart);
  }

  const indicatorNames = { momentum: '动量', sentiment: '情绪', valuation: '估值', volatility: '波动率', funding: '资金' };
  const indicator = Object.values(indicatorNames).map(n => ({ name: n, max: 100 }));
  const data = dimensions ? Object.keys(indicatorNames).map(k => dimensions[k] || 0) : [0,0,0,0,0];

  chart.setOption({
    tooltip: { trigger: 'item' },
    radar: { indicator, center: ['50%', '50%'], radius: '65%' },
    series: [{
      type: 'radar',
      data: [{ value: data, name: '当前', areaStyle: { color: 'rgba(25, 118, 210, 0.2)' }, lineStyle: { color: '#1976d2', width: 2 }, itemStyle: { color: '#1976d2' } }],
    }],
  });
}

// ── Indicator Bars (re-renderable) ──
function initIndicatorChart(scores, statuses, extremeSignals) {
  const dom = document.getElementById('indicator-chart');
  if (!dom) return;
  let chart = getChart('indicator');
  if (!chart) {
    chart = echarts.init(dom);
    registerChart('indicator', chart);
  }

  const names = { M1: '涨停板数', M2: '散户意愿', M3: '偏离60日线', M4: '创业板活跃', S2: '股吧热度', S3: '封单量', V1: '风险溢价', V2: 'ΔERP Z', V4: '期权波动', F1: '融资余额', F2: '基金仓位', F3: '主力偏好' };
  const indOrder = ['M1','M2','M3','M4','S2','S3','V1','V2','V4','F1','F2','F3'];
  const vals = indOrder.map(k => (scores && scores[k] != null) ? scores[k] : null);

  const extremeSet = new Set();
  if (extremeSignals) {
    (extremeSignals.high || []).forEach(e => extremeSet.add(e[0]));
    (extremeSignals.low || []).forEach(e => extremeSet.add(e[0]));
  }

  const colors = vals.map(v => {
    if (v == null) return '#ccc';
    if (v >= 80) return '#2e7d32';
    if (v <= 20) return '#d32f2f';
    return '#1976d2';
  });

  chart.setOption({
    tooltip: {
      trigger: 'axis',
      formatter: params => {
        const p = params[0];
        const idx = indOrder.indexOf(p.name);
        const key = indOrder[idx];
        const st = (statuses && statuses[key]) || 'normal';
        const stLabel = st !== 'normal' ? ` (${st})` : '';
        return `<strong>${names[p.name] || p.name}</strong><br/>得分: <strong>${p.value != null ? p.value.toFixed(1) : '--'}</strong>${stLabel}`;
      }
    },
    grid: { left: 70, right: 20, top: 5, bottom: 20 },
    xAxis: {
      type: 'category',
      data: indOrder.map(k => names[k] || k),
      axisLabel: { fontSize: 9, interval: 0, rotate: 30 },
    },
    yAxis: { type: 'value', min: 0, max: 100, splitLine: { lineStyle: { type: 'dashed', color: '#e0e0e0' } } },
    series: [{
      type: 'bar',
      data: vals.map((v, i) => ({
        value: v,
        itemStyle: {
          color: colors[i],
          borderColor: extremeSet.has(indOrder[i]) ? '#000' : 'transparent',
          borderWidth: extremeSet.has(indOrder[i]) ? 2 : 0,
        },
      })),
      barWidth: '60%',
      markLine: {
        silent: true,
        data: [
          { yAxis: 85, label: { formatter: '85 ⬆', color: '#2e7d32', fontSize: 9 }, lineStyle: { color: '#2e7d32', type: 'dashed' } },
          { yAxis: 15, label: { formatter: '15 ⬇', color: '#d32f2f', fontSize: 9 }, lineStyle: { color: '#d32f2f', type: 'dashed' } },
        ],
      },
    }],
  });
}

// ── Distribution (re-renderable) ──
function initDistributionChart(history, currentFgi, dateStr) {
  const dom = document.getElementById('distribution-chart');
  if (!dom) return;
  let chart = getChart('distribution');
  if (!chart) {
    chart = echarts.init(dom);
    registerChart('distribution', chart);
  }

  const bucketSize = 5;
  const buckets = Array(20).fill(0);
  history.forEach(d => {
    if (d.FGI_final != null) {
      const idx = Math.min(Math.floor(d.FGI_final / bucketSize), 19);
      buckets[idx]++;
    }
  });
  const labels = buckets.map((_, i) => `${i * bucketSize}-${(i+1) * bucketSize}`);
  const currentBucket = currentFgi != null ? Math.min(Math.floor(currentFgi / bucketSize), 19) : -1;
  const barColors = buckets.map((_, i) => i === currentBucket ? '#ff9800' : '#1976d2');

  chart.setOption({
    tooltip: {
      trigger: 'axis',
      formatter: params => {
        const p = params[0];
        const extra = p.dataIndex === currentBucket ? `<br/>⬅ ${dateStr}` : '';
        return `${p.axisValue} 分位<br/>交易日: <strong>${p.value}</strong>${extra}`;
      }
    },
    grid: { left: 45, right: 10, top: 5, bottom: 30 },
    xAxis: {
      type: 'category', data: labels,
      axisLabel: { fontSize: 8, interval: 1, rotate: 45 },
    },
    yAxis: { type: 'value', splitLine: { lineStyle: { type: 'dashed', color: '#e0e0e0' } } },
    series: [{
      type: 'bar',
      data: buckets.map((v, i) => ({ value: v, itemStyle: { color: barColors[i] } })),
      barWidth: '90%',
    }],
  });
}

// ── Trend Explorer (initialized once) ──
function initTrendChart(indicatorsHistory) {
  const dom = document.getElementById('trend-chart');
  if (!dom || getChart('trend')) return;
  const chart = echarts.init(dom);
  registerChart('trend', chart);

  if (!indicatorsHistory || !indicatorsHistory.indicators) return;

  const dimNames = { momentum: '动量', sentiment: '情绪', valuation: '估值', volatility: '波动率', funding: '资金' };
  const dimIndicators = { momentum: ['M1','M2','M3','M4'], sentiment: ['S2','S3'], valuation: ['V1','V2'], volatility: ['V4'], funding: ['F1','F2','F3'] };
  const indNames = { M1:'涨停板数',M2:'散户意愿',M3:'偏离60日线',M4:'创业板活跃',S2:'股吧热度',S3:'封单量',V1:'风险溢价',V2:'ΔERP Z',V4:'期权波动',F1:'融资余额',F2:'基金仓位',F3:'主力偏好' };

  const trendType = document.getElementById('trend-type');
  const trendSeries = document.getElementById('trend-series');

  function populateSeries() {
    trendSeries.innerHTML = '';
    if (trendType.value === 'dimension') {
      Object.keys(dimNames).forEach(k => {
        const opt = document.createElement('option');
        opt.value = k; opt.textContent = dimNames[k];
        trendSeries.appendChild(opt);
      });
    } else {
      Object.keys(indNames).forEach(k => {
        const opt = document.createElement('option');
        opt.value = k; opt.textContent = indNames[k];
        trendSeries.appendChild(opt);
      });
    }
  }

  function updateChart() {
    const type = trendType.value;
    const series = trendSeries.value;

    let dataPoints = [];
    if (type === 'dimension' && dimIndicators[series]) {
      const indKeys = dimIndicators[series];
      const indMaps = {};
      indKeys.forEach(k => {
        const arr = indicatorsHistory.indicators[k];
        if (arr) {
          arr.forEach(d => {
            if (!indMaps[d.date]) indMaps[d.date] = [];
            indMaps[d.date].push(d.value);
          });
        }
      });
      dataPoints = Object.entries(indMaps)
        .map(([date, vals]) => ({ date, value: vals.reduce((a,b) => a + b, 0) / vals.length }))
        .filter(d => d.value != null)
        .sort((a, b) => a.date.localeCompare(b.date));
    } else if (type === 'indicator') {
      const arr = indicatorsHistory.indicators[series];
      if (arr) dataPoints = arr;
    }

    if (!dataPoints.length) return;

    chart.setOption({
      tooltip: { trigger: 'axis', formatter: p => `<strong>${p[0].axisValue}</strong><br/>${p[0].seriesName}: <strong>${p[0].value.toFixed(1)}</strong>` },
      grid: { left: 45, right: 15, top: 5, bottom: 40 },
      xAxis: { type: 'category', data: dataPoints.map(d => d.date), boundaryGap: false, axisLabel: { fontSize: 9, interval: 'auto' } },
      yAxis: { type: 'value', min: 0, max: 100, splitLine: { lineStyle: { type: 'dashed', color: '#e0e0e0' } } },
      dataZoom: [
        { type: 'inside', start: 60, end: 100 },
        { type: 'slider', show: true, height: 15, bottom: 5, start: 60, end: 100 },
      ],
      series: [{
        type: 'line',
        data: dataPoints.map(d => d.value),
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 2, color: '#1976d2' },
        areaStyle: { color: 'rgba(25, 118, 210, 0.1)' },
      }],
    });
  }

  trendType.addEventListener('change', () => { populateSeries(); updateChart(); });
  trendSeries.addEventListener('change', updateChart);
  populateSeries();
  updateChart();
}

// ── Click handler on history chart to switch date ──
function enableHistoryChartClick() {
  const chart = getChart('history');
  if (!chart) return;

  chart.on('click', params => {
    if (params.componentType === 'series') {
      const dateStr = params.name;
      if (dateStr && state.allDatesIndex[dateStr]) {
        switchToDate(dateStr);
      }
    }
  });
}
