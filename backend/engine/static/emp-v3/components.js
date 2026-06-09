/* ================================================================
   components.js  --  Shared UI utilities & visual components
   Loaded first; all functions are global (window scope).
   ================================================================ */

// ── FORMATTING ──────────────────────────────────────────────────

function fmt(n) {
  if (n == null || isNaN(n)) return '\u20B90';
  return '\u20B9' + Number(n).toLocaleString('en-IN');
}

function fmtN(n) {
  if (n == null || isNaN(n)) return '0';
  return Number(n).toLocaleString('en-IN');
}

function fmtCompact(n) {
  if (n == null || isNaN(n)) return '\u20B90';
  var v = Number(n);
  var sign = v < 0 ? '-' : '';
  var abs = Math.abs(v);
  if (abs >= 1e7) {
    var cr = abs / 1e7;
    return sign + '\u20B9' + (cr % 1 === 0 ? cr.toFixed(0) : cr.toFixed(1)) + 'Cr';
  }
  if (abs >= 1e5) {
    var lk = abs / 1e5;
    return sign + '\u20B9' + (lk % 1 === 0 ? lk.toFixed(0) : lk.toFixed(1)) + 'L';
  }
  return sign + '\u20B9' + abs.toLocaleString('en-IN');
}

// ── CLASSIFICATION ──────────────────────────────────────────────

function pctClass(pct) {
  if (pct >= 80) return 'excellent';
  if (pct >= 60) return 'good';
  if (pct >= 40) return 'average';
  return 'poor';
}

function ratingLabel(pct) {
  if (pct >= 80) return 'Excellent';
  if (pct >= 60) return 'Good';
  if (pct >= 40) return 'Average';
  return 'Needs Improvement';
}

function ratingColor(pct) {
  if (pct >= 80) return '#00e676';
  if (pct >= 60) return '#448aff';
  if (pct >= 40) return '#ffd600';
  return '#ff5252';
}

// ── VISUAL COMPONENTS ───────────────────────────────────────────

function renderLineChart(trend) {
  if (!trend || !trend.length) return '<div class="empty-state">No trend data</div>';

  var W = 600, H = 220;
  var padL = 45, padR = 20, padT = 20, padB = 50;
  var chartW = W - padL - padR;
  var chartH = H - padT - padB;

  // Y-axis: 0-100%
  var minY = 0, maxY = 100;
  var n = trend.length;

  // Build points
  var points = [];
  for (var i = 0; i < n; i++) {
    var x = padL + (n === 1 ? chartW / 2 : (i / (n - 1)) * chartW);
    var pct = Math.max(0, Math.min(100, trend[i].collection_pct || 0));
    var y = padT + chartH - (pct / maxY) * chartH;
    points.push({ x: x, y: y, pct: pct, label: trend[i].date_display || trend[i].date_iso });
  }

  // SVG start
  var svg = '<svg class="trend-chart" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet">';

  // Grid lines + Y labels
  var ySteps = [0, 25, 50, 75, 100];
  for (var s = 0; s < ySteps.length; s++) {
    var gy = padT + chartH - (ySteps[s] / maxY) * chartH;
    svg += '<line x1="' + padL + '" y1="' + gy + '" x2="' + (W - padR) + '" y2="' + gy + '" stroke="rgba(255,255,255,0.06)" stroke-width="1"/>';
    svg += '<text x="' + (padL - 8) + '" y="' + (gy + 4) + '" text-anchor="end" fill="rgba(255,255,255,0.35)" font-size="10" font-family="JetBrains Mono, monospace">' + ySteps[s] + '%</text>';
  }

  // Gradient fill under line
  var gradId = 'trendGrad' + Math.random().toString(36).substr(2, 5);
  svg += '<defs><linearGradient id="' + gradId + '" x1="0" y1="0" x2="0" y2="1">';
  svg += '<stop offset="0%" stop-color="#00d4ff" stop-opacity="0.25"/>';
  svg += '<stop offset="100%" stop-color="#00d4ff" stop-opacity="0.02"/>';
  svg += '</linearGradient></defs>';

  // Area path (fill under curve)
  var areaPath = 'M' + points[0].x + ',' + (padT + chartH);
  for (var i = 0; i < points.length; i++) {
    areaPath += ' L' + points[i].x + ',' + points[i].y;
  }
  areaPath += ' L' + points[points.length - 1].x + ',' + (padT + chartH) + ' Z';
  svg += '<path d="' + areaPath + '" fill="url(#' + gradId + ')"/>';

  // Line path
  var linePath = 'M' + points[0].x + ',' + points[0].y;
  for (var i = 1; i < points.length; i++) {
    linePath += ' L' + points[i].x + ',' + points[i].y;
  }
  svg += '<path d="' + linePath + '" fill="none" stroke="#00d4ff" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>';

  // Data dots + X labels
  for (var i = 0; i < points.length; i++) {
    var dotColor = ratingColor(points[i].pct);
    svg += '<circle cx="' + points[i].x + '" cy="' + points[i].y + '" r="4" fill="' + dotColor + '" stroke="#0a0a12" stroke-width="2"/>';
    // Value label above dot
    svg += '<text x="' + points[i].x + '" y="' + (points[i].y - 10) + '" text-anchor="middle" fill="' + dotColor + '" font-size="10" font-weight="600" font-family="JetBrains Mono, monospace">' + Math.round(points[i].pct) + '%</text>';
    // X-axis date label (rotated for readability)
    var showLabel = n <= 10 || i % Math.ceil(n / 10) === 0 || i === n - 1;
    if (showLabel) {
      svg += '<text x="' + points[i].x + '" y="' + (H - 8) + '" text-anchor="middle" fill="rgba(255,255,255,0.4)" font-size="9" font-family="JetBrains Mono, monospace" transform="rotate(-30 ' + points[i].x + ' ' + (H - 8) + ')">' + points[i].label + '</text>';
    }
  }

  svg += '</svg>';
  return svg;
}

function renderProgressBar(pct) {
  var p = Math.max(0, Math.min(100, pct || 0));
  var color = ratingColor(p);
  return '' +
    '<div class="vba-progress">' +
      '<div class="vba-progress-fill" style="width:' + p + '%;background:' + color + '"></div>' +
    '</div>';
}

function renderKPICard(opts) {
  var icon = opts.icon || '';
  var label = opts.label || '';
  var value = opts.value || '';
  var subtext = opts.subtext || '';
  var colorClass = opts.colorClass || '';

  var barHTML = '';
  if (opts.pct != null) {
    var barColor = ratingColor(opts.pct);
    barHTML =
      '<div class="kpi-bar">' +
        '<div class="kpi-bar-fill" style="width:' + Math.min(100, opts.pct) + '%;background:' + barColor + '"></div>' +
      '</div>';
  }

  return '' +
    '<div class="kpi-card ' + colorClass + '">' +
      (icon ? '<div class="kpi-icon">' + icon + '</div>' : '') +
      '<div class="kpi-value">' + value + '</div>' +
      '<div class="kpi-label">' + label + '</div>' +
      (subtext ? '<div class="kpi-subtext">' + subtext + '</div>' : '') +
      barHTML +
    '</div>';
}

/**
 * renderSectionCard(section, index, fyMode)
 *   section  — object from the sections list:
 *              { title, overall:{demand,collection,collection_pct,ftod|balance},
 *                fy:{...}, products:[{name,demand,collection,collection_pct,...}] }
 *   index    — position in sections array (0-4) for left-border color
 *   fyMode   — 'fy' to show FY sub-section, anything else hides it
 */
function renderSectionCard(section, index, fyMode) {
  var borderColors = ['#00bcd4', '#00e676', '#ffd600', '#ff9100', '#ff5252'];
  var borderColor = borderColors[index] || '#448aff';

  var title = section.title || 'Section';
  var overall = section.overall || {};
  var fy = section.fy || null;
  var isNPA = title === 'NPA' || (title && title.toLowerCase().indexOf('npa') !== -1 && title.toLowerCase().indexOf('potential') === -1);

  // Header with account badge from overall
  var accounts = overall.accounts != null ? overall.accounts : '';
  var header = '' +
    '<div class="vba-header">' +
      '<span class="vba-header-title">' + title + '</span>' +
      (accounts !== '' ? '<span class="vba-badge">' + accounts + ' Accounts</span>' : '') +
    '</div>';

  // Overall metrics
  var metricsHTML = '';
  if (isNPA) {
    metricsHTML = renderNPAMetrics(overall);
  } else {
    var demand = overall.demand || 0;
    var collection = overall.collection || 0;
    var rate = overall.collection_pct != null ? overall.collection_pct : 0;
    var thirdLabel = 'balance' in overall ? 'Balance' : 'FTOD';
    var thirdVal = 'balance' in overall ? overall.balance : overall.ftod;

    metricsHTML = '' +
      renderMetricRow('Demand', fmt(demand), null) +
      renderMetricRow('Collection', fmt(collection), rate) +
      renderMetricRow(thirdLabel, fmt(thirdVal || 0), null) +
      renderMetricRow('Collection Rate', Math.round(rate) + '%', rate);
  }

  // FY sub-section
  var fyHTML = '';
  if (fyMode === 'fy' && fy) {
    var fyAccounts = fy.accounts != null ? fy.accounts : '';
    var fyBody = '';
    if (isNPA) {
      fyBody = renderNPAMetrics(fy);
    } else {
      var fyDemand = fy.demand || 0;
      var fyCollection = fy.collection || 0;
      var fyRate = fy.collection_pct != null ? fy.collection_pct : 0;
      var fyThirdLabel = 'balance' in fy ? 'Balance' : 'FTOD';
      var fyThirdVal = 'balance' in fy ? fy.balance : fy.ftod;

      fyBody = '' +
        renderMetricRow('Demand', fmt(fyDemand), null) +
        renderMetricRow('Collection', fmt(fyCollection), fyRate) +
        renderMetricRow(fyThirdLabel, fmt(fyThirdVal || 0), null) +
        renderMetricRow('Collection Rate', Math.round(fyRate) + '%', fyRate);
    }

    fyHTML = '' +
      '<div class="vba-fy">' +
        '<div class="vba-fy-header">' +
          '<span class="vba-fy-title">FY Disbursement</span>' +
          (fyAccounts !== '' ? '<span class="vba-badge">' + fyAccounts + ' Accounts</span>' : '') +
        '</div>' +
        fyBody +
      '</div>';
  }

  // Products sub-section
  var productsHTML = '';
  if (section.products && section.products.length) {
    var hasAny = section.products.some(function (p) { return p.demand > 0; });
    if (hasAny) {
      productsHTML = '<div class="product-cards">';
      section.products.forEach(function (p) {
        if (p.demand <= 0) return;
        productsHTML += '<div class="product-card">';
        productsHTML += '<div class="product-card-name">' + (p.name || '') + '</div>';
        if (isNPA) {
          productsHTML += renderNPAMetrics(p);
        } else {
          var pRate = p.collection_pct != null ? p.collection_pct : 0;
          var pThirdLabel = 'balance' in p ? 'Balance' : 'FTOD';
          var pThirdVal = 'balance' in p ? p.balance : p.ftod;
          productsHTML += '' +
            renderMetricRow('Demand', fmt(p.demand), null) +
            renderMetricRow('Collection', fmt(p.collection || 0), pRate) +
            renderMetricRow(pThirdLabel, fmt(pThirdVal || 0), null) +
            renderMetricRow('Collection %', Math.round(pRate) + '%', pRate);
        }
        productsHTML += '</div>';
      });
      productsHTML += '</div>';
    }
  }

  var sectionKeys = ['regular_demand', 'bucket_1_30', 'bucket_31_60', 'pnpa', 'npa'];
  var sectionKey = sectionKeys[index] || ('section_' + index);

  return '' +
    '<div class="vba-section" data-section="' + sectionKey + '">' +
      header +
      '<div class="vba-metrics">' +
        metricsHTML +
      '</div>' +
      fyHTML +
      productsHTML +
    '</div>';
}

/** Render NPA-specific metrics (activation/closure accounts & amounts) */
function renderNPAMetrics(d) {
  return '' +
    renderMetricRow('Demand', fmt(d.demand || 0), null) +
    renderMetricRow('Activation Accts', fmtN(d.activation_account || 0), null) +
    renderMetricRow('Activation Amt', fmt(d.activation_amount || 0), null) +
    renderMetricRow('Closure Accts', fmtN(d.closure_account || 0), null) +
    renderMetricRow('Closure Amt', fmt(d.closure_amount || 0), null);
}

function renderMetricRow(label, value, pct) {
  var bar = pct != null ? renderProgressBar(pct) : '';
  return '' +
    '<div class="vba-metric">' +
      '<div class="vba-metric-row">' +
        '<div class="vba-metric-label">' + label + '</div>' +
        '<div class="vba-metric-value">' + value + '</div>' +
      '</div>' +
      bar +
    '</div>';
}

// ── TOAST ───────────────────────────────────────────────────────

function showToast(msg, type) {
  type = type || 'error';
  var el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.className = 'toast toast--' + type;
  el.classList.add('visible');
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(function () {
    el.classList.remove('visible');
  }, 3000);
}

// ── DETAIL MODAL ─────────────────────────────────────────────────

function renderDetailModal(section, index) {
  var borderColors = ['#00bcd4', '#00e676', '#ffd600', '#ff9100', '#ff5252'];
  var color = borderColors[index] || '#448aff';
  var title = section.title || 'Section';
  var overall = section.overall || {};
  var fy = section.fy || null;
  var isNPA = title === 'NPA' || (title && title.toLowerCase().indexOf('npa') !== -1 && title.toLowerCase().indexOf('potential') === -1);

  // Header
  var headerEl = document.getElementById('detail-modal-header');
  var accounts = overall.accounts != null ? overall.accounts : '';
  headerEl.innerHTML =
    '<div class="detail-modal-accent" style="background:' + color + '"></div>' +
    '<div class="detail-modal-title">' + title + '</div>' +
    (accounts !== '' ? '<span class="vba-badge">' + accounts + ' Accounts</span>' : '');

  // Body
  var bodyEl = document.getElementById('detail-modal-body');
  var html = '';

  // --- Overall Summary ---
  html += '<div class="detail-summary-title">Overall Summary</div>';
  html += '<div class="detail-summary-grid">';

  if (isNPA) {
    html += renderDetailStat('Demand', fmt(overall.demand || 0), null);
    html += renderDetailStat('Activation Accounts', fmtN(overall.activation_account || 0), null);
    html += renderDetailStat('Activation Amount', fmt(overall.activation_amount || 0), null);
    html += renderDetailStat('Closure Accounts', fmtN(overall.closure_account || 0), null);
    html += renderDetailStat('Closure Amount', fmt(overall.closure_amount || 0), null);
  } else {
    var rate = overall.collection_pct != null ? overall.collection_pct : 0;
    var thirdLabel = 'balance' in overall ? 'Balance' : 'FTOD';
    var thirdVal = 'balance' in overall ? overall.balance : overall.ftod;
    html += renderDetailStat('Demand', fmt(overall.demand || 0), null);
    html += renderDetailStat('Collection', fmt(overall.collection || 0), rate);
    html += renderDetailStat(thirdLabel, fmt(thirdVal || 0), null);
    html += renderDetailStat('Collection Rate', Math.round(rate) + '%', rate);
  }
  html += '</div>';

  // --- FY Disbursement ---
  if (fy) {
    html += '<div class="detail-fy-section">';
    html += '<div class="detail-summary-title">FY Disbursement</div>';
    html += '<div class="detail-summary-grid">';
    if (isNPA) {
      html += renderDetailStat('Demand', fmt(fy.demand || 0), null);
      html += renderDetailStat('Activation Accounts', fmtN(fy.activation_account || 0), null);
      html += renderDetailStat('Activation Amount', fmt(fy.activation_amount || 0), null);
      html += renderDetailStat('Closure Accounts', fmtN(fy.closure_account || 0), null);
      html += renderDetailStat('Closure Amount', fmt(fy.closure_amount || 0), null);
    } else {
      var fyRate = fy.collection_pct != null ? fy.collection_pct : 0;
      var fyThirdLabel = 'balance' in fy ? 'Balance' : 'FTOD';
      var fyThirdVal = 'balance' in fy ? fy.balance : fy.ftod;
      html += renderDetailStat('Demand', fmt(fy.demand || 0), null);
      html += renderDetailStat('Collection', fmt(fy.collection || 0), fyRate);
      html += renderDetailStat(fyThirdLabel, fmt(fyThirdVal || 0), null);
      html += renderDetailStat('Collection Rate', Math.round(fyRate) + '%', fyRate);
    }
    html += '</div>';
    html += '</div>';
  }

  // --- Products Breakdown ---
  if (section.products && section.products.length) {
    var hasProducts = section.products.some(function(p) { return p.demand > 0; });
    if (hasProducts) {
      html += '<div class="detail-summary-title" style="margin-top:24px">Product Breakdown</div>';
      html += '<div class="detail-products-grid">';
      section.products.forEach(function(p) {
        if (p.demand <= 0) return;
        html += '<div class="detail-product-card">';
        html += '<div class="detail-product-name">' + (p.name || 'Unknown') + '</div>';
        if (isNPA) {
          html += renderDetailProductMetric('Demand', fmt(p.demand || 0));
          html += renderDetailProductMetric('Activation Accts', fmtN(p.activation_account || 0));
          html += renderDetailProductMetric('Activation Amt', fmt(p.activation_amount || 0));
          html += renderDetailProductMetric('Closure Accts', fmtN(p.closure_account || 0));
          html += renderDetailProductMetric('Closure Amt', fmt(p.closure_amount || 0));
        } else {
          var pRate = p.collection_pct != null ? p.collection_pct : 0;
          var pThirdLabel = 'balance' in p ? 'Balance' : 'FTOD';
          var pThirdVal = 'balance' in p ? p.balance : p.ftod;
          html += renderDetailProductMetric('Demand', fmt(p.demand || 0));
          html += renderDetailProductMetric('Collection', fmt(p.collection || 0));
          html += renderDetailProductMetric(pThirdLabel, fmt(pThirdVal || 0));
          html += renderDetailProductMetric('Collection %', Math.round(pRate) + '%');
        }
        html += '</div>';
      });
      html += '</div>';
    }
  }

  bodyEl.innerHTML = html;
  document.getElementById('detail-modal').classList.add('active');
}

function renderDetailStat(label, value, pct) {
  var bar = '';
  if (pct != null) {
    bar = '<div class="detail-stat-bar">' + renderProgressBar(pct) + '</div>';
  }
  return '' +
    '<div class="detail-stat-card">' +
      '<div class="detail-stat-label">' + label + '</div>' +
      '<div class="detail-stat-value">' + value + '</div>' +
      bar +
    '</div>';
}

function renderDetailProductMetric(label, value) {
  return '' +
    '<div class="detail-product-metric">' +
      '<span class="detail-product-metric-label">' + label + '</span>' +
      '<span class="detail-product-metric-value">' + value + '</span>' +
    '</div>';
}

function closeDetailModal() {
  document.getElementById('detail-modal').classList.remove('active');
}
