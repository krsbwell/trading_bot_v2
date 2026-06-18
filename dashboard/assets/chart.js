/* ─────────────────────────────────────────────────────────────────────────
   Apex Trading Bot — TradingView Lightweight Charts v5 multi-pane chart
   Pane 0 (60%): Candlestick + EMAs   Pane 1 (20%): CCI   Pane 2 (20%): MACD
   ───────────────────────────────────────────────────────────────────────── */
(function () {
    'use strict';

    const TOTAL_H  = 680;
    const MAIN_H   = Math.round(TOTAL_H * 0.60);
    const IND_H    = Math.round(TOTAL_H * 0.20);
    const TEN_DAYS = 10 * 24 * 3600;

    /* ── chart state ─────────────────────────────────────────────────────── */
    let chart = null, S = {}, wm = null;
    let _lastPairTf = null, _currentPair = '', _currentTf = '';
    let _candleData = [];
    let _autoSaveTimer = null;

    const WM_COLOR = 'rgba(100,110,120,0.22)';
    const WM_FONT  = "'IBM Plex Sans',sans-serif";
    const WM_SIZE  = 38;

    /* ── plain drawing tools (h-line) ────────────────────────────────────── */
    let drawMode = null, drawColor = '#ffd700', drawWidth = 1, drawStyle = 'solid';
    let drawings = [];
    let selectedIdx = -1, isDragging = false;
    let dragStartPrice = null, dragStartData = null, _mouseListeners = null;

    /* ── SVG trendline tool ───────────────────────────────────────────────── */
    let trendlines = [], trendIdCtr = 0, trendOverlay = null;
    let selectedTrend = -1;
    let trendP1 = null;         // first click point {time, price} during drawing
    let _trendPreviewEl = null; // live preview line in SVG
    let trendResize = null;     // { idx, handle:'p1'|'p2' }
    let trendMove = null;       // { idx, startTime, startPrice, origT1, origP1, origT2, origP2 }

    /* ── position tools ──────────────────────────────────────────────────── */
    let positions = [], posIdCtr = 0, posOverlay = null;
    let posDrag = null;
    let _tradePriceLines = []; // TWLC price lines for open broker trades

    /* ── box / consolidation tools ───────────────────────────────────────── */
    let boxes = [], boxIdCtr = 0, boxOverlay = null;
    let boxDraw = null, boxResize = null, boxMove = null, selectedBox = -1;

    /* ═══════════════════════════════════════════════════════════════════════
       HELPERS
       ═══════════════════════════════════════════════════════════════════════ */

    function LC()     { return window.LightweightCharts; }
    function isJPY(p) { return p && p.toUpperCase().indexOf('JPY') >= 0; }
    function pipSize(pair) { return isJPY(pair) ? 0.01 : 0.0001; }
    function fmtPrice(p)   { return p.toFixed(isJPY(_currentPair) ? 3 : 5); }

    function priceFormat(pair) {
        return isJPY(pair)
            ? { type: 'price', precision: 3, minMove: 0.001   }
            : { type: 'price', precision: 5, minMove: 0.00001 };
    }

    function dotted(price, sref) {
        try { sref.createPriceLine({ price, color: '#7d8590', lineWidth: 1,
              lineStyle: LC().LineStyle.Dotted, axisLabelVisible: false }); } catch(e) {}
    }
    function cciRef(price, color) {
        try { S.cci.createPriceLine({ price, color, lineWidth: 2,
              lineStyle: LC().LineStyle.Dashed,
              axisLabelVisible: true, axisLabelColor: color }); } catch(e) {}
    }

    /* coordinate converters */
    function _timeToX(t)   {
        try { var x = chart.timeScale().timeToCoordinate(t);
              if (x != null && isFinite(x)) return x; } catch(e) {}
        return null;
    }
    function _xToTime(x)   {
        try { var t = chart.timeScale().coordinateToTime(x);
              if (t != null) return t; } catch(e) {}
        return null;
    }
    function _priceToY(p)  {
        try { var y = S.candle.priceToCoordinate(p);
              if (y != null && isFinite(y)) return y; } catch(e) {}
        return null;
    }
    function _yToPrice(y)  {
        try { var p = S.candle.coordinateToPrice(y);
              if (p != null && isFinite(p)) return p; } catch(e) {}
        return null;
    }

    function psWidth() {
        try { return chart.priceScale('right').width() || 62; } catch(e) { return 62; }
    }

    function _candleInterval() {
        if (_candleData.length < 2) return 3600;
        var n = Math.min(_candleData.length, 5);
        var sum = 0;
        for (var i = _candleData.length - n; i < _candleData.length - 1; i++) {
            sum += _candleData[i + 1].time - _candleData[i].time;
        }
        return Math.round(sum / (n - 1));
    }

    function _hexToRgba(hex, alpha) {
        hex = (hex || '#ffd700').replace('#', '');
        if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
        return 'rgba(' + parseInt(hex.substr(0,2),16) + ',' +
                         parseInt(hex.substr(2,2),16) + ',' +
                         parseInt(hex.substr(4,2),16) + ',' + alpha + ')';
    }

    /* ── Robust time ↔ pixel converters (extrapolate beyond visible range) ── */

    function _getTimeScale() {
        /* Returns { refT, refX, pps } — pixels-per-second from last visible candles.
           Used to extrapolate positions that lie outside the current scroll window. */
        if (!chart || _candleData.length < 2) return null;
        var n = _candleData.length;
        for (var i = n - 1; i >= 1; i--) {
            var ta = _candleData[i - 1].time, xa = _timeToX(ta);
            var tb = _candleData[i].time,     xb = _timeToX(tb);
            if (xa != null && xb != null && tb !== ta) {
                return { refT: tb, refX: xb, pps: (xb - xa) / (tb - ta) };
            }
        }
        var tc = _candleData[0].time, xc = _timeToX(tc);
        var td = _candleData[1].time, xd = _timeToX(td);
        if (xc != null && xd != null && td !== tc) {
            return { refT: tc, refX: xc, pps: (xd - xc) / (td - tc) };
        }
        return null;
    }

    /* time → pixel: extrapolates into past/future space when TWLC returns null */
    function _timeToXExtrap(t) {
        var x = _timeToX(t);
        if (x != null) return x;
        var ts = _getTimeScale();
        if (!ts) return null;
        return ts.refX + ts.pps * (t - ts.refT);
    }

    /* pixel → time: extrapolates when the cursor is outside the visible time range */
    function _xToTimeExtrap(x) {
        var t = _xToTime(x);
        if (t != null) return t;
        var ts = _getTimeScale();
        if (!ts || ts.pps === 0) return null;
        return Math.round(ts.refT + (x - ts.refX) / ts.pps);
    }

    /* Storage key helpers — keyed per PAIR ONLY (no timeframe).
       Drawings use absolute timestamp+price anchors so they are valid on any TF.
       This means trendlines drawn on Daily are visible on 4H and 1H automatically. */
    function _hlKey()     { return 'apex_hlines_'    + _currentPair; }
    function _trendKey()  { return 'apex_trendlines_' + _currentPair; }
    function _boxKey()    { return 'apex_boxes_'      + _currentPair; }
    function _posKey()    { return 'apex_pos_'        + _currentPair; }

    /* Legacy aliases — same as primary keys now (for smooth migration) */
    function _legacyDrawKey() { return _hlKey();    }
    function _legacyBoxKey()  { return _boxKey();   }
    function _legacyPosKey()  { return _posKey();   }

    function _svgDash(style) {
        if (style === 'dashed') return '8 4';
        if (style === 'dotted') return '2 4';
        return 'none';
    }

    /* ── Measure stats calculator ─────────────────────────────────────────── */
    function _measureStats(box) {
        var pip = pipSize(_currentPair);
        /* Chronological start/end so sign is always meaningful */
        var startPrice = box.t1 <= box.t2 ? box.p1 : box.p2;
        var endPrice   = box.t1 <= box.t2 ? box.p2 : box.p1;
        var priceDiff  = endPrice - startPrice;
        var pips       = pip > 0 ? priceDiff / pip : 0;
        var pct        = startPrice > 0 ? (priceDiff / startPrice) * 100 : 0;
        var t1b = Math.min(box.t1, box.t2), t2b = Math.max(box.t1, box.t2);
        var bars = _candleData.filter(function(c){ return c.time >= t1b && c.time <= t2b; }).length;
        var secs = Math.abs(box.t2 - box.t1);
        var days = Math.floor(secs / 86400);
        var hrs  = Math.floor((secs % 86400) / 3600);
        var mins = Math.floor((secs % 3600) / 60);
        var parts = [];
        if (days) parts.push(days + 'd');
        if (hrs)  parts.push(hrs + 'h');
        if (!parts.length) parts.push(mins + 'm');
        var dec   = isJPY(_currentPair) ? 3 : 5;
        var isUp  = priceDiff >= 0;
        var col   = isUp ? '#00ff88' : '#ff3366';
        var sign  = isUp ? '+' : '';
        var arrow = isUp ? '▲' : '▼';
        return {
            pips:      sign + pips.toFixed(1) + 'p',
            pct:       sign + pct.toFixed(2) + '%',
            price:     sign + priceDiff.toFixed(dec),
            bars:      bars + (bars === 1 ? ' bar' : ' bars'),
            time:      parts.join(' '),
            color:     col,
            arrow:     arrow,
            priceDiff: priceDiff,
        };
    }

    /* ═══════════════════════════════════════════════════════════════════════
       SVG TRENDLINE TOOL
       ═══════════════════════════════════════════════════════════════════════ */

    function ensureTrendOverlay() {
        var el = document.getElementById('tvlw-chart');
        if (!el) return null;
        if (trendOverlay && trendOverlay.parentNode === el) return trendOverlay;
        trendOverlay = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        trendOverlay.id = 'apex-trend-overlay';
        trendOverlay.style.cssText =
            'position:absolute;top:0;left:0;z-index:13;pointer-events:none;overflow:visible;';
        trendOverlay.setAttribute('width', '100%');
        trendOverlay.setAttribute('height', MAIN_H + 'px');
        el.style.position = 'relative';
        el.appendChild(trendOverlay);
        return trendOverlay;
    }

    function _makeTrendHandle(t, which) {
        var h = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        h.setAttribute('r', '5');
        h.setAttribute('fill', '#ffffff');
        h.setAttribute('stroke-width', '1.5');
        h.setAttribute('pointer-events', 'all');
        h.setAttribute('cursor', 'crosshair');
        h.setAttribute('display', 'none');
        h.addEventListener('mousedown', function(e) {
            if (drawMode) return;
            e.stopPropagation(); e.preventDefault();
            var idx = trendlines.indexOf(t);
            if (idx < 0 || t.locked) return;
            _selectTrend(idx);
            trendResize = { idx: idx, handle: which };
            try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
        });
        return h;
    }

    function buildTrendEl(t) {
        var svg = ensureTrendOverlay(); if (!svg) return;
        var g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        g.setAttribute('data-trend-id', t.id);

        /* Glow / selection halo behind the line */
        var glow = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        glow.setAttribute('pointer-events', 'none');
        glow.setAttribute('opacity', '0.25');
        glow.setAttribute('display', 'none');

        /* Wide transparent hit-strip for easy click/drag */
        var hit = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        hit.setAttribute('stroke', 'transparent');
        hit.setAttribute('stroke-width', '14');
        hit.setAttribute('pointer-events', 'stroke');
        hit.setAttribute('cursor', 'move');
        hit.addEventListener('mousedown', function(e) {
            if (drawMode) return;
            e.stopPropagation(); e.preventDefault();
            var idx = trendlines.indexOf(t);
            if (idx < 0) return;
            _selectTrend(idx);
            _deselectBox();
            if (t.locked) return;
            var chartEl = document.getElementById('tvlw-chart');
            if (!chartEl) return;
            var r = chartEl.getBoundingClientRect();
            var cX = e.clientX - r.left, cY = e.clientY - r.top;
            trendMove = {
                idx: idx,
                startTime:  _xToTime(cX)  || t.t1,
                startPrice: _yToPrice(cY) || t.p1,
                origT1: t.t1, origP1: t.p1,
                origT2: t.t2, origP2: t.p2
            };
            try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
        });

        /* Visual line */
        var line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('pointer-events', 'none');

        /* Endpoint handles */
        var h1 = _makeTrendHandle(t, 'p1');
        var h2 = _makeTrendHandle(t, 'p2');

        /* Delete button — circle+× at midpoint, shown when selected */
        var del = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        del.setAttribute('cursor', 'pointer');
        del.setAttribute('pointer-events', 'all');
        del.setAttribute('display', 'none');
        var dCirc = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        dCirc.setAttribute('r', '8');
        dCirc.setAttribute('fill', 'rgba(13,17,23,0.9)');
        dCirc.setAttribute('stroke', '#555');
        dCirc.setAttribute('stroke-width', '1');
        var dTxt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        dTxt.setAttribute('text-anchor', 'middle');
        dTxt.setAttribute('dominant-baseline', 'central');
        dTxt.setAttribute('fill', '#ccc');
        dTxt.setAttribute('font-size', '12');
        dTxt.setAttribute('font-family', 'monospace');
        dTxt.setAttribute('pointer-events', 'none');
        dTxt.textContent = '×';
        del.appendChild(dCirc); del.appendChild(dTxt);
        del.addEventListener('click', function(e) {
            e.stopPropagation();
            var idx = trendlines.indexOf(t);
            if (idx >= 0) delTrendline(idx);
        });

        /* Lock toggle button — shown when selected */
        var lockG = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        lockG.setAttribute('cursor', 'pointer');
        lockG.setAttribute('pointer-events', 'all');
        lockG.setAttribute('display', 'none');
        var lCirc = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        lCirc.setAttribute('r', '8');
        lCirc.setAttribute('fill', 'rgba(13,17,23,0.9)');
        lCirc.setAttribute('stroke', '#555');
        lCirc.setAttribute('stroke-width', '1');
        var lTxt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        lTxt.setAttribute('text-anchor', 'middle');
        lTxt.setAttribute('dominant-baseline', 'central');
        lTxt.setAttribute('fill', '#ffd700');
        lTxt.setAttribute('font-size', '9');
        lTxt.setAttribute('pointer-events', 'none');
        lockG.appendChild(lCirc); lockG.appendChild(lTxt);
        lockG.addEventListener('click', function(e) {
            e.stopPropagation();
            t.locked = !t.locked;
            updateTrendline(t);
            saveTrendlines();
        });

        /* Duplicate button */
        var dupG = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        dupG.setAttribute('cursor', 'pointer');
        dupG.setAttribute('pointer-events', 'all');
        dupG.setAttribute('display', 'none');
        var dupCirc = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        dupCirc.setAttribute('r', '8');
        dupCirc.setAttribute('fill', 'rgba(13,17,23,0.9)');
        dupCirc.setAttribute('stroke', '#555');
        dupCirc.setAttribute('stroke-width', '1');
        var dupTxt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        dupTxt.setAttribute('text-anchor', 'middle');
        dupTxt.setAttribute('dominant-baseline', 'central');
        dupTxt.setAttribute('fill', '#38b6ff');
        dupTxt.setAttribute('font-size', '11');
        dupTxt.setAttribute('font-family', 'monospace');
        dupTxt.setAttribute('pointer-events', 'none');
        dupTxt.textContent = '+';
        dupG.appendChild(dupCirc); dupG.appendChild(dupTxt);
        dupG.addEventListener('click', function(e) {
            e.stopPropagation();
            var idx = trendlines.indexOf(t);
            if (idx >= 0) _dupTrendline(idx);
        });

        g.appendChild(glow);
        g.appendChild(hit);
        g.appendChild(line);
        g.appendChild(h1); g.appendChild(h2);
        g.appendChild(del);
        g.appendChild(lockG);
        g.appendChild(dupG);
        svg.appendChild(g);

        t.el = g; t.lineEl = line; t.hitEl = hit; t.glowEl = glow;
        t.h1El = h1; t.h2El = h2; t.delEl = del; t.lockEl = lockG; t.dupEl = dupG;
        t.lockTxt = lTxt;
    }

    function updateTrendline(t) {
        if (!t.el || !chart || !S.candle) return;
        var x1 = _timeToXExtrap(t.t1), y1 = _priceToY(t.p1);
        var x2 = _timeToXExtrap(t.t2), y2 = _priceToY(t.p2);

        if (x1 == null || y1 == null || x2 == null || y2 == null) {
            t.el.setAttribute('display', 'none'); return;
        }
        t.el.removeAttribute('display');

        function setLine(el, a, b, c, d) {
            el.setAttribute('x1', a); el.setAttribute('y1', b);
            el.setAttribute('x2', c); el.setAttribute('y2', d);
        }
        setLine(t.lineEl, x1, y1, x2, y2);
        setLine(t.hitEl,  x1, y1, x2, y2);
        setLine(t.glowEl, x1, y1, x2, y2);

        var col  = t.color || '#ffd700';
        var w    = t.width || 1;
        var isSel = (selectedTrend === trendlines.indexOf(t));
        var dash  = _svgDash(t.style);

        t.lineEl.setAttribute('stroke', col);
        t.lineEl.setAttribute('stroke-width', isSel ? w + 1 : w);
        t.lineEl.setAttribute('stroke-dasharray', dash);

        t.glowEl.setAttribute('stroke', col);
        t.glowEl.setAttribute('stroke-width', w + 10);
        t.glowEl.setAttribute('stroke-dasharray', dash);
        t.glowEl.setAttribute('display', isSel ? '' : 'none');

        if (isSel) {
            /* Endpoint handles */
            t.h1El.setAttribute('cx', x1); t.h1El.setAttribute('cy', y1);
            t.h2El.setAttribute('cx', x2); t.h2El.setAttribute('cy', y2);
            t.h1El.setAttribute('stroke', col);
            t.h2El.setAttribute('stroke', col);
            t.h1El.setAttribute('display', t.locked ? 'none' : '');
            t.h2El.setAttribute('display', t.locked ? 'none' : '');
            t.hitEl.setAttribute('cursor', t.locked ? 'default' : 'move');

            /* Action buttons at midpoint, offset upward */
            var mx = (x1 + x2) / 2, my = (y1 + y2) / 2 - 16;
            t.delEl.setAttribute('transform', 'translate(' + mx + ',' + my + ')');
            t.lockEl.setAttribute('transform', 'translate(' + (mx + 20) + ',' + my + ')');
            t.dupEl.setAttribute('transform',  'translate(' + (mx - 20) + ',' + my + ')');
            t.delEl.setAttribute('display', '');
            t.lockEl.setAttribute('display', '');
            t.dupEl.setAttribute('display', '');
            t.lockTxt.textContent = t.locked ? 'L' : 'U';
            t.lockTxt.setAttribute('fill', t.locked ? '#ffd700' : '#8b949e');
        } else {
            t.h1El.setAttribute('display', 'none');
            t.h2El.setAttribute('display', 'none');
            t.delEl.setAttribute('display', 'none');
            t.lockEl.setAttribute('display', 'none');
            t.dupEl.setAttribute('display', 'none');
            t.hitEl.setAttribute('cursor', 'move');
        }
    }

    function updateAllTrendlines() { trendlines.forEach(updateTrendline); }

    function _selectTrend(idx) {
        selectedTrend = idx;
        trendlines.forEach(updateTrendline);
    }
    function _deselectTrend() {
        if (selectedTrend < 0) return;
        selectedTrend = -1;
        trendlines.forEach(updateTrendline);
    }

    function addTrendline(t1, p1, t2, p2, color, width, style, locked, existingId) {
        var svg = ensureTrendOverlay(); if (!svg) return null;
        var id = (existingId != null) ? existingId : trendIdCtr++;
        if (id >= trendIdCtr) trendIdCtr = id + 1;
        var t = { id: id, t1: t1, p1: p1, t2: t2, p2: p2,
                  color: color || '#ffd700', width: width || 1,
                  style: style || 'solid', locked: locked || false };
        buildTrendEl(t);
        trendlines.push(t);
        requestAnimationFrame(function() { updateTrendline(t); });
        saveTrendlines();
        return t;
    }

    function delTrendline(idx) {
        var t = trendlines[idx]; if (!t) return;
        if (t.el && t.el.parentNode) t.el.parentNode.removeChild(t.el);
        trendlines.splice(idx, 1);
        if (selectedTrend === idx) selectedTrend = -1;
        else if (selectedTrend > idx) selectedTrend--;
        saveTrendlines();
    }

    function _dupTrendline(idx) {
        var t = trendlines[idx]; if (!t) return;
        var iv = _candleInterval();
        var pip = pipSize(_currentPair) * 5;
        addTrendline(t.t1 + iv, t.p1 + pip, t.t2 + iv, t.p2 + pip,
                     t.color, t.width, t.style, false);
    }

    /* Preview line while placing second point of a trendline */
    function _showTrendPreview(x1, y1, x2, y2) {
        var svg = ensureTrendOverlay(); if (!svg) return;
        if (!_trendPreviewEl) {
            _trendPreviewEl = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            _trendPreviewEl.setAttribute('pointer-events', 'none');
            svg.appendChild(_trendPreviewEl);
        }
        _trendPreviewEl.setAttribute('stroke', drawColor);
        _trendPreviewEl.setAttribute('stroke-width', drawWidth);
        _trendPreviewEl.setAttribute('stroke-dasharray', _svgDash(drawStyle));
        _trendPreviewEl.setAttribute('opacity', '0.55');
        _trendPreviewEl.setAttribute('x1', x1); _trendPreviewEl.setAttribute('y1', y1);
        _trendPreviewEl.setAttribute('x2', x2); _trendPreviewEl.setAttribute('y2', y2);
    }

    function _hideTrendPreview() {
        if (_trendPreviewEl && _trendPreviewEl.parentNode) {
            _trendPreviewEl.parentNode.removeChild(_trendPreviewEl);
        }
        _trendPreviewEl = null;
    }

    /* Global mouse handlers for trendline resize + move */
    document.addEventListener('mousemove', function(e) {
        if (!trendResize && !trendMove) return;
        /* Claim the event immediately — MUST be before any early returns so that
           null-coordinate frames never fall through to TVLC's pan handler. */
        e.preventDefault();
        var chartEl = document.getElementById('tvlw-chart'); if (!chartEl) return;
        var r  = chartEl.getBoundingClientRect();
        var cX = e.clientX - r.left, cY = e.clientY - r.top;

        if (trendResize) {
            var tr = trendResize, t = trendlines[tr.idx]; if (!t) return;
            var ct = _xToTimeExtrap(cX), cp = _yToPrice(cY);
            if (ct == null || cp == null) return;
            if (tr.handle === 'p1') { t.t1 = ct; t.p1 = cp; }
            else                    { t.t2 = ct; t.p2 = cp; }
            updateTrendline(t);
        }
        if (trendMove) {
            var tm = trendMove, t2 = trendlines[tm.idx]; if (!t2) return;
            var ct2 = _xToTimeExtrap(cX), cp2 = _yToPrice(cY);
            if (ct2 == null || cp2 == null) return;
            var dt = ct2 - tm.startTime, dp = cp2 - tm.startPrice;
            t2.t1 = tm.origT1 + dt; t2.p1 = tm.origP1 + dp;
            t2.t2 = tm.origT2 + dt; t2.p2 = tm.origP2 + dp;
            updateTrendline(t2);
        }
    });

    document.addEventListener('mouseup', function() {
        if (trendResize || trendMove) {
            try { if (chart) chart.applyOptions({ handleScroll:true, handleScale:true }); } catch(ex) {}
            saveTrendlines();
            trendResize = null; trendMove = null;
        }
    });

    /* Trendline persistence */
    function saveTrendlines() {
        try {
            localStorage.setItem(_trendKey(), JSON.stringify(
                trendlines.map(function(t) {
                    return { id:t.id, t1:t.t1, p1:t.p1, t2:t.t2, p2:t.p2,
                             color:t.color, width:t.width, style:t.style, locked:t.locked };
                })
            ));
        } catch(e) {}
    }

    function loadTrendlines() {
        try {
            /* Try new per-TF key first, fall back to legacy key */
            var raw = localStorage.getItem(_trendKey());
            if (!raw) {
                /* Migrate from old apex_drawings_PAIR which held all drawing types */
                var legacy = localStorage.getItem(_legacyDrawKey());
                if (legacy) {
                    var arr = JSON.parse(legacy);
                    arr.forEach(function(d) {
                        if (d.type === 'trend' && d.data && d.data.length === 2) {
                            addTrendline(d.data[0].time, d.data[0].value,
                                         d.data[1].time, d.data[1].value,
                                         d.color, d.width, 'solid', false, d.id);
                        }
                    });
                }
                return;
            }
            JSON.parse(raw).forEach(function(d) {
                addTrendline(d.t1, d.p1, d.t2, d.p2,
                             d.color, d.width, d.style||'solid', d.locked||false, d.id);
            });
        } catch(e) {}
    }

    /* ═══════════════════════════════════════════════════════════════════════
       BOX / CONSOLIDATION TOOL  (rect + square modes)
       ═══════════════════════════════════════════════════════════════════════ */

    function ensureBoxOverlay() {
        var el = document.getElementById('tvlw-chart');
        if (!el) return null;
        if (boxOverlay && boxOverlay.parentNode === el) return boxOverlay;
        boxOverlay = document.createElement('div');
        boxOverlay.id = 'apex-box-overlay';
        boxOverlay.style.cssText =
            'position:absolute;top:0;left:0;pointer-events:none;z-index:12;' +
            'width:100%;height:' + MAIN_H + 'px;overflow:hidden;';
        el.style.position = 'relative';
        el.appendChild(boxOverlay);
        return boxOverlay;
    }

    const HANDLE_POS = {
        tl:[0,0],tc:[0.5,0],tr:[1,0], ml:[0,0.5],mr:[1,0.5], bl:[0,1],bc:[0.5,1],br:[1,1]
    };
    const HANDLE_CURSOR = {
        tl:'nw-resize',tc:'n-resize',tr:'ne-resize',
        ml:'w-resize',              mr:'e-resize',
        bl:'sw-resize',bc:'s-resize',br:'se-resize'
    };

    function buildBoxEl(box) {
        var wrap = document.createElement('div');
        wrap.className = 'apex-box';
        wrap.dataset.boxId = box.id;
        wrap.style.cssText = 'position:absolute;pointer-events:none;box-sizing:border-box;';

        var bg = document.createElement('div');
        bg.className = 'apex-box-bg';
        bg.style.cssText = 'position:absolute;inset:0;pointer-events:none;';

        var drag = document.createElement('div');
        drag.className = 'apex-box-drag';
        drag.style.cssText = 'position:absolute;inset:0;z-index:10;cursor:move;pointer-events:auto;';
        drag.addEventListener('mousedown', function(e) {
            if (drawMode) return;
            e.stopPropagation(); e.preventDefault();
            var chartEl = document.getElementById('tvlw-chart'); if (!chartEl) return;
            var r   = chartEl.getBoundingClientRect();
            var cX  = e.clientX - r.left, cY = e.clientY - r.top;
            var idx = boxes.findIndex(function(b) { return String(b.id) === String(wrap.dataset.boxId); });
            if (idx < 0) return;
            var b = boxes[idx];
            _selectBox(idx);
            _deselectTrend();
            boxMove = { idx, startX:cX, startY:cY,
                startTime:  _xToTimeExtrap(cX) || b.t1,
                startPrice: _yToPrice(cY)       || b.p1,
                origT1:b.t1, origT2:b.t2, origP1:b.p1, origP2:b.p2 };
            try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
        });

        var stats = document.createElement('div');
        stats.className = 'apex-box-stats';
        stats.style.cssText =
            'position:absolute;top:4px;right:4px;z-index:12;pointer-events:none;' +
            'font-family:monospace;font-size:9px;text-align:right;line-height:1.5;' +
            'background:rgba(13,17,23,0.72);padding:2px 5px;border-radius:2px;';

        var hw = document.createElement('div');
        hw.style.cssText = 'position:absolute;inset:0;z-index:15;pointer-events:none;';
        Object.keys(HANDLE_POS).forEach(function(h) {
            var hEl = document.createElement('div');
            hEl.className = 'apex-box-handle';
            hEl.dataset.handle = h;
            hEl.style.cssText =
                'position:absolute;width:7px;height:7px;background:#fff;' +
                'border:1px solid #444;border-radius:1px;pointer-events:auto;z-index:16;' +
                'cursor:' + HANDLE_CURSOR[h] + ';transform:translate(-50%,-50%);';
            hEl.addEventListener('mousedown', function(e) {
                if (drawMode) return;
                e.stopPropagation(); e.preventDefault();
                var el2 = document.getElementById('tvlw-chart'); if (!el2) return;
                var idx  = boxes.findIndex(function(b) { return String(b.id)===String(wrap.dataset.boxId); });
                if (idx < 0) return;
                var b = boxes[idx];
                _selectBox(idx);
                boxResize = { idx, handle:h,
                    origT1:b.t1, origT2:b.t2, origP1:b.p1, origP2:b.p2 };
                try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
            });
            hw.appendChild(hEl);
        });

        var delBtn = document.createElement('div');
        delBtn.dataset.boxId = box.id;
        delBtn.textContent = '×';
        delBtn.style.cssText =
            'position:absolute;top:-8px;right:-8px;z-index:20;color:#ccc;font-size:13px;' +
            'cursor:pointer;pointer-events:auto;background:rgba(13,17,23,0.88);' +
            'width:16px;height:16px;line-height:16px;text-align:center;' +
            'border-radius:50%;border:1px solid #555;';
        delBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            var idx = boxes.findIndex(function(b) { return String(b.id)===String(delBtn.dataset.boxId); });
            if (idx >= 0) delBox(idx);
        });

        /* Duplicate button */
        var dupBtn = document.createElement('div');
        dupBtn.dataset.boxId = box.id;
        dupBtn.textContent = '+';
        dupBtn.title = 'Duplicate';
        dupBtn.style.cssText =
            'position:absolute;top:-8px;right:14px;z-index:20;color:#38b6ff;font-size:11px;' +
            'cursor:pointer;pointer-events:auto;background:rgba(13,17,23,0.88);' +
            'width:16px;height:16px;line-height:16px;text-align:center;' +
            'border-radius:50%;border:1px solid #555;';
        dupBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            var idx = boxes.findIndex(function(b) { return String(b.id)===String(dupBtn.dataset.boxId); });
            if (idx >= 0) _dupBox(idx);
        });

        /* Stats label for measure-type boxes — upper-right corner */
        var mStats = document.createElement('div');
        mStats.className = 'apex-measure-stats';
        mStats.style.cssText =
            'position:absolute;top:4px;right:4px;' +
            'z-index:12;pointer-events:none;font-family:monospace;text-align:right;' +
            'line-height:1.7;background:rgba(13,17,23,0.82);' +
            'padding:4px 8px;border-radius:4px;white-space:nowrap;display:none;';

        wrap.appendChild(bg); wrap.appendChild(drag); wrap.appendChild(stats);
        wrap.appendChild(mStats);
        wrap.appendChild(hw); wrap.appendChild(delBtn); wrap.appendChild(dupBtn);
        box.el = wrap; box.bgEl = bg; box.statsEl = stats;
        box.measureStatsEl = mStats; box.handleWrap = hw;
    }

    function updateBox(box) {
        if (!box.el || !chart || !S.candle) return;
        var chartEl = document.getElementById('tvlw-chart'); if (!chartEl) return;

        var x1 = _timeToXExtrap(box.t1), x2 = _timeToXExtrap(box.t2);
        var y1 = _priceToY(box.p1),      y2 = _priceToY(box.p2);
        if (x1 == null || x2 == null || y1 == null || y2 == null) {
            box.el.style.display = 'none'; return;
        }

        var chartW = chartEl.clientWidth - psWidth();
        var left = Math.min(x1,x2), right = Math.max(x1,x2);
        var top  = Math.min(y1,y2), bottom= Math.max(y1,y2);
        var w = right-left, h = bottom-top;

        /* Only hide if entirely below/above the price pane (x is CSS-clipped by overflow:hidden) */
        if (w < 2 || h < 2 || bottom < 0 || top > MAIN_H) {
            box.el.style.display = 'none'; return;
        }
        box.el.style.display = 'block';
        box.el.style.left = left+'px'; box.el.style.top  = top+'px';
        box.el.style.width= w+'px';   box.el.style.height= h+'px';

        var isSel = (selectedBox === boxes.indexOf(box));
        box.handleWrap.style.display = isSel ? 'block' : 'none';

        box.el.querySelectorAll('.apex-box-handle').forEach(function(hEl) {
            var hp = HANDLE_POS[hEl.dataset.handle];
            if (hp) { hEl.style.left = (hp[0]*100)+'%'; hEl.style.top = (hp[1]*100)+'%'; }
        });

        if (box.type === 'measure') {
            /* ── Measure box: direction-aware colour + rich stats ── */
            var ms  = _measureStats(box);
            var col = ms.color;
            box.el.style.border   = (isSel ? '2px' : '1px') + ' dashed ' + col;
            box.bgEl.style.background = _hexToRgba(col, 0.07);
            box.statsEl.textContent = '';
            if (box.measureStatsEl) {
                box.measureStatsEl.style.display = 'block';
                box.measureStatsEl.style.color = col;
                box.measureStatsEl.innerHTML =
                    '<span style="font-size:13px;font-weight:700">' +
                        ms.arrow + '&ensp;' + ms.pips + '</span>' +
                    '<br><span style="font-size:11px">' +
                        ms.pct + '&ensp;' + ms.price + '</span>' +
                    '<br><span style="font-size:10px;color:#8b949e">' +
                        ms.bars + '&ensp;&middot;&ensp;' + ms.time + '</span>';
            }
        } else {
            /* ── Regular rect/square box — no bar-count overlay ── */
            var bw  = box.borderWidth || 1;
            var col = box.color || drawColor;
            var fill = _hexToRgba(col, box.fillOpacity != null ? box.fillOpacity : 0.10);
            var borderStyle = _cssBorderStyle(box.borderStyle || 'solid');
            box.el.style.border       = (isSel ? bw+1 : bw) + 'px ' + borderStyle + ' ' + col;
            box.bgEl.style.background = fill;
            if (box.measureStatsEl) box.measureStatsEl.style.display = 'none';
            box.statsEl.textContent = '';
        }
    }

    function _cssBorderStyle(s) {
        if (s === 'dashed') return 'dashed';
        if (s === 'dotted') return 'dotted';
        return 'solid';
    }

    function updateAllBoxes() { boxes.forEach(updateBox); }
    function _selectBox(idx) { selectedBox = idx; boxes.forEach(updateBox); }
    function _deselectBox()  { selectedBox = -1;  boxes.forEach(updateBox); }

    function _startBoxPreview(sx, sy) {
        var ov = ensureBoxOverlay(); if (!ov) return;
        var prev = document.createElement('div');
        prev.id = 'apex-box-preview';
        var isMeas = boxDraw && boxDraw.isMeasure;
        prev.style.cssText =
            'position:absolute;pointer-events:none;box-sizing:border-box;overflow:hidden;' +
            'border:1px dashed ' + (isMeas ? '#8b949e' : drawColor) + ';' +
            'background:' + _hexToRgba(isMeas ? '#e6edf3' : drawColor, 0.04) + ';';
        prev.style.left = sx+'px'; prev.style.top = sy+'px';
        prev.style.width = '0px'; prev.style.height = '0px';
        /* Live stats label — only for measure tool */
        if (isMeas) {
            var sl = document.createElement('div');
            sl.className = 'apex-preview-stats';
            sl.style.cssText =
                'position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);' +
                'font-family:monospace;text-align:center;line-height:1.7;' +
                'background:rgba(13,17,23,0.85);padding:5px 10px;border-radius:4px;' +
                'white-space:nowrap;font-size:11px;display:none;pointer-events:none;';
            prev.appendChild(sl);
        }
        ov.appendChild(prev);
        if (boxDraw) boxDraw.previewEl = prev;
    }

    function _updateBoxPreview(cx, cy) {
        if (!boxDraw) return;
        var prev = boxDraw.previewEl || document.getElementById('apex-box-preview');
        if (!prev) { _startBoxPreview(boxDraw.startX, boxDraw.startY); return; }
        var x1 = boxDraw.startX, y1 = boxDraw.startY;
        var fx = cx, fy = cy;
        /* Square mode: constrain to equal pixel size */
        if (boxDraw.square) {
            var dx = fx - x1, dy = fy - y1;
            var sz = Math.min(Math.abs(dx), Math.abs(dy));
            fx = x1 + (dx >= 0 ? sz : -sz);
            fy = y1 + (dy >= 0 ? sz : -sz);
        }
        var pw = Math.abs(fx - x1), ph = Math.abs(fy - y1);
        prev.style.left   = Math.min(x1, fx) + 'px';
        prev.style.top    = Math.min(y1, fy) + 'px';
        prev.style.width  = pw + 'px';
        prev.style.height = ph + 'px';
        boxDraw._fx = fx; boxDraw._fy = fy;

        /* Live stats inside measure preview */
        if (boxDraw.isMeasure) {
            var sl = prev.querySelector('.apex-preview-stats');
            if (sl) {
                var curT = _xToTimeExtrap(fx), curP = _yToPrice(fy);
                if (curT && curP && pw >= 24 && ph >= 24) {
                    var tmpBox = { t1:boxDraw.startTime, p1:boxDraw.startPrice, t2:curT, p2:curP };
                    var ms = _measureStats(tmpBox);
                    prev.style.borderColor  = ms.color;
                    prev.style.background   = _hexToRgba(ms.color, 0.05);
                    sl.style.display = 'block';
                    sl.style.color   = ms.color;
                    sl.innerHTML =
                        '<span style="font-size:13px;font-weight:700">' + ms.arrow + '&ensp;' + ms.pips + '</span>' +
                        '<br><span style="font-size:11px">' + ms.pct + '&ensp;' + ms.price + '</span>' +
                        '<br><span style="font-size:10px;color:#8b949e">' + ms.bars + '&ensp;&middot;&ensp;' + ms.time + '</span>';
                } else {
                    sl.style.display = 'none';
                }
            }
        }
    }

    function _finalizeBoxDraw(cx, cy) {
        if (!boxDraw) return;
        var prev = boxDraw.previewEl || document.getElementById('apex-box-preview');
        if (prev && prev.parentNode) prev.parentNode.removeChild(prev);
        var fx = boxDraw._fx != null ? boxDraw._fx : cx;
        var fy = boxDraw._fy != null ? boxDraw._fy : cy;
        var ct = _xToTimeExtrap(fx), cp = _yToPrice(fy);
        if (!ct || !cp || Math.abs(fx-boxDraw.startX)<8 || Math.abs(fy-boxDraw.startY)<8) {
            boxDraw = null; return;
        }
        addBox(boxDraw.startTime, boxDraw.startPrice, ct, cp,
               drawColor, drawWidth, null, null, null, drawStyle,
               boxDraw.isMeasure ? 'measure' : 'rect');
        boxDraw = null;
    }

    function addBox(t1, p1, t2, p2, color, borderWidth, existingId, fillOpacity, _sq, borderStyle, type) {
        var ov = ensureBoxOverlay(); if (!ov) return;
        var id = (existingId != null) ? existingId : boxIdCtr++;
        if (id >= boxIdCtr) boxIdCtr = id + 1;
        var box = { id, t1, p1, t2, p2, color: color||'#ffd700',
                    borderWidth: borderWidth||1,
                    fillOpacity: (fillOpacity!=null) ? fillOpacity : 0.10,
                    borderStyle: borderStyle || 'solid',
                    type: type || 'rect' };
        buildBoxEl(box);
        ov.appendChild(box.el);
        boxes.push(box);
        requestAnimationFrame(function(){ updateBox(box); });
        saveBoxes();
    }

    function delBox(idx) {
        var box = boxes[idx]; if (!box) return;
        if (box.el && box.el.parentNode) box.el.parentNode.removeChild(box.el);
        boxes.splice(idx, 1);
        if (selectedBox === idx) selectedBox = -1;
        else if (selectedBox > idx) selectedBox--;
        saveBoxes();
    }

    function _dupBox(idx) {
        var b = boxes[idx]; if (!b) return;
        var iv = _candleInterval();
        addBox(b.t1 + iv, b.p1, b.t2 + iv, b.p2,
               b.color, b.borderWidth, null, b.fillOpacity, null, b.borderStyle, b.type||'rect');
    }

    /* Box global handlers */
    document.addEventListener('mousemove', function(e) {
        if (!boxDraw && !boxResize && !boxMove) return;
        e.preventDefault();   // claimed before any early-return so TVLC never pans
        var chartEl = document.getElementById('tvlw-chart'); if (!chartEl) return;
        var r  = chartEl.getBoundingClientRect();
        var cX = e.clientX - r.left, cY = e.clientY - r.top;

        if (boxDraw) { _updateBoxPreview(cX, cY); return; }
        if (boxResize) {
            var br = boxResize, b = boxes[br.idx]; if (!b) return;
            var h = br.handle;
            var ct = _xToTimeExtrap(cX), cp = _yToPrice(cY);
            if ((h==='tl'||h==='ml'||h==='bl') && ct) b.t1 = ct;
            if ((h==='tr'||h==='mr'||h==='br') && ct) b.t2 = ct;
            if ((h==='tl'||h==='tc'||h==='tr') && cp) b.p1 = cp;
            if ((h==='bl'||h==='bc'||h==='br') && cp) b.p2 = cp;
            updateBox(b);
        }
        if (boxMove) {
            var bm = boxMove, b2 = boxes[bm.idx]; if (!b2) return;
            var ct2 = _xToTimeExtrap(cX), cp2 = _yToPrice(cY);
            if (ct2 == null || cp2 == null) return;
            b2.t1 = bm.origT1 + (ct2 - bm.startTime);
            b2.t2 = bm.origT2 + (ct2 - bm.startTime);
            b2.p1 = bm.origP1 + (cp2 - bm.startPrice);
            b2.p2 = bm.origP2 + (cp2 - bm.startPrice);
            updateBox(b2);
        }
    });

    document.addEventListener('mouseup', function(e) {
        if (boxDraw) {
            var chartEl2 = document.getElementById('tvlw-chart');
            if (chartEl2) {
                var r2 = chartEl2.getBoundingClientRect();
                _finalizeBoxDraw(e.clientX - r2.left, e.clientY - r2.top);
            } else { boxDraw = null; }
            /* Re-enable chart scroll/scale now that drawing is done */
            try { if (chart) chart.applyOptions({ handleScroll:true, handleScale:true }); } catch(ex) {}
            return;
        }
        if (boxResize || boxMove) {
            try { if (chart) chart.applyOptions({ handleScroll:true, handleScale:true }); } catch(ex) {}
            saveBoxes(); boxResize = null; boxMove = null;
        }
    });

    function saveBoxes() {
        try { localStorage.setItem(_boxKey(), JSON.stringify(
            boxes.map(function(b){ return { id:b.id, t1:b.t1, p1:b.p1, t2:b.t2, p2:b.p2,
                color:b.color, borderWidth:b.borderWidth, fillOpacity:b.fillOpacity,
                borderStyle:b.borderStyle||'solid', type:b.type||'rect' }; })
        )); } catch(e) {}
    }

    function loadBoxes() {
        try {
            var raw = localStorage.getItem(_boxKey());
            if (!raw) raw = localStorage.getItem(_legacyBoxKey());
            if (!raw) return;
            JSON.parse(raw).forEach(function(d){
                addBox(d.t1,d.p1,d.t2,d.p2,d.color,d.borderWidth,d.id,d.fillOpacity,null,d.borderStyle,d.type||'rect');
            });
        } catch(e) {}
    }

    /* ═══════════════════════════════════════════════════════════════════════
       POSITION TOOL  (Long / Short)
       ═══════════════════════════════════════════════════════════════════════ */

    function ensurePosOverlay() {
        var el = document.getElementById('tvlw-chart'); if (!el) return null;
        if (posOverlay && posOverlay.parentNode === el) return posOverlay;
        posOverlay = document.createElement('div');
        posOverlay.id = 'apex-pos-overlay';
        posOverlay.style.cssText =
            'position:absolute;top:0;left:0;pointer-events:none;z-index:15;' +
            'width:100%;height:' + MAIN_H + 'px;overflow:visible;';
        el.style.position = 'relative';
        el.appendChild(posOverlay);
        return posOverlay;
    }

    function posCalc(pos) {
        var risk    = Math.abs(pos.entry - pos.sl);
        var reward  = Math.abs(pos.tp    - pos.entry);
        var rr      = risk > 0 ? reward / risk : 0;
        var pip     = pipSize(_currentPair);
        var bal     = window._apexAccountBalance || 500;
        var qty     = risk > 0 ? Math.max(1, Math.round((bal * 0.01) / risk)) : 0;
        var riskPct = pos.entry > 0 ? risk   / pos.entry * 100 : 0;
        var rewPct  = pos.entry > 0 ? reward / pos.entry * 100 : 0;
        var cur     = window._apexLastPrice || pos.entry;
        var pnl     = pos.direction === 'long' ? (cur-pos.entry)*qty : (pos.entry-cur)*qty;
        var riskPips = pip > 0 ? risk   / pip : 0;
        var rewPips  = pip > 0 ? reward / pip : 0;
        return { risk, reward, rr, qty, riskPct, rewPct, pnl,
                 riskPips, rewPips, riskAmt: risk*qty, rewardAmt: reward*qty };
    }

    function _makeLine(posId, lineType) {
        var line = document.createElement('div');
        line.className = 'apex-pz-line';
        line.dataset.posId = posId; line.dataset.lineType = lineType;
        line.style.cssText =
            'position:absolute;left:0;right:0;height:6px;' +
            'cursor:ns-resize;pointer-events:auto;z-index:20;';
        line.addEventListener('mousedown', function(e) {
            e.stopPropagation(); e.preventDefault();
            var idx = positions.findIndex(function(p){ return String(p.id)===String(line.dataset.posId); });
            if (idx < 0) return;
            var p = positions[idx];
            posDrag = { type:'v', idx,
                lineType: line.dataset.lineType,
                startEntry:p.entry, startTp:p.tp, startSl:p.sl };
            try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
        });
        return line;
    }

    function buildPosEl(pos) {
        var wrap = document.createElement('div');
        wrap.className = 'apex-pos';
        wrap.dataset.posId = pos.id;
        wrap.style.cssText =
            'position:absolute;top:0;height:' + MAIN_H + 'px;' +
            'pointer-events:none;overflow:hidden;';

        var pZone  = document.createElement('div');
        pZone.style.cssText = 'position:absolute;left:0;right:0;pointer-events:none;box-sizing:border-box;';
        var pLabel = document.createElement('div');
        pLabel.style.cssText = 'position:absolute;font-family:monospace;font-size:10px;' +
            'white-space:nowrap;padding:1px 5px;border-radius:2px;font-weight:600;' +
            'background:rgba(0,0,0,0.55);color:#00ff88;pointer-events:none;';
        pZone.appendChild(pLabel);

        var lZone  = document.createElement('div');
        lZone.style.cssText = 'position:absolute;left:0;right:0;pointer-events:none;box-sizing:border-box;';
        var lLabel = document.createElement('div');
        lLabel.style.cssText = 'position:absolute;font-family:monospace;font-size:10px;' +
            'white-space:nowrap;padding:1px 5px;border-radius:2px;font-weight:600;' +
            'background:rgba(0,0,0,0.55);color:#ff3366;pointer-events:none;';
        lZone.appendChild(lLabel);

        var cLabel = document.createElement('div');
        cLabel.style.cssText = 'position:absolute;left:6px;pointer-events:none;' +
            'font-family:monospace;font-size:10px;white-space:nowrap;' +
            'padding:3px 7px;border-radius:3px;' +
            'border:1px solid rgba(120,120,120,0.35);background:rgba(13,17,23,0.88);';

        var tpLine  = _makeLine(pos.id, 'tp');
        var entLine = _makeLine(pos.id, 'entry');
        var slLine  = _makeLine(pos.id, 'sl');

        var dragArea = document.createElement('div');
        dragArea.style.cssText =
            'position:absolute;left:0;right:0;cursor:move;pointer-events:auto;z-index:10;';
        dragArea.addEventListener('mousedown', function(e) {
            e.stopPropagation(); e.preventDefault();
            var chartEl = document.getElementById('tvlw-chart'); if (!chartEl) return;
            var r   = chartEl.getBoundingClientRect();
            var cX  = e.clientX - r.left, cY = e.clientY - r.top;
            var idx = positions.findIndex(function(p){ return String(p.id)===String(wrap.dataset.posId); });
            if (idx < 0) return;
            var p = positions[idx];
            posDrag = { type:'move', idx,
                startTime:  _xToTime(cX)  || p.t1,
                startPrice: _yToPrice(cY) || p.entry,
                startT1: p.t1, startT2: p.t2,
                startEntry: p.entry, startTp: p.tp, startSl: p.sl };
            try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
        });

        var resizeR = document.createElement('div');
        resizeR.style.cssText =
            'position:absolute;right:0;top:0;bottom:0;width:6px;' +
            'cursor:ew-resize;pointer-events:auto;z-index:25;background:transparent;';
        resizeR.addEventListener('mousedown', function(e) {
            e.stopPropagation(); e.preventDefault();
            var idx = positions.findIndex(function(p){ return String(p.id)===String(wrap.dataset.posId); });
            if (idx < 0) return;
            posDrag = { type:'resize-r', idx };
            try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
        });

        var delBtn = document.createElement('div');
        delBtn.dataset.posId = pos.id;
        delBtn.textContent = '×';
        delBtn.style.cssText =
            'position:absolute;right:4px;color:#ccc;font-size:14px;cursor:pointer;' +
            'pointer-events:auto;z-index:30;background:rgba(13,17,23,0.88);' +
            'width:16px;height:16px;line-height:16px;text-align:center;' +
            'border-radius:50%;border:1px solid #555;';
        delBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            var idx = positions.findIndex(function(p){ return String(p.id)===String(delBtn.dataset.posId); });
            if (idx >= 0) delPosition(idx);
        });

        wrap.appendChild(pZone); wrap.appendChild(lZone);
        wrap.appendChild(dragArea); wrap.appendChild(cLabel);
        wrap.appendChild(tpLine); wrap.appendChild(entLine); wrap.appendChild(slLine);
        wrap.appendChild(resizeR); wrap.appendChild(delBtn);

        pos.el       = wrap;   pos.pZone   = pZone;   pos.lZone   = lZone;
        pos.pLabel   = pLabel; pos.lLabel  = lLabel;  pos.cLabel  = cLabel;
        pos.tpLine   = tpLine; pos.entLine = entLine; pos.slLine  = slLine;
        pos.dragArea = dragArea; pos.resizeR = resizeR; pos.delBtn = delBtn;
    }

    function updatePos(pos) {
        if (!pos.el || !chart || !S.candle) return;

        var x1 = _timeToXExtrap(pos.t1), x2 = _timeToXExtrap(pos.t2);
        var chartEl = document.getElementById('tvlw-chart');
        var chartW  = chartEl ? chartEl.clientWidth - psWidth() : 700;

        if (x1 == null || x2 == null) { pos.el.style.display = 'none'; return; }

        var posLeft  = Math.min(x1, x2);
        var posWidth = Math.max(Math.abs(x2 - x1), 10);

        var entY = _priceToY(pos.entry);
        var tpY  = _priceToY(pos.tp);
        var slY  = _priceToY(pos.sl);
        if (entY==null||tpY==null||slY==null) { pos.el.style.display='none'; return; }

        pos.el.style.display = 'block';
        pos.el.style.left  = posLeft  + 'px';
        pos.el.style.width = posWidth + 'px';

        var profTop, profBot, lossTop, lossBot;
        if (pos.direction === 'long') {
            profTop = Math.min(tpY, entY); profBot = Math.max(tpY, entY);
            lossTop = Math.min(entY, slY); lossBot = Math.max(entY, slY);
        } else {
            lossTop = Math.min(slY, entY); lossBot = Math.max(slY, entY);
            profTop = Math.min(entY, tpY); profBot = Math.max(entY, tpY);
        }
        var profH = Math.max(profBot - profTop, 4);
        var lossH = Math.max(lossBot - lossTop, 4);

        var dec = isJPY(_currentPair) ? 3 : 5;
        var fp  = function(n){ return n.toFixed(dec); };
        var fpc = function(n){ return n.toFixed(3); };
        var fpp = function(n){ return n.toFixed(1); };
        var m   = posCalc(pos);

        var tpText = 'Target: ' + fp(pos.tp) + ' (' + fpc(m.rewPct) + '%) ' +
                     fpp(m.rewPips) + 'p  Amt:' + m.rewardAmt.toFixed(2);
        var slText = 'Stop:   ' + fp(pos.sl) + ' (' + fpc(m.riskPct) + '%) ' +
                     fpp(m.riskPips) + 'p  Amt:' + m.riskAmt.toFixed(2);

        var pBorder = pos.direction==='long' ? 'border-top' : 'border-bottom';
        pos.pZone.style.cssText =
            'position:absolute;left:0;right:0;top:' + profTop + 'px;height:' + profH + 'px;' +
            'background:rgba(0,255,100,0.11);box-sizing:border-box;pointer-events:none;' +
            pBorder + ':2px solid rgba(0,255,100,0.82);';
        pos.pLabel.style.position = 'absolute'; pos.pLabel.style.left = '5px';
        if (pos.direction==='long') { pos.pLabel.style.top='3px'; pos.pLabel.style.bottom=''; }
        else                        { pos.pLabel.style.bottom='3px'; pos.pLabel.style.top=''; }
        pos.pLabel.textContent = tpText;

        var lBorder = pos.direction==='long' ? 'border-bottom' : 'border-top';
        pos.lZone.style.cssText =
            'position:absolute;left:0;right:0;top:' + lossTop + 'px;height:' + lossH + 'px;' +
            'background:rgba(255,51,102,0.11);box-sizing:border-box;pointer-events:none;' +
            lBorder + ':2px solid rgba(255,51,102,0.82);';
        pos.lLabel.style.position = 'absolute'; pos.lLabel.style.left = '5px';
        if (pos.direction==='long') { pos.lLabel.style.bottom='3px'; pos.lLabel.style.top=''; }
        else                        { pos.lLabel.style.top='3px';    pos.lLabel.style.bottom=''; }
        pos.lLabel.textContent = slText;

        var lBase = 'position:absolute;left:0;right:0;height:6px;';
        pos.tpLine.style.cssText  = lBase + 'top:' + (tpY-3)  + 'px;background:rgba(0,255,100,0.85);cursor:ns-resize;pointer-events:auto;z-index:20;';
        pos.entLine.style.cssText = lBase + 'top:' + (entY-3) + 'px;background:transparent;border-top:2px dashed rgba(255,255,255,0.85);cursor:ns-resize;pointer-events:auto;z-index:20;';
        pos.slLine.style.cssText  = lBase + 'top:' + (slY-3)  + 'px;background:rgba(255,51,102,0.85);cursor:ns-resize;pointer-events:auto;z-index:20;';

        var boxTop = Math.min(profTop, lossTop);
        var boxBot = Math.max(profBot, lossBot);
        var boxH   = Math.max(boxBot - boxTop, 20);
        pos.dragArea.style.top    = boxTop + 'px';
        pos.dragArea.style.height = boxH   + 'px';

        var pnlColor = m.pnl >= 0 ? '#00ff88' : '#ff3366';
        pos.cLabel.style.top = Math.max(entY - 34, boxTop + 4) + 'px';
        pos.cLabel.innerHTML =
            '<span style="color:' + pnlColor + '">P&L: ' + m.pnl.toFixed(5) + '</span>' +
            '  Qty:' + m.qty + '<br>R/R: <strong>' + m.rr.toFixed(2) + '</strong>';

        pos.delBtn.style.top = Math.max(entY - 8, boxTop) + 'px';
    }

    function updateAllPos() { positions.forEach(updatePos); }

    function addPosition(direction, entry, tp, sl, t1, t2, existingId) {
        var ov = ensurePosOverlay(); if (!ov) return;
        var id = (existingId != null) ? existingId : posIdCtr++;
        if (id >= posIdCtr) posIdCtr = id + 1;
        var interval = _candleInterval();
        if (!t1 || !t2) {
            var now = _candleData.length > 0
                ? _candleData[_candleData.length - 1].time
                : Math.floor(Date.now() / 1000);
            t1 = t1 || now;
            t2 = t2 || (t1 + 20 * interval);
        }
        var pos = { id, direction, entry, tp, sl, t1, t2 };
        buildPosEl(pos);
        ov.appendChild(pos.el);
        positions.push(pos);
        requestAnimationFrame(function(){ updatePos(pos); });
        savePos();
    }

    function delPosition(idx) {
        var pos = positions[idx]; if (!pos) return;
        if (pos.el && pos.el.parentNode) pos.el.parentNode.removeChild(pos.el);
        positions.splice(idx, 1);
        savePos();
    }

    function savePos() {
        try { localStorage.setItem(_posKey(), JSON.stringify(
            positions.map(function(p){
                return { id:p.id, direction:p.direction,
                         entry:p.entry, tp:p.tp, sl:p.sl,
                         t1:p.t1, t2:p.t2 };
            })
        )); } catch(e) {}
    }

    function loadPos() {
        try {
            var raw = localStorage.getItem(_posKey());
            if (!raw) raw = localStorage.getItem(_legacyPosKey());
            if (!raw) return;
            JSON.parse(raw).forEach(function(d){
                addPosition(d.direction, d.entry, d.tp, d.sl, d.t1, d.t2, d.id);
            });
        } catch(e) {}
    }

    /* Position drag globals */
    document.addEventListener('mousemove', function(e) {
        if (!posDrag || !chart || !S.candle) return;
        e.preventDefault();   // claimed before any early-return so TVLC never pans
        var el = document.getElementById('tvlw-chart'); if (!el) return;
        var r  = el.getBoundingClientRect();
        var cX = e.clientX - r.left, cY = e.clientY - r.top;
        var pos = positions[posDrag.idx]; if (!pos) return;

        if (posDrag.type === 'v') {
            var price = _yToPrice(cY); if (!price) return;
            var lt = posDrag.lineType;
            if (lt === 'entry') {
                var d = price - posDrag.startEntry;
                pos.entry = posDrag.startEntry + d;
                pos.tp    = posDrag.startTp    + d;
                pos.sl    = posDrag.startSl    + d;
            } else if (lt === 'tp') { pos.tp = price; }
              else if (lt === 'sl') { pos.sl = price; }
        } else if (posDrag.type === 'move') {
            var ct = _xToTimeExtrap(cX), cp = _yToPrice(cY);
            if (ct == null || cp == null) return;
            var dt = ct - posDrag.startTime, dp = cp - posDrag.startPrice;
            pos.t1    = posDrag.startT1    + dt; pos.t2    = posDrag.startT2    + dt;
            pos.entry = posDrag.startEntry + dp; pos.tp    = posDrag.startTp    + dp;
            pos.sl    = posDrag.startSl    + dp;
        } else if (posDrag.type === 'resize-r') {
            var ct2 = _xToTimeExtrap(cX); if (ct2 == null) return;
            var iv   = _candleInterval();
            pos.t2   = Math.max(pos.t1 + 5*iv, Math.min(pos.t1 + 100*iv, ct2));
        }
        updatePos(pos);
    });

    document.addEventListener('mouseup', function() {
        if (posDrag) {
            try { if (chart) chart.applyOptions({ handleScroll:true, handleScale:true }); } catch(ex) {}
            savePos(); posDrag = null;
        }
    });

    /* ═══════════════════════════════════════════════════════════════════════
       H-LINE / PIPS DRAWING TOOLS
       ═══════════════════════════════════════════════════════════════════════ */

    function saveDrawings() {
        try {
            var data = drawings
                .filter(function(d){ return d.type==='h-line'; })
                .map(function(d) {
                    return { type:'h-line', price:d.price, color:d.color, width:d.width, style:d.style };
                });
            localStorage.setItem(_hlKey(), JSON.stringify(data));
        } catch(e) {}
    }

    function loadDrawings() {
        try {
            var raw = localStorage.getItem(_hlKey());
            /* Fallback: read old combined key and extract h-line/pips */
            if (!raw) raw = localStorage.getItem(_legacyDrawKey());
            if (!raw) return;
            JSON.parse(raw).forEach(function(d) {
                if (d.type === 'h-line') {
                    try {
                        var style = d.style || 'solid';
                        var ls = _twlcLineStyle(style);
                        var pl = S.candle.createPriceLine({ price:d.price, color:d.color||'#ffd700',
                            lineWidth:d.width||1, lineStyle:ls,
                            axisLabelVisible:true, title:fmtPrice(d.price) });
                        drawings.push({ type:'h-line', priceLine:pl, price:d.price, color:d.color, width:d.width, style:style });
                    } catch(e) {}
                }
                /* pips type (legacy) and trend type are ignored here */
            });
        } catch(e) {}
    }

    function _twlcLineStyle(style) {
        if (!LC()) return 0;
        if (style === 'dashed') return LC().LineStyle.Dashed;
        if (style === 'dotted') return LC().LineStyle.Dotted;
        return LC().LineStyle.Solid;
    }

    function _removeOne(d) {
        try {
            if (d.type==='h-line') S.candle.removePriceLine(d.priceLine);
        } catch(e) {}
    }

    function copyDragData(d) {
        if (d.type==='h-line') return { price:d.price };
        return {};
    }

    function findNearest(price, time) {
        var pip = pipSize(_currentPair), thr = pip*12, best = -1, bestD = Infinity;
        for (var i = 0; i < drawings.length; i++) {
            var d = drawings[i], dist = Infinity;
            if (d.type==='h-line') { dist = Math.abs((d.price||0) - price); }
            if (dist<thr && dist<bestD) { bestD=dist; best=i; }
        }
        return best;
    }

    function selectDrawing(idx) {
        if (selectedIdx >= 0) deselectDrawing();
        var d = drawings[idx]; selectedIdx = idx; d._selected = true;
        try {
            if (d.type==='h-line') d.priceLine.applyOptions({ lineStyle:LC().LineStyle.LargeDashed, lineWidth:(d.width||drawWidth)+1 });
        } catch(e) {}
    }

    function deselectDrawing() {
        if (selectedIdx < 0) return;
        var d = drawings[selectedIdx]; d._selected = false;
        try {
            if (d.type==='h-line') d.priceLine.applyOptions({ lineStyle:_twlcLineStyle(d.style||'solid'), lineWidth:d.width||drawWidth });
        } catch(e) {}
        selectedIdx = -1;
    }

    function moveDrawing(idx, priceDelta) {
        var d = drawings[idx];
        if (d.type==='h-line' && dragStartData) {
            var np = (dragStartData.price||0) + priceDelta;
            try { d.priceLine.applyOptions({ price:np, title:fmtPrice(np) }); d.price=np; } catch(e) {}
        }
        saveDrawings();
    }

    /* ═══════════════════════════════════════════════════════════════════════
       POSITION ENTRY FORM  (Long / Short tool — manual price input)
       ═══════════════════════════════════════════════════════════════════════ */

    function _posFormField(label, id, value, step, color) {
        return '<div style="margin-bottom:8px">' +
            '<div style="color:' + color + ';font-size:10px;font-family:monospace;' +
            'margin-bottom:3px;font-weight:600;letter-spacing:0.04em">' + label + '</div>' +
            '<input id="' + id + '" type="number" step="' + step + '" value="' + value + '"' +
            ' style="width:100%;background:#21262d;color:#e6edf3;' +
            'border:1px solid #30363d;border-radius:3px;padding:5px 8px;' +
            'font-family:monospace;font-size:12px;box-sizing:border-box;outline:none">' +
            '</div>';
    }

    function _showPositionForm(dir, entry, tp, sl, barTime, iv, screenX, screenY) {
        _hidePositionForm();
        var col   = dir === 'long' ? '#00ff88' : '#ff3366';
        var bgCol = dir === 'long' ? '#003d1f'  : '#3d0010';
        var arrow = dir === 'long' ? '▲ LONG'   : '▼ SHORT';
        var dec   = isJPY(_currentPair) ? 3  : 5;
        var step  = isJPY(_currentPair) ? 0.001 : 0.00001;

        var chartEl = document.getElementById('tvlw-chart');
        if (!chartEl) return;
        var chartW = chartEl.clientWidth;

        /* Keep form inside chart bounds */
        var fx = Math.min(screenX + 18, chartW - 230);
        var fy = Math.max(screenY - 110, 8);

        var form = document.createElement('div');
        form.id = 'apex-pos-form';
        form.style.cssText =
            'position:absolute;left:' + fx + 'px;top:' + fy + 'px;' +
            'z-index:60;background:rgba(13,17,23,0.97);' +
            'border:1.5px solid ' + col + ';border-radius:8px;' +
            'padding:14px;width:215px;box-sizing:border-box;' +
            'box-shadow:0 8px 28px rgba(0,0,0,0.7);' +
            'font-family:monospace;';

        form.innerHTML =
            '<div style="color:' + col + ';font-weight:700;font-size:13px;' +
            'margin-bottom:11px;display:flex;align-items:center">' +
            arrow +
            '<span style="color:#7d8590;font-size:10px;font-weight:400;margin-left:auto">' +
            (_currentPair || '') + '</span></div>' +
            _posFormField('Entry',      'apf-entry', entry.toFixed(dec), step, '#e6edf3') +
            _posFormField('Stop Loss',  'apf-sl',    sl.toFixed(dec),    step, '#ff3366') +
            _posFormField('Take Profit','apf-tp',    tp.toFixed(dec),    step, '#00ff88') +
            '<div style="display:flex;gap:6px;margin-top:12px">' +
            '<button id="apf-place" style="flex:1;background:' + bgCol + ';color:' + col +
            ';border:1px solid ' + col + ';border-radius:4px;padding:7px;cursor:pointer;' +
            'font-weight:700;font-size:12px;font-family:monospace">Place</button>' +
            '<button id="apf-cancel" style="background:#21262d;color:#8b949e;' +
            'border:1px solid #30363d;border-radius:4px;padding:7px 11px;' +
            'cursor:pointer;font-size:12px">✕</button></div>';

        chartEl.appendChild(form);

        function _doPlace() {
            var e = parseFloat((document.getElementById('apf-entry')  || {}).value);
            var s = parseFloat((document.getElementById('apf-sl')     || {}).value);
            var t = parseFloat((document.getElementById('apf-tp')     || {}).value);
            if (isNaN(e) || isNaN(s) || isNaN(t)) return;
            addPosition(dir, e, t, s, barTime, barTime + 20 * iv);
            _hidePositionForm();
        }

        document.getElementById('apf-place').addEventListener('click', _doPlace);
        document.getElementById('apf-cancel').addEventListener('click', _hidePositionForm);

        form.addEventListener('keydown', function(ev) {
            if (ev.key === 'Enter')  _doPlace();
            if (ev.key === 'Escape') _hidePositionForm();
            ev.stopPropagation();
        });

        setTimeout(function() {
            var inp = document.getElementById('apf-entry');
            if (inp) { inp.focus(); inp.select(); }
        }, 60);
    }

    function _hidePositionForm() {
        var f = document.getElementById('apex-pos-form');
        if (f && f.parentNode) f.parentNode.removeChild(f);
    }

    /* Dismiss form when clicking anywhere outside it */
    document.addEventListener('mousedown', function(e) {
        var f = document.getElementById('apex-pos-form');
        if (f && !f.contains(e.target)) _hidePositionForm();
    });

    /* Dismiss form on Escape */
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') _hidePositionForm();
    });

    /* ═══════════════════════════════════════════════════════════════════════
       AUTO-SAVE TIMER
       ═══════════════════════════════════════════════════════════════════════ */

    function _startAutoSave() {
        _stopAutoSave();
        _autoSaveTimer = setInterval(function() {
            saveDrawings(); saveTrendlines(); saveBoxes(); savePos();
        }, 60000);
    }

    function _stopAutoSave() {
        if (_autoSaveTimer) { clearInterval(_autoSaveTimer); _autoSaveTimer = null; }
    }

    /* ═══════════════════════════════════════════════════════════════════════
       CHART LIFECYCLE  (destroy / init / load)
       ═══════════════════════════════════════════════════════════════════════ */

    function destroy() {
        _stopAutoSave();
        if (_mouseListeners) {
            var old = document.getElementById('tvlw-chart');
            if (old) {
                old.removeEventListener('mousedown', _mouseListeners.down);
                old.removeEventListener('mousemove', _mouseListeners.move);
                old.removeEventListener('mouseup',   _mouseListeners.up);
            }
            document.removeEventListener('mouseup', _mouseListeners.up);
            _mouseListeners = null;
        }
        _tradePriceLines = [];   /* cleared before chart.remove() so TWLC handles cleanup */
        _hidePositionForm();
        if (chart) { try { chart.remove(); } catch(e) {} }
        chart = null; S = {}; wm = null;
        drawings = [];
        selectedIdx = -1; isDragging = false;
        dragStartPrice = null; dragStartData = null;

        /* Trendlines */
        trendlines.forEach(function(t){ if (t.el&&t.el.parentNode) t.el.parentNode.removeChild(t.el); });
        trendlines = [];
        if (trendOverlay && trendOverlay.parentNode) trendOverlay.parentNode.removeChild(trendOverlay);
        trendOverlay = null; selectedTrend = -1; trendP1 = null;
        _hideTrendPreview(); trendResize = null; trendMove = null;

        /* Positions */
        positions.forEach(function(pos){ if (pos.el&&pos.el.parentNode) pos.el.parentNode.removeChild(pos.el); });
        positions = [];
        if (posOverlay && posOverlay.parentNode) posOverlay.parentNode.removeChild(posOverlay);
        posOverlay = null; posDrag = null;

        /* Boxes */
        boxes.forEach(function(b){ if (b.el&&b.el.parentNode) b.el.parentNode.removeChild(b.el); });
        boxes = [];
        if (boxOverlay && boxOverlay.parentNode) boxOverlay.parentNode.removeChild(boxOverlay);
        boxOverlay = null; boxDraw = null; boxResize = null; boxMove = null; selectedBox = -1;
    }

    function init() {
        var el = document.getElementById('tvlw-chart');
        if (!el || el.clientWidth === 0 || !LC()) return false;
        destroy();

        chart = LC().createChart(el, {
            width: el.clientWidth, height: TOTAL_H,
            layout: { background:{ type:'solid', color:'#0d1117' },
                textColor:'#e6edf3', fontFamily:"'IBM Plex Sans', sans-serif" },
            grid: { vertLines:{ color:'#21262d' }, horzLines:{ color:'#21262d' } },
            crosshair: {
                mode: LC().CrosshairMode.Normal,
                vertLine:{ color:'rgba(180,180,180,0.55)', width:1,
                    style:LC().LineStyle.Solid, labelBackgroundColor:'#21262d' },
                horzLine:{ color:'rgba(180,180,180,0.55)', width:1,
                    style:LC().LineStyle.Solid, labelBackgroundColor:'#21262d' },
            },
            rightPriceScale: { borderColor:'#30363d', visible:true },
            leftPriceScale:  { visible:false },
            timeScale: { borderColor:'#30363d', timeVisible:true,
                secondsVisible:false, rightOffset:5, shiftVisibleRangeOnNewBar:false },
        });

        S.candle = chart.addSeries(LC().CandlestickSeries, {
            upColor:'#00ff88', downColor:'#ff3366',
            borderUpColor:'#00ff88', borderDownColor:'#ff3366',
            wickUpColor:'#00ff88', wickDownColor:'#ff3366',
            priceFormat: priceFormat(_currentPair),
        }, 0);
        S.emas = [];

        S.cci = chart.addSeries(LC().LineSeries, { color:'#38b6ff', lineWidth:1.5,
            lastValueVisible:false, priceLineVisible:false, crosshairMarkerVisible:false }, 1);
        cciRef(100,'#00ff88'); cciRef(0,'#ffd700'); cciRef(-100,'#ff3366');

        S.macd = chart.addSeries(LC().HistogramSeries, {
            lastValueVisible:false, priceLineVisible:false }, 2);
        dotted(0, S.macd);

        requestAnimationFrame(function() {
            var panes = chart.panes();
            try { if (panes[0]) panes[0].setHeight(MAIN_H); } catch(e) {}
            try { if (panes[1]) panes[1].setHeight(IND_H);  } catch(e) {}
            try { if (panes[2]) panes[2].setHeight(IND_H);  } catch(e) {}
        });

        try {
            wm = LC().createTextWatermark(chart.panes()[0], {
                horzAlign:'center', vertAlign:'center',
                lines:[{ text:'', color:WM_COLOR, fontSize:WM_SIZE, fontFamily:WM_FONT }],
            });
        } catch(e) {}

        loadDrawings(); loadTrendlines(); loadPos(); loadBoxes();
        _startAutoSave();

        try {
            chart.timeScale().subscribeVisibleLogicalRangeChange(function() {
                updateAllTrendlines(); updateAllPos(); updateAllBoxes();
            });
        } catch(e) {}
        try {
            chart.subscribeCrosshairMove(function() {
                if (trendlines.length > 0) updateAllTrendlines();
                if (positions.length > 0)  updateAllPos();
                if (boxes.length > 0)      updateAllBoxes();
            });
        } catch(e) {}

        /* ── subscribeClick ─────────────────────────────────────────────── */
        try {
            chart.subscribeClick(function(param) {
                if (!drawMode || !param || !param.point) return;
                var price = null;
                try { price = S.candle.coordinateToPrice(param.point.y); } catch(e) {}
                if (price==null || !isFinite(price)) return;
                var time = param.time; if (!time) return;

                if (drawMode==='long-pos' || drawMode==='short-pos') {
                    var dir = drawMode==='long-pos' ? 'long' : 'short';
                    var pip = pipSize(_currentPair);
                    var tp  = dir==='long' ? price + pip*50 : price - pip*50;
                    var sl  = dir==='long' ? price - pip*25 : price + pip*25;
                    var iv  = _candleInterval();
                    /* Show manual-entry form so user can set precise levels */
                    var sx = param.point ? param.point.x : 200;
                    var sy = param.point ? param.point.y : 200;
                    _showPositionForm(dir, price, tp, sl, time, iv, sx, sy);
                    return;
                }

                if (drawMode==='h-line') {
                    try {
                        var pl = S.candle.createPriceLine({ price, color:drawColor,
                            lineWidth:drawWidth, lineStyle:_twlcLineStyle(drawStyle),
                            axisLabelVisible:true, title:fmtPrice(price) });
                        drawings.push({ type:'h-line', priceLine:pl, price, color:drawColor, width:drawWidth, style:drawStyle });
                        saveDrawings();
                    } catch(e) {}
                    return;
                }

                if (drawMode==='trend') {
                    if (!trendP1) {
                        trendP1 = { time, price };
                    } else {
                        var p1 = trendP1; trendP1 = null;
                        _hideTrendPreview();
                        if (p1.time === time) return;
                        var t1v = p1.time, p1v = p1.price, t2v = time, p2v = price;
                        if (t1v > t2v) {
                            var tmp; tmp=t1v; t1v=t2v; t2v=tmp;
                            tmp=p1v; p1v=p2v; p2v=tmp;
                        }
                        addTrendline(t1v, p1v, t2v, p2v, drawColor, drawWidth, drawStyle);
                    }
                    return;
                }

                /* pips (measure) mode is handled by the drag system — no click action here */
            });
        } catch(e) {}

        /* ── Mouse events ───────────────────────────────────────────────── */
        var _onDown = function(e) {
            if (posDrag || boxResize || boxMove || trendResize || trendMove) return;
            var rect   = el.getBoundingClientRect();
            var chartX = e.clientX - rect.left;
            var chartY = e.clientY - rect.top;

            /* Box / square / measure draw start */
            if (drawMode === 'box' || drawMode === 'square' || drawMode === 'pips') {
                var bTime  = _xToTimeExtrap(chartX), bPrice = _yToPrice(chartY);
                if (!bTime || !bPrice) return;
                boxDraw = { startX:chartX, startY:chartY, startTime:bTime, startPrice:bPrice,
                            previewEl:null, square: drawMode === 'square',
                            isMeasure: drawMode === 'pips', _fx:null, _fy:null };
                /* Prevent TWLC from panning while drawing — same as every other drag op */
                try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
                _startBoxPreview(chartX, chartY);
                e.stopPropagation(); e.preventDefault();
                return;
            }

            if (drawMode) return;

            /* Deselect all overlays when clicking neutral area */
            _deselectBox();
            _deselectTrend();

            var price=null, time=null;
            try { price = S.candle.coordinateToPrice(chartY); } catch(err) {}
            try { time  = chart.timeScale().coordinateToTime(chartX); } catch(err) {}
            if (price==null || !isFinite(price)) return;
            var idx = findNearest(price, time);
            if (idx >= 0) {
                selectDrawing(idx); isDragging=true;
                dragStartPrice=price; dragStartData=copyDragData(drawings[idx]);
                e.stopPropagation();
            } else { deselectDrawing(); }
        };

        var _onMove = function(e) {
            if (posDrag || boxResize || boxMove || trendResize || trendMove) return;
            if (boxDraw) {
                var r0 = el.getBoundingClientRect();
                _updateBoxPreview(e.clientX-r0.left, e.clientY-r0.top);
                return;
            }
            var rect   = el.getBoundingClientRect();
            var chartY = e.clientY - rect.top;
            var chartX = e.clientX - rect.left;
            var price=null, time=null;
            try { price = S.candle.coordinateToPrice(chartY); } catch(err) {}
            try { time  = chart.timeScale().coordinateToTime(chartX); } catch(err) {}

            /* Preview line for second trend point */
            if (drawMode === 'trend' && trendP1) {
                var px1 = _timeToX(trendP1.time), py1 = _priceToY(trendP1.price);
                if (px1 != null && py1 != null) {
                    _showTrendPreview(px1, py1, chartX, chartY);
                }
            }

            if (isDragging && selectedIdx>=0 && price!=null && isFinite(price)) {
                moveDrawing(selectedIdx, price-dragStartPrice); e.preventDefault(); return;
            }
            if (!drawMode && price!=null && isFinite(price)) {
                el.style.cursor = findNearest(price,time)>=0 ? 'move' : 'default';
            }
        };

        var _onUp = function() {
            if (isDragging) saveDrawings();
            isDragging=false; dragStartPrice=null; dragStartData=null;
        };

        el.addEventListener('mousedown', _onDown);
        el.addEventListener('mousemove', _onMove);
        el.addEventListener('mouseup',   _onUp);
        document.addEventListener('mouseup', _onUp);
        _mouseListeners = { down:_onDown, move:_onMove, up:_onUp };

        new ResizeObserver(function() {
            if (chart && el) {
                try { chart.applyOptions({ width:el.clientWidth, height:el.clientHeight||TOTAL_H }); } catch(e) {}
                updateAllTrendlines(); updateAllPos(); updateAllBoxes();
            }
        }).observe(el);

        window._apexChart = chart; window._apexSeries = S;
        return true;
    }

    /* ═══════════════════════════════════════════════════════════════════════
       LOAD DATA
       ═══════════════════════════════════════════════════════════════════════ */

    function load(data, fitIt) {
        if (!chart || !data) return;
        try { S.candle.applyOptions({ priceFormat:priceFormat(data.pair||'') }); } catch(e) {}
        try { S.candle.setData(data.candlestick||[]); } catch(e) {}

        S.emas.forEach(function(s){ try { chart.removeSeries(s); } catch(e) {} });
        S.emas = [];
        (data.emas||[]).forEach(function(ema) {
            try {
                var s = chart.addSeries(LC().LineSeries, { color:ema.color, lineWidth:ema.width||1.5,
                    lastValueVisible:false, priceLineVisible:false, crosshairMarkerVisible:false,
                    title:'EMA '+ema.period }, 0);
                s.setData(ema.data); S.emas.push(s);
            } catch(e) {}
        });

        try { S.cci.setData(data.cci||[]); } catch(e) {}
        try { S.macd.setData(data.macd||[]); } catch(e) {}

        try {
            if (wm) wm.applyOptions({ lines:[{
                text:(data.pair||'')+'   '+(data.tf||''), color:WM_COLOR, fontSize:WM_SIZE, fontFamily:WM_FONT
            }] });
        } catch(e) {}

        if (data.candlestick && data.candlestick.length > 0) {
            _candleData = data.candlestick;
            window._apexLastPrice = data.candlestick[data.candlestick.length-1].close;
        }

        /* ── Open broker/paper trades — Entry, SL, TP price lines ── */
        _tradePriceLines.forEach(function(pl){ try { S.candle.removePriceLine(pl); } catch(e){} });
        _tradePriceLines = [];
        var trades = data.open_trades || [];
        trades.forEach(function(t) {
            try {
                var isBuy = t.direction === 'long';
                var col   = isBuy ? '#00ff88' : '#ff3366';
                _tradePriceLines.push(S.candle.createPriceLine({
                    price: t.entry, color: col, lineWidth: 2,
                    lineStyle: LC().LineStyle.Solid,
                    axisLabelVisible: true,
                    title: (isBuy ? '▲ ' : '▼ ') + 'Entry',
                }));
                if (t.sl) {
                    _tradePriceLines.push(S.candle.createPriceLine({
                        price: t.sl, color: '#ff3366', lineWidth: 1,
                        lineStyle: LC().LineStyle.Dashed,
                        axisLabelVisible: true, title: 'SL',
                    }));
                }
                if (t.tp) {
                    _tradePriceLines.push(S.candle.createPriceLine({
                        price: t.tp, color: '#00ff88', lineWidth: 1,
                        lineStyle: LC().LineStyle.Dashed,
                        axisLabelVisible: true, title: 'TP',
                    }));
                }
            } catch(ex) {}
        });

        /* ── Signal overlay — Entry / SL / TP from the last generated signal ── */
        if (S._signalLines) {
            S._signalLines.forEach(function(pl){ try { S.candle.removePriceLine(pl); } catch(e){} });
        }
        S._signalLines = [];
        var sig = data.signal_levels;
        if (sig) {
            function _sigLine(price, color, title) {
                if (!price) return;
                try {
                    S._signalLines.push(S.candle.createPriceLine({
                        price: price, color: color, lineWidth: 1,
                        lineStyle: LC().LineStyle.LargeDashed,
                        axisLabelVisible: true, axisLabelColor: color,
                        title: title,
                    }));
                } catch(e) {}
            }
            _sigLine(sig.entry, '#38b6ff', (sig.direction === 'long' ? '▲ ' : '▼ ') + 'Signal');
            _sigLine(sig.sl,  '#ff3366', 'SL');
            _sigLine(sig.tp1, '#00ff88', 'TP1');
            _sigLine(sig.tp2, '#00cc66', 'TP2');
            _sigLine(sig.tp3, '#009944', 'TP3');
        }

        requestAnimationFrame(function(){ updateAllTrendlines(); updateAllPos(); updateAllBoxes(); });

        if (fitIt) {
            /* Use ACTUAL candle timestamps so zoom is consistent for every asset.
               Wall-clock time fails for crypto (24/7) when the last bar is old.
               Show ~240 bars (≈ 10 days of H1 / 60 days of 4H / 240 days of D). */
            if (_candleData.length > 0) {
                var interval = _candleInterval();           // avg seconds per bar
                var nShow    = Math.min(_candleData.length, 240);
                var firstVis = _candleData[Math.max(0, _candleData.length - nShow)];
                var lastBar  = _candleData[_candleData.length - 1];
                try {
                    chart.timeScale().setVisibleRange({
                        from: firstVis.time - interval * 2,   // tiny left margin
                        to:   lastBar.time  + interval * 10,  // right breathing room
                    });
                } catch(e) {
                    try { chart.timeScale().fitContent(); } catch(e2) {}
                }
            } else {
                try { chart.timeScale().fitContent(); } catch(e) {}
            }
        }
    }

    /* ═══════════════════════════════════════════════════════════════════════
       PUBLIC API
       ═══════════════════════════════════════════════════════════════════════ */

    /* Live trade price-line refresh — called by clientside callback on trade change */
    window._apexUpdateTrades = function(trades) {
        if (!chart || !S.candle) return;
        _tradePriceLines.forEach(function(pl){ try { S.candle.removePriceLine(pl); } catch(e){} });
        _tradePriceLines = [];
        (trades || []).forEach(function(t) {
            try {
                var isBuy = t.direction === 'long';
                var col   = isBuy ? '#00ff88' : '#ff3366';
                _tradePriceLines.push(S.candle.createPriceLine({
                    price: t.entry, color: col, lineWidth: 2,
                    lineStyle: LC().LineStyle.Solid,
                    axisLabelVisible: true,
                    title: (isBuy ? '▲ ' : '▼ ') + 'Entry',
                }));
                if (t.sl) {
                    _tradePriceLines.push(S.candle.createPriceLine({
                        price: t.sl, color: '#ff3366', lineWidth: 1,
                        lineStyle: LC().LineStyle.Dashed,
                        axisLabelVisible: true, title: 'SL',
                    }));
                }
                if (t.tp) {
                    _tradePriceLines.push(S.candle.createPriceLine({
                        price: t.tp, color: '#00ff88', lineWidth: 1,
                        lineStyle: LC().LineStyle.Dashed,
                        axisLabelVisible: true, title: 'TP',
                    }));
                }
            } catch(ex) {}
        });
    };

    window._apexSetDrawMode = function(mode) {
        drawMode = mode || null;
        trendP1 = null;
        _hideTrendPreview();
        _hidePositionForm();
        if (!drawMode) { deselectDrawing(); }
        var el = document.getElementById('tvlw-chart');
        if (el) el.style.cursor = drawMode ? 'crosshair' : 'default';
    };

    window._apexSetDrawColor = function(c) { drawColor = c || '#ffd700'; };
    window._apexSetDrawWidth = function(w) { drawWidth = parseInt(w,10) || 1; };
    window._apexSetDrawStyle = function(s) { drawStyle = s || 'solid'; };

    window._apexDeleteSelected = function() {
        if (selectedTrend >= 0) {
            delTrendline(selectedTrend);
            return true;
        }
        if (selectedBox >= 0) {
            delBox(selectedBox);
            return true;
        }
        if (selectedIdx >= 0) {
            _removeOne(drawings[selectedIdx]);
            drawings.splice(selectedIdx, 1);
            selectedIdx = -1; saveDrawings();
            return true;
        }
        return false;
    };

    window._apexLockSelected = function() {
        if (selectedTrend >= 0) {
            var t = trendlines[selectedTrend];
            if (t) { t.locked = !t.locked; updateTrendline(t); saveTrendlines(); return true; }
        }
        return false;
    };

    window._apexDuplicateSelected = function() {
        if (selectedTrend >= 0) { _dupTrendline(selectedTrend); return true; }
        if (selectedBox >= 0)   { _dupBox(selectedBox); return true; }
        return false;
    };

    window._apexClearDrawings = function() {
        deselectDrawing();
        drawings.forEach(_removeOne); drawings = [];
        saveDrawings();

        trendlines.forEach(function(t){ if (t.el&&t.el.parentNode) t.el.parentNode.removeChild(t.el); });
        trendlines = []; selectedTrend = -1; saveTrendlines();

        positions.forEach(function(p){ if (p.el&&p.el.parentNode) p.el.parentNode.removeChild(p.el); });
        positions = []; savePos();

        boxes.forEach(function(b){ if (b.el&&b.el.parentNode) b.el.parentNode.removeChild(b.el); });
        boxes = []; selectedBox = -1; saveBoxes();
    };

    window._apexUpdateChart = function(data) {
        if (!data) return;
        _currentPair = data.pair || '';
        _currentTf   = data.tf   || '';
        var pairTf   = _currentPair + '|' + _currentTf;
        if (data.accountBalance) window._apexAccountBalance = data.accountBalance;

        if (chart && _lastPairTf !== null && pairTf !== _lastPairTf) {
            saveDrawings(); saveTrendlines(); saveBoxes(); savePos();
            destroy();
        }

        if (!chart) {
            if (!init()) {
                var elapsed=0, t=setInterval(function() {
                    elapsed+=150;
                    if (init()) { clearInterval(t); _lastPairTf=pairTf; load(data,true); }
                    else if (elapsed>5000) clearInterval(t);
                }, 150);
                return;
            }
        }

        var fitIt = (_lastPairTf===null || pairTf!==_lastPairTf);
        _lastPairTf = pairTf;
        load(data, fitIt);
    };

    /* ── bootstrap ───────────────────────────────────────────────────────── */
    function boot() {
        if (LC() && document.getElementById('tvlw-chart')) { init(); }
        else { setTimeout(boot, 100); }
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
    else setTimeout(boot, 0);

}());

/* ── Floating trade panel — draggable ─────────────────────────────────────── */
(function() {
    'use strict';
    var dragging = false, ox = 0, oy = 0, sx = 0, sy = 0, panel = null;

    function onDown(e) {
        if (e.button !== 0) return;
        dragging = true;
        var parent = panel.offsetParent || document.body;
        var pr = panel.getBoundingClientRect();
        var pParent = parent.getBoundingClientRect();
        ox = pr.left - pParent.left;
        oy = pr.top  - pParent.top;
        sx = e.clientX; sy = e.clientY;
        panel.style.transition = 'none';
        e.preventDefault(); e.stopPropagation();
    }

    function onMove(e) {
        if (!dragging) return;
        panel.style.left = (ox + e.clientX - sx) + 'px';
        panel.style.top  = (oy + e.clientY - sy) + 'px';
        panel.style.right = 'auto'; panel.style.bottom = 'auto';
    }

    function onUp() { dragging = false; }

    function initPanel() {
        panel = document.getElementById('quick-trade-panel');
        if (!panel) { setTimeout(initPanel, 400); return; }
        var handle = panel.querySelector('.apex-drag-handle');
        (handle || panel).addEventListener('mousedown', onDown);
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup',   onUp);
    }
    initPanel();
}());
