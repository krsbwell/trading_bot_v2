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
    let _lastPairTf = null, _currentPair = '', _currentTf = '', _currentRawTf = '';
    let _candleData = [];
    let _autoSaveTimer = null;
    var _lastChartData = null;
    let _paneResizeActive = false;  // prevents ResizeObserver from overriding managed heights

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
    let _trendMoveStart = null; // pending: wait for drag threshold before committing to a move

    /* ── position tools ──────────────────────────────────────────────────── */
    let positions = [], posIdCtr = 0, posOverlay = null;
    let posDrag = null;
    let selectedPos = -1;
    let _tradePriceLines = []; // TWLC price lines for open broker trades (entry only)
    let _tradeSvgLines  = []; // SVG drag handles for SL/TP of live trades
    let _liveTradeData  = []; // current live trade snapshot (for updateAll)
    let tradeDrag       = null; // { tsl, snapshot }

    /* ── circle drawing tool ──────────────────────────────────────────────── */
    let circles = [], circleIdCtr = 0, selectedCircle = -1;
    let circleDraw = null;   // { cx_time, cy_price, startX, startY } — while drawing
    let circleDrag = null;   // { type:'move'|'resize', idx, ... } — while editing
    let _circlePreviewEl = null;

    /* ── text label tool ────────────────────────────────────────────────── */
    let textDrawings = [], textIdCtr = 0, selectedText = -1;
    let textOverlay = null, textMove = null, _textInput = null;

    /* ── box / consolidation tools ───────────────────────────────────────── */
    let boxes = [], boxIdCtr = 0, boxOverlay = null;
    let boxDraw = null, boxResize = null, boxMove = null, selectedBox = -1;

    /* ── fibonacci retracement tool ──────────────────────────────────────── */
    let fibs = [], fibIdCtr = 0, fibOverlay = null;
    let fibDraw = null;   // { startX, startY, startTime, startPrice } — used while drawing
    let fibDrag = null;   // { type:'move'|'p1'|'p2', idx, startX, startY, origP1,origP2,origT1,origT2 }
    let selectedFib = -1;
    const FIB_LEVELS = [
        { pct: 0,     col: '#00ff88', lbl: '0%'    },
        { pct: 0.236, col: '#ffd700', lbl: '23.6%' },
        { pct: 0.382, col: '#ff9900', lbl: '38.2%' },
        { pct: 0.500, col: '#38b6ff', lbl: '50%'   },
        { pct: 0.618, col: '#ff9900', lbl: '61.8%' },
        { pct: 0.786, col: '#ffd700', lbl: '78.6%' },
        { pct: 1.0,   col: '#ff3366', lbl: '100%'  },
    ];

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
        try {
            var w = chart.priceScale('right').width();
            if (w && w > 20) return w;
        } catch(e) {}
        /* Fallback: measure the right-most child element of the chart container */
        try {
            var el = document.getElementById('tvlw-chart');
            if (el) {
                var tds = el.querySelectorAll('table td');
                if (tds.length >= 2) {
                    var last = tds[tds.length - 1];
                    if (last && last.clientWidth > 20) return last.clientWidth;
                }
            }
        } catch(e2) {}
        return 80; /* safe over-estimate — better to clip 18px early than bleed into scale */
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
    function _circleKey() { return 'apex_circles_'   + _currentPair; }

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
    function _tfSeconds(tf) {
        var m = { '1m':60,'5m':300,'15m':900,'30m':1800,
                  'H1':3600,'1H':3600,'H4':14400,'4H':14400,
                  'D':86400,'1D':86400,'W':604800,'1W':604800 };
        return m[tf] || m[(tf||'').toUpperCase()] || 3600;
    }

    /* Count actual candles between two timestamps using loaded chart data.
       Avoids overcounting due to forex weekend gaps in raw timestamp differences. */
    function _countBars(t1, t2) {
        var candles = _lastChartData && _lastChartData.candlestick;
        if (!candles || candles.length === 0) {
            return Math.round(Math.abs((t2||0)-(t1||0)) / _tfSeconds(_currentRawTf || _currentTf));
        }
        var tMin = Math.min(t1||0, t2||0);
        var tMax = Math.max(t1||0, t2||0);
        var count = 0;
        for (var i = 0; i < candles.length; i++) {
            var ct = candles[i].time;
            if (ct >= tMin && ct <= tMax) count++;
        }
        return count;
    }

    function _measureStats(box) {
        var startPrice = box.t1 <= box.t2 ? box.p1 : box.p2;
        var endPrice   = box.t1 <= box.t2 ? box.p2 : box.p1;
        var priceDiff  = endPrice - startPrice;
        var pct        = startPrice > 0 ? (priceDiff / startPrice) * 100 : 0;
        var isUp  = priceDiff >= 0;
        var col   = isUp ? '#00ff88' : '#ff3366';
        var sign  = isUp ? '+' : '';
        var dec   = isJPY(_currentPair) ? 3 : 5;
        var pip   = pipSize(_currentPair);
        var pips  = Math.round(Math.abs(priceDiff) / pip);
        var bars  = _countBars(box.t1, box.t2);
        var label = sign + priceDiff.toFixed(dec) + ' (' + sign + pct.toFixed(2) + '%)';
        return { label: label, color: col, isUp: isUp, priceDiff: priceDiff, pips: pips, bars: bars, sign: sign };
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
            'position:absolute;top:0;left:0;z-index:13;pointer-events:none;overflow:hidden;';
        trendOverlay.setAttribute('width', '100%');
        trendOverlay.setAttribute('height', TOTAL_H + 'px');
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
        /* Double-click → settings popup (Issue #6) */
        hit.addEventListener('dblclick', function(e) {
            e.stopPropagation(); e.preventDefault();
            var idx = trendlines.indexOf(t);
            if (idx >= 0) _showDrawingSettings(e.clientX, e.clientY, 'trend', idx);
        });

        hit.addEventListener('mousedown', function(e) {
            if (drawMode) return;
            e.stopPropagation(); e.preventDefault();
            var idx = trendlines.indexOf(t);
            if (idx < 0) return;
            _selectTrend(idx);
            if (t.locked) return;
            var chartEl = document.getElementById('tvlw-chart');
            if (!chartEl) return;
            var r = chartEl.getBoundingClientRect();
            var cX = e.clientX - r.left, cY = e.clientY - r.top;
            /* Pending — only promote to a real move after 5px drag to avoid
               accidentally moving the line when the user clicks to select or pans */
            _trendMoveStart = {
                idx: idx, downX: e.clientX, downY: e.clientY,
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

        /* Persistent lock badge — always visible when locked */
        var lockBadge = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        lockBadge.setAttribute('text-anchor', 'middle');
        lockBadge.setAttribute('dominant-baseline', 'central');
        lockBadge.setAttribute('fill', '#ffd700');
        lockBadge.setAttribute('font-size', '13');
        lockBadge.setAttribute('pointer-events', 'none');
        lockBadge.setAttribute('display', 'none');
        lockBadge.textContent = '🔒';

        g.appendChild(glow);
        g.appendChild(hit);
        g.appendChild(line);
        g.appendChild(lockBadge);
        g.appendChild(h1); g.appendChild(h2);
        svg.appendChild(g);

        t.el = g; t.lineEl = line; t.hitEl = hit; t.glowEl = glow;
        t.h1El = h1; t.h2El = h2; t.lockBadge = lockBadge;
    }

    function updateTrendline(t) {
        if (!t.el || !chart || !S.candle) return;
        if (t.visibility && t.visibility.length > 0 && t.visibility.indexOf(_currentTf) < 0) {
            t.el.setAttribute('display', 'none'); return;
        }
        /* Keep the SVG viewport clipped to the chart area, excluding the price scale */
        var _tEl = document.getElementById('tvlw-chart');
        if (_tEl && trendOverlay) {
            trendOverlay.setAttribute('width', (_tEl.clientWidth - psWidth()) + 'px');
        }
        var x1 = _timeToXExtrap(t.t1), y1 = _priceToY(t.p1);
        var x2 = _timeToXExtrap(t.t2), y2 = _priceToY(t.p2);

        if (x1 == null || y1 == null || x2 == null || y2 == null) {
            t.el.setAttribute('display', 'none'); return;
        }
        t.el.removeAttribute('display');

        /* Clip line segment to the chart area — stops the line entering the price column */
        (function() {
            var chartElC = document.getElementById('tvlw-chart');
            if (!chartElC) return;
            var maxX = chartElC.clientWidth - psWidth();
            /* Parametric clip: given segment a→b, clip so both endpoints are x ≤ maxX */
            function clipEnd(ax, ay, bx, by) {
                if (bx <= maxX) return [bx, by];   // b already inside
                if (ax >= maxX) return [maxX, ay];  // a also outside — pin to boundary
                var t2 = (maxX - ax) / (bx - ax);
                return [maxX, ay + t2 * (by - ay)];
            }
            var r1 = clipEnd(x2, y2, x1, y1); x1 = r1[0]; y1 = r1[1];
            var r2 = clipEnd(x1, y1, x2, y2); x2 = r2[0]; y2 = r2[1];
        })();

        function setLine(el, a, b, c, d) {
            el.setAttribute('x1', a); el.setAttribute('y1', b);
            el.setAttribute('x2', c); el.setAttribute('y2', d);
        }
        setLine(t.lineEl, x1, y1, x2, y2);
        setLine(t.hitEl,  x1, y1, x2, y2);
        setLine(t.glowEl, x1, y1, x2, y2);

        /* Lock badge — always visible when locked so user has clear feedback */
        if (t.lockBadge) {
            if (t.locked) {
                t.lockBadge.setAttribute('x', (x1 + x2) / 2);
                t.lockBadge.setAttribute('y', (y1 + y2) / 2 - 10);
                t.lockBadge.setAttribute('display', '');
            } else {
                t.lockBadge.setAttribute('display', 'none');
            }
        }

        var col  = t.color || '#ffd700';
        var w    = t.width || 1;
        var isSel = (selectedTrend === trendlines.indexOf(t));
        var dash  = _svgDash(t.style);

        t.lineEl.setAttribute('stroke', col);
        t.lineEl.setAttribute('stroke-width', isSel ? w + 1 : w);
        t.lineEl.setAttribute('stroke-dasharray', dash);
        t.lineEl.setAttribute('stroke-opacity', t.locked ? '0.55' : '1');

        t.glowEl.setAttribute('stroke', col);
        t.glowEl.setAttribute('stroke-width', w + 10);
        t.glowEl.setAttribute('stroke-dasharray', dash);
        t.glowEl.setAttribute('display', isSel ? '' : 'none');

        if (isSel) {
            /* Endpoint handles visible when selected and unlocked */
            t.h1El.setAttribute('cx', x1); t.h1El.setAttribute('cy', y1);
            t.h2El.setAttribute('cx', x2); t.h2El.setAttribute('cy', y2);
            t.h1El.setAttribute('stroke', col);
            t.h2El.setAttribute('stroke', col);
            t.h1El.setAttribute('display', t.locked ? 'none' : '');
            t.h2El.setAttribute('display', t.locked ? 'none' : '');
            t.hitEl.setAttribute('cursor', t.locked ? 'default' : 'move');
        } else {
            t.h1El.setAttribute('display', 'none');
            t.h2El.setAttribute('display', 'none');
            t.hitEl.setAttribute('cursor', 'move');
        }
    }

    function updateAllTrendlines() { trendlines.forEach(updateTrendline); }

    function _selectTrend(idx) {
        _deselectFib(); _deselectPos(); _deselectBox(); _deselectText();
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
        /* Promote pending move to active once drag exceeds 5px threshold */
        if (_trendMoveStart && !trendMove) {
            var dx = e.clientX - _trendMoveStart.downX;
            var dy = e.clientY - _trendMoveStart.downY;
            if (Math.sqrt(dx * dx + dy * dy) >= 5) {
                trendMove = {
                    idx:         _trendMoveStart.idx,
                    startTime:   _trendMoveStart.startTime,
                    startPrice:  _trendMoveStart.startPrice,
                    origT1:      _trendMoveStart.origT1, origP1: _trendMoveStart.origP1,
                    origT2:      _trendMoveStart.origT2, origP2: _trendMoveStart.origP2,
                };
                _trendMoveStart = null;
            }
        }
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
        if (_trendMoveStart) {
            /* Drag never reached threshold — just a click, restore scroll */
            _trendMoveStart = null;
            try { if (chart) chart.applyOptions({ handleScroll:true, handleScale:true }); } catch(ex) {}
        }
        if (trendResize || trendMove) {
            try { if (chart) chart.applyOptions({ handleScroll:true, handleScale:true }); } catch(ex) {}
            saveTrendlines();
            trendResize = null; trendMove = null; _trendMoveStart = null;
        }
    });

    /* Trendline persistence */
    function saveTrendlines() {
        try {
            localStorage.setItem(_trendKey(), JSON.stringify(
                trendlines.map(function(t) {
                    return { id:t.id, t1:t.t1, p1:t.p1, t2:t.t2, p2:t.p2,
                             color:t.color, width:t.width, style:t.style, locked:t.locked,
                             customLabel:t.customLabel||'', visibility:t.visibility||[] };
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
                var t = addTrendline(d.t1, d.p1, d.t2, d.p2,
                             d.color, d.width, d.style||'solid', d.locked||false, d.id);
                if (t) { t.customLabel = d.customLabel||''; t.visibility = d.visibility||[]; }
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
            'width:100%;height:' + MAIN_H + 'px;overflow:visible;';
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

        /* Shared event handlers — reused by drag div (rect) or border strips (measure) */
        var isMeasure = (box.type === 'measure');
        function _onDragDblClick(e) {
            e.stopPropagation(); e.preventDefault();
            var idx = boxes.findIndex(function(b) { return String(b.id) === String(wrap.dataset.boxId); });
            if (idx >= 0) _showDrawingSettings(e.clientX, e.clientY, 'box', idx);
        }
        function _onDragMouseDown(e) {
            if (drawMode) return;
            var idx = boxes.findIndex(function(b) { return String(b.id) === String(wrap.dataset.boxId); });
            if (idx < 0) return;
            var b = boxes[idx];
            _selectBox(idx);
            _deselectTrend();
            e.stopPropagation();
            if (b.locked) return;
            e.preventDefault();
            var chartEl = document.getElementById('tvlw-chart'); if (!chartEl) return;
            var r   = chartEl.getBoundingClientRect();
            var cX  = e.clientX - r.left, cY = e.clientY - r.top;
            boxMove = { idx, startX:cX, startY:cY,
                startTime:  _xToTimeExtrap(cX) || b.t1,
                startPrice: _yToPrice(cY)       || b.p1,
                origT1:b.t1, origT2:b.t2, origP1:b.p1, origP2:b.p2 };
            try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
        }

        var drag = document.createElement('div');
        drag.className = 'apex-box-drag';
        if (isMeasure) {
            /* Measure box: interior is transparent — only 8px border strips capture events */
            drag.style.cssText = 'position:absolute;inset:0;z-index:10;pointer-events:none;';
            ['top','bottom','left','right'].forEach(function(side) {
                var s = document.createElement('div');
                s.style.cssText = 'position:absolute;pointer-events:auto;cursor:move;' + (
                    side === 'top'    ? 'top:0;left:0;right:0;height:8px;'     :
                    side === 'bottom' ? 'bottom:0;left:0;right:0;height:8px;'  :
                    side === 'left'   ? 'top:8px;bottom:8px;left:0;width:8px;' :
                                        'top:8px;bottom:8px;right:0;width:8px;'
                );
                s.addEventListener('dblclick',  _onDragDblClick);
                s.addEventListener('mousedown', _onDragMouseDown);
                drag.appendChild(s);
            });
        } else {
            drag.style.cssText = 'position:absolute;inset:0;z-index:10;cursor:move;pointer-events:auto;';
            drag.addEventListener('dblclick',  _onDragDblClick);
            drag.addEventListener('mousedown', _onDragMouseDown);
        }

        var stats = document.createElement('div');
        stats.className = 'apex-box-stats';
        stats.style.cssText =
            'position:absolute;bottom:100%;right:0;margin-bottom:3px;z-index:20;pointer-events:none;' +
            'font-family:monospace;font-weight:600;text-align:right;line-height:1.4;white-space:nowrap;' +
            'background:none;padding:3px 8px;';

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
        delBtn.addEventListener('mousedown', function(e) { e.stopPropagation(); });
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
        dupBtn.addEventListener('mousedown', function(e) { e.stopPropagation(); });
        dupBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            var idx = boxes.findIndex(function(b) { return String(b.id)===String(dupBtn.dataset.boxId); });
            if (idx >= 0) _dupBox(idx);
        });

        /* Stats label for measure-type boxes — auto-positioned top-center or bottom-center */
        var mStats = document.createElement('div');
        mStats.className = 'apex-measure-stats';
        mStats.style.cssText =
            'position:absolute;left:50%;transform:translateX(-50%);' +
            'z-index:12;pointer-events:none;font-family:monospace;text-align:center;' +
            'padding:3px 8px;border-radius:3px;white-space:nowrap;display:none;';

        wrap.appendChild(bg); wrap.appendChild(drag); wrap.appendChild(stats);
        wrap.appendChild(mStats);
        wrap.appendChild(hw); wrap.appendChild(delBtn); wrap.appendChild(dupBtn);
        box.el = wrap; box.bgEl = bg; box.statsEl = stats;
        box.measureStatsEl = mStats; box.handleWrap = hw;
        box.delBtn = delBtn; box.dupBtn = dupBtn;
    }

    function updateBox(box) {
        if (!box.el || !chart || !S.candle) return;
        if (box.visibility && box.visibility.length > 0 && box.visibility.indexOf(_currentTf) < 0) {
            box.el.style.display = 'none'; return;
        }
        var chartEl = document.getElementById('tvlw-chart'); if (!chartEl) return;

        var x1r = _timeToXExtrap(box.t1), x2r = _timeToXExtrap(box.t2);
        var y1  = _priceToY(box.p1),     y2  = _priceToY(box.p2);
        if (x1r == null || x2r == null || y1 == null || y2 == null) {
            box.el.style.display = 'none'; return;
        }

        var chartW = chartEl.clientWidth - psWidth();

        /* If both anchors are fully off the same side, hide the box entirely */
        if ((x1r < 0 && x2r < 0) || (x1r > chartW && x2r > chartW)) {
            box.el.style.display = 'none'; return;
        }

        /* Clamp to chart bounds so the box never stretches off-screen */
        var x1 = Math.max(0, Math.min(chartW, x1r));
        var x2 = Math.max(0, Math.min(chartW, x2r));

        var left = Math.min(x1,x2), right = Math.max(x1,x2);
        var top  = Math.min(y1,y2), bottom= Math.max(y1,y2);
        var w = right-left, h = bottom-top;

        if (w < 2 || h < 2 || bottom < 0 || top > MAIN_H) {
            box.el.style.display = 'none'; return;
        }
        box.el.style.display = 'block';
        box.el.style.left = left+'px'; box.el.style.top  = top+'px';
        box.el.style.width= w+'px';   box.el.style.height= h+'px';

        var isSel = (selectedBox === boxes.indexOf(box));
        box.handleWrap.style.display = isSel ? 'block' : 'none';
        if (box.delBtn) box.delBtn.style.display = isSel ? '' : 'none';
        if (box.dupBtn) box.dupBtn.style.display = isSel ? '' : 'none';

        box.el.querySelectorAll('.apex-box-handle').forEach(function(hEl) {
            var hp = HANDLE_POS[hEl.dataset.handle];
            if (hp) { hEl.style.left = (hp[0]*100)+'%'; hEl.style.top = (hp[1]*100)+'%'; }
        });

        if (box.type === 'measure') {
            /* ── Measure box: user colour for border/fill, direction colour for label only ── */
            var ms     = _measureStats(box);
            var lblCol = ms.color;                          // green/red based on direction
            var boxCol = box.color || '#8b949e';            // user-chosen colour (settable via dbl-click)
            var bwM    = box.borderWidth || 1;
            box.el.style.border       = (isSel ? bwM+1 : bwM) + 'px dashed ' + boxCol;
            box.bgEl.style.background = _hexToRgba(boxCol, 0.07);
            box.statsEl.textContent = '';
            if (box.measureStatsEl) {
                box.measureStatsEl.style.display    = 'block';
                box.measureStatsEl.style.color      = '#000';
                box.measureStatsEl.style.background = '#ffd700';
                box.measureStatsEl.style.border     = '1px solid #b8960a';
                box.measureStatsEl.style.left       = '50%';
                box.measureStatsEl.style.bottom     = '';
                box.measureStatsEl.innerHTML =
                    '<span style="font-size:11px;font-weight:700;display:block">' + ms.pips + ' pips &nbsp;|&nbsp; ' + ms.bars + ' bars</span>' +
                    '<span style="font-size:10px">' + ms.label + '</span>';
                /* Auto-position: use transform so the label stays inside the chart viewport.
                   top<34 → label below the box; otherwise label above the box.
                   transform handles both centering (X) and vertical offset (Y).        */
                if (top < 34) {
                    box.measureStatsEl.style.top       = '100%';
                    box.measureStatsEl.style.transform = 'translate(-50%, 4px)';
                } else {
                    box.measureStatsEl.style.top       = '0';
                    box.measureStatsEl.style.transform = 'translate(-50%, calc(-100% - 4px))';
                }
            }
        } else {
            /* ── Regular rect/square box — pip span label in upper-right ── */
            var bw  = box.borderWidth || 1;
            var col = box.color || drawColor;
            var fill = _hexToRgba(col, box.fillOpacity != null ? box.fillOpacity : 0.10);
            var borderStyle = _cssBorderStyle(box.borderStyle || 'solid');
            box.el.style.border       = (isSel ? bw+1 : bw) + 'px ' + borderStyle + ' ' + col;
            box.bgEl.style.background = fill;
            if (box.measureStatsEl) box.measureStatsEl.style.display = 'none';
            /* Pip+bar label — floats above the box, flips below if box is near chart top */
            var pipSpan = Math.round(Math.abs(box.p2 - box.p1) / pipSize(_currentPair));
            var barSpan = _countBars(box.t1, box.t2);
            var minDim  = Math.min(w, h);
            var fSize   = Math.max(11, Math.min(18, Math.floor(minDim / 5)));
            box.statsEl.style.color    = col;
            box.statsEl.style.fontSize = fSize + 'px';
            box.statsEl.style.display  = (w > 60) ? '' : 'none';
            box.statsEl.textContent    = pipSpan + ' pips  |  ' + barSpan + ' bars';
            /* Flip below the box when near the chart top so the label stays on-screen */
            if (top < 28) {
                box.statsEl.style.bottom = ''; box.statsEl.style.top = '100%';
                box.statsEl.style.marginBottom = ''; box.statsEl.style.marginTop = '3px';
            } else {
                box.statsEl.style.bottom = '100%'; box.statsEl.style.top = '';
                box.statsEl.style.marginTop = ''; box.statsEl.style.marginBottom = '3px';
            }
        }
    }

    function _cssBorderStyle(s) {
        if (s === 'dashed') return 'dashed';
        if (s === 'dotted') return 'dotted';
        return 'solid';
    }

    function updateAllBoxes() { boxes.forEach(updateBox); }
    function _selectBox(idx) { _deselectFib(); _deselectPos(); _deselectTrend(); _deselectText(); selectedBox = idx; boxes.forEach(updateBox); }
    function _deselectBox()  { selectedBox = -1;  boxes.forEach(updateBox); }

    function _selectFib(idx) {
        _deselectBox(); _deselectTrend(); _deselectText(); selectedPos = -1;
        selectedFib = idx; fibs.forEach(updateFib);
    }
    function _deselectFib() { if (selectedFib < 0) return; selectedFib = -1; fibs.forEach(updateFib); }

    function _selectPos(idx) {
        _deselectBox(); _deselectTrend(); _deselectText(); selectedFib = -1;
        selectedPos = idx; positions.forEach(updatePos);
    }
    function _deselectPos() { if (selectedPos < 0) return; selectedPos = -1; positions.forEach(updatePos); }

    function _startBoxPreview(sx, sy) {
        var ov = ensureBoxOverlay(); if (!ov) return;
        var prev = document.createElement('div');
        prev.id = 'apex-box-preview';
        var isMeas = boxDraw && boxDraw.isMeasure;
        prev.style.cssText =
            'position:absolute;pointer-events:none;box-sizing:border-box;overflow:visible;' +
            'border:1px dashed ' + drawColor + ';' +
            'background:' + _hexToRgba(drawColor, 0.04) + ';';
        prev.style.left = sx+'px'; prev.style.top = sy+'px';
        prev.style.width = '0px'; prev.style.height = '0px';
        /* Live stats label — only for measure tool */
        if (isMeas) {
            var sl = document.createElement('div');
            sl.className = 'apex-preview-stats';
            /* Position ABOVE the preview box so candles aren't obscured */
            sl.style.cssText =
                'position:absolute;top:-3px;left:50%;transform:translate(-50%,-100%);' +
                'font-family:monospace;text-align:center;font-size:12px;font-weight:700;' +
                'padding:3px 8px;border-radius:3px;' +
                'white-space:nowrap;display:none;pointer-events:none;';
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
                    prev.style.background   = _hexToRgba(drawColor, 0.05);
                    sl.style.display    = 'block';
                    sl.style.color      = '#000';
                    sl.style.background = '#ffd700';
                    sl.style.border     = '1px solid #b8960a';
                    sl.innerHTML = '<span style="font-size:11px;font-weight:700;display:block">' + ms.pips + ' pips &nbsp;|&nbsp; ' + ms.bars + ' bars</span><span style="font-size:10px">' + ms.label + '</span>';
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
        return box;
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
                borderStyle:b.borderStyle||'solid', type:b.type||'rect',
                locked: !!b.locked, customLabel:b.customLabel||'', visibility:b.visibility||[] }; })
        )); } catch(e) {}
    }

    function loadBoxes() {
        try {
            var raw = localStorage.getItem(_boxKey());
            if (!raw) raw = localStorage.getItem(_legacyBoxKey());
            if (!raw) return;
            JSON.parse(raw).forEach(function(d){
                var box = addBox(d.t1,d.p1,d.t2,d.p2,d.color,d.borderWidth,d.id,d.fillOpacity,null,d.borderStyle,d.type||'rect');
                if (box) {
                    if (d.locked) { box.locked = true; updateBox(box); }
                    box.customLabel = d.customLabel||''; box.visibility = d.visibility||[];
                }
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
            'width:100%;height:' + TOTAL_H + 'px;overflow:visible;';
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
            if (drawMode) return; // drawMode active: let event bubble for new drawing
            var idx = positions.findIndex(function(p){ return String(p.id)===String(line.dataset.posId); });
            if (idx < 0) return;
            var p = positions[idx];
            if (p.locked) return;
            e.stopPropagation(); e.preventDefault();
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
            'position:absolute;top:0;height:' + TOTAL_H + 'px;' +
            'pointer-events:none;overflow:visible;';

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
        /* Centred horizontally so the stats panel sits in the middle of the position box */
        cLabel.style.cssText = 'position:absolute;left:50%;transform:translateX(-50%);pointer-events:none;' +
            'font-family:monospace;font-size:10px;white-space:nowrap;' +
            'padding:3px 7px;border-radius:3px;' +
            'border:1px solid rgba(120,120,120,0.35);background:rgba(13,17,23,0.88);';

        var tpLine  = _makeLine(pos.id, 'tp');
        var entLine = _makeLine(pos.id, 'entry');
        var slLine  = _makeLine(pos.id, 'sl');

        var dragArea = document.createElement('div');
        /* pointer-events:auto always — locked check is inside the handlers,
           so dblclick still fires on a locked tool */
        dragArea.style.cssText =
            'position:absolute;left:0;right:0;pointer-events:auto;z-index:10;';
        dragArea.addEventListener('mousedown', function(e) {
            if (drawMode) return; // drawMode active: let event bubble so new drawing is created
            var idx = positions.findIndex(function(p){ return String(p.id)===String(wrap.dataset.posId); });
            if (idx < 0) return;
            var p = positions[idx];
            _selectPos(idx);
            /* Always stop propagation so _onDown doesn't immediately deselect */
            e.stopPropagation();
            if (p.locked) return; // locked: selected but no drag
            e.preventDefault();
            var chartEl = document.getElementById('tvlw-chart'); if (!chartEl) return;
            var r   = chartEl.getBoundingClientRect();
            var cX  = e.clientX - r.left, cY = e.clientY - r.top;
            posDrag = { type:'move', idx,
                startTime:  _xToTime(cX)  || p.t1,
                startPrice: _yToPrice(cY) || p.entry,
                startT1: p.t1, startT2: p.t2,
                startEntry: p.entry, startTp: p.tp, startSl: p.sl };
            try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
        });
        dragArea.addEventListener('dblclick', function(e) {
            e.stopPropagation();
            var idx = positions.findIndex(function(p){ return String(p.id)===String(wrap.dataset.posId); });
            if (idx >= 0) _showPositionEditForm(positions[idx], e.clientX, e.clientY);
        });

        /* Pass mousemove through to the chart so the crosshair still tracks */
        dragArea.addEventListener('mousemove', function(e) {
            if (posDrag) return;
            dragArea.style.pointerEvents = 'none';
            var below = document.elementFromPoint(e.clientX, e.clientY);
            dragArea.style.pointerEvents = 'auto';
            if (below && below !== dragArea) {
                below.dispatchEvent(new MouseEvent('mousemove', {
                    bubbles: true, cancelable: true,
                    clientX: e.clientX, clientY: e.clientY, view: window
                }));
            }
        });

        var resizeR = document.createElement('div');
        resizeR.style.cssText =
            'position:absolute;right:0;top:0;bottom:0;width:6px;' +
            'cursor:ew-resize;pointer-events:auto;z-index:25;background:transparent;';
        resizeR.addEventListener('mousedown', function(e) {
            if (drawMode) return;
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

        var x1r = _timeToXExtrap(pos.t1), x2r = _timeToXExtrap(pos.t2);
        var chartEl = document.getElementById('tvlw-chart');
        var chartW  = chartEl ? chartEl.clientWidth - psWidth() : 700;

        if (x1r == null || x2r == null) { pos.el.style.display = 'none'; return; }
        if ((x1r < 0 && x2r < 0) || (x1r > chartW && x2r > chartW)) {
            pos.el.style.display = 'none'; return;
        }
        var x1 = Math.max(0, Math.min(chartW, x1r));
        var x2 = Math.max(0, Math.min(chartW, x2r));

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

        /* Locked state uses cursor hints only — pointer-events stay auto so
           dblclick reaches the handler which opens the edit/unlock form. */
        var locked = !!pos.locked;
        var lCur = locked ? 'default' : 'ns-resize';
        var lBase = 'position:absolute;left:0;right:0;height:6px;pointer-events:auto;z-index:20;';
        pos.tpLine.style.cssText  = lBase + 'top:' + (tpY-3)  + 'px;background:rgba(0,255,100,0.85);cursor:' + lCur + ';';
        pos.entLine.style.cssText = lBase + 'top:' + (entY-3) + 'px;background:transparent;border-top:2px dashed rgba(255,255,255,0.85);cursor:' + lCur + ';';
        pos.slLine.style.cssText  = lBase + 'top:' + (slY-3)  + 'px;background:rgba(255,51,102,0.85);cursor:' + lCur + ';';
        pos.dragArea.style.cursor        = locked ? 'default' : 'move';
        pos.resizeR.style.pointerEvents  = locked ? 'none' : 'auto';
        pos.el.title = locked ? 'Locked — double-click to edit' : '';

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

    function addPosition(direction, entry, tp, sl, t1, t2, existingId, locked) {
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
        var pos = { id, direction, entry, tp, sl, t1, t2, locked: !!locked };
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
        if (selectedPos === idx) selectedPos = -1;
        else if (selectedPos > idx) selectedPos--;
        savePos();
    }

    function savePos() {
        try { localStorage.setItem(_posKey(), JSON.stringify(
            positions.map(function(p){
                return { id:p.id, direction:p.direction,
                         entry:p.entry, tp:p.tp, sl:p.sl,
                         t1:p.t1, t2:p.t2, locked: !!p.locked };
            })
        )); } catch(e) {}
    }

    function loadPos() {
        try {
            var raw = localStorage.getItem(_posKey());
            if (!raw) raw = localStorage.getItem(_legacyPosKey());
            if (!raw) return;
            JSON.parse(raw).forEach(function(d){
                addPosition(d.direction, d.entry, d.tp, d.sl, d.t1, d.t2, d.id, !!d.locked);
            });
        } catch(e) {}
    }

    /* Position / trade-line / circle drag — document-level so mouse can leave chart */
    document.addEventListener('mousemove', function(e) {
        /* ── Position tool drag ── */
        if (posDrag && chart && S.candle) {
            e.preventDefault();
            var el2 = document.getElementById('tvlw-chart'); if (!el2) return;
            var r2  = el2.getBoundingClientRect();
            var cX2 = e.clientX - r2.left, cY2 = e.clientY - r2.top;
            var pos = positions[posDrag.idx]; if (!pos) return;
            if (posDrag.type === 'v') {
                var price = _yToPrice(cY2); if (!price) return;
                var lt = posDrag.lineType;
                if (lt === 'entry') {
                    var d = price - posDrag.startEntry;
                    pos.entry = posDrag.startEntry + d;
                    pos.tp    = posDrag.startTp    + d;
                    pos.sl    = posDrag.startSl    + d;
                } else if (lt === 'tp') { pos.tp = price; }
                  else if (lt === 'sl') { pos.sl = price; }
            } else if (posDrag.type === 'move') {
                var ct = _xToTimeExtrap(cX2), cp = _yToPrice(cY2);
                if (ct == null || cp == null) return;
                var dt = ct - posDrag.startTime, dp = cp - posDrag.startPrice;
                pos.t1    = posDrag.startT1    + dt; pos.t2    = posDrag.startT2    + dt;
                pos.entry = posDrag.startEntry + dp; pos.tp    = posDrag.startTp    + dp;
                pos.sl    = posDrag.startSl    + dp;
            } else if (posDrag.type === 'resize-r') {
                var ct3 = _xToTimeExtrap(cX2); if (ct3 == null) return;
                var iv   = _candleInterval();
                pos.t2   = Math.max(pos.t1 + 5*iv, Math.min(pos.t1 + 100*iv, ct3));
            }
            updatePos(pos);
            return;
        }

        /* ── Live trade SL/TP drag ── */
        if (tradeDrag && chart && S.candle) {
            e.preventDefault();
            var el3 = document.getElementById('tvlw-chart'); if (!el3) return;
            var r3  = el3.getBoundingClientRect();
            var cY3 = e.clientY - r3.top;
            var newPrice = _yToPrice(cY3); if (!newPrice) return;
            tradeDrag.tsl.price = newPrice;
            _updateTradeSvgLine(tradeDrag.tsl);
            return;
        }

        /* ── Circle drag (move or resize) ── */
        if (circleDrag && chart && S.candle) {
            e.preventDefault();
            var el4 = document.getElementById('tvlw-chart'); if (!el4) return;
            var r4  = el4.getBoundingClientRect();
            var cX4 = e.clientX - r4.left, cY4 = e.clientY - r4.top;
            var cc  = circles[circleDrag.idx]; if (!cc) return;
            if (circleDrag.type === 'move') {
                var newCxTime  = _xToTimeExtrap(cX4);
                var newCyPrice = _yToPrice(cY4);
                if (newCxTime)  cc.cx_time  = newCxTime;
                if (newCyPrice) cc.cy_price = newCyPrice;
            } else if (circleDrag.type === 'resize') {
                var centerY = _priceToY(cc.cy_price);
                if (centerY != null) {
                    var rPx = Math.abs(cY4 - centerY);
                    var edgePrice = _yToPrice(centerY + rPx);
                    if (edgePrice) cc.r_price = Math.abs(edgePrice - cc.cy_price);
                }
            }
            updateCircle(cc);
            return;
        }
    });

    document.addEventListener('mouseup', function() {
        if (posDrag) {
            try { if (chart) chart.applyOptions({ handleScroll:true, handleScale:true }); } catch(ex) {}
            savePos(); posDrag = null;
        }
        if (tradeDrag) {
            try { if (chart) chart.applyOptions({ handleScroll:true, handleScale:true }); } catch(ex) {}
            /* Write updated prices to Dash store → triggers server-side modify_trade */
            var snap = tradeDrag.snapshot;
            var tsl  = tradeDrag.tsl;
            var lt   = tsl.lineType;
            var payload = { id: snap.id, sl: snap.sl, tp1: snap.tp1, tp2: snap.tp2, tp3: snap.tp3 };
            if (lt === 'sl')  payload.sl  = tsl.price;
            if (lt === 'tp1') payload.tp1 = tsl.price;
            if (lt === 'tp2') payload.tp2 = tsl.price;
            if (lt === 'tp3') payload.tp3 = tsl.price;
            /* Also update _liveTradeData so updateAll re-renders at correct position */
            _liveTradeData.forEach(function(t) {
                if (String(t.id) === String(snap.id)) {
                    if (lt === 'sl')  t.sl  = tsl.price;
                    if (lt === 'tp1') t.tp1 = tsl.price;
                    if (lt === 'tp2') t.tp2 = tsl.price;
                    if (lt === 'tp3') t.tp3 = tsl.price;
                }
            });
            try {
                window.dash_clientside.set_props('trade-modify-store', { data: payload });
            } catch(ex) {}
            tradeDrag = null;
        }
        if (circleDrag) {
            try { if (chart) chart.applyOptions({ handleScroll:true, handleScale:true }); } catch(ex) {}
            saveCircles();
            circleDrag = null;
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
                    return { type:'h-line', price:d.price, color:d.color, width:d.width, style:d.style,
                             locked:d.locked||false, customLabel:d.customLabel||'', visibility:d.visibility||[] };
                });
            localStorage.setItem(_hlKey(), JSON.stringify(data));
        } catch(e) {}
    }

    /* ── SVG horizontal line builder / updater ───────────────────────────── */
    function buildHLineEl(d) {
        var svg = ensureTrendOverlay(); if (!svg) return;
        var g   = document.createElementNS('http://www.w3.org/2000/svg', 'g');

        /* Wide transparent hit strip — makes the thin line easy to click */
        var hit = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        hit.setAttribute('stroke', 'transparent');
        hit.setAttribute('stroke-width', '14');
        hit.setAttribute('pointer-events', 'stroke');
        hit.setAttribute('cursor', 'move');
        hit.addEventListener('dblclick', function(e) {
            e.stopPropagation(); e.preventDefault();
            var idx = drawings.indexOf(d);
            if (idx >= 0) _showDrawingSettings(e.clientX, e.clientY, 'h-line', idx);
        });

        /* Visual line */
        var line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('pointer-events', 'none');

        /* Price label — right-aligned just before the price scale */
        var lbl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        lbl.setAttribute('font-size', '10');
        lbl.setAttribute('font-family', 'monospace');
        lbl.setAttribute('font-weight', '600');
        lbl.setAttribute('dominant-baseline', 'central');
        lbl.setAttribute('text-anchor', 'end');
        lbl.setAttribute('pointer-events', 'none');

        /* Selection handle (small circle at right end) */
        var handle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        handle.setAttribute('r', '4');
        handle.setAttribute('stroke-width', '2');
        handle.setAttribute('pointer-events', 'none');
        handle.setAttribute('display', 'none');

        /* Lock badge */
        var lockBadge = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        lockBadge.setAttribute('font-size', '12');
        lockBadge.setAttribute('text-anchor', 'middle');
        lockBadge.setAttribute('dominant-baseline', 'central');
        lockBadge.setAttribute('pointer-events', 'none');
        lockBadge.setAttribute('display', 'none');
        lockBadge.textContent = '🔒';

        /* Delete button — × shown above line when selected */
        var delBtn = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        delBtn.textContent = '×';
        delBtn.setAttribute('font-size', '16');
        delBtn.setAttribute('font-family', 'monospace');
        delBtn.setAttribute('font-weight', '700');
        delBtn.setAttribute('dominant-baseline', 'central');
        delBtn.setAttribute('text-anchor', 'middle');
        delBtn.setAttribute('cursor', 'pointer');
        delBtn.setAttribute('display', 'none');
        delBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            var idx = drawings.indexOf(d);
            if (idx < 0) return;
            _removeOne(d);
            drawings.splice(idx, 1);
            if (selectedIdx === idx) selectedIdx = -1;
            else if (selectedIdx > idx) selectedIdx--;
            saveDrawings();
        });

        /* Duplicate button — + shown above line when selected */
        var dupBtn = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        dupBtn.textContent = '+';
        dupBtn.setAttribute('font-size', '14');
        dupBtn.setAttribute('font-family', 'monospace');
        dupBtn.setAttribute('font-weight', '700');
        dupBtn.setAttribute('dominant-baseline', 'central');
        dupBtn.setAttribute('text-anchor', 'middle');
        dupBtn.setAttribute('cursor', 'pointer');
        dupBtn.setAttribute('display', 'none');
        dupBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            var idx = drawings.indexOf(d);
            if (idx >= 0) _dupHLine(idx);
        });

        /* Custom text label — shown at midpoint above the line */
        var customLbl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        customLbl.setAttribute('font-size', '10');
        customLbl.setAttribute('font-family', 'monospace');
        customLbl.setAttribute('font-weight', '600');
        customLbl.setAttribute('dominant-baseline', 'central');
        customLbl.setAttribute('text-anchor', 'middle');
        customLbl.setAttribute('pointer-events', 'none');
        customLbl.setAttribute('display', 'none');

        g.appendChild(hit); g.appendChild(line);
        g.appendChild(lbl); g.appendChild(customLbl); g.appendChild(handle); g.appendChild(lockBadge);
        g.appendChild(delBtn); g.appendChild(dupBtn);
        svg.appendChild(g);

        d.el = g; d.lineEl = line; d.hitEl = hit;
        d.lblEl = lbl; d.customLabelEl = customLbl; d.handleEl = handle; d.lockBadge = lockBadge;
        d.delBtn = delBtn; d.dupBtn = dupBtn;

        updateHLine(d);
    }

    function updateHLine(d) {
        if (!d.el || !chart || !S.candle) return;
        /* Visibility filter */
        if (d.visibility && d.visibility.length > 0 && d.visibility.indexOf(_currentTf) < 0) {
            d.el.setAttribute('display', 'none'); return;
        }
        var y = _priceToY(d.price);
        if (y == null) { d.el.setAttribute('display', 'none'); return; }
        d.el.removeAttribute('display');

        var chartEl = document.getElementById('tvlw-chart');
        var maxX    = chartEl ? (chartEl.clientWidth - psWidth()) : 800;
        var col     = d.color || '#ffd700';
        var isSel   = (selectedIdx === drawings.indexOf(d));
        var w       = d.width || 1;
        var dash    = _svgDash(d.style);

        function setH(el) {
            el.setAttribute('x1', 0); el.setAttribute('y1', y);
            el.setAttribute('x2', maxX); el.setAttribute('y2', y);
        }
        setH(d.lineEl); setH(d.hitEl);

        d.lineEl.setAttribute('stroke', col);
        d.lineEl.setAttribute('stroke-width', isSel ? w + 1 : w);
        d.lineEl.setAttribute('stroke-dasharray', dash);
        d.lineEl.setAttribute('stroke-opacity', d.locked ? '0.55' : '1');
        d.hitEl.setAttribute('cursor', d.locked ? 'default' : 'move');

        /* Price label tucked right against the chart boundary */
        d.lblEl.setAttribute('x', maxX - 3);
        d.lblEl.setAttribute('y', y - 8);
        d.lblEl.setAttribute('fill', col);
        d.lblEl.textContent = fmtPrice(d.price);

        /* Selection handle at right end */
        if (isSel && !d.locked) {
            d.handleEl.setAttribute('cx', maxX);
            d.handleEl.setAttribute('cy', y);
            d.handleEl.setAttribute('fill', 'rgba(13,17,23,0.92)');
            d.handleEl.setAttribute('stroke', col);
            d.handleEl.setAttribute('display', '');
            /* × delete button and + dup button — left side, above the line */
            d.delBtn.setAttribute('x', 18);
            d.delBtn.setAttribute('y', y - 11);
            d.delBtn.setAttribute('fill', '#ff5252');
            d.delBtn.setAttribute('display', '');
            d.dupBtn.setAttribute('x', 36);
            d.dupBtn.setAttribute('y', y - 11);
            d.dupBtn.setAttribute('fill', '#38b6ff');
            d.dupBtn.setAttribute('display', '');
        } else {
            d.handleEl.setAttribute('display', 'none');
            if (d.delBtn) d.delBtn.setAttribute('display', 'none');
            if (d.dupBtn) d.dupBtn.setAttribute('display', 'none');
        }

        /* Lock badge at midpoint */
        if (d.locked) {
            d.lockBadge.setAttribute('x', maxX / 2);
            d.lockBadge.setAttribute('y', y - 10);
            d.lockBadge.setAttribute('display', '');
        } else {
            d.lockBadge.setAttribute('display', 'none');
        }

        /* Custom text label */
        if (d.customLabelEl) {
            if (d.customLabel) {
                d.customLabelEl.textContent = d.customLabel;
                d.customLabelEl.setAttribute('x', maxX / 2);
                d.customLabelEl.setAttribute('y', y - 10);
                d.customLabelEl.setAttribute('fill', col);
                d.customLabelEl.setAttribute('display', '');
            } else {
                d.customLabelEl.setAttribute('display', 'none');
            }
        }
    }

    function updateAllHLines() {
        drawings.forEach(function(d) { if (d.type === 'h-line') updateHLine(d); });
    }

    /* ═══════════════════════════════════════════════════════════════════════
       CIRCLE DRAWING TOOL
       ═══════════════════════════════════════════════════════════════════════ */

    function saveCircles() {
        try {
            localStorage.setItem(_circleKey(), JSON.stringify(
                circles.map(function(c) {
                    return { cx_time:c.cx_time, cy_price:c.cy_price, r_price:c.r_price,
                             color:c.color, width:c.width, style:c.style||'solid', locked:c.locked||false,
                             customLabel:c.customLabel||'', visibility:c.visibility||[] };
                })
            ));
        } catch(e) {}
    }

    function loadCircles() {
        try {
            var raw = localStorage.getItem(_circleKey()); if (!raw) return;
            JSON.parse(raw).forEach(function(d) {
                try {
                    var obj = { cx_time:d.cx_time, cy_price:d.cy_price, r_price:d.r_price,
                                color:d.color||'#ffd700', width:d.width||1,
                                style:d.style||'solid', locked:d.locked||false,
                                customLabel:d.customLabel||'', visibility:d.visibility||[] };
                    circles.push(obj);
                    buildCircleEl(obj);
                } catch(e) {}
            });
        } catch(e) {}
    }

    /* ═══════════════════════════════════════════════════════════════════════
       TEXT LABEL TOOL
       ═══════════════════════════════════════════════════════════════════════ */

    function _textKey() { return 'apex_texts_' + _currentPair; }

    function saveTexts() {
        try {
            localStorage.setItem(_textKey(), JSON.stringify(
                textDrawings.map(function(t) {
                    return { time:t.time, price:t.price, content:t.content,
                             color:t.color, fontSize:t.fontSize, bold:t.bold, locked:t.locked||false };
                })
            ));
        } catch(e) {}
    }

    function loadTexts() {
        try {
            var raw = localStorage.getItem(_textKey()); if (!raw) return;
            JSON.parse(raw).forEach(function(d) {
                try {
                    var obj = { id:textIdCtr++, time:d.time, price:d.price,
                                content:d.content||'', color:d.color||'#ffffff',
                                fontSize:d.fontSize||14, bold:d.bold||false, locked:d.locked||false };
                    textDrawings.push(obj);
                    buildTextEl(obj);
                } catch(e) {}
            });
        } catch(e) {}
    }

    function ensureTextOverlay() {
        var el = document.getElementById('tvlw-chart'); if (!el) return null;
        if (textOverlay && textOverlay.parentNode === el) return textOverlay;
        textOverlay = document.createElement('div');
        textOverlay.id = 'apex-text-overlay';
        textOverlay.style.cssText =
            'position:absolute;top:0;left:0;pointer-events:none;z-index:16;' +
            'width:100%;height:100%;overflow:visible;';
        el.appendChild(textOverlay);
        return textOverlay;
    }

    function buildTextEl(t) {
        var ov = ensureTextOverlay(); if (!ov) return;

        var wrap = document.createElement('div');
        wrap.style.cssText = 'position:absolute;pointer-events:none;';

        var textEl = document.createElement('div');
        textEl.className = 'apex-text-label';
        textEl.style.cssText =
            'display:inline-block;pointer-events:auto;cursor:move;white-space:pre;' +
            'font-family:sans-serif;padding:2px 5px;border-radius:2px;' +
            'text-shadow:0 1px 3px rgba(0,0,0,0.9);user-select:none;';

        textEl.addEventListener('mousedown', function(e) {
            if (drawMode) return;
            var idx = textDrawings.indexOf(t); if (idx < 0) return;
            _selectText(idx);
            e.stopPropagation();
            if (t.locked) return;
            e.preventDefault();
            var chartEl = document.getElementById('tvlw-chart'); if (!chartEl) return;
            var r = chartEl.getBoundingClientRect();
            textMove = { idx:idx,
                startX: e.clientX - r.left, startY: e.clientY - r.top,
                origTime: t.time, origPrice: t.price };
            try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
        });
        textEl.addEventListener('dblclick', function(e) {
            e.stopPropagation();
            var idx = textDrawings.indexOf(t); if (idx < 0) return;
            _startTextEdit(t, e.clientX, e.clientY);
        });

        var delBtn = document.createElement('div');
        delBtn.textContent = '×';
        delBtn.style.cssText =
            'position:absolute;top:-8px;right:-8px;z-index:22;color:#ccc;font-size:13px;' +
            'cursor:pointer;pointer-events:auto;background:rgba(13,17,23,0.88);' +
            'width:16px;height:16px;line-height:16px;text-align:center;' +
            'border-radius:50%;border:1px solid #555;display:none;';
        delBtn.addEventListener('mousedown', function(e) { e.stopImmediatePropagation(); e.preventDefault(); });
        delBtn.addEventListener('mouseup', function(e) {
            e.stopImmediatePropagation(); e.preventDefault();
            var idx = textDrawings.indexOf(t);
            if (idx >= 0) delText(idx);
        });

        wrap.appendChild(textEl); wrap.appendChild(delBtn);
        ov.appendChild(wrap);
        t.el = wrap; t.textEl = textEl; t.delBtn = delBtn;
        updateText(t);
    }

    function updateText(t) {
        if (!t.el || !chart || !S.candle) return;
        var x = _timeToXExtrap(t.time);
        var y = _priceToY(t.price);
        if (x == null || y == null) { t.el.style.display = 'none'; return; }
        var chartEl = document.getElementById('tvlw-chart');
        var chartW  = chartEl ? chartEl.clientWidth - psWidth() : 800;
        if (x < -200 || x > chartW + 200) { t.el.style.display = 'none'; return; }
        t.el.style.display = 'block';
        t.el.style.left = x + 'px';
        t.el.style.top  = y + 'px';
        t.textEl.textContent  = t.content || '';
        t.textEl.style.color      = t.color || '#ffffff';
        t.textEl.style.fontSize   = (t.fontSize || 14) + 'px';
        t.textEl.style.fontWeight = t.bold ? '700' : '400';
        var isSel = (selectedText === textDrawings.indexOf(t));
        t.textEl.style.cursor     = t.locked ? 'default' : 'move';
        t.textEl.style.outline    = isSel ? '1px dashed ' + (t.color || '#ffffff') : 'none';
        t.textEl.style.background = isSel ? 'rgba(255,255,255,0.07)' : 'none';
        if (t.delBtn) t.delBtn.style.display = (isSel && !t.locked) ? '' : 'none';
    }

    function updateAllTexts() { textDrawings.forEach(updateText); }

    function _selectText(idx) {
        _deselectBox(); _deselectTrend(); _deselectFib(); _deselectPos(); _deselectCircle();
        deselectDrawing();
        selectedText = idx;
        textDrawings.forEach(updateText);
    }
    function _deselectText() {
        if (selectedText < 0) return;
        selectedText = -1;
        textDrawings.forEach(updateText);
    }

    function delText(idx) {
        var t = textDrawings[idx]; if (!t) return;
        if (t.el && t.el.parentNode) t.el.parentNode.removeChild(t.el);
        textDrawings.splice(idx, 1);
        if (selectedText === idx) selectedText = -1;
        else if (selectedText > idx) selectedText--;
        saveTexts();
    }

    function addText(time, price, content, color, fontSize, bold, existingId) {
        var ov = ensureTextOverlay(); if (!ov) return;
        var id = (existingId != null) ? existingId : textIdCtr++;
        if (id >= textIdCtr) textIdCtr = id + 1;
        var t = { id:id, time:time, price:price, content:content||'',
                  color:color||drawColor||'#ffffff', fontSize:fontSize||14,
                  bold:bold||false, locked:false };
        textDrawings.push(t);
        buildTextEl(t);
        _selectText(textDrawings.length - 1);
        saveTexts();
    }

    /* ── Inline text input ───────────────────────────────────────────────── */
    function _startTextInput(time, price, screenX, screenY) {
        _commitTextInput();
        var inp = document.createElement('textarea');
        inp.rows = 1;
        inp.placeholder = 'Type text… Enter to place, Shift+Enter for new line';
        inp.style.cssText =
            'position:fixed;z-index:99999;resize:both;min-width:180px;max-width:420px;' +
            'left:' + screenX + 'px;top:' + (screenY - 12) + 'px;' +
            'font-family:sans-serif;font-size:14px;color:#ffffff;' +
            'background:rgba(13,17,23,0.94);border:1px solid #ffd700;' +
            'border-radius:4px;padding:5px 9px;outline:none;';
        document.body.appendChild(inp);
        inp.focus();
        _textInput = { inp:inp, time:time, price:price, editing:null };
        inp.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') { _cancelTextInput(); return; }
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _commitTextInput(); }
        });
        inp.addEventListener('blur', function() { setTimeout(_commitTextInput, 150); });
    }

    function _startTextEdit(t, screenX, screenY) {
        _commitTextInput();
        var inp = document.createElement('textarea');
        inp.value = t.content || '';
        inp.rows = Math.max(1, (t.content || '').split('\n').length);
        inp.style.cssText =
            'position:fixed;z-index:99999;resize:both;min-width:180px;max-width:420px;' +
            'left:' + screenX + 'px;top:' + (screenY - 12) + 'px;' +
            'font-family:sans-serif;font-size:' + (t.fontSize||14) + 'px;' +
            'color:' + (t.color||'#ffffff') + ';' +
            'background:rgba(13,17,23,0.94);border:1px solid #ffd700;' +
            'border-radius:4px;padding:5px 9px;outline:none;';
        document.body.appendChild(inp);
        inp.focus(); inp.select();
        _textInput = { inp:inp, time:t.time, price:t.price, editing:t };
        inp.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') { _cancelTextInput(); return; }
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _commitTextInput(); }
        });
        inp.addEventListener('blur', function() { setTimeout(_commitTextInput, 150); });
    }

    function _cancelTextInput() {
        if (!_textInput) return;
        if (_textInput.inp && _textInput.inp.parentNode)
            _textInput.inp.parentNode.removeChild(_textInput.inp);
        _textInput = null;
    }

    function _commitTextInput() {
        if (!_textInput) return;
        var content  = (_textInput.inp ? _textInput.inp.value : '').trim();
        var time     = _textInput.time, price = _textInput.price;
        var editing  = _textInput.editing;
        _cancelTextInput();
        if (editing) {
            if (content) { editing.content = content; updateText(editing); saveTexts(); }
            else { var idx = textDrawings.indexOf(editing); if (idx >= 0) delText(idx); }
            return;
        }
        if (!content) return;
        addText(time, price, content, drawColor, 14, false);
    }

    function buildCircleEl(c) {
        var svg = ensureTrendOverlay(); if (!svg) return;
        var g = document.createElementNS('http://www.w3.org/2000/svg', 'g');

        var hitEl = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        hitEl.setAttribute('fill', 'none');
        hitEl.setAttribute('stroke', 'transparent');
        hitEl.setAttribute('stroke-width', '14');
        hitEl.setAttribute('pointer-events', 'stroke');
        hitEl.setAttribute('cursor', 'move');

        var circEl = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        circEl.setAttribute('fill', 'none');
        circEl.setAttribute('pointer-events', 'none');

        var cHandle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        cHandle.setAttribute('r', '5');
        cHandle.setAttribute('stroke', '#fff');
        cHandle.setAttribute('stroke-width', '1.5');
        cHandle.setAttribute('pointer-events', 'all');
        cHandle.setAttribute('cursor', 'move');
        cHandle.setAttribute('display', 'none');

        var rHandle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        rHandle.setAttribute('r', '5');
        rHandle.setAttribute('stroke', '#fff');
        rHandle.setAttribute('stroke-width', '1.5');
        rHandle.setAttribute('pointer-events', 'all');
        rHandle.setAttribute('cursor', 'ew-resize');
        rHandle.setAttribute('display', 'none');

        var lockBadge = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        lockBadge.textContent = '🔒';
        lockBadge.setAttribute('font-size', '11');
        lockBadge.setAttribute('pointer-events', 'none');
        lockBadge.setAttribute('display', 'none');

        g.appendChild(hitEl); g.appendChild(circEl);
        g.appendChild(cHandle); g.appendChild(rHandle);
        g.appendChild(lockBadge);
        svg.appendChild(g);

        c.el = g; c.circEl = circEl; c.hitEl = hitEl;
        c.cHandle = cHandle; c.rHandle = rHandle; c.lockBadge = lockBadge;

        hitEl.addEventListener('dblclick', function(e) {
            e.stopPropagation(); e.preventDefault();
            var idx = circles.indexOf(c);
            if (idx >= 0) _showDrawingSettings(e.clientX, e.clientY, 'circle', idx);
        });
        hitEl.addEventListener('mousedown', function(e) {
            if (drawMode) return;
            var idx = circles.indexOf(c); if (idx < 0) return;
            _selectCircle(idx);
            e.stopPropagation();
            if (c.locked) return;
            e.preventDefault();
            var chartEl2 = document.getElementById('tvlw-chart'); if (!chartEl2) return;
            var rr = chartEl2.getBoundingClientRect();
            circleDrag = { type:'move', idx:idx,
                startX: e.clientX - rr.left, startY: e.clientY - rr.top,
                origCxTime: c.cx_time, origCyPrice: c.cy_price };
            try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
        });
        hitEl.addEventListener('mousemove', function(e) {
            if (circleDrag) return;
            hitEl.setAttribute('pointer-events', 'none');
            var below = document.elementFromPoint(e.clientX, e.clientY);
            hitEl.setAttribute('pointer-events', 'stroke');
            if (below && below !== hitEl) {
                below.dispatchEvent(new MouseEvent('mousemove', {
                    bubbles:true, cancelable:true,
                    clientX:e.clientX, clientY:e.clientY, view:window
                }));
            }
        });
        cHandle.addEventListener('mousedown', function(e) {
            if (drawMode) return;
            var idx = circles.indexOf(c); if (idx < 0) return;
            e.stopPropagation();
            if (c.locked) return;
            e.preventDefault();
            var chartEl2 = document.getElementById('tvlw-chart'); if (!chartEl2) return;
            var rr = chartEl2.getBoundingClientRect();
            circleDrag = { type:'move', idx:idx,
                startX: e.clientX - rr.left, startY: e.clientY - rr.top,
                origCxTime: c.cx_time, origCyPrice: c.cy_price };
            try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
        });
        rHandle.addEventListener('mousedown', function(e) {
            if (drawMode) return;
            var idx = circles.indexOf(c); if (idx < 0) return;
            e.stopPropagation();
            if (c.locked) return;
            e.preventDefault();
            circleDrag = { type:'resize', idx:idx, origRPrice: c.r_price, origCyPrice: c.cy_price };
            try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
        });
    }

    function updateCircle(c) {
        if (!c.el || !chart || !S.candle) return;
        if (c.visibility && c.visibility.length > 0 && c.visibility.indexOf(_currentTf) < 0) {
            c.el.setAttribute('display', 'none'); return;
        }
        var scx = _timeToXExtrap(c.cx_time);
        var scy = _priceToY(c.cy_price);
        if (scx == null || scy == null) { c.el.setAttribute('display', 'none'); return; }
        c.el.setAttribute('display', '');

        var scy2 = _priceToY(c.cy_price + c.r_price);
        var sr = (scy2 != null) ? Math.abs(scy - scy2) : 20;
        sr = Math.max(sr, 3);

        var idx = circles.indexOf(c);
        var isSel = (idx >= 0 && selectedCircle === idx);
        var col   = c.color || '#ffd700';
        var lw    = c.width || 1;

        [c.circEl, c.hitEl].forEach(function(el) {
            el.setAttribute('cx', String(scx));
            el.setAttribute('cy', String(scy));
            el.setAttribute('r',  String(sr));
        });
        c.circEl.setAttribute('stroke', col);
        c.circEl.setAttribute('stroke-width', String(lw));
        c.circEl.setAttribute('stroke-opacity', c.locked ? '0.55' : '1');
        if (c.style === 'dashed') {
            c.circEl.setAttribute('stroke-dasharray', '8,5');
        } else if (c.style === 'dotted') {
            c.circEl.setAttribute('stroke-dasharray', '2,4');
        } else {
            c.circEl.removeAttribute('stroke-dasharray');
        }
        c.hitEl.setAttribute('cursor', c.locked ? 'default' : 'move');

        if (isSel && !c.locked) {
            c.cHandle.setAttribute('cx', String(scx)); c.cHandle.setAttribute('cy', String(scy));
            c.cHandle.setAttribute('fill', col); c.cHandle.setAttribute('display', '');
            c.rHandle.setAttribute('cx', String(scx + sr)); c.rHandle.setAttribute('cy', String(scy));
            c.rHandle.setAttribute('fill', col); c.rHandle.setAttribute('display', '');
        } else {
            c.cHandle.setAttribute('display', 'none');
            c.rHandle.setAttribute('display', 'none');
        }

        if (c.locked) {
            c.lockBadge.setAttribute('x', String(scx + 5));
            c.lockBadge.setAttribute('y', String(scy - sr - 2));
            c.lockBadge.setAttribute('display', '');
        } else {
            c.lockBadge.setAttribute('display', 'none');
        }
    }

    function updateAllCircles() { circles.forEach(updateCircle); }

    function _selectCircle(idx) {
        if (selectedCircle >= 0) _deselectCircle();
        _deselectText();
        selectedCircle = idx;
        updateCircle(circles[idx]);
    }
    function _deselectCircle() {
        if (selectedCircle < 0) return;
        var old = selectedCircle; selectedCircle = -1;
        if (circles[old]) updateCircle(circles[old]);
    }

    function delCircle(idx) {
        var c = circles[idx]; if (!c) return;
        if (c.el && c.el.parentNode) c.el.parentNode.removeChild(c.el);
        circles.splice(idx, 1);
        if (selectedCircle === idx) selectedCircle = -1;
        else if (selectedCircle > idx) selectedCircle--;
        saveCircles();
    }

    function _dupCircle(idx) {
        var c = circles[idx]; if (!c) return;
        var pip = pipSize(_currentPair) * 20;
        var obj = { cx_time:c.cx_time, cy_price:c.cy_price + pip, r_price:c.r_price,
                    color:c.color, width:c.width, style:c.style||'solid', locked:false };
        circles.push(obj);
        buildCircleEl(obj);
        _selectCircle(circles.length - 1);
        saveCircles();
    }

    function _dupHLine(idx) {
        var d = drawings[idx]; if (!d) return;
        var pip = pipSize(_currentPair) * 5;
        var obj = { type:'h-line', price:d.price + pip, color:d.color,
                    width:d.width, style:d.style||'solid', locked:false };
        drawings.push(obj);
        buildHLineEl(obj);
        selectDrawing(drawings.length - 1);
        saveDrawings();
    }

    /* ── Circle preview (shown while dragging to set radius) ──────────── */
    function _updateCirclePreview(cx, cy) {
        if (!circleDraw) return;
        var svg = ensureTrendOverlay(); if (!svg) return;
        if (!_circlePreviewEl) {
            _circlePreviewEl = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            _circlePreviewEl.setAttribute('fill', 'none');
            _circlePreviewEl.setAttribute('pointer-events', 'none');
            svg.appendChild(_circlePreviewEl);
        }
        var dx = cx - circleDraw.startX, dy = cy - circleDraw.startY;
        var r  = Math.sqrt(dx * dx + dy * dy);
        _circlePreviewEl.setAttribute('cx', String(circleDraw.startX));
        _circlePreviewEl.setAttribute('cy', String(circleDraw.startY));
        _circlePreviewEl.setAttribute('r',  String(r));
        _circlePreviewEl.setAttribute('stroke', drawColor);
        _circlePreviewEl.setAttribute('stroke-width', String(drawWidth));
        _circlePreviewEl.setAttribute('stroke-dasharray', '6,4');
        _circlePreviewEl.setAttribute('display', '');
    }
    function _clearCirclePreview() {
        if (_circlePreviewEl) _circlePreviewEl.setAttribute('display', 'none');
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
                        var obj = { type:'h-line', price:d.price,
                                    color:d.color||'#ffd700', width:d.width||1,
                                    style:d.style||'solid', locked:d.locked||false,
                                    customLabel:d.customLabel||'', visibility:d.visibility||[] };
                        drawings.push(obj);
                        buildHLineEl(obj);
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
        if (d.type === 'h-line' && d.el && d.el.parentNode) {
            d.el.parentNode.removeChild(d.el);
        }
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
        _deselectText();
        var d = drawings[idx]; selectedIdx = idx; d._selected = true;
        if (d.type === 'h-line') updateHLine(d);
    }

    function deselectDrawing() {
        if (selectedIdx < 0) return;
        var d = drawings[selectedIdx]; d._selected = false;
        if (d.type === 'h-line') updateHLine(d);
        selectedIdx = -1;
    }

    function moveDrawing(idx, priceDelta) {
        var d = drawings[idx];
        if (d.type === 'h-line' && dragStartData) {
            d.price = (dragStartData.price || 0) + priceDelta;
            updateHLine(d);
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

    /* ── Make any fixed/absolute element draggable by its handle ─────────────── */
    function _makeDraggable(el, handle) {
        if (!el || !handle) return;
        var startX, startY, startLeft, startTop;
        handle.addEventListener('mousedown', function(e) {
            e.preventDefault();
            startX    = e.clientX;
            startY    = e.clientY;
            startLeft = parseInt(el.style.left) || 0;
            startTop  = parseInt(el.style.top)  || 0;
            function onMove(ev) {
                el.style.left = (startLeft + ev.clientX - startX) + 'px';
                el.style.top  = (startTop  + ev.clientY - startY) + 'px';
            }
            function onUp() {
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup',   onUp);
            }
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup',   onUp);
        });
    }

    function _showPositionForm(dir, entry, tp, sl, barTime, iv, screenX, screenY) {
        _hidePositionForm();
        var col   = dir === 'long' ? '#00ff88' : '#ff3366';
        var bgCol = dir === 'long' ? '#003d1f'  : '#3d0010';
        var arrow = dir === 'long' ? '▲ LONG'   : '▼ SHORT';
        var dec   = isJPY(_currentPair) ? 3  : 5;
        var step  = isJPY(_currentPair) ? 0.001 : 0.00001;

        /* Keep form inside viewport — use fixed positioning anchored to screen coords */
        var vpW = window.innerWidth  || 1200;
        var vpH = window.innerHeight || 800;
        var fx  = Math.min(screenX + 18, vpW - 240);
        var fy  = Math.max(screenY - 110, 8);
        fy = Math.min(fy, vpH - 280);   /* don't run off bottom */

        var form = document.createElement('div');
        form.id = 'apex-pos-form';
        form.style.cssText =
            'position:fixed;left:' + fx + 'px;top:' + fy + 'px;' +
            'z-index:99999;background:rgba(13,17,23,0.97);' +
            'border:1.5px solid ' + col + ';border-radius:8px;' +
            'padding:14px;width:215px;box-sizing:border-box;' +
            'box-shadow:0 8px 28px rgba(0,0,0,0.7);' +
            'font-family:monospace;';

        form.innerHTML =
            '<div id="apf-drag-handle" style="color:' + col + ';font-weight:700;font-size:13px;' +
            'margin-bottom:11px;display:flex;align-items:center;cursor:move;' +
            'padding-bottom:7px;border-bottom:1px solid rgba(255,255,255,0.08);">' +
            arrow +
            '<span style="color:#7d8590;font-size:10px;font-weight:400;margin-left:auto">' +
            (_currentPair || '') + ' &nbsp;⠿</span></div>' +
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

        document.body.appendChild(form);
        _makeDraggable(form, document.getElementById('apf-drag-handle'));

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

    /* Edit form opened by double-clicking an existing position tool */
    function _showPositionEditForm(pos, screenX, screenY) {
        _hidePositionForm();
        var dir   = pos.direction;
        var col   = dir === 'long' ? '#00ff88' : '#ff3366';
        var bgCol = dir === 'long' ? '#003d1f'  : '#3d0010';
        var arrow = dir === 'long' ? '▲ LONG'   : '▼ SHORT';
        var dec   = isJPY(_currentPair) ? 3  : 5;
        var step  = isJPY(_currentPair) ? 0.001 : 0.00001;

        /* screenX/Y are viewport clientX/Y from dblclick event — use fixed positioning */
        var vpW = window.innerWidth  || 1200;
        var vpH = window.innerHeight || 800;
        var fx  = Math.min(screenX + 18, vpW - 250);
        var fy  = Math.max(screenY - 130, 8);
        fy = Math.min(fy, vpH - 320);

        var form = document.createElement('div');
        form.id = 'apex-pos-form';
        form.style.cssText =
            'position:fixed;left:' + fx + 'px;top:' + fy + 'px;z-index:99999;' +
            'background:rgba(13,17,23,0.97);border:1.5px solid ' + col + ';border-radius:8px;' +
            'padding:14px;width:220px;box-sizing:border-box;' +
            'box-shadow:0 8px 28px rgba(0,0,0,0.7);font-family:monospace;';

        var lockLabel = pos.locked ? '🔒 Locked' : '🔓 Unlocked';
        var lockCol   = pos.locked ? '#ffd700' : '#8b949e';

        form.innerHTML =
            '<div id="apf-drag-handle" style="color:' + col + ';font-weight:700;font-size:13px;' +
            'margin-bottom:11px;display:flex;align-items:center;gap:6px;cursor:move;' +
            'padding-bottom:7px;border-bottom:1px solid rgba(255,255,255,0.08);">' +
            arrow +
            '<span style="color:#7d8590;font-size:10px;font-weight:400;margin-left:auto">' +
            (_currentPair || '') + ' &nbsp;⠿</span></div>' +
            _posFormField('Entry',       'apf-entry', pos.entry.toFixed(dec), step, '#e6edf3') +
            _posFormField('Stop Loss',   'apf-sl',    pos.sl.toFixed(dec),    step, '#ff3366') +
            _posFormField('Take Profit', 'apf-tp',    pos.tp.toFixed(dec),    step, '#00ff88') +
            '<div style="display:flex;gap:5px;margin-top:12px">' +
            '<button id="apf-apply" style="flex:1;background:' + bgCol + ';color:' + col +
            ';border:1px solid ' + col + ';border-radius:4px;padding:7px;cursor:pointer;' +
            'font-weight:700;font-size:12px;font-family:monospace">Apply</button>' +
            '<button id="apf-lock" style="background:#21262d;color:' + lockCol +
            ';border:1px solid #30363d;border-radius:4px;padding:6px 8px;' +
            'cursor:pointer;font-size:11px;white-space:nowrap">' + lockLabel + '</button>' +
            '<button id="apf-cancel" style="background:#21262d;color:#8b949e;' +
            'border:1px solid #30363d;border-radius:4px;padding:7px 10px;' +
            'cursor:pointer;font-size:12px">✕</button></div>';

        document.body.appendChild(form);
        _makeDraggable(form, document.getElementById('apf-drag-handle'));

        function _doApply() {
            var ev = parseFloat((document.getElementById('apf-entry') || {}).value);
            var sv = parseFloat((document.getElementById('apf-sl')    || {}).value);
            var tv = parseFloat((document.getElementById('apf-tp')    || {}).value);
            if (isNaN(ev) || isNaN(sv) || isNaN(tv)) return;
            pos.entry = ev; pos.sl = sv; pos.tp = tv;
            updatePos(pos); savePos(); _hidePositionForm();
        }

        document.getElementById('apf-apply').addEventListener('click', _doApply);
        document.getElementById('apf-lock').addEventListener('click', function() {
            pos.locked = !pos.locked;
            updatePos(pos); savePos(); _hidePositionForm();
        });
        document.getElementById('apf-cancel').addEventListener('click', _hidePositionForm);

        form.addEventListener('keydown', function(ev) {
            if (ev.key === 'Enter')  _doApply();
            if (ev.key === 'Escape') _hidePositionForm();
            ev.stopPropagation();
        });
        setTimeout(function() {
            var inp = document.getElementById('apf-entry');
            if (inp) { inp.focus(); inp.select(); }
        }, 60);
    }

    /* Dismiss form when clicking anywhere outside it */
    document.addEventListener('mousedown', function(e) {
        var f = document.getElementById('apex-pos-form');
        if (f && !f.contains(e.target)) _hidePositionForm();
    });

    /* Keyboard shortcuts — Delete/Backspace deletes selected drawing; Escape dismisses forms */
    document.addEventListener('keydown', function(e) {
        /* Never intercept keys while user is typing in an input or textarea */
        var tag = e.target && e.target.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
        if (e.target && e.target.isContentEditable) return;

        if (e.key === 'Escape') {
            _hidePositionForm();
            _hideDrawingSettings();
            _deselectBox(); _deselectTrend(); _deselectFib(); _deselectPos();
            return;
        }

        if (e.key === 'Delete' || e.key === 'Backspace') {
            if (window._apexDeleteSelected && window._apexDeleteSelected()) {
                e.preventDefault();
            }
        }

        if (e.key === 'l' || e.key === 'L') {
            if (window._apexLockSelected) window._apexLockSelected();
            e.preventDefault();
        }
    });

    /* ═══════════════════════════════════════════════════════════════════════
       DRAWING SETTINGS POPUP  (Issue #6 — double-click any drawing to edit)
       ═══════════════════════════════════════════════════════════════════════ */

    /* ── Drawing settings constants ─────────────────────────────────────── */
    var _ALL_VIS       = ['M5','M15','M30','H1','H4','D','W'];
    var _TF_LABELS_MAP = {M5:'5m',M15:'15m',M30:'30m',H1:'1H',H4:'4H',D:'D',W:'W'};
    var _SETTINGS_COLORS = [
        '#ffffff','#ff3366','#00ff88','#38b6ff','#ffd700','#ff9900','#cc44ff','#ff69b4','#7d8590','#00ccff'
    ];

    /* ── Settings helper functions ────────────────────────────────────────── */
    function _drawingTypeName(type) {
        return {'trend':'Trendline','h-line':'H-Line','box':'Box','fib':'Fibonacci','circle':'Circle'}[type] || type;
    }
    function _getDrawingObj(type, idx) {
        if (type==='trend')  return trendlines[idx];
        if (type==='h-line') return drawings[idx];
        if (type==='box')    return boxes[idx];
        if (type==='fib')    return fibs[idx];
        if (type==='circle') return circles[idx];
        return null;
    }
    function _updateDrawingAndSave(type, obj) {
        if (type==='trend')  { updateTrendline(obj); saveTrendlines(); }
        if (type==='h-line') { updateHLine(obj);     saveDrawings();   }
        if (type==='box')    { updateBox(obj);        saveBoxes();      }
        if (type==='fib')    { updateFib(obj);        saveFibs();       }
        if (type==='circle') { updateCircle(obj);     saveCircles();    }
    }
    function _deleteDrawingByTypeIdx(type, idx) {
        if (type==='trend')  { delTrendline(idx); return; }
        if (type==='box')    { delBox(idx); return; }
        if (type==='fib')    { delFib(idx); return; }
        if (type==='h-line') {
            var dh = drawings[idx]; if (!dh) return;
            _removeOne(dh); drawings.splice(idx, 1);
            if (selectedIdx===idx) selectedIdx=-1; else if (selectedIdx>idx) selectedIdx--;
            saveDrawings(); return;
        }
        if (type==='circle') {
            var cc2 = circles[idx]; if (!cc2) return;
            if (cc2.el && cc2.el.parentNode) cc2.el.parentNode.removeChild(cc2.el);
            circles.splice(idx, 1); saveCircles();
        }
    }
    function _dsRow(labelText, content) {
        var row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:8px;margin-bottom:8px;';
        if (labelText) {
            var lbl = document.createElement('span');
            lbl.textContent = labelText;
            lbl.style.cssText = 'color:#8b949e;font-size:10px;min-width:48px;flex-shrink:0;';
            row.appendChild(lbl);
        }
        if (content) row.appendChild(content);
        return row;
    }
    function _dsBtnStyle(active) {
        return 'cursor:pointer;padding:3px 8px;border-radius:3px;font-size:11px;outline:none;border:1px solid ' +
               (active ? '#58a6ff;background:#1f3559;color:#58a6ff;' : '#30363d;background:#21262d;color:#e6edf3;');
    }
    function _dsActionStyle(danger) {
        return 'cursor:pointer;padding:4px 12px;border-radius:4px;font-size:11px;outline:none;border:1px solid ' +
               (danger ? '#ff3366;background:rgba(255,51,102,0.12);color:#ff3366;'
                       : '#30363d;background:#21262d;color:#e6edf3;');
    }

    /* ── Main settings modal ──────────────────────────────────────────────── */
    function _showDrawingSettings(cx, cy, drawingType, idx) {
        _hideDrawingSettings();
        var obj = _getDrawingObj(drawingType, idx);
        if (!obj) return;

        /* Modal container */
        var modal = document.createElement('div');
        modal.id = 'apex-drawing-settings';
        modal.style.cssText =
            'position:fixed;z-index:9999;background:#161b22;border:1px solid #30363d;' +
            'border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,0.85);' +
            'font-family:"IBM Plex Sans",sans-serif;font-size:12px;min-width:290px;user-select:none;';
        var lft = Math.min(cx + 10, window.innerWidth  - 300);
        var top = Math.min(cy + 10, window.innerHeight - 320);
        modal.style.left = Math.max(4, lft) + 'px';
        modal.style.top  = Math.max(4, top) + 'px';

        /* Title bar (draggable) */
        var titleBar = document.createElement('div');
        titleBar.style.cssText =
            'display:flex;justify-content:space-between;align-items:center;' +
            'padding:8px 12px;border-bottom:1px solid #21262d;cursor:move;border-radius:8px 8px 0 0;';
        var titleSpan = document.createElement('span');
        titleSpan.textContent = _drawingTypeName(drawingType);
        titleSpan.style.cssText = 'color:#e6edf3;font-weight:600;font-size:12px;';
        var closeX = document.createElement('span');
        closeX.textContent = '×';
        closeX.style.cssText = 'color:#8b949e;cursor:pointer;font-size:18px;line-height:1;';
        closeX.addEventListener('click', _hideDrawingSettings);
        titleBar.appendChild(titleSpan); titleBar.appendChild(closeX);
        modal.appendChild(titleBar);

        /* Drag-to-move the modal */
        (function() {
            var ds = null;
            titleBar.addEventListener('mousedown', function(e) {
                if (e.target === closeX) return;
                ds = { ox: e.clientX - parseInt(modal.style.left||'0',10),
                       oy: e.clientY - parseInt(modal.style.top||'0',10) };
                e.preventDefault();
            });
            document.addEventListener('mousemove', function(e) {
                if (!ds) return;
                modal.style.left = Math.max(0, e.clientX - ds.ox) + 'px';
                modal.style.top  = Math.max(0, e.clientY - ds.oy) + 'px';
            });
            document.addEventListener('mouseup', function() { ds = null; });
        }());

        /* Tab system */
        var TAB_NAMES = ['Style','Text','Coordinates','Visibility'];
        var tabBar = document.createElement('div');
        tabBar.style.cssText = 'display:flex;border-bottom:1px solid #21262d;';
        var tabPanels = {};
        TAB_NAMES.forEach(function(name) {
            var btn = document.createElement('button');
            btn.setAttribute('data-tab', name);
            btn.textContent = name;
            btn.addEventListener('click', function() { _activateTab(name); });
            tabBar.appendChild(btn);
            var pnl = document.createElement('div');
            pnl.style.cssText = 'padding:10px 12px;display:none;';
            tabPanels[name] = pnl;
        });
        modal.appendChild(tabBar);
        TAB_NAMES.forEach(function(name) { modal.appendChild(tabPanels[name]); });

        function _activateTab(name) {
            TAB_NAMES.forEach(function(n) {
                var b = tabBar.querySelector('[data-tab="' + n + '"]');
                if (b) b.style.cssText = 'padding:7px 11px;cursor:pointer;font-size:11px;border:none;background:none;outline:none;border-bottom:2px solid ' +
                    (n===name ? '#58a6ff;color:#58a6ff;' : 'transparent;color:#8b949e;');
                if (tabPanels[n]) tabPanels[n].style.display = (n===name) ? 'block' : 'none';
            });
        }

        /* ── STYLE TAB ── */
        var sp = tabPanels['Style'];

        /* Color swatches */
        var swWrap = document.createElement('div');
        swWrap.style.cssText = 'display:flex;gap:5px;align-items:center;flex-wrap:wrap;';
        _SETTINGS_COLORS.forEach(function(col) {
            var sw = document.createElement('div');
            var isActive = (col === (obj.color||'#ffd700'));
            sw.style.cssText = 'width:16px;height:16px;border-radius:50%;background:' + col +
                ';cursor:pointer;flex-shrink:0;border:' +
                (isActive ? '2px solid #fff;' : '1.5px solid rgba(255,255,255,0.2);');
            sw.addEventListener('click', function() {
                obj.color = col; _updateDrawingAndSave(drawingType, obj); _hideDrawingSettings();
            });
            swWrap.appendChild(sw);
        });
        var cpick = document.createElement('input');
        cpick.type = 'color'; cpick.value = obj.color || '#ffd700';
        cpick.title = 'Custom color';
        cpick.style.cssText = 'width:22px;height:22px;border:none;background:none;cursor:pointer;padding:0;border-radius:3px;';
        cpick.addEventListener('change', function() { obj.color = cpick.value; _updateDrawingAndSave(drawingType, obj); });
        swWrap.appendChild(cpick);
        sp.appendChild(_dsRow('Color', swWrap));

        /* Width (not for fib) */
        if (drawingType !== 'fib') {
            var wWrap = document.createElement('div'); wWrap.style.cssText = 'display:flex;gap:4px;';
            var widthField = (drawingType==='box') ? 'borderWidth' : 'width';
            [1,2,3].forEach(function(w) {
                var wb = document.createElement('button');
                wb.style.cssText = _dsBtnStyle((obj[widthField]||1)===w);
                wb.innerHTML = ['─','━','▬'][w-1] + ' ' + w;
                wb.addEventListener('click', function() {
                    obj[widthField] = w; _updateDrawingAndSave(drawingType, obj);
                    wWrap.querySelectorAll('button').forEach(function(b2,i){ b2.style.cssText=_dsBtnStyle(i+1===w); });
                });
                wWrap.appendChild(wb);
            });
            sp.appendChild(_dsRow('Width', wWrap));
        }

        /* Line style (trend / h-line / circle) */
        if (drawingType==='trend' || drawingType==='h-line' || drawingType==='circle') {
            var lsWrap = document.createElement('div'); lsWrap.style.cssText = 'display:flex;gap:4px;';
            [['solid','——'],['dashed','- -'],['dotted','···']].forEach(function(pair) {
                var sb = document.createElement('button');
                sb.style.cssText = _dsBtnStyle((obj.style||'solid')===pair[0]);
                sb.textContent = pair[1];
                sb.addEventListener('click', function() {
                    obj.style = pair[0]; _updateDrawingAndSave(drawingType, obj);
                    lsWrap.querySelectorAll('button').forEach(function(b2,i){
                        b2.style.cssText = _dsBtnStyle([['solid'],['dashed'],['dotted']][i][0]===pair[0]);
                    });
                });
                lsWrap.appendChild(sb);
            });
            sp.appendChild(_dsRow('Style', lsWrap));
        }

        /* Fill opacity (box only) */
        if (drawingType==='box') {
            var opSlider = document.createElement('input');
            opSlider.type='range'; opSlider.min='0'; opSlider.max='100'; opSlider.step='5';
            opSlider.value = Math.round((obj.fillOpacity||0.1)*100);
            opSlider.style.cssText = 'flex:1;accent-color:#58a6ff;cursor:pointer;';
            var opVal = document.createElement('span');
            opVal.textContent = opSlider.value + '%';
            opVal.style.cssText = 'color:#e6edf3;min-width:30px;text-align:right;font-size:11px;';
            opSlider.addEventListener('input', function() {
                opVal.textContent = opSlider.value + '%';
                obj.fillOpacity = parseInt(opSlider.value,10) / 100;
                _updateDrawingAndSave(drawingType, obj);
            });
            var opWrap = document.createElement('div'); opWrap.style.cssText='display:flex;gap:6px;align-items:center;flex:1;';
            opWrap.appendChild(opSlider); opWrap.appendChild(opVal);
            sp.appendChild(_dsRow('Fill %', opWrap));
        }

        /* Lock toggle */
        var lockBtn = document.createElement('button');
        function _refreshLock() {
            var locked = !!obj.locked;
            lockBtn.textContent = locked ? '🔒 Locked' : '🔓 Unlocked';
            lockBtn.style.cssText = _dsActionStyle(false) + (locked ? 'border-color:#ffd700;color:#ffd700;' : '');
        }
        _refreshLock();
        lockBtn.addEventListener('click', function() { obj.locked = !obj.locked; _updateDrawingAndSave(drawingType, obj); _refreshLock(); });
        sp.appendChild(lockBtn);

        /* ── TEXT TAB ── */
        var tp = tabPanels['Text'];
        var labelInp = document.createElement('input');
        labelInp.type = 'text'; labelInp.placeholder = 'Custom label…';
        labelInp.value = obj.customLabel || '';
        labelInp.style.cssText =
            'width:100%;box-sizing:border-box;background:#0d1117;border:1px solid #30363d;' +
            'border-radius:4px;padding:5px 8px;color:#e6edf3;font-size:12px;outline:none;';
        labelInp.addEventListener('input', function() { obj.customLabel = labelInp.value; _updateDrawingAndSave(drawingType, obj); });
        tp.appendChild(_dsRow('Label', labelInp));

        /* ── COORDINATES TAB ── */
        var coordP = tabPanels['Coordinates'];
        function _pInp(lbl, getter, setter) {
            var inp = document.createElement('input');
            inp.type='number'; inp.step='any';
            var v = getter(); inp.value = (typeof v==='number') ? fmtPrice(v) : (v||'');
            inp.style.cssText =
                'width:100%;box-sizing:border-box;background:#0d1117;border:1px solid #30363d;' +
                'border-radius:4px;padding:5px 8px;color:#e6edf3;font-size:12px;outline:none;';
            inp.addEventListener('change', function() { var n=parseFloat(inp.value); if(!isNaN(n)){setter(n);_updateDrawingAndSave(drawingType, obj);} });
            coordP.appendChild(_dsRow(lbl, inp));
        }
        if (drawingType==='h-line') {
            _pInp('Price', function(){return obj.price;}, function(v){obj.price=v;});
        } else if (drawingType==='trend'||drawingType==='box'||drawingType==='fib') {
            _pInp('Price 1', function(){return obj.p1;}, function(v){obj.p1=v;});
            _pInp('Price 2', function(){return obj.p2;}, function(v){obj.p2=v;});
        } else if (drawingType==='circle') {
            _pInp('Center', function(){return obj.cy_price;}, function(v){obj.cy_price=v;});
            _pInp('Radius', function(){return obj.r_price;},  function(v){obj.r_price=v;});
        }

        /* ── VISIBILITY TAB ── */
        var vp = tabPanels['Visibility'];
        var visCont = document.createElement('div');
        visCont.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;';
        _ALL_VIS.forEach(function(tf) {
            var lbl2 = document.createElement('label');
            lbl2.style.cssText = 'display:flex;align-items:center;gap:4px;cursor:pointer;color:#e6edf3;font-size:11px;';
            var cb = document.createElement('input');
            cb.type = 'checkbox'; cb.value = tf;
            var vis = obj.visibility || [];
            cb.checked = (vis.length===0 || vis.indexOf(tf)>=0);
            cb.style.cssText = 'accent-color:#58a6ff;cursor:pointer;';
            cb.addEventListener('change', function() {
                var cur = (obj.visibility && obj.visibility.length>0) ? obj.visibility.slice() : _ALL_VIS.slice();
                if (cb.checked) { if (cur.indexOf(tf)<0) cur.push(tf); }
                else { cur = cur.filter(function(x){return x!==tf;}); }
                obj.visibility = (cur.length===_ALL_VIS.length) ? [] : cur;
                _updateDrawingAndSave(drawingType, obj);
            });
            lbl2.appendChild(cb);
            var tfSpan = document.createElement('span'); tfSpan.textContent = _TF_LABELS_MAP[tf]||tf;
            lbl2.appendChild(tfSpan);
            visCont.appendChild(lbl2);
        });
        vp.appendChild(_dsRow('Show on', visCont));

        /* Action row (delete) */
        var actionRow = document.createElement('div');
        actionRow.style.cssText = 'display:flex;justify-content:flex-end;padding:8px 12px;border-top:1px solid #21262d;';
        var delBtn2 = document.createElement('button');
        delBtn2.textContent = '🗑 Delete';
        delBtn2.style.cssText = _dsActionStyle(true);
        delBtn2.addEventListener('click', function() { _hideDrawingSettings(); _deleteDrawingByTypeIdx(drawingType, idx); });
        actionRow.appendChild(delBtn2);
        modal.appendChild(actionRow);

        document.body.appendChild(modal);
        _activateTab('Style');

        setTimeout(function() { document.addEventListener('mousedown', _settingsOutsideClick); }, 50);
    }

    function _settingsOutsideClick(e) {
        var p = document.getElementById('apex-drawing-settings');
        if (p && !p.contains(e.target)) _hideDrawingSettings();
    }

    function _hideDrawingSettings() {
        var p = document.getElementById('apex-drawing-settings');
        if (p && p.parentNode) p.parentNode.removeChild(p);
        document.removeEventListener('mousedown', _settingsOutsideClick);
    }

    /* ═══════════════════════════════════════════════════════════════════════
       FIBONACCI RETRACEMENT TOOL
       ═══════════════════════════════════════════════════════════════════════ */

    function _fibKey() {
        return 'apex_fibs_' + (_currentPair || 'default');
    }

    function saveFibs() {
        try {
            var data = fibs.map(function(f) {
                return { id:f.id, t1:f.t1, p1:f.p1, t2:f.t2, p2:f.p2,
                         color:f.color, locked: !!f.locked,
                         customLabel:f.customLabel||'', visibility:f.visibility||[] };
            });
            localStorage.setItem(_fibKey(), JSON.stringify(data));
        } catch(e) {}
    }

    function loadFibs() {
        try {
            var raw = localStorage.getItem(_fibKey());
            if (!raw) return;
            var arr = JSON.parse(raw);
            arr.forEach(function(d) {
                var fib = addFib(d.t1, d.p1, d.t2, d.p2, d.color || drawColor, d.id, !!d.locked);
                if (fib) { fib.customLabel = d.customLabel||''; fib.visibility = d.visibility||[]; }
                /* Keep fibIdCtr above any loaded ids so new fibs don't collide */
                var numId = parseInt(d.id, 10);
                if (!isNaN(numId) && numId > fibIdCtr) fibIdCtr = numId;
            });
        } catch(e) {}
    }

    function ensureFibOverlay() {
        if (fibOverlay && fibOverlay.parentNode) return fibOverlay;
        var el = document.getElementById('tvlw-chart'); if (!el) return null;
        fibOverlay = document.createElement('div');
        fibOverlay.id = 'apex-fib-overlay';
        fibOverlay.style.cssText = 'position:absolute;inset:0;pointer-events:none;overflow:visible;z-index:4;';
        el.style.position = 'relative';
        el.appendChild(fibOverlay);
        return fibOverlay;
    }

    function buildFibEl(fib) {
        /* ── outer wrapper: full overlay size; overflow:visible so labels escape ── */
        var wrap = document.createElement('div');
        wrap.className = 'apex-fib';
        wrap.dataset.fibId = String(fib.id);
        wrap.style.cssText = 'position:absolute;inset:0;pointer-events:none;overflow:visible;';

        /* ── inner band: horizontally bounded between x1 and x2 ── */
        var band = document.createElement('div');
        band.style.cssText = 'position:absolute;top:0;bottom:0;overflow:visible;pointer-events:none;';
        wrap.appendChild(band);

        /* ── level lines + labels + per-level hit strip inside the band ── */
        var levels = [];
        FIB_LEVELS.forEach(function(lvl) {
            var lineEl = document.createElement('div');
            lineEl.style.cssText =
                'position:absolute;left:0;right:0;height:1px;pointer-events:none;' +
                'background:' + lvl.col + ';opacity:0.80;';

            var labelEl = document.createElement('div');
            labelEl.style.cssText =
                'position:absolute;left:calc(100% + 5px);font-family:monospace;font-size:12px;' +
                'pointer-events:none;white-space:nowrap;' +
                'color:' + lvl.col + ';background:rgba(13,17,23,0.72);' +
                'padding:1px 4px;border-radius:2px;transform:translateY(-50%);';

            /* Thin hit strip centred on this level line — replaces the old full-area moveArea.
               Only the level lines are interactive; space between them is transparent to events. */
            var hitStrip = document.createElement('div');
            hitStrip.style.cssText =
                'position:absolute;left:0;right:0;height:10px;pointer-events:auto;' +
                'cursor:move;z-index:8;';
            hitStrip.addEventListener('mousedown', function(e) { _fibMousedown('move', e); });
            /* Pass mousemove through so crosshair still tracks above/below a level */
            hitStrip.addEventListener('mousemove', function(e) {
                if (fibDrag) return;
                hitStrip.style.pointerEvents = 'none';
                var below = document.elementFromPoint(e.clientX, e.clientY);
                hitStrip.style.pointerEvents = 'auto';
                if (below && below !== hitStrip) {
                    below.dispatchEvent(new MouseEvent('mousemove', {
                        bubbles: true, cancelable: true,
                        clientX: e.clientX, clientY: e.clientY, view: window
                    }));
                }
            });

            band.appendChild(lineEl);
            band.appendChild(labelEl);
            band.appendChild(hitStrip);
            levels.push({ lineEl: lineEl, labelEl: labelEl, hitStrip: hitStrip,
                          pct: lvl.pct, col: lvl.col });
        });

        /* ── p1 and p2 endpoint circles for resize ── */
        function _mkHandle() {
            var h = document.createElement('div');
            h.style.cssText =
                'position:absolute;width:10px;height:10px;border-radius:50%;' +
                'background:#fff;border:1.5px solid #666;cursor:crosshair;' +
                'pointer-events:auto;z-index:18;transform:translate(-50%,-50%);';
            return h;
        }
        var h1 = _mkHandle();  // p1 endpoint handle
        var h2 = _mkHandle();  // p2 endpoint handle
        wrap.appendChild(h1);
        wrap.appendChild(h2);

        /* ── delete button (shown on hover) ── */
        var delBtn = document.createElement('div');
        delBtn.textContent = '×';
        delBtn.style.cssText =
            'position:absolute;z-index:22;color:#ccc;font-size:12px;cursor:pointer;' +
            'pointer-events:auto;background:rgba(13,17,23,0.88);' +
            'width:14px;height:14px;line-height:14px;text-align:center;' +
            'border-radius:50%;border:1px solid #555;display:none;';
        wrap.appendChild(delBtn);

        /* ── wire events ── */
        function _getIdx() {
            return fibs.findIndex(function(f) { return String(f.id) === String(fib.id); });
        }
        function _fibMousedown(type, e) {
            if (drawMode) return; // drawMode active: let event bubble so new drawing is created
            var idx = _getIdx(); if (idx < 0) return;
            var ff = fibs[idx];
            _selectFib(idx);
            /* Always stop propagation — prevents _onDown from immediately deselecting the fib */
            e.stopPropagation();
            if (ff.locked) return; // locked: selected but no drag
            e.preventDefault();
            var chartEl = document.getElementById('tvlw-chart'); if (!chartEl) return;
            var r = chartEl.getBoundingClientRect();
            fibDrag = {
                type: type, idx: idx,
                startX: e.clientX - r.left, startY: e.clientY - r.top,
                origP1: ff.p1, origP2: ff.p2, origT1: ff.t1, origT2: ff.t2,
            };
            try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
        }
        h1.addEventListener('mousedown', function(e) { _fibMousedown('p1', e); });
        h2.addEventListener('mousedown', function(e) { _fibMousedown('p2', e); });

        delBtn.addEventListener('mousedown', function(e) { e.stopPropagation(); });
        delBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            var idx = _getIdx(); if (idx >= 0) delFib(idx);
        });
        /* Delete button visibility is now managed in updateFib (shown when selected) */
        wrap.addEventListener('dblclick', function(e) {
            var idx = _getIdx(); if (idx >= 0) _showDrawingSettings(e.clientX, e.clientY, 'fib', idx);
        });

        fib.el     = wrap;
        fib.band   = band;
        fib.h1     = h1;
        fib.h2     = h2;
        fib.delBtn = delBtn;
        fib.levels = levels;
    }

    function updateFib(fib) {
        if (!fib.el || !chart || !S.candle) return;
        if (fib.visibility && fib.visibility.length > 0 && fib.visibility.indexOf(_currentTf) < 0) {
            fib.el.style.display = 'none'; return;
        }
        var chartEl = document.getElementById('tvlw-chart'); if (!chartEl) return;
        var chartW  = chartEl.clientWidth - psWidth();

        var x1r = _timeToXExtrap(fib.t1);
        var x2r = _timeToXExtrap(fib.t2);
        if (x1r == null || x2r == null) { fib.el.style.display = 'none'; return; }

        /* Hide if both anchors are off the same side; clamp if only one is off-screen */
        if ((x1r < 0 && x2r < 0) || (x1r > chartW && x2r > chartW)) {
            fib.el.style.display = 'none'; return;
        }
        var x1 = Math.max(0, Math.min(chartW, x1r));
        var x2 = Math.max(0, Math.min(chartW, x2r));

        var xLeft  = Math.min(x1, x2);
        var xRight = Math.max(x1, x2);
        var w      = Math.max(xRight - xLeft, 4);

        fib.el.style.display  = 'block';
        fib.band.style.left   = xLeft + 'px';
        fib.band.style.width  = w + 'px';

        var dec   = (_currentPair && _currentPair.indexOf('JPY') !== -1) ? 3 : 5;
        var range = fib.p2 - fib.p1;

        fib.levels.forEach(function(lv) {
            var price = fib.p1 + lv.pct * range;
            var y = _priceToY(price);
            if (y == null) {
                lv.lineEl.style.display   = 'none';
                lv.labelEl.style.display  = 'none';
                lv.hitStrip.style.display = 'none';
                return;
            }
            lv.lineEl.style.display   = 'block';
            lv.labelEl.style.display  = 'block';
            lv.hitStrip.style.display = 'block';
            lv.lineEl.style.top       = y + 'px';
            lv.labelEl.style.top      = y + 'px';
            lv.hitStrip.style.top     = (y - 5) + 'px'; /* ±5px centred on the line */
            var pctStr = lv.pct === 0 ? '0%' : lv.pct === 1 ? '100%' : (lv.pct * 100).toFixed(1) + '%';
            lv.labelEl.textContent    = pctStr + '  ' + price.toFixed(dec);
        });

        /* Position endpoint handles (absolute within the full overlay) */
        var locked = !!fib.locked;
        var y1 = _priceToY(fib.p1), y2 = _priceToY(fib.p2);

        /* Update hit strip cursor based on locked state */
        fib.levels.forEach(function(lv) {
            lv.hitStrip.style.cursor = locked ? 'default' : 'move';
        });

        fib.el.title = locked ? 'Locked — double-click to edit' : '';

        /* Resize handles: visible only when selected and unlocked */
        var isSel = (selectedFib === fibs.indexOf(fib));
        if (isSel && !locked && y1 != null) {
            fib.h1.style.display = 'block';
            fib.h1.style.left    = x1 + 'px';
            fib.h1.style.top     = y1 + 'px';
        } else { fib.h1.style.display = 'none'; }

        if (isSel && !locked && y2 != null) {
            fib.h2.style.display = 'block';
            fib.h2.style.left    = x2 + 'px';
            fib.h2.style.top     = y2 + 'px';
        } else { fib.h2.style.display = 'none'; }

        /* Delete button near p1 handle — shown when selected and not locked */
        if (isSel && !locked && y1 != null) {
            fib.delBtn.style.display = 'block';
            fib.delBtn.style.left = (x1 + 6) + 'px';
            fib.delBtn.style.top  = (y1 - 6) + 'px';
        } else {
            fib.delBtn.style.display = 'none';
        }
    }

    function updateAllFibs() { fibs.forEach(updateFib); }

    function addFib(t1, p1, t2, p2, color, existingId, locked) {
        var ov = ensureFibOverlay(); if (!ov) return null;
        var fib = {
            id: existingId != null ? existingId : (++fibIdCtr),
            t1: t1, p1: p1, t2: t2, p2: p2, color: color || drawColor,
            locked: !!locked,
        };
        buildFibEl(fib);
        ov.appendChild(fib.el);
        fibs.push(fib);
        updateFib(fib);
        if (existingId == null) saveFibs();
        return fib;
    }

    function delFib(idx) {
        var fib = fibs[idx];
        if (fib && fib.el && fib.el.parentNode) fib.el.parentNode.removeChild(fib.el);
        fibs.splice(idx, 1);
        if (selectedFib === idx) selectedFib = -1;
        else if (selectedFib > idx) selectedFib--;
        saveFibs();
    }

    /* ═══════════════════════════════════════════════════════════════════════
       AUTO-SAVE TIMER
       ═══════════════════════════════════════════════════════════════════════ */

    function _startAutoSave() {
        _stopAutoSave();
        _autoSaveTimer = setInterval(function() {
            saveDrawings(); saveTrendlines(); saveBoxes(); savePos(); saveFibs(); saveTexts();
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
        _hideTrendPreview(); trendResize = null; trendMove = null; _trendMoveStart = null;

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

        /* Fibonacci */
        fibs.forEach(function(f){ if (f.el&&f.el.parentNode) f.el.parentNode.removeChild(f.el); });
        fibs = [];
        if (fibOverlay && fibOverlay.parentNode) fibOverlay.parentNode.removeChild(fibOverlay);
        fibOverlay = null; fibDraw = null; selectedFib = -1;

        /* Circles — stored in trend overlay (removed above with trendOverlay) */
        circles = []; selectedCircle = -1; circleDraw = null; circleDrag = null;
        _circlePreviewEl = null;

        /* Text labels */
        _cancelTextInput();
        textDrawings.forEach(function(t){ if (t.el&&t.el.parentNode) t.el.parentNode.removeChild(t.el); });
        textDrawings = []; selectedText = -1; textMove = null;
        if (textOverlay && textOverlay.parentNode) textOverlay.parentNode.removeChild(textOverlay);
        textOverlay = null;

        /* Trade SVG lines — also in trend overlay */
        _tradeSvgLines = []; _liveTradeData = []; tradeDrag = null;
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
        /* TWLC v5: markers are a separate plugin, not series.setMarkers() */
        S.markers = LC().createSeriesMarkers(S.candle, []);
        S.emas = [];

        /* CCI and MACD — deferred panes: created on first show, destroyed on hide.
           LWCHARTS auto-collapses pane[0] fills the freed space automatically.   */
        S.cci = S.cciMa = S.cciBbUpper = S.cciBbLower = null;
        S.macd = S.macdLine = S.macdSignal = null;

        /* Bollinger Bands — overlay on price pane (initially invisible) */
        S.bbUpper = chart.addSeries(LC().LineSeries, {
            color: '#38b6ff', lineWidth: 1,
            lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
            title: 'BB+', visible: false,
        }, 0);
        S.bbBasis = chart.addSeries(LC().LineSeries, {
            color: '#ffd700', lineWidth: 1, lineStyle: 2,
            lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
            title: 'BB Mid', visible: false,
        }, 0);
        S.bbLower = chart.addSeries(LC().LineSeries, {
            color: '#38b6ff', lineWidth: 1,
            lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
            title: 'BB-', visible: false,
        }, 0);

        /* Volume — histogram overlay on price pane (initially invisible) */
        S.volume = chart.addSeries(LC().HistogramSeries, {
            priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false,
            visible: false,
        }, 0);
        try {
            chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 }, visible: false });
        } catch(e) {}

        /* RSI and Stochastic panes are deferred — created on first enable, destroyed on disable */
        S.rsi    = null;
        S.stochK = null;
        S.stochD = null;

        requestAnimationFrame(function() {
            _loadIndSettings();
            _resizePanes();
        });

        try {
            wm = LC().createTextWatermark(chart.panes()[0], {
                horzAlign:'center', vertAlign:'center',
                lines:[{ text:'', color:WM_COLOR, fontSize:WM_SIZE, fontFamily:WM_FONT }],
            });
        } catch(e) {}

        loadDrawings(); loadTrendlines(); loadPos(); loadBoxes(); loadFibs(); loadCircles(); loadTexts();
        _startAutoSave();

        try {
            chart.timeScale().subscribeVisibleLogicalRangeChange(function() {
                updateAllTrendlines(); updateAllPos(); updateAllBoxes(); updateAllFibs(); updateAllHLines();
                updateAllCircles(); _updateAllTradeSvgLines(); updateAllTexts();
            });
            /* Save zoom (time range) whenever the user scrolls/zooms, keyed per pair+TF */
            chart.timeScale().subscribeVisibleTimeRangeChange(function(range) {
                if (!range) return;
                var zoomKey = 'apex_zoom_' + (_currentPair||'') + '_' + (_currentTf||'');
                try { localStorage.setItem(zoomKey, JSON.stringify({ from: range.from, to: range.to })); } catch(e) {}
            });
        } catch(e) {}
        try {
            chart.subscribeCrosshairMove(function() {
                if (trendlines.length > 0)      updateAllTrendlines();
                if (positions.length > 0)       updateAllPos();
                if (boxes.length > 0)           updateAllBoxes();
                if (fibs.length > 0)            updateAllFibs();
                if (drawings.length > 0)        updateAllHLines();
                if (circles.length > 0)         updateAllCircles();
                if (_tradeSvgLines.length > 0)  _updateAllTradeSvgLines();
                if (textDrawings.length > 0)    updateAllTexts();
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
                    /* Use raw mouse event client coords (screen-level) for reliable form placement */
                    var sx = (param.sourceEvent && param.sourceEvent.clientX != null)
                             ? param.sourceEvent.clientX
                             : (param.point ? param.point.x : 200);
                    var sy = (param.sourceEvent && param.sourceEvent.clientY != null)
                             ? param.sourceEvent.clientY
                             : (param.point ? param.point.y : 200);
                    _showPositionForm(dir, price, tp, sl, time, iv, sx, sy);
                    return;
                }

                if (drawMode==='h-line') {
                    try {
                        var hobj = { type:'h-line', price:price, color:drawColor,
                                     width:drawWidth, style:drawStyle, locked:false };
                        drawings.push(hobj);
                        buildHLineEl(hobj);
                        selectDrawing(drawings.length - 1);
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
                        _selectTrend(trendlines.length - 1);
                    }
                    return;
                }

                /* pips (measure) and fib modes are handled by the drag system — no click action here */
            });
        } catch(e) {}

        /* ── Mouse events ───────────────────────────────────────────────── */
        var _onDown = function(e) {
            if (posDrag || boxResize || boxMove || trendResize || trendMove || fibDrag) return;
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

            /* Fibonacci retracement draw start — drag from p1 to p2 */
            if (drawMode === 'fib') {
                var fTime = _xToTimeExtrap(chartX), fPrice = _yToPrice(chartY);
                if (!fTime || !fPrice) return;
                fibDraw = { startX:chartX, startY:chartY, startTime:fTime, startPrice:fPrice };
                try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
                e.stopPropagation(); e.preventDefault();
                return;
            }

            /* Circle draw start — click center, drag to set radius */
            if (drawMode === 'circle') {
                var cTime2 = _xToTimeExtrap(chartX), cPrice2 = _yToPrice(chartY);
                if (!cTime2 || !cPrice2) return;
                circleDraw = { cx_time:cTime2, cy_price:cPrice2, startX:chartX, startY:chartY };
                try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
                e.stopPropagation(); e.preventDefault();
                return;
            }

            /* Text label — single click to place */
            if (drawMode === 'text') {
                var tTime = _xToTimeExtrap(chartX), tPrice = _yToPrice(chartY);
                if (!tTime || !tPrice) return;
                e.stopPropagation(); e.preventDefault();
                _startTextInput(tTime, tPrice, e.clientX, e.clientY);
                return;
            }

            if (drawMode) return;

            /* Deselect all overlays when clicking neutral area */
            _deselectBox();
            _deselectTrend();
            _deselectFib();
            _deselectPos();
            _deselectCircle();
            _deselectText();

            var price=null, time=null;
            try { price = S.candle.coordinateToPrice(chartY); } catch(err) {}
            try { time  = chart.timeScale().coordinateToTime(chartX); } catch(err) {}
            if (price==null || !isFinite(price)) return;
            var idx = findNearest(price, time);
            if (idx >= 0) {
                selectDrawing(idx);
                if (!drawings[idx].locked) {
                    isDragging=true;
                    dragStartPrice=price; dragStartData=copyDragData(drawings[idx]);
                    try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
                }
                e.stopPropagation();
            } else { deselectDrawing(); }
        };

        var _onMove = function(e) {
            if (textMove) {
                var rtm = el.getBoundingClientRect();
                var cxm = e.clientX - rtm.left, cym = e.clientY - rtm.top;
                var t = textDrawings[textMove.idx]; if (!t) return;
                var newTime  = _xToTimeExtrap(cxm) || textMove.origTime;
                var newPrice = _yToPrice(cym)       || textMove.origPrice;
                t.time = newTime; t.price = newPrice;
                updateText(t);
                e.stopPropagation(); e.preventDefault(); return;
            }
            if (posDrag || boxResize || boxMove || trendResize || trendMove) return;
            if (boxDraw) {
                var r0 = el.getBoundingClientRect();
                _updateBoxPreview(e.clientX-r0.left, e.clientY-r0.top);
                return;
            }
            /* fibDrag — move or resize an existing fib */
            if (fibDrag) {
                var rfd = el.getBoundingClientRect();
                var cxd = e.clientX - rfd.left, cyd = e.clientY - rfd.top;
                var ff  = fibs[fibDrag.idx]; if (!ff) return;
                if (fibDrag.type === 'move') {
                    var dprice = (_yToPrice(cyd)||fibDrag.origP1) - (_yToPrice(fibDrag.startY)||fibDrag.origP1);
                    var dt     = (_xToTimeExtrap(cxd)||fibDrag.origT1) - (_xToTimeExtrap(fibDrag.startX)||fibDrag.origT1);
                    ff.p1 = fibDrag.origP1 + dprice;
                    ff.p2 = fibDrag.origP2 + dprice;
                    ff.t1 = fibDrag.origT1 + dt;
                    ff.t2 = fibDrag.origT2 + dt;
                } else if (fibDrag.type === 'p1') {
                    ff.p1 = _yToPrice(cyd)           || fibDrag.origP1;
                    ff.t1 = _xToTimeExtrap(cxd)      || fibDrag.origT1;
                } else if (fibDrag.type === 'p2') {
                    ff.p2 = _yToPrice(cyd)           || fibDrag.origP2;
                    ff.t2 = _xToTimeExtrap(cxd)      || fibDrag.origT2;
                }
                updateFib(ff);
                e.stopPropagation(); e.preventDefault();
                return;
            }
            if (fibDraw) {
                /* Live update: show a temporary fib while dragging */
                var r0f = el.getBoundingClientRect();
                var cx = e.clientX - r0f.left, cy = e.clientY - r0f.top;
                var curP = _yToPrice(cy);
                /* Remove old preview fib if any, then add fresh one */
                if (fibDraw._previewIdx != null) {
                    var pf = fibs[fibDraw._previewIdx];
                    if (pf) { if (pf.el&&pf.el.parentNode) pf.el.parentNode.removeChild(pf.el); fibs.splice(fibDraw._previewIdx,1); }
                    fibDraw._previewIdx = null;
                }
                if (curP && Math.abs(cy - fibDraw.startY) > 8) {
                    var curT = _xToTimeExtrap(cx) || fibDraw.startTime;
                    addFib(fibDraw.startTime, fibDraw.startPrice, curT, curP, drawColor, '__preview__');
                    fibDraw._previewIdx = fibs.length - 1;
                }
                return;
            }
            if (circleDraw) {
                var r0c = el.getBoundingClientRect();
                _updateCirclePreview(e.clientX - r0c.left, e.clientY - r0c.top);
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

        var _onUp = function(e) {
            if (textMove) {
                var t = textDrawings[textMove.idx];
                if (t) saveTexts();
                textMove = null;
                try { chart.applyOptions({ handleScroll:true, handleScale:true }); } catch(ex) {}
            }
            if (isDragging) {
                saveDrawings();
                try { chart.applyOptions({ handleScroll:true, handleScale:true }); } catch(ex) {}
            }
            isDragging=false; dragStartPrice=null; dragStartData=null;

            /* Finish fibDrag (move/resize) */
            if (fibDrag) {
                var ff2 = fibs[fibDrag.idx];
                if (ff2) { updateFib(ff2); saveFibs(); }
                fibDrag = null;
                try { chart.applyOptions({ handleScroll:true, handleScale:true }); } catch(ex3) {}
            }

            if (fibDraw) {
                /* Remove live preview fib */
                if (fibDraw._previewIdx != null) {
                    var pf = fibs[fibDraw._previewIdx];
                    if (pf) { if (pf.el&&pf.el.parentNode) pf.el.parentNode.removeChild(pf.el); fibs.splice(fibDraw._previewIdx,1); }
                }
                /* Only place if dragged enough vertically */
                var r0u = el.getBoundingClientRect();
                var ey  = (e ? e.clientY : fibDraw.startY) - (r0u ? r0u.top : 0);
                var endP = _yToPrice(ey);
                var ex  = e ? (e.clientX - r0u.left) : fibDraw.startX;
                var endT = _xToTimeExtrap(ex) || fibDraw.startTime;
                if (endP && Math.abs(ey - fibDraw.startY) > 10) {
                    addFib(fibDraw.startTime, fibDraw.startPrice, endT, endP, drawColor);
                }
                fibDraw = null;
                try { chart.applyOptions({ handleScroll:true, handleScale:true }); } catch(ex2) {}
            }

            /* Circle draw finalize */
            if (circleDraw) {
                _clearCirclePreview();
                var r0ci = el.getBoundingClientRect();
                var ex2  = e ? (e.clientX - r0ci.left) : circleDraw.startX;
                var ey2  = e ? (e.clientY - r0ci.top)  : circleDraw.startY;
                var dx2  = ex2 - circleDraw.startX, dy2 = ey2 - circleDraw.startY;
                var rPx  = Math.sqrt(dx2*dx2 + dy2*dy2);
                if (rPx > 5) {
                    /* Convert pixel radius to price units */
                    var cySc  = circleDraw.startY;
                    var edgeP = _yToPrice(cySc + rPx);
                    var cenP2 = _yToPrice(cySc);
                    var rPrice = (edgeP && cenP2) ? Math.abs(edgeP - cenP2) : 0;
                    if (rPrice > 0) {
                        var cobj = { cx_time:circleDraw.cx_time, cy_price:circleDraw.cy_price,
                                     r_price:rPrice, color:drawColor, width:drawWidth,
                                     style:drawStyle, locked:false };
                        circles.push(cobj);
                        buildCircleEl(cobj);
                        updateCircle(cobj);
                        _selectCircle(circles.length - 1);
                        saveCircles();
                    }
                }
                circleDraw = null;
                try { chart.applyOptions({ handleScroll:true, handleScale:true }); } catch(ex3) {}
            }
        };

        el.addEventListener('mousedown', _onDown);
        el.addEventListener('mousemove', _onMove);
        el.addEventListener('mouseup',   _onUp);
        document.addEventListener('mouseup', _onUp);
        _mouseListeners = { down:_onDown, move:_onMove, up:_onUp };

        new ResizeObserver(function() {
            if (chart && el) {
                try {
                    if (_paneResizeActive) {
                        /* Height is being managed by _resizePanes — only update width */
                        chart.applyOptions({ width: el.clientWidth });
                    } else {
                        chart.applyOptions({ width: el.clientWidth, height: el.clientHeight || TOTAL_H });
                    }
                } catch(e) {}
                updateAllTrendlines(); updateAllPos(); updateAllBoxes(); updateAllFibs();
                updateAllHLines(); updateAllCircles(); _updateAllTradeSvgLines(); updateAllTexts();
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
        _lastChartData = data;
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

        /* CCI and MACD — update data if series already exist (creation/removal handled by _applyIndVisualSettings) */
        try { if (S.cci)        S.cci.setData(data.cci||[]); }              catch(e) {}
        try { if (S.cciMa)      S.cciMa.setData(data.cci_ma||[]); }        catch(e) {}
        try { if (S.cciBbUpper) S.cciBbUpper.setData(data.cci_bb_upper||[]); } catch(e) {}
        try { if (S.cciBbLower) S.cciBbLower.setData(data.cci_bb_lower||[]); } catch(e) {}
        try { if (S.macd)       S.macd.setData(data.macd||[]); }            catch(e) {}
        try { if (S.macdLine)   S.macdLine.setData(data.macd_line||[]); }   catch(e) {}
        try { if (S.macdSignal) S.macdSignal.setData(data.macd_signal||[]); } catch(e) {}

        /* ── New indicators ── */
        var toggles = data.ind_toggles || {};

        /* RSI — deferred pane: create on first enable, remove on disable.
           Use chart.panes().length as the target to get a sequential index. */
        if (!!toggles.rsi) {
            if (!S.rsi) {
                try {
                    var _rsiPane = chart.panes().length;
                    S.rsi = chart.addSeries(LC().LineSeries, {
                        color: '#aa00ff', lineWidth: 1.5,
                        lastValueVisible: false, priceLineVisible: false,
                        crosshairMarkerVisible: false, title: 'RSI',
                    }, _rsiPane);
                    _rsiPane = chart.panes().length - 1;
                    dotted(70, S.rsi); dotted(30, S.rsi); dotted(50, S.rsi);
                    (function(pi) { requestAnimationFrame(function() {
                        try { chart.panes()[pi].setHeight(IND_H); } catch(e) {}
                    }); })(_rsiPane);
                } catch(e) {}
            }
            try { if (S.rsi) S.rsi.setData(data.rsi || []); } catch(e) {}
        } else if (S.rsi) {
            try { chart.removeSeries(S.rsi); } catch(e) {}
            S.rsi = null;
        }

        /* Stochastic — deferred pane: create on first enable, remove on disable.
           K and D must share the same pane — use panes().length-1 for D after K is created. */
        if (!!toggles.stoch) {
            if (!S.stochK) {
                try {
                    var _stochPane = chart.panes().length;
                    S.stochK = chart.addSeries(LC().LineSeries, {
                        color: '#2962ff', lineWidth: 1.5,
                        lastValueVisible: false, priceLineVisible: false,
                        crosshairMarkerVisible: false, title: '%K',
                    }, _stochPane);
                    _stochPane = chart.panes().length - 1;  /* actual pane after K creation */
                    S.stochD = chart.addSeries(LC().LineSeries, {
                        color: '#ff6d00', lineWidth: 1.5,
                        lastValueVisible: false, priceLineVisible: false,
                        crosshairMarkerVisible: false, title: '%D',
                    }, _stochPane);
                    dotted(80, S.stochK); dotted(20, S.stochK);
                    (function(pi) { requestAnimationFrame(function() {
                        try { chart.panes()[pi].setHeight(IND_H); } catch(e) {}
                    }); })(_stochPane);
                } catch(e) {}
            }
            try { if (S.stochK) S.stochK.setData(data.stoch_k || []); } catch(e) {}
            try { if (S.stochD) S.stochD.setData(data.stoch_d || []); } catch(e) {}
        } else if (S.stochK) {
            try { chart.removeSeries(S.stochK); } catch(e) {}
            try { chart.removeSeries(S.stochD); } catch(e) {}
            S.stochK = null;
            S.stochD = null;
        }

        /* Bollinger Bands (pane 0 overlay) */
        try { if (S.bbUpper) { S.bbUpper.setData(data.bb_upper||[]); S.bbUpper.applyOptions({ visible: !!toggles.bb }); } } catch(e) {}
        try { if (S.bbBasis) { S.bbBasis.setData(data.bb_basis||[]); S.bbBasis.applyOptions({ visible: !!toggles.bb }); } } catch(e) {}
        try { if (S.bbLower) { S.bbLower.setData(data.bb_lower||[]); S.bbLower.applyOptions({ visible: !!toggles.bb }); } } catch(e) {}
        /* Volume (pane 0 overlay) */
        try {
            if (S.volume) {
                S.volume.setData(data.volume||[]);
                S.volume.applyOptions({ visible: !!toggles.volume });
                chart.priceScale('vol').applyOptions({ visible: !!toggles.volume });
            }
        } catch(e) {}

        try {
            if (wm) wm.applyOptions({ lines:[{
                text:(data.pair||'')+'   '+(data.tf||''), color:WM_COLOR, fontSize:WM_SIZE, fontFamily:WM_FONT
            }] });
        } catch(e) {}

        if (data.candlestick && data.candlestick.length > 0) {
            _candleData = data.candlestick;
            window._apexLastPrice = data.candlestick[data.candlestick.length-1].close;
        }

        /* ── All candle markers (open + closed trades) — built together then applied once ── */
        var existingMarkers = [];

        /* ── Open broker/paper trades — Entry arrow + SL/TP price lines ── */
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
                    title: (isBuy ? '▲ BUY ' : '▼ SELL ') + (t.id || ''),
                }));
                if (t.sl) {
                    _tradePriceLines.push(S.candle.createPriceLine({
                        price: t.sl, color: '#ff4444', lineWidth: 1,
                        lineStyle: LC().LineStyle.Dashed,
                        axisLabelVisible: true, title: '— SL',
                    }));
                }
                if (t.tp1) {
                    _tradePriceLines.push(S.candle.createPriceLine({
                        price: t.tp1, color: '#00ff88', lineWidth: 1,
                        lineStyle: LC().LineStyle.Dashed,
                        axisLabelVisible: true, title: '✓ TP1',
                    }));
                }
                if (t.tp2) {
                    _tradePriceLines.push(S.candle.createPriceLine({
                        price: t.tp2, color: '#00cc66', lineWidth: 1,
                        lineStyle: LC().LineStyle.Dashed,
                        axisLabelVisible: true, title: '✓ TP2',
                    }));
                }
                if (t.tp3) {
                    _tradePriceLines.push(S.candle.createPriceLine({
                        price: t.tp3, color: '#009944', lineWidth: 1,
                        lineStyle: LC().LineStyle.Dashed,
                        axisLabelVisible: true, title: '✓ TP3',
                    }));
                }
                /* Blue entry arrow on the candle where this open trade was entered */
                if (t.open_time) {
                    existingMarkers.push({
                        time:     t.open_time,
                        position: isBuy ? 'belowBar' : 'aboveBar',
                        color:    '#2962ff',
                        shape:    isBuy ? 'arrowUp' : 'arrowDown',
                        text:     (isBuy ? '▲ BUY' : '▼ SELL') + ' ' + t.entry.toFixed(5),
                        size:     1,
                    });
                }
            } catch(ex) {}
        });

        /* ── Historical closed trade markers — entry/exit arrows on candles ── */
        try {
            var closedTrades = data.closed_trades || [];
            closedTrades.forEach(function(ct) {
                var isBuy  = ct.direction === 'long';
                var pnlStr = (ct.realised_pnl >= 0 ? '+' : '') + ct.realised_pnl.toFixed(2);
                /* Entry marker — Blue arrow */
                if (ct.open_time) {
                    existingMarkers.push({
                        time:     ct.open_time,
                        position: isBuy ? 'belowBar' : 'aboveBar',
                        color:    '#2962ff',
                        shape:    isBuy ? 'arrowUp' : 'arrowDown',
                        text:     (isBuy ? '▲ BUY' : '▼ SELL') + ' ' + ct.entry.toFixed(5),
                        size:     1,
                    });
                }
                /* Exit marker — Green for long exits, Red for short exits */
                if (ct.close_time) {
                    existingMarkers.push({
                        time:     ct.close_time,
                        position: isBuy ? 'aboveBar' : 'belowBar',
                        color:    isBuy ? '#00ff88' : '#ff3366',
                        shape:    isBuy ? 'arrowDown' : 'arrowUp',
                        text:     (isBuy ? '✓ EXIT' : '✓ COVER') + ' ' + ct.exit_price.toFixed(5) + ' (' + pnlStr + ')',
                        size:     1,
                    });
                }
            });
            /* TWLC v5 requires markers sorted by time; use plugin not series.setMarkers */
            existingMarkers.sort(function(a, b){ return a.time - b.time; });
            if (S.markers) { S.markers.setMarkers(existingMarkers); }
        } catch(ex) {}

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

        _rebuildDetachedOverlays();
        requestAnimationFrame(function(){ updateAllTrendlines(); updateAllPos(); updateAllBoxes(); updateAllFibs(); updateAllHLines(); updateAllCircles(); _updateAllTradeSvgLines(); updateAllTexts(); });

        /* Apply saved indicator visual settings after data loads */
        try { window._apexApplyIndSettings(data.ind_params || null); } catch(e) {}

        if (fitIt) {
            /* Try to restore a previously-saved zoom for this pair+TF */
            var zoomKey     = 'apex_zoom_' + (_currentPair||'') + '_' + (_currentTf||'');
            var savedRange  = null;
            try {
                var zRaw = localStorage.getItem(zoomKey);
                if (zRaw) savedRange = JSON.parse(zRaw);
            } catch(e) {}

            if (savedRange && savedRange.from && savedRange.to) {
                /* Restore saved zoom */
                try { chart.timeScale().setVisibleRange(savedRange); } catch(e) {
                    try { chart.timeScale().fitContent(); } catch(e2) {}
                }
            } else if (_candleData.length > 0) {
                /* Default: show last ~240 bars (≈ 10 days H1 / 60 days 4H / 240 days D) */
                var interval = _candleInterval();
                var nShow    = Math.min(_candleData.length, 240);
                var firstVis = _candleData[Math.max(0, _candleData.length - nShow)];
                var lastBar  = _candleData[_candleData.length - 1];
                try {
                    chart.timeScale().setVisibleRange({
                        from: firstVis.time - interval * 2,
                        to:   lastBar.time  + interval * 10,
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

    /* ── Trade SVG drag lines (SL / TP) ─────────────────────────────────── */

    function _clearTradeSvgLines() {
        _tradeSvgLines.forEach(function(tsl) {
            if (tsl.g && tsl.g.parentNode) tsl.g.parentNode.removeChild(tsl.g);
        });
        _tradeSvgLines = [];
    }

    function _buildTradeSvgLine(tradeId, lineType, price, color, title) {
        var svg = ensureTrendOverlay(); if (!svg) return null;
        var g   = document.createElementNS('http://www.w3.org/2000/svg', 'g');

        var hitEl = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        hitEl.setAttribute('stroke', 'transparent');
        hitEl.setAttribute('stroke-width', '14');
        hitEl.setAttribute('pointer-events', 'stroke');
        hitEl.setAttribute('cursor', 'ns-resize');

        var lineEl = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        lineEl.setAttribute('stroke', color);
        lineEl.setAttribute('stroke-width', '1.5');
        lineEl.setAttribute('stroke-dasharray', '8,5');
        lineEl.setAttribute('pointer-events', 'none');

        var lblEl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        lblEl.setAttribute('font-size', '10');
        lblEl.setAttribute('font-family', 'monospace');
        lblEl.setAttribute('font-weight', '600');
        lblEl.setAttribute('fill', color);
        lblEl.setAttribute('dominant-baseline', 'central');
        lblEl.setAttribute('text-anchor', 'end');
        lblEl.setAttribute('pointer-events', 'none');

        g.appendChild(hitEl); g.appendChild(lineEl); g.appendChild(lblEl);
        svg.appendChild(g);

        var tsl = { tradeId:tradeId, lineType:lineType, price:price,
                    color:color, title:title, g:g, hitEl:hitEl, lineEl:lineEl, lblEl:lblEl };

        hitEl.addEventListener('mousedown', function(e) {
            if (drawMode) return;
            e.stopPropagation(); e.preventDefault();
            /* Snapshot all current prices for this trade so we can write all fields on mouseup */
            var snap = null;
            _liveTradeData.forEach(function(t) {
                if (String(t.id) === String(tradeId)) snap = JSON.parse(JSON.stringify(t));
            });
            if (!snap) return;
            tradeDrag = { tsl:tsl, snapshot:snap, startPrice:tsl.price };
            try { chart.applyOptions({ handleScroll:false, handleScale:false }); } catch(ex) {}
        });
        hitEl.addEventListener('mousemove', function(e) {
            if (tradeDrag) return;
            hitEl.setAttribute('pointer-events', 'none');
            var below = document.elementFromPoint(e.clientX, e.clientY);
            hitEl.setAttribute('pointer-events', 'stroke');
            if (below && below !== hitEl) {
                below.dispatchEvent(new MouseEvent('mousemove', {
                    bubbles:true, cancelable:true,
                    clientX:e.clientX, clientY:e.clientY, view:window
                }));
            }
        });

        return tsl;
    }

    function _updateTradeSvgLine(tsl) {
        if (!tsl.g || !chart || !S.candle) return;
        var chartEl2 = document.getElementById('tvlw-chart');
        var maxX = chartEl2 ? (chartEl2.clientWidth - psWidth()) : 800;
        var y = _priceToY(tsl.price);
        if (y == null) { tsl.g.setAttribute('display', 'none'); return; }
        tsl.g.setAttribute('display', '');
        tsl.hitEl.setAttribute('x1', '0');  tsl.hitEl.setAttribute('y1', String(y));
        tsl.hitEl.setAttribute('x2', String(maxX)); tsl.hitEl.setAttribute('y2', String(y));
        tsl.lineEl.setAttribute('x1', '0');  tsl.lineEl.setAttribute('y1', String(y));
        tsl.lineEl.setAttribute('x2', String(maxX)); tsl.lineEl.setAttribute('y2', String(y));
        tsl.lblEl.setAttribute('x', String(maxX - 3));
        tsl.lblEl.setAttribute('y', String(y - 8));
        tsl.lblEl.textContent = tsl.title + ' ' + fmtPrice(tsl.price);
    }

    function _updateAllTradeSvgLines() { _tradeSvgLines.forEach(_updateTradeSvgLine); }

    /* Live trade price-line refresh — called by clientside callback on trade change */
    window._apexUpdateTrades = function(trades) {
        if (!chart || !S.candle) return;
        /* Remove old TWLC entry price lines */
        _tradePriceLines.forEach(function(pl){ try { S.candle.removePriceLine(pl); } catch(e){} });
        _tradePriceLines = [];
        /* Remove old SVG SL/TP lines */
        _clearTradeSvgLines();
        _liveTradeData = (trades || []).slice();

        (trades || []).forEach(function(t) {
            try {
                var isBuy = t.direction === 'long';
                var col   = isBuy ? '#00ff88' : '#ff3366';
                /* Entry: keep as TWLC price line (shows trade ID in price scale label) */
                _tradePriceLines.push(S.candle.createPriceLine({
                    price: t.entry, color: col, lineWidth: 2,
                    lineStyle: LC().LineStyle.Solid,
                    axisLabelVisible: true,
                    title: (isBuy ? '▲ BUY ' : '▼ SELL ') + (t.id || ''),
                }));
                /* SL / TP: SVG overlay so they are draggable */
                if (t.sl) {
                    var tsl = _buildTradeSvgLine(t.id, 'sl',  t.sl,  '#ff4444', '— SL');
                    if (tsl) { _updateTradeSvgLine(tsl); _tradeSvgLines.push(tsl); }
                }
                if (t.tp1) {
                    var tsl1 = _buildTradeSvgLine(t.id, 'tp1', t.tp1, '#00ff88', '✓ TP1');
                    if (tsl1) { _updateTradeSvgLine(tsl1); _tradeSvgLines.push(tsl1); }
                }
                if (t.tp2) {
                    var tsl2 = _buildTradeSvgLine(t.id, 'tp2', t.tp2, '#00cc66', '✓ TP2');
                    if (tsl2) { _updateTradeSvgLine(tsl2); _tradeSvgLines.push(tsl2); }
                }
                if (t.tp3) {
                    var tsl3 = _buildTradeSvgLine(t.id, 'tp3', t.tp3, '#009944', '✓ TP3');
                    if (tsl3) { _updateTradeSvgLine(tsl3); _tradeSvgLines.push(tsl3); }
                }
            } catch(ex) {}
        });
    };

    window._apexSetDrawMode = function(mode) {
        drawMode = mode || null;
        trendP1 = null;
        fibDraw = null;
        fibDrag = null;
        if (circleDraw) { _clearCirclePreview(); circleDraw = null; }
        _hideTrendPreview();
        _hidePositionForm();
        _hideDrawingSettings();
        if (!drawMode) { deselectDrawing(); }
        var el = document.getElementById('tvlw-chart');
        if (el) {
            el.style.cursor = drawMode === 'text' ? 'text' : (drawMode ? 'crosshair' : 'default');
            el.classList.toggle('apex-draw-active', !!drawMode);
        }
        /* Inject once: while a new drawing is being placed, all existing drawing
           hit-areas become transparent so mouse events reach the canvas cleanly. */
        if (!document.getElementById('apex-draw-mode-style')) {
            var s = document.createElement('style');
            s.id = 'apex-draw-mode-style';
            s.textContent =
                '#tvlw-chart.apex-draw-active .apex-box-drag,' +
                '#tvlw-chart.apex-draw-active .apex-box-drag > *,' +
                '#tvlw-chart.apex-draw-active .apex-box-handle,' +
                '#tvlw-chart.apex-draw-active .apex-box { cursor:crosshair !important; }' +
                '#tvlw-chart.apex-draw-active .apex-box-drag,' +
                '#tvlw-chart.apex-draw-active .apex-box-handle { pointer-events:none !important; }';
            document.head.appendChild(s);
        }
    };

    window._apexSetDrawColor = function(c) { drawColor = c || '#ffd700'; };
    window._apexSetDrawWidth = function(w) { drawWidth = parseInt(w,10) || 1; };
    window._apexSetDrawStyle = function(s) { drawStyle = s || 'solid'; };

    /* ── MA panel outside-click: close when clicking away ───────────────── */
    document.addEventListener('mousedown', function(e) {
        var panel = document.getElementById('ma-settings-panel');
        var btn   = document.getElementById('ma-settings-btn');
        if (!panel || panel.style.display === 'none') return;
        if ((panel.contains && panel.contains(e.target)) ||
            (btn   && btn.contains   && btn.contains(e.target))) return;
        try {
            window.dash_clientside.set_props('ma-outside-click', { data: Date.now() });
        } catch(ex) {}
    });

    window._apexDeleteSelected = function() {
        if (selectedTrend >= 0) {
            delTrendline(selectedTrend);
            return true;
        }
        if (selectedBox >= 0) {
            delBox(selectedBox);
            return true;
        }
        if (selectedFib >= 0) {
            delFib(selectedFib);
            return true;
        }
        if (selectedPos >= 0) {
            delPosition(selectedPos);
            return true;
        }
        if (selectedCircle >= 0) {
            delCircle(selectedCircle);
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
        if (selectedBox >= 0) {
            var b = boxes[selectedBox];
            if (b) { b.locked = !b.locked; updateBox(b); saveBoxes(); return true; }
        }
        if (selectedFib >= 0) {
            var f = fibs[selectedFib];
            if (f) { f.locked = !f.locked; updateFib(f); saveFibs(); return true; }
        }
        if (selectedPos >= 0) {
            var p = positions[selectedPos];
            if (p) { p.locked = !p.locked; updatePos(p); savePos(); return true; }
        }
        if (selectedCircle >= 0) {
            var c = circles[selectedCircle];
            if (c) { c.locked = !c.locked; updateCircle(c); saveCircles(); return true; }
        }
        if (selectedIdx >= 0) {
            var d = drawings[selectedIdx];
            if (d) { d.locked = !d.locked; if (d.type === 'h-line') updateHLine(d); saveDrawings(); return true; }
        }
        if (selectedText >= 0) {
            var tx = textDrawings[selectedText];
            if (tx) { tx.locked = !tx.locked; updateText(tx); saveTexts(); return true; }
        }
        return false;
    };

    window._apexDuplicateSelected = function() {
        if (selectedTrend >= 0)   { _dupTrendline(selectedTrend); return true; }
        if (selectedBox >= 0)     { _dupBox(selectedBox); return true; }
        if (selectedCircle >= 0)  { _dupCircle(selectedCircle); return true; }
        if (selectedIdx >= 0)     { _dupHLine(selectedIdx); return true; }
        if (selectedText >= 0)    { _dupText(selectedText); return true; }
        return false;
    };

    window._apexClearDrawings = function() {
        deselectDrawing();
        drawings.forEach(_removeOne); drawings = [];
        saveDrawings();

        trendlines.forEach(function(t){ if (t.el&&t.el.parentNode) t.el.parentNode.removeChild(t.el); });
        trendlines = []; selectedTrend = -1; saveTrendlines();

        positions.forEach(function(p){ if (p.el&&p.el.parentNode) p.el.parentNode.removeChild(p.el); });
        positions = []; selectedPos = -1; savePos();

        boxes.forEach(function(b){ if (b.el&&b.el.parentNode) b.el.parentNode.removeChild(b.el); });
        boxes = []; selectedBox = -1; saveBoxes();

        fibs.forEach(function(f){ if (f.el&&f.el.parentNode) f.el.parentNode.removeChild(f.el); });
        fibs = []; selectedFib = -1; saveFibs();

        circles.forEach(function(c){ if (c.el&&c.el.parentNode) c.el.parentNode.removeChild(c.el); });
        circles = []; selectedCircle = -1; saveCircles();

        textDrawings.forEach(function(t){ if (t.el&&t.el.parentNode) t.el.parentNode.removeChild(t.el); });
        textDrawings = []; selectedText = -1; saveTexts();
    };

    /* Re-attach any SVG/div overlay elements that were orphaned by a Dash DOM update.
       Called before every updateAll* pass so drawings reappear without a full reload. */
    function _rebuildDetachedOverlays() {
        /* Trendlines + circles share the same SVG overlay */
        if (trendlines.length > 0 || circles.length > 0) {
            var svg = ensureTrendOverlay();
            if (svg) {
                trendlines.forEach(function(t) {
                    if (!t.el || !t.el.parentNode) { buildTrendEl(t); }
                });
                circles.forEach(function(c) {
                    if (!c.el || !c.el.parentNode) { buildCircleEl(c); }
                });
            }
        }
        /* Boxes */
        if (boxes.length > 0) {
            var bov = ensureBoxOverlay();
            if (bov) {
                boxes.forEach(function(b) {
                    if (!b.el || !b.el.parentNode) { buildBoxEl(b); bov.appendChild(b.el); }
                });
            }
        }
        /* Positions */
        if (positions.length > 0) {
            var pov = ensurePosOverlay();
            if (pov) {
                positions.forEach(function(p) {
                    if (!p.el || !p.el.parentNode) { buildPosEl(p); pov.appendChild(p.el); }
                });
            }
        }
        /* Fibs */
        if (fibs.length > 0) {
            var fov = ensureFibOverlay();
            if (fov) {
                fibs.forEach(function(f) {
                    if (!f.el || !f.el.parentNode) { buildFibEl(f); }
                });
            }
        }
        /* Text labels */
        if (textDrawings.length > 0) {
            var tov = ensureTextOverlay();
            if (tov) {
                textDrawings.forEach(function(t) {
                    if (!t.el || !t.el.parentNode) { buildTextEl(t); }
                });
            }
        }
    }

    window._apexUpdateChart = function(data) {
        if (!data) return;
        var newPair  = data.pair   || '';
        var newTf    = data.tf     || '';
        var newRawTf = data.raw_tf || data.tf || '';
        var pairTf   = newPair + '|' + newTf;
        if (data.accountBalance) window._apexAccountBalance = data.accountBalance;

        if (chart && _lastPairTf !== null && pairTf !== _lastPairTf) {
            /* Save using the OLD _currentPair key before switching — prevents drawings
               from the previous pair contaminating the new pair's localStorage slot. */
            saveDrawings(); saveTrendlines(); saveBoxes(); savePos(); saveFibs(); saveCircles(); saveTexts();
            destroy();
        }

        /* Update state AFTER the conditional save so saves always use the correct old key. */
        _currentPair  = newPair;
        _currentTf    = newTf;
        _currentRawTf = newRawTf;

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

    /* ═══════════════════════════════════════════════════════════════════════
       INDICATOR SETTINGS  (CCI / MACD)
       ─────────────────────────────────────────────────────────────────────
       Architecture:
         • All settings (visual + computational) live in localStorage per pair.
         • Visual changes (color/width/style/visibility) are applied immediately
           via series.applyOptions() — no server round-trip.
         • Computational changes (CCI length/source, MACD fast/slow/signal) are
           also written to the Dash `ind-comp-store` via set_props, triggering
           a chart-data rebuild.
         • Profiles are stored in `apex_ind_profiles`.
       ═══════════════════════════════════════════════════════════════════════ */

    /* ── Default settings (matches TV CCI & MACD script defaults) ────────── */
    var _IND_DEFAULTS = {
        cci: {
            length: 20, source: 'hlc3',
            color: '#2962ff', width: 1.5, lineStyle: 0, opacity: 1, visible: true,
            upperLevel: 100, lowerLevel: -100,
            upperColor: '#787b86', lowerColor: '#787b86', midColor: '#787b86',
            upperStyle: 2, lowerStyle: 2, midStyle: 2,
            showUpper: true, showLower: true, showMid: true,
            maType: 'SMA', maLength: 14, maColor: '#ffd700', maVisible: true,
            bbMult: 2.0, bbVisible: false,
        },
        macd: {
            fast: 12, slow: 26, signal: 9,
            macdColor: '#38b6ff', signalColor: '#ff6d00',
            histColorUp: '#26a69a', histColorDown: '#ff5252',
            macdWidth: 1.5, signalWidth: 1.5,
            macdStyle: 0, signalStyle: 0,
            histOpacity: 1, visible: true,
            showMacd: true, showSignal: true, showHist: true,
        },
        rsi: {
            period: 14, visible: false,
            color: '#aa00ff', width: 1.5,
            obLevel: 70, osLevel: 30,
            obColor: '#787b86', osColor: '#787b86',
        },
        bb: {
            period: 20, mult: 2.0, visible: false,
            upperColor: '#38b6ff', basisColor: '#ffd700', lowerColor: '#38b6ff',
            width: 1,
        },
        volume: { visible: false },
        stoch: {
            k: 14, d: 3, smooth: 3, visible: false,
            kColor: '#2962ff', dColor: '#ff6d00',
            width: 1.5, obLevel: 80, osLevel: 20,
        },
    };

    /* ── Per-pair settings cache ──────────────────────────────────────────── */
    var _indSettings = null;   // settings for current pair (live reference)

    function _indKey() {
        /* Pair-only key — settings are shared across all timeframes for a pair */
        return 'apex_ind_' + (_currentPair || 'default');
    }

    function _loadIndSettings() {
        try {
            var raw = localStorage.getItem(_indKey());
            if (raw) {
                var saved = JSON.parse(raw);
                /* Deep-merge with defaults so new fields added later are populated */
                _indSettings = {
                    cci:    Object.assign({}, _IND_DEFAULTS.cci,    saved.cci    || {}),
                    macd:   Object.assign({}, _IND_DEFAULTS.macd,   saved.macd   || {}),
                    rsi:    Object.assign({}, _IND_DEFAULTS.rsi,    saved.rsi    || {}),
                    bb:     Object.assign({}, _IND_DEFAULTS.bb,     saved.bb     || {}),
                    volume: Object.assign({}, _IND_DEFAULTS.volume, saved.volume || {}),
                    stoch:  Object.assign({}, _IND_DEFAULTS.stoch,  saved.stoch  || {}),
                };
            } else {
                _indSettings = JSON.parse(JSON.stringify(_IND_DEFAULTS));
            }
        } catch(e) {
            _indSettings = JSON.parse(JSON.stringify(_IND_DEFAULTS));
        }
    }

    function _saveIndSettings() {
        if (!_indSettings) return;
        try {
            localStorage.setItem(_indKey(), JSON.stringify(_indSettings));
        } catch(e) {}
    }

    /* Push computational params to Dash so Python rebuilds indicator data */
    function _pushIndCompParams() {
        if (!_indSettings || !_currentPair || !(_currentRawTf || _currentTf)) return;
        try {
            /* Key matches Python's pair-only key (shared across all TFs) */
            var key = _currentPair;
            var all = {};
            try {
                var raw = localStorage.getItem('ind-comp-store');
                if (raw) all = JSON.parse(raw);
            } catch(ee) {}
            var rsi = _indSettings.rsi || {};
            var bb  = _indSettings.bb  || {};
            var vol = _indSettings.volume || {};
            var st  = _indSettings.stoch || {};
            all[key] = {
                cci_length:   _indSettings.cci.length,
                cci_src:      _indSettings.cci.source,
                macd_fast:    _indSettings.macd.fast,
                macd_slow:    _indSettings.macd.slow,
                macd_signal:  _indSettings.macd.signal,
                show_rsi:     !!(rsi.visible),
                rsi_period:   rsi.period || 14,
                show_bb:      !!(bb.visible),
                bb_period:    bb.period  || 20,
                bb_mult:      bb.mult    || 2.0,
                show_volume:  !!(vol.visible),
                show_stoch:   !!(st.visible),
                stoch_k:      st.k      || 14,
                stoch_d:      st.d      || 3,
                stoch_smooth: st.smooth || 3,
            };
            if (window.dash_clientside && window.dash_clientside.set_props) {
                window.dash_clientside.set_props('ind-comp-store', { data: all });
            }
        } catch(e) {}
    }

    /* Keep the chart container pinned to TOTAL_H.
       CCI/MACD panes collapse automatically when their series are removed —
       LWCHARTS redistributes the freed height to pane[0] without any setHeight() calls. */
    function _resizePanes() {
        if (!chart) return;
        var el = document.getElementById('tvlw-chart');
        if (el) { el.style.height = TOTAL_H + 'px'; el.style.overflow = 'hidden'; }
    }

    /* Apply visual settings to live chart series (no reload needed) */
    function _applyIndVisualSettings() {
        if (!_indSettings || !chart) return;
        var c = _indSettings.cci,  m = _indSettings.macd;
        var LC_ = LC(); if (!LC_) return;

        /* CCI — create pane when shown, remove series (collapses pane) when hidden.
           Use chart.panes().length as the target so LWCHARTS always gets a sequential index
           (avoids pane-splitting when intermediate panes don't exist). */
        if (c.visible && !S.cci && _lastChartData) {
            try {
                var _cciPane = chart.panes().length;
                S.cci = chart.addSeries(LC_.BaselineSeries, {
                    baseValue: { type: 'price', price: 0 },
                    topLineColor: '#00ff88', topFillColor1: 'rgba(0,255,136,0.12)',
                    topFillColor2: 'rgba(0,255,136,0.02)',
                    bottomLineColor: '#ff3366', bottomFillColor1: 'rgba(255,51,102,0.12)',
                    bottomFillColor2: 'rgba(255,51,102,0.02)',
                    lineWidth: 1.5,
                    lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
                }, _cciPane);
                _cciPane = chart.panes().length - 1;  /* actual index after creation */
                S.cciMa = chart.addSeries(LC_.LineSeries, {
                    color: '#ffd700', lineWidth: 1.5,
                    lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
                    title: 'CCI MA',
                }, _cciPane);
                S.cciBbUpper = chart.addSeries(LC_.LineSeries, {
                    color: '#00cc66', lineWidth: 1, lineStyle: 2,
                    lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
                    title: 'BB+',
                }, _cciPane);
                S.cciBbLower = chart.addSeries(LC_.LineSeries, {
                    color: '#00cc66', lineWidth: 1, lineStyle: 2,
                    lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
                    title: 'BB-',
                }, _cciPane);
                S.cci.setData(_lastChartData.cci || []);
                if (S.cciMa)      S.cciMa.setData(_lastChartData.cci_ma || []);
                if (S.cciBbUpper) S.cciBbUpper.setData(_lastChartData.cci_bb_upper || []);
                if (S.cciBbLower) S.cciBbLower.setData(_lastChartData.cci_bb_lower || []);
            } catch(e) {}
        } else if (!c.visible && S.cci) {
            try { chart.removeSeries(S.cci); }       catch(e) {}
            try { if (S.cciMa)      chart.removeSeries(S.cciMa); }      catch(e) {}
            try { if (S.cciBbUpper) chart.removeSeries(S.cciBbUpper); } catch(e) {}
            try { if (S.cciBbLower) chart.removeSeries(S.cciBbLower); } catch(e) {}
            S.cci = S.cciMa = S.cciBbUpper = S.cciBbLower = null;
        }
        /* CCI visual options — only when series exists */
        try {
            if (S.cci) {
                S.cci.applyOptions({
                    topLineColor:     c.color,
                    bottomLineColor:  c.color,
                    topFillColor1:    _hexToRgba(c.color, 0.12 * c.opacity),
                    topFillColor2:    _hexToRgba(c.color, 0.02 * c.opacity),
                    bottomFillColor1: _hexToRgba(c.color, 0.12 * c.opacity),
                    bottomFillColor2: _hexToRgba(c.color, 0.02 * c.opacity),
                    lineWidth: c.width,
                    lineStyle: c.lineStyle,
                });
            }
        } catch(e) {}
        try {
            if (S.cciMa) S.cciMa.applyOptions({ visible: !!c.maVisible, color: c.maColor, lineWidth: c.width });
        } catch(e) {}
        try {
            if (S.cciBbUpper) S.cciBbUpper.applyOptions({ visible: !!c.bbVisible, color: c.maColor });
            if (S.cciBbLower) S.cciBbLower.applyOptions({ visible: !!c.bbVisible, color: c.maColor });
        } catch(e) {}
        /* CCI reference lines */
        try {
            if (S.cci && S.cci._priceLinesCreatedByPanel) {
                S.cci._priceLinesCreatedByPanel.forEach(function(pl){ try { S.cci.removePriceLine(pl); } catch(e){} });
            }
            if (S.cci) {
                S.cci._priceLinesCreatedByPanel = [];
                function _cciLvl(price, col, style, show) {
                    if (!show || !S.cci) return;
                    try {
                        var pl = S.cci.createPriceLine({ price: price, color: col, lineWidth: 1,
                            lineStyle: style, axisLabelVisible: true, axisLabelColor: col });
                        S.cci._priceLinesCreatedByPanel.push(pl);
                    } catch(e) {}
                }
                _cciLvl(c.upperLevel, c.upperColor, c.upperStyle, c.showUpper);
                _cciLvl(0,            c.midColor,   c.midStyle,   c.showMid);
                _cciLvl(c.lowerLevel, c.lowerColor, c.lowerStyle, c.showLower);
            }
        } catch(e) {}

        /* MACD — create pane when shown, remove series (collapses pane) when hidden.
           All three series must share the same pane — use panes().length to get the
           next sequential index, then panes().length-1 for the co-located series. */
        if (m.visible && !S.macd && _lastChartData) {
            try {
                var _macdPane = chart.panes().length;
                S.macd = chart.addSeries(LC_.HistogramSeries, {
                    lastValueVisible: false, priceLineVisible: false,
                }, _macdPane);
                _macdPane = chart.panes().length - 1;  /* actual index after creation */
                S.macdLine = chart.addSeries(LC_.LineSeries, {
                    color: '#38b6ff', lineWidth: 1.5,
                    lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
                    title: 'MACD',
                }, _macdPane);
                S.macdSignal = chart.addSeries(LC_.LineSeries, {
                    color: '#ff6d00', lineWidth: 1.5,
                    lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
                    title: 'Signal',
                }, _macdPane);
                dotted(0, S.macd);  /* add price line after all series are placed */
                S.macd.setData(_lastChartData.macd || []);
                if (S.macdLine)   S.macdLine.setData(_lastChartData.macd_line || []);
                if (S.macdSignal) S.macdSignal.setData(_lastChartData.macd_signal || []);
            } catch(e) {}
        } else if (!m.visible && S.macd) {
            try { chart.removeSeries(S.macd); }         catch(e) {}
            try { if (S.macdLine)   chart.removeSeries(S.macdLine); }   catch(e) {}
            try { if (S.macdSignal) chart.removeSeries(S.macdSignal); } catch(e) {}
            S.macd = S.macdLine = S.macdSignal = null;
        }
        /* MACD visual options — only when series exists */
        try {
            if (S.macd) S.macd.applyOptions({ visible: !!(m.showHist) });
        } catch(e) {}
        try {
            if (S.macdLine) S.macdLine.applyOptions({
                visible: !!(m.showMacd),
                color: m.macdColor, lineWidth: m.macdWidth, lineStyle: m.macdStyle,
            });
        } catch(e) {}
        try {
            if (S.macdSignal) S.macdSignal.applyOptions({
                visible: !!(m.showSignal),
                color: m.signalColor, lineWidth: m.signalWidth, lineStyle: m.signalStyle,
            });
        } catch(e) {}

        /* RSI */
        var r = _indSettings.rsi || {};
        try {
            if (S.rsi) S.rsi.applyOptions({ visible: !!r.visible, color: r.color || '#aa00ff', lineWidth: r.width || 1.5 });
        } catch(e) {}

        /* Bollinger Bands */
        var b = _indSettings.bb || {};
        try {
            if (S.bbUpper) S.bbUpper.applyOptions({ visible: !!b.visible, color: b.upperColor || '#38b6ff', lineWidth: b.width || 1 });
            if (S.bbBasis) S.bbBasis.applyOptions({ visible: !!b.visible, color: b.basisColor || '#ffd700', lineWidth: b.width || 1 });
            if (S.bbLower) S.bbLower.applyOptions({ visible: !!b.visible, color: b.lowerColor || '#38b6ff', lineWidth: b.width || 1 });
        } catch(e) {}

        /* Volume */
        var vol = _indSettings.volume || {};
        try {
            if (S.volume) {
                S.volume.applyOptions({ visible: !!vol.visible });
                chart.priceScale('vol').applyOptions({ visible: !!vol.visible });
            }
        } catch(e) {}

        /* Stochastic */
        var st = _indSettings.stoch || {};
        try {
            if (S.stochK) S.stochK.applyOptions({ visible: !!st.visible, color: st.kColor || '#2962ff', lineWidth: st.width || 1.5 });
            if (S.stochD) S.stochD.applyOptions({ visible: !!st.visible, color: st.dColor || '#ff6d00', lineWidth: st.width || 1.5 });
        } catch(e) {}

        /* Collapse/expand CCI and MACD panes based on visibility */
        _resizePanes();
    }

    /* ── Settings panel DOM ───────────────────────────────────────────────── */
    var _indPanelEl = null;
    var _indActiveTab = 'cci';

    function _ensureIndPanel() {
        if (_indPanelEl && document.body.contains(_indPanelEl)) return _indPanelEl;
        var el = document.createElement('div');
        el.id = 'apex-ind-panel';
        el.innerHTML = '<div class="aip-header"><span class="aip-title">⚙ Indicator Settings</span><button class="aip-close" title="Close">✕</button></div>' +
            '<div class="aip-tabs">' +
            '<button class="aip-tab aip-active" data-tab="cci">CCI</button>' +
            '<button class="aip-tab" data-tab="macd">MACD</button>' +
            '<button class="aip-tab" data-tab="rsi">RSI</button>' +
            '<button class="aip-tab" data-tab="bb">BB</button>' +
            '<button class="aip-tab" data-tab="volume">Vol</button>' +
            '<button class="aip-tab" data-tab="stoch">Stoch</button>' +
            '<button class="aip-tab" data-tab="profiles">Profiles</button></div>' +
            '<div class="aip-body">' +
            '<div id="aip-cci-tab"     class="aip-tab-content"></div>' +
            '<div id="aip-macd-tab"    class="aip-tab-content" style="display:none"></div>' +
            '<div id="aip-rsi-tab"     class="aip-tab-content" style="display:none"></div>' +
            '<div id="aip-bb-tab"      class="aip-tab-content" style="display:none"></div>' +
            '<div id="aip-volume-tab"  class="aip-tab-content" style="display:none"></div>' +
            '<div id="aip-stoch-tab"   class="aip-tab-content" style="display:none"></div>' +
            '<div id="aip-profiles-tab" class="aip-tab-content" style="display:none"></div>' +
            '</div>';
        document.body.appendChild(el);
        _indPanelEl = el;

        /* Close button */
        el.querySelector('.aip-close').onclick = _hideIndPanel;

        /* Tab switching */
        el.querySelectorAll('.aip-tab').forEach(function(btn) {
            btn.onclick = function() {
                el.querySelectorAll('.aip-tab').forEach(function(b){ b.classList.remove('aip-active'); });
                btn.classList.add('aip-active');
                _indActiveTab = btn.dataset.tab;
                el.querySelectorAll('.aip-tab-content').forEach(function(c){ c.style.display = 'none'; });
                document.getElementById('aip-' + _indActiveTab + '-tab').style.display = 'block';
            };
        });

        /* Draggable panel */
        var drag = null;
        el.querySelector('.aip-header').addEventListener('mousedown', function(e) {
            if (e.target.classList.contains('aip-close')) return;
            drag = { x: e.clientX - el.offsetLeft, y: e.clientY - el.offsetTop };
            e.preventDefault();
        });
        document.addEventListener('mousemove', function(e) {
            if (!drag) return;
            el.style.left = (e.clientX - drag.x) + 'px';
            el.style.top  = (e.clientY - drag.y) + 'px';
        });
        document.addEventListener('mouseup', function() { drag = null; });

        return el;
    }

    function _closeIndOnOutsideClick(e) {
        var el = document.getElementById('apex-ind-panel');
        if (el && !el.contains(e.target)) {
            _hideIndPanel();
        }
    }

    function _showIndPanel() {
        _loadIndSettings();
        var el = _ensureIndPanel();
        _renderIndTabs();
        el.style.display = 'block';
        // Attach click-outside listener after a tick so the opening click doesn't close it
        setTimeout(function() {
            document.addEventListener('click', _closeIndOnOutsideClick);
        }, 0);
    }

    function _hideIndPanel() {
        if (_indPanelEl) _indPanelEl.style.display = 'none';
        document.removeEventListener('click', _closeIndOnOutsideClick);
    }

    /* ── Tab render helpers ───────────────────────────────────────────────── */
    var _SOURCES = ['close','open','high','low','hl2','hlc3','ohlc4'];
    var _MA_TYPES = ['None','SMA','EMA','SMMA (RMA)','WMA','SMA + Bollinger Bands'];
    var _LINE_STYLES = [{v:0,l:'Solid'},{v:1,l:'Dotted'},{v:2,l:'Dashed'},{v:3,l:'LargeDashed'},{v:4,l:'SparseDotted'}];
    var _WIDTHS = [1, 1.5, 2, 2.5, 3];
    var _SWATCH_COLORS = ['#2962ff','#00ff88','#ff3366','#ffd700','#ff9900','#38b6ff','#ff6d00','#26a69a','#ff5252','#ffffff','#8b949e'];

    function _row(label, content) {
        return '<div class="aip-row"><span class="aip-label">' + label + '</span><span class="aip-ctrl">' + content + '</span></div>';
    }

    function _select(id, options, value) {
        var html = '<select id="' + id + '" class="aip-select">';
        options.forEach(function(o) {
            var v = typeof o === 'object' ? o.v : o;
            var l = typeof o === 'object' ? o.l : o;
            html += '<option value="' + v + '"' + (String(v) === String(value) ? ' selected' : '') + '>' + l + '</option>';
        });
        return html + '</select>';
    }

    function _numInput(id, value, min, max, step) {
        return '<input type="number" id="' + id + '" class="aip-num" value="' + value +
               '" min="' + (min||1) + '" max="' + (max||999) + '" step="' + (step||1) + '">';
    }

    function _toggle(id, value, label) {
        return '<label class="aip-toggle"><input type="checkbox" id="' + id + '"' + (value ? ' checked' : '') + '>' +
               '<span class="aip-toggle-track"></span>' + (label ? '<span class="aip-toggle-lbl">' + label + '</span>' : '') + '</label>';
    }

    function _colorInput(id, value) {
        return '<input type="color" id="' + id + '" class="aip-color" value="' + (value||'#ffffff') + '">';
    }

    function _swatches(idPrefix, current) {
        var html = '<div class="aip-swatches">';
        _SWATCH_COLORS.forEach(function(c) {
            html += '<button class="aip-swatch' + (c === current ? ' aip-swatch-active' : '') +
                    '" data-id="' + idPrefix + '" data-color="' + c + '" style="background:' + c + '" title="' + c + '"></button>';
        });
        html += '<input type="color" class="aip-swatch-custom" data-id="' + idPrefix + '" value="' + (current||'#ffffff') + '" title="Custom color">';
        html += '</div>';
        return html;
    }

    function _renderCCITab() {
        var c = _indSettings.cci;
        var tab = document.getElementById('aip-cci-tab');
        tab.innerHTML =
            '<div class="aip-section">CCI Settings</div>' +
            _row('Length',      _numInput('aip-cci-length', c.length, 1, 500)) +
            _row('Source',      _select('aip-cci-source', _SOURCES, c.source)) +
            _row('Visible',     _toggle('aip-cci-visible', c.visible)) +

            '<div class="aip-section">Line Style</div>' +
            _row('Color',       _swatches('cci-color', c.color) + _colorInput('aip-cci-color', c.color)) +
            _row('Width',       _select('aip-cci-width', _WIDTHS.map(function(w){return {v:w,l:w+'px'};}), c.width)) +
            _row('Style',       _select('aip-cci-linestyle', _LINE_STYLES, c.lineStyle)) +
            _row('Opacity',     '<input type="range" id="aip-cci-opacity" min="0.1" max="1" step="0.05" value="' + c.opacity + '" class="aip-range"><span id="aip-cci-opacity-val">' + Math.round(c.opacity*100) + '%</span>') +

            '<div class="aip-section">Levels</div>' +
            _row('Upper Level', _numInput('aip-cci-upper', c.upperLevel, 0, 500) + _colorInput('aip-cci-upperclr', c.upperColor) + _select('aip-cci-upperstyle', _LINE_STYLES, c.upperStyle) + _toggle('aip-cci-showupper', c.showUpper)) +
            _row('Mid Line',    _colorInput('aip-cci-midclr', c.midColor)    + _select('aip-cci-midstyle',   _LINE_STYLES, c.midStyle)   + _toggle('aip-cci-showmid',   c.showMid)) +
            _row('Lower Level', _numInput('aip-cci-lower', c.lowerLevel, -500, 0) + _colorInput('aip-cci-lowerclr', c.lowerColor) + _select('aip-cci-lowerstyle', _LINE_STYLES, c.lowerStyle) + _toggle('aip-cci-showlower', c.showLower)) +

            '<div class="aip-section">Smoothing MA</div>' +
            _row('Type',        _select('aip-cci-matype', _MA_TYPES, c.maType)) +
            _row('MA Length',   _numInput('aip-cci-malength', c.maLength, 1, 200)) +
            _row('MA Color',    _swatches('cci-macolor', c.maColor) + _colorInput('aip-cci-macolor', c.maColor)) +
            _row('MA Visible',  _toggle('aip-cci-mavisible', c.maVisible)) +
            _row('BB Multiplier', _numInput('aip-cci-bbmult', c.bbMult, 0.1, 10, 0.1)) +
            _row('BB Visible',  _toggle('aip-cci-bbvisible', c.bbVisible));

        _bindCCIEvents(tab);
    }

    function _renderMACDTab() {
        var m = _indSettings.macd;
        var tab = document.getElementById('aip-macd-tab');
        tab.innerHTML =
            '<div class="aip-section">MACD Lengths</div>' +
            _row('Fast Length',   _numInput('aip-macd-fast', m.fast, 1, 200)) +
            _row('Slow Length',   _numInput('aip-macd-slow', m.slow, 1, 500)) +
            _row('Signal Length', _numInput('aip-macd-signal', m.signal, 1, 100)) +
            _row('Visible',       _toggle('aip-macd-visible', m.visible)) +

            '<div class="aip-section">MACD Line</div>' +
            _row('Color',   _swatches('macd-line-color', m.macdColor) + _colorInput('aip-macd-linecolor', m.macdColor)) +
            _row('Width',   _select('aip-macd-linewidth', _WIDTHS.map(function(w){return {v:w,l:w+'px'};}), m.macdWidth)) +
            _row('Style',   _select('aip-macd-linestyle', _LINE_STYLES, m.macdStyle)) +
            _row('Show',    _toggle('aip-macd-showmacd', m.showMacd)) +

            '<div class="aip-section">Signal Line</div>' +
            _row('Color',   _swatches('macd-sig-color', m.signalColor) + _colorInput('aip-macd-sigcolor', m.signalColor)) +
            _row('Width',   _select('aip-macd-sigwidth', _WIDTHS.map(function(w){return {v:w,l:w+'px'};}), m.signalWidth)) +
            _row('Style',   _select('aip-macd-sigstyle', _LINE_STYLES, m.signalStyle)) +
            _row('Show',    _toggle('aip-macd-showsig', m.showSignal)) +

            '<div class="aip-section">Histogram</div>' +
            _row('Bull Color',  _swatches('macd-hist-up',   m.histColorUp)   + _colorInput('aip-macd-histup',   m.histColorUp)) +
            _row('Bear Color',  _swatches('macd-hist-down', m.histColorDown) + _colorInput('aip-macd-histdown', m.histColorDown)) +
            _row('Opacity',     '<input type="range" id="aip-macd-histopacity" min="0.1" max="1" step="0.05" value="' + m.histOpacity + '" class="aip-range"><span id="aip-macd-opacity-val">' + Math.round(m.histOpacity*100) + '%</span>') +
            _row('Show',        _toggle('aip-macd-showhist', m.showHist));

        _bindMACDEvents(tab);
    }

    function _renderProfilesTab() {
        var tab = document.getElementById('aip-profiles-tab');
        var profiles = {};
        try { profiles = JSON.parse(localStorage.getItem('apex_ind_profiles') || '{}'); } catch(e) {}
        var names = Object.keys(profiles);

        var html = '<div class="aip-section">Profiles</div>' +
            '<div class="aip-profile-list">';
        if (names.length === 0) {
            html += '<span class="aip-hint">No saved profiles yet.</span>';
        } else {
            names.forEach(function(n) {
                html += '<div class="aip-profile-row">' +
                    '<span class="aip-profile-name">' + n + '</span>' +
                    '<button class="aip-btn-sm aip-btn-blue"  data-load="' + n + '">Load</button>' +
                    '<button class="aip-btn-sm aip-btn-red"   data-del="'  + n + '">Delete</button>' +
                    '</div>';
            });
        }
        html += '</div>' +
            '<div class="aip-section">Save Current Settings</div>' +
            '<div class="aip-row">' +
            '<input type="text" id="aip-profile-name" class="aip-num" style="width:120px" placeholder="Profile name">' +
            '<button class="aip-btn aip-btn-blue" id="aip-profile-save">Save</button>' +
            '</div>' +
            '<div class="aip-section">Built-in Templates</div>' +
            '<div class="aip-profile-list">' +
            ['Scalping','Swing Trading','Trend Following'].map(function(n){
                return '<button class="aip-btn-sm aip-btn-grey" data-template="' + n + '">' + n + '</button>';
            }).join('') +
            '</div>';

        tab.innerHTML = html;

        /* Bind profile events */
        var saveBtn = tab.querySelector('#aip-profile-save');
        if (saveBtn) saveBtn.onclick = function() {
            var nameEl = tab.querySelector('#aip-profile-name');
            var name = (nameEl ? nameEl.value.trim() : '') || 'Custom';
            var ps = {};
            try { ps = JSON.parse(localStorage.getItem('apex_ind_profiles') || '{}'); } catch(e) {}
            ps[name] = JSON.parse(JSON.stringify(_indSettings));
            localStorage.setItem('apex_ind_profiles', JSON.stringify(ps));
            _renderProfilesTab();
        };

        tab.querySelectorAll('[data-load]').forEach(function(btn) {
            btn.onclick = function() {
                var ps = {};
                try { ps = JSON.parse(localStorage.getItem('apex_ind_profiles') || '{}'); } catch(e) {}
                var p = ps[btn.dataset.load];
                if (!p) return;
                _indSettings = {
                    cci:  Object.assign({}, _IND_DEFAULTS.cci,  p.cci  || {}),
                    macd: Object.assign({}, _IND_DEFAULTS.macd, p.macd || {}),
                };
                _saveIndSettings();
                _applyIndVisualSettings();
                _pushIndCompParams();
                _renderIndTabs();
            };
        });

        tab.querySelectorAll('[data-del]').forEach(function(btn) {
            btn.onclick = function() {
                var ps = {};
                try { ps = JSON.parse(localStorage.getItem('apex_ind_profiles') || '{}'); } catch(e) {}
                delete ps[btn.dataset.del];
                localStorage.setItem('apex_ind_profiles', JSON.stringify(ps));
                _renderProfilesTab();
            };
        });

        /* Built-in templates */
        var _TEMPLATES = {
            'Scalping':       { cci: { length:  14, source: 'close' }, macd: { fast: 6,  slow: 13, signal: 5  } },
            'Swing Trading':  { cci: { length:  20, source: 'hlc3'  }, macd: { fast: 12, slow: 26, signal: 9  } },
            'Trend Following':{ cci: { length: 100, source: 'hlc3'  }, macd: { fast: 34, slow: 89, signal: 21 } },
        };
        tab.querySelectorAll('[data-template]').forEach(function(btn) {
            btn.onclick = function() {
                var t = _TEMPLATES[btn.dataset.template]; if (!t) return;
                _indSettings.cci  = Object.assign({}, _indSettings.cci,  t.cci  || {});
                _indSettings.macd = Object.assign({}, _indSettings.macd, t.macd || {});
                _saveIndSettings();
                _applyIndVisualSettings();
                _pushIndCompParams();

                /* If the user typed a profile name, also save under that name */
                var nameEl = tab.querySelector('#aip-profile-name');
                var customName = nameEl ? nameEl.value.trim() : '';
                if (customName) {
                    var ps = {};
                    try { ps = JSON.parse(localStorage.getItem('apex_ind_profiles') || '{}'); } catch(e) {}
                    ps[customName] = JSON.parse(JSON.stringify(_indSettings));
                    localStorage.setItem('apex_ind_profiles', JSON.stringify(ps));
                }

                _renderIndTabs();
            };
        });
    }

    function _renderIndTabs() {
        if (!_indSettings) _loadIndSettings();
        _renderCCITab();
        _renderMACDTab();
        _renderRSITab();
        _renderBBTab();
        _renderVolumeTab();
        _renderStochTab();
        _renderProfilesTab();
    }

    function _renderRSITab() {
        var r = _indSettings.rsi;
        var tab = document.getElementById('aip-rsi-tab');
        if (!tab) return;
        tab.innerHTML =
            '<div class="aip-section">RSI</div>' +
            _row('Show RSI',  _toggle('aip-rsi-visible', r.visible)) +
            _row('Period',    _numInput('aip-rsi-period', r.period, 2, 200)) +
            _row('OB Level',  _numInput('aip-rsi-ob', r.obLevel, 50, 100)) +
            _row('OS Level',  _numInput('aip-rsi-os', r.osLevel, 0, 50)) +
            _row('Color',     _colorInput('aip-rsi-color', r.color));
        tab.querySelector('#aip-rsi-visible').onchange  =
        tab.querySelector('#aip-rsi-period').oninput    =
        tab.querySelector('#aip-rsi-ob').oninput        =
        tab.querySelector('#aip-rsi-os').oninput        =
        tab.querySelector('#aip-rsi-color').oninput     = function() {
            r.visible  = tab.querySelector('#aip-rsi-visible').checked;
            r.period   = parseInt(tab.querySelector('#aip-rsi-period').value) || r.period;
            r.obLevel  = parseFloat(tab.querySelector('#aip-rsi-ob').value)   || r.obLevel;
            r.osLevel  = parseFloat(tab.querySelector('#aip-rsi-os').value)   || r.osLevel;
            r.color    = tab.querySelector('#aip-rsi-color').value;
            _saveIndSettings(); _pushIndCompParams();
        };
    }

    function _renderBBTab() {
        var b = _indSettings.bb;
        var tab = document.getElementById('aip-bb-tab');
        if (!tab) return;
        tab.innerHTML =
            '<div class="aip-section">Bollinger Bands</div>' +
            _row('Show BB',    _toggle('aip-bb-visible', b.visible)) +
            _row('Length',     _numInput('aip-bb-period', b.period, 2, 500)) +
            _row('Std Dev',    _numInput('aip-bb-mult', b.mult, 0.5, 10, 0.1)) +
            _row('Upper/Lower Color', _colorInput('aip-bb-upper-color', b.upperColor)) +
            _row('Basis Color',       _colorInput('aip-bb-basis-color', b.basisColor));
        tab.querySelector('#aip-bb-visible').onchange      =
        tab.querySelector('#aip-bb-period').oninput        =
        tab.querySelector('#aip-bb-mult').oninput          =
        tab.querySelector('#aip-bb-upper-color').oninput   =
        tab.querySelector('#aip-bb-basis-color').oninput   = function() {
            b.visible    = tab.querySelector('#aip-bb-visible').checked;
            b.period     = parseInt(tab.querySelector('#aip-bb-period').value) || b.period;
            b.mult       = parseFloat(tab.querySelector('#aip-bb-mult').value) || b.mult;
            b.upperColor = tab.querySelector('#aip-bb-upper-color').value;
            b.lowerColor = b.upperColor;
            b.basisColor = tab.querySelector('#aip-bb-basis-color').value;
            _saveIndSettings(); _pushIndCompParams();
        };
    }

    function _renderVolumeTab() {
        var v = _indSettings.volume;
        var tab = document.getElementById('aip-volume-tab');
        if (!tab) return;
        tab.innerHTML =
            '<div class="aip-section">Volume</div>' +
            _row('Show Volume', _toggle('aip-vol-visible', v.visible));
        tab.querySelector('#aip-vol-visible').onchange = function() {
            v.visible = tab.querySelector('#aip-vol-visible').checked;
            _saveIndSettings(); _pushIndCompParams();
        };
    }

    function _renderStochTab() {
        var s = _indSettings.stoch;
        var tab = document.getElementById('aip-stoch-tab');
        if (!tab) return;
        tab.innerHTML =
            '<div class="aip-section">Stochastic</div>' +
            _row('Show Stoch', _toggle('aip-stoch-visible', s.visible)) +
            _row('%K Period',  _numInput('aip-stoch-k', s.k, 1, 200)) +
            _row('%D Period',  _numInput('aip-stoch-d', s.d, 1, 50)) +
            _row('Smooth',     _numInput('aip-stoch-smooth', s.smooth, 1, 50)) +
            _row('OB Level',   _numInput('aip-stoch-ob', s.obLevel, 50, 100)) +
            _row('OS Level',   _numInput('aip-stoch-os', s.osLevel, 0, 50)) +
            _row('%K Color',   _colorInput('aip-stoch-k-color', s.kColor)) +
            _row('%D Color',   _colorInput('aip-stoch-d-color', s.dColor));
        var changed = function() {
            s.visible  = tab.querySelector('#aip-stoch-visible').checked;
            s.k        = parseInt(tab.querySelector('#aip-stoch-k').value)      || s.k;
            s.d        = parseInt(tab.querySelector('#aip-stoch-d').value)      || s.d;
            s.smooth   = parseInt(tab.querySelector('#aip-stoch-smooth').value) || s.smooth;
            s.obLevel  = parseFloat(tab.querySelector('#aip-stoch-ob').value)   || s.obLevel;
            s.osLevel  = parseFloat(tab.querySelector('#aip-stoch-os').value)   || s.osLevel;
            s.kColor   = tab.querySelector('#aip-stoch-k-color').value;
            s.dColor   = tab.querySelector('#aip-stoch-d-color').value;
            _saveIndSettings(); _pushIndCompParams();
        };
        ['#aip-stoch-visible','#aip-stoch-k','#aip-stoch-d','#aip-stoch-smooth',
         '#aip-stoch-ob','#aip-stoch-os','#aip-stoch-k-color','#aip-stoch-d-color'].forEach(function(sel) {
            var el = tab.querySelector(sel);
            if (el) { el.onchange = changed; el.oninput = changed; }
        });
    }

    /* ── Bind CCI events (auto-save on every change) ─────────────────────── */
    function _bindCCIEvents(tab) {
        /* Helper: read input, update _indSettings.cci, save, apply */
        function _cciChanged(isComp) {
            var c = _indSettings.cci;
            c.length    = parseInt(tab.querySelector('#aip-cci-length').value)   || c.length;
            c.source    = tab.querySelector('#aip-cci-source').value             || c.source;
            c.visible   = tab.querySelector('#aip-cci-visible').checked;
            c.color     = tab.querySelector('#aip-cci-color').value              || c.color;
            c.width     = parseFloat(tab.querySelector('#aip-cci-width').value)  || c.width;
            c.lineStyle = parseInt(tab.querySelector('#aip-cci-linestyle').value);
            c.opacity   = parseFloat(tab.querySelector('#aip-cci-opacity').value) || c.opacity;
            c.upperLevel= parseFloat(tab.querySelector('#aip-cci-upper').value)  || c.upperLevel;
            c.lowerLevel= parseFloat(tab.querySelector('#aip-cci-lower').value)  || c.lowerLevel;
            c.upperColor= tab.querySelector('#aip-cci-upperclr').value           || c.upperColor;
            c.lowerColor= tab.querySelector('#aip-cci-lowerclr').value           || c.lowerColor;
            c.midColor  = tab.querySelector('#aip-cci-midclr').value             || c.midColor;
            c.upperStyle= parseInt(tab.querySelector('#aip-cci-upperstyle').value);
            c.lowerStyle= parseInt(tab.querySelector('#aip-cci-lowerstyle').value);
            c.midStyle  = parseInt(tab.querySelector('#aip-cci-midstyle').value);
            c.showUpper = tab.querySelector('#aip-cci-showupper').checked;
            c.showLower = tab.querySelector('#aip-cci-showlower').checked;
            c.showMid   = tab.querySelector('#aip-cci-showmid').checked;
            c.maType    = tab.querySelector('#aip-cci-matype').value             || c.maType;
            c.maLength  = parseInt(tab.querySelector('#aip-cci-malength').value) || c.maLength;
            c.maColor   = tab.querySelector('#aip-cci-macolor').value            || c.maColor;
            c.maVisible = tab.querySelector('#aip-cci-mavisible').checked;
            c.bbMult    = parseFloat(tab.querySelector('#aip-cci-bbmult').value) || c.bbMult;
            c.bbVisible = tab.querySelector('#aip-cci-bbvisible').checked;

            /* Update opacity display */
            var opEl = tab.querySelector('#aip-cci-opacity-val');
            if (opEl) opEl.textContent = Math.round(c.opacity * 100) + '%';

            _saveIndSettings();
            _applyIndVisualSettings();
            if (isComp) _pushIndCompParams();
        }

        /* Computational: length/source → needs server rebuild */
        ['aip-cci-length','aip-cci-source','aip-cci-matype','aip-cci-malength','aip-cci-bbmult'].forEach(function(id) {
            var el = tab.querySelector('#' + id); if (!el) return;
            el.addEventListener('change', function(){ _cciChanged(true); });
        });

        /* Visual-only: no server rebuild needed */
        ['aip-cci-visible','aip-cci-color','aip-cci-width','aip-cci-linestyle','aip-cci-opacity',
         'aip-cci-upper','aip-cci-lower','aip-cci-upperclr','aip-cci-lowerclr','aip-cci-midclr',
         'aip-cci-upperstyle','aip-cci-lowerstyle','aip-cci-midstyle',
         'aip-cci-showupper','aip-cci-showlower','aip-cci-showmid',
         'aip-cci-macolor','aip-cci-mavisible','aip-cci-bbvisible'].forEach(function(id) {
            var el = tab.querySelector('#' + id); if (!el) return;
            el.addEventListener('change', function(){ _cciChanged(false); });
            el.addEventListener('input',  function(){ _cciChanged(false); });
        });

        /* Colour swatch clicks */
        tab.querySelectorAll('.aip-swatch[data-id^="cci-color"]').forEach(function(sw) {
            sw.onclick = function() {
                tab.querySelector('#aip-cci-color').value = sw.dataset.color;
                tab.querySelectorAll('.aip-swatch[data-id^="cci-color"]').forEach(function(s){ s.classList.remove('aip-swatch-active'); });
                sw.classList.add('aip-swatch-active');
                _cciChanged(false);
            };
        });
        tab.querySelectorAll('.aip-swatch[data-id="cci-macolor"]').forEach(function(sw) {
            sw.onclick = function() {
                tab.querySelector('#aip-cci-macolor').value = sw.dataset.color;
                _cciChanged(false);
            };
        });
    }

    /* ── Bind MACD events ─────────────────────────────────────────────────── */
    function _bindMACDEvents(tab) {
        function _macdChanged(isComp) {
            var m = _indSettings.macd;
            m.fast         = parseInt(tab.querySelector('#aip-macd-fast').value)       || m.fast;
            m.slow         = parseInt(tab.querySelector('#aip-macd-slow').value)       || m.slow;
            m.signal       = parseInt(tab.querySelector('#aip-macd-signal').value)     || m.signal;
            m.visible      = tab.querySelector('#aip-macd-visible').checked;
            m.macdColor    = tab.querySelector('#aip-macd-linecolor').value            || m.macdColor;
            m.macdWidth    = parseFloat(tab.querySelector('#aip-macd-linewidth').value)|| m.macdWidth;
            m.macdStyle    = parseInt(tab.querySelector('#aip-macd-linestyle').value);
            m.showMacd     = tab.querySelector('#aip-macd-showmacd').checked;
            m.signalColor  = tab.querySelector('#aip-macd-sigcolor').value             || m.signalColor;
            m.signalWidth  = parseFloat(tab.querySelector('#aip-macd-sigwidth').value) || m.signalWidth;
            m.signalStyle  = parseInt(tab.querySelector('#aip-macd-sigstyle').value);
            m.showSignal   = tab.querySelector('#aip-macd-showsig').checked;
            m.histColorUp  = tab.querySelector('#aip-macd-histup').value               || m.histColorUp;
            m.histColorDown= tab.querySelector('#aip-macd-histdown').value             || m.histColorDown;
            m.histOpacity  = parseFloat(tab.querySelector('#aip-macd-histopacity').value) || m.histOpacity;
            m.showHist     = tab.querySelector('#aip-macd-showhist').checked;

            var opEl = tab.querySelector('#aip-macd-opacity-val');
            if (opEl) opEl.textContent = Math.round(m.histOpacity * 100) + '%';

            _saveIndSettings();
            _applyIndVisualSettings();
            if (isComp) _pushIndCompParams();
        }

        /* Computational */
        ['aip-macd-fast','aip-macd-slow','aip-macd-signal'].forEach(function(id) {
            var el = tab.querySelector('#' + id); if (!el) return;
            el.addEventListener('change', function(){ _macdChanged(true); });
        });

        /* Visual */
        ['aip-macd-visible','aip-macd-linecolor','aip-macd-linewidth','aip-macd-linestyle','aip-macd-showmacd',
         'aip-macd-sigcolor','aip-macd-sigwidth','aip-macd-sigstyle','aip-macd-showsig',
         'aip-macd-histup','aip-macd-histdown','aip-macd-histopacity','aip-macd-showhist'].forEach(function(id) {
            var el = tab.querySelector('#' + id); if (!el) return;
            el.addEventListener('change', function(){ _macdChanged(false); });
            el.addEventListener('input',  function(){ _macdChanged(false); });
        });

        /* Colour swatches */
        [['macd-line-color','#aip-macd-linecolor'],['macd-sig-color','#aip-macd-sigcolor'],
         ['macd-hist-up','#aip-macd-histup'],['macd-hist-down','#aip-macd-histdown']].forEach(function(pair_) {
            var swId = pair_[0], inpSel = pair_[1];
            tab.querySelectorAll('.aip-swatch[data-id="' + swId + '"]').forEach(function(sw) {
                sw.onclick = function() {
                    var inp = tab.querySelector(inpSel); if (inp) inp.value = sw.dataset.color;
                    tab.querySelectorAll('.aip-swatch[data-id="' + swId + '"]').forEach(function(s){ s.classList.remove('aip-swatch-active'); });
                    sw.classList.add('aip-swatch-active');
                    _macdChanged(false);
                };
            });
        });
    }

    /* ── Expose show/hide to Dash button ─────────────────────────────────── */
    window._apexShowIndPanel = function() {
        _loadIndSettings();
        _showIndPanel();
    };
    window._apexHideIndPanel = _hideIndPanel;

    /* Called after chart data loads — applies saved visual settings */
    window._apexApplyIndSettings = function(indParams) {
        _loadIndSettings();
        /* If Python used different params (e.g. first load), reconcile */
        if (indParams) {
            if (indParams.cci_length) _indSettings.cci.length = indParams.cci_length;
            if (indParams.cci_src)    _indSettings.cci.source = indParams.cci_src;
            if (indParams.macd_fast)  _indSettings.macd.fast   = indParams.macd_fast;
            if (indParams.macd_slow)  _indSettings.macd.slow   = indParams.macd_slow;
            if (indParams.macd_signal) _indSettings.macd.signal = indParams.macd_signal;
        }
        _applyIndVisualSettings();
        /* Refresh panel if open */
        if (_indPanelEl && _indPanelEl.style.display !== 'none') _renderIndTabs();
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
