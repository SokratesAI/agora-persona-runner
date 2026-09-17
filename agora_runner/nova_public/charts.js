/* The costs and retrospective pages, and the whole ECharts layer under
 * them (issue #233, step 12).
 *
 * The fourth piece of `app.js` moved out whole, after `mermaid.js`,
 * `attach.js` and `chat-dock.js`. Two pages and one chart layer travel
 * together because nothing else on this site draws a chart: `ensureECharts`
 * lazily fetches the 1.0 MB vendored library on the first chart, `mountEChart`
 * keeps the live instances so a resize or a theme change can repaint them,
 * and `/costs` and `/retro` are the only two callers. Moving the pages and
 * leaving the layer behind would have left a chart engine in `app.js` with
 * no chart in it.
 *
 * What it needs from `app.js` arrives as one argument, the same seam
 * `chat-dock.js` uses and for the same reason: the list is something
 * somebody has to add to on purpose, and a name missing from it is a
 * `ReferenceError` on the first line that uses it rather than a page that
 * half draws. `app.js` calls this at the point in its own body where the
 * costs page used to be defined, and takes back the four names the rest of
 * the file still reaches -- `loadCosts` and `loadRetro` from the router,
 * `closeFullChart` from the navigation teardown, and `fmtStamp`, which the
 * heartbeats page prints its "Last run" line with.
 */
(function () {
  "use strict";

  window.novaCharts = function (shared) {
    var el = shared.el;
    var feed = shared.feed;
    var statusEl = shared.statusEl;
    var fetchPage = shared.fetchPage;
    var markNav = shared.markNav;
    var route = shared.route;
    var savedCopyLine = shared.savedCopyLine;
    var stopPolling = shared.stopPolling;
    var wordmark = shared.wordmark;

    /* ---- The costs page (issues.md #57, page 2) --------------------------
     *
     * the owner, 2026-08-08: "I want you to figure out the optimal method of
     * quota spendage for projects. I do not know the optimal way. Figure
     * this out by trial and error and gained experience." Every cycle has
     * been writing its own cost into a ledger since; this is the first time
     * either of us can see the shape of it rather than one row at a time.
     *
     * Two charts and no third, because there are exactly two questions:
     * what one cycle costs, and how close the week is to running out. They
     * are different units, so they are two charts sharing one time axis
     * rather than one chart with two y-scales.
     *
     * Every mark used to be built by hand with createElementNS. As of
     * 2026-08-20 the drawing belongs to Apache ECharts and this file only
     * describes what to draw -- see the chart layer below for why.
     */

    /* The two series colours. Validated against this app's own dark surface
     * (#12131a) rather than chosen: lightness band, chroma floor, contrast,
     * and colour-vision separation for the pair -- worst adjacent deltaE
     * 25.2 under protanopia, 21.4 under tritanopia, 26.3 for normal vision.
     * The app's --accent (#7aa2f7) and --warn (#e8b75c) both fail the
     * lightness band against this surface, which is why these are their own
     * two values and not the theme's. */
    var SERIES_A = "#5d86dd";
    var SERIES_B = "#bd8b2f";
    var GRID = "#2a2d3a";
    var AXIS_INK = "#7d8296";

    function fmtTokens(n) {
      if (!isFinite(n)) return "—";
      if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + "M";
      if (Math.abs(n) >= 1e3) return Math.round(n / 1e3) + "k";
      return String(Math.round(n));
    }

    function fmtMinutes(seconds) {
      if (!isFinite(seconds)) return "—";
      return (seconds / 60).toFixed(1) + " min";
    }

    /* The reader's own clock, deliberately. The ledger stores UTC and the
     * payload carries epoch milliseconds precisely so that the one place
     * that knows what timezone the reader is in gets to decide -- Nova
     * writes Oslo time everywhere for the same reason, and here the browser
     * already knows. */
    function fmtDay(ms) {
      return new Date(ms).toLocaleDateString(undefined, { day: "numeric", month: "short" });
    }

    function fmtStamp(ms) {
      return new Date(ms).toLocaleString(undefined, {
        day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
      });
    }

    /* ---- Charts, on a real charting library --------------------------------
     *
     * the owner, 2026-08-20, on the hand-rolled version this replaces: "there
     * are still quite the amount of bugs and better ux improvements. Can we
     * just use a third party library for this? We do not have to reinvent
     * the wheel here. ... The zoom works, but it does not give me any more
     * granulation in the graph, it just makes the graph bars larger. I want
     * actual graph zoom as in expanding the values on the x/y axis and
     * showing more granularity. Also the hover effect when i press the graph
     * only works for a split second. I should be able to select stuff, move
     * around."
     *
     * He is describing the exact limit of what the old code could do. It
     * zoomed by putting a CSS `transform: scale()` on the rendered SVG,
     * which magnifies the picture and cannot add a tick to an axis: the
     * bars got fatter and the two date labels stayed the same two dates.
     * Real zoom means re-deriving the scales and redrawing, and doing that
     * with a crosshair, a sticky tooltip, pinch, drag-pan and rubber-band
     * selection on top is a charting library. So: Apache ECharts 5.5.1,
     * vendored at `/vendor/echarts.min.js` (Apache-2.0), and about 400
     * lines of hand-written SVG deleted.
     *
     * Vendored rather than a CDN because the app is served over a tailnet
     * and is meant to work on a dead link -- a CDN script tag is a chart
     * page that goes blank the moment the phone is off the internet, which
     * is the failure the service worker exists to prevent. It is 1.0 MB, so
     * it is loaded lazily on the first chart rather than in the shell, and
     * cached by the worker on first use.
     */
    var ECHARTS_SRC = "/vendor/echarts.min.js";
    var echartsLoading = null;

    function ensureECharts() {
      if (window.echarts) return Promise.resolve(window.echarts);
      if (echartsLoading) return echartsLoading;
      echartsLoading = new Promise(function (resolve, reject) {
        var tag = document.createElement("script");
        tag.src = ECHARTS_SRC;
        tag.async = true;
        tag.onload = function () {
          if (window.echarts) resolve(window.echarts);
          else reject(new Error("echarts loaded but did not register"));
        };
        tag.onerror = function () { reject(new Error("could not load " + ECHARTS_SRC)); };
        document.head.appendChild(tag);
      });
      // A failed load must not poison every later chart: drop the memo so
      // the next page visit retries. Offline once is not offline forever.
      echartsLoading.catch(function () { echartsLoading = null; });
      return echartsLoading;
    }

    /* A finger is not a mouse, and on these charts the difference decides
     * whether the page can scroll.
     *
     * ECharts' `inside` dataZoom pans on drag, and its drag handler calls
     * preventDefault on the event it was handed -- which on a phone is the
     * touchmove. Two full-width charts stacked down the costs page therefore
     * become a wall: a finger that lands on a chart pans the chart, and the
     * page underneath does not move. Nova is read on a phone first, so that
     * is the common case, not the edge one.
     *
     * On a coarse pointer the finger belongs to the page, and the chart is
     * moved with the slider under it instead -- a control that cannot be hit
     * by accident. Pinch-to-zoom is untouched: ECharts registers its pinch
     * handler on the zoom branch of the roam controller and gates only the
     * drag on `moveOnMouseMove`, so turning pan off leaves zooming alone. On
     * a mouse, drag-to-pan stays exactly as it was.
     */
    var COARSE_POINTER = (function () {
      try {
        return !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
      } catch (err) {
        // A browser that cannot answer the question is treated as a mouse:
        // that is the behaviour this app already shipped.
        return false;
      }
    })();

    /* One chart's frame: the caption, the box ECharts mounts into, and the
     * full-screen button. Everything inside the box -- axes, grid, marks,
     * crosshair, tooltip, zoom, pan, selection -- belongs to the library
     * now, which is the point of the change.
     */
    function chartFrame(title, subtitle) {
      var figure = el("figure", "chart");
      figure.appendChild(el("figcaption", "chart-title", title));
      if (subtitle) figure.appendChild(el("p", "chart-sub", subtitle));
      var plot = el("div", "chart-plot");
      figure.appendChild(plot);
      var chart = { figure: figure, plot: plot, title: title };

      var tools = el("div", "chart-tools");
      // Said in words, not left to an icon. The library's own toolbox
      // glyphs sit inside the plot and are unlabelled; this is the one
      // control that changes the page rather than the picture.
      var full = el("button", "chart-tool chart-tool-full", "Full screen");
      full.type = "button";
      full.setAttribute("aria-label", "Full screen: " + title);
      full.title = full.getAttribute("aria-label");
      full.addEventListener("click", function () {
        setChartFullscreen(chart, !figure.classList.contains("chart-full"));
      });
      tools.appendChild(el(
        "span", "chart-tools-hint",
        COARSE_POINTER
          ? "Pinch to zoom · drag the bar below to pan · tap a point to pin the readout"
          : "Scroll to zoom · drag to pan · click a point to pin the readout"
      ));
      tools.appendChild(full);
      figure.appendChild(tools);
      return chart;
    }

    /* Shared option scaffolding.
     *
     * The three things the owner asked for, each named where it is set:
     *  - granularity: `dataZoom` re-scales the axis and ECharts re-derives
     *    its ticks, so zooming in genuinely turns "14 Aug — 20 Aug" into
     *    hours. `filterMode: "none"` keeps the marks outside the window
     *    drawn rather than dropped, so panning does not blank a line.
     *  - a readout that stays: `triggerOn: "mousemove|click"` means a tap
     *    on a phone pins the tooltip instead of showing it for the length
     *    of the touch, which is the "split second" he is describing.
     *  - selection and moving around: `toolbox.dataZoom` is rubber-band
     *    select-to-zoom, `type: "inside"` is pinch and drag-pan, and
     *    `restore` puts it all back.
     */
    var CHART_FONT = 11;

    function baseOption(opts) {
      // See COARSE_POINTER: drag-to-pan on a touchscreen eats the page's
      // scroll, so on a phone the slider does the panning.
      var dragPans = !COARSE_POINTER;
      var yZoom = opts.zoomY === false ? [] : [
        { type: "inside", yAxisIndex: 0, filterMode: "none",
          zoomOnMouseWheel: "shift", moveOnMouseMove: dragPans },
      ];
      return {
        animation: false,
        backgroundColor: "transparent",
        textStyle: { color: AXIS_INK, fontSize: CHART_FONT },
        grid: { left: 44, right: 12, top: 12, bottom: 56, containLabel: false },
        tooltip: {
          trigger: "axis",
          triggerOn: "mousemove|click",
          confine: true,
          axisPointer: { type: "cross", label: { show: false },
                         crossStyle: { color: AXIS_INK }, lineStyle: { color: AXIS_INK } },
          backgroundColor: "rgba(16,18,26,0.94)",
          borderColor: GRID,
          textStyle: { color: "#e6e8f0", fontSize: CHART_FONT + 1 },
          formatter: opts.tooltip,
        },
        toolbox: {
          right: 8, top: 2, itemSize: 13,
          iconStyle: { borderColor: AXIS_INK },
          emphasis: { iconStyle: { borderColor: "#e6e8f0" } },
          feature: {
            dataZoom: { yAxisIndex: "none", title: { zoom: "Select an area to zoom", back: "Undo zoom" } },
            restore: { title: "Reset" },
          },
        },
        dataZoom: [
          { type: "inside", xAxisIndex: 0, filterMode: "none", moveOnMouseMove: dragPans },
          {
            type: "slider", xAxisIndex: 0, filterMode: "none",
            height: 22, bottom: 8,
            borderColor: GRID, fillerColor: "rgba(93,134,221,0.16)",
            handleStyle: { color: SERIES_A, borderColor: SERIES_A },
            moveHandleStyle: { color: GRID },
            dataBackground: { lineStyle: { color: AXIS_INK }, areaStyle: { color: GRID } },
            textStyle: { color: AXIS_INK, fontSize: CHART_FONT - 1 },
          },
        ].concat(yZoom),
        xAxis: {
          type: "time",
          min: opts.from, max: opts.to,
          axisLine: { lineStyle: { color: GRID } },
          axisTick: { lineStyle: { color: GRID } },
          axisLabel: { color: AXIS_INK, hideOverlap: true },
          splitLine: { show: false },
        },
        yAxis: {
          type: "value",
          min: opts.min, max: opts.max,
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { color: AXIS_INK, formatter: opts.yLabel },
          splitLine: { lineStyle: { color: GRID } },
        },
        series: opts.series,
      };
    }

    /* Mount a built option into a frame once the library and the layout are
     * both ready.
     *
     * `init` needs a box with a real size, and the figure is not in the
     * document at the moment the render function returns it -- the caller
     * appends it afterwards. So this waits a frame and checks: an element
     * that never lands (the reader navigated away mid-load) is dropped
     * rather than initialised into a zero-width canvas.
     */
    var liveCharts = [];

    /* Forget the charts whose element has left the document, and give the
     * library back the canvas.
     *
     * Every ECharts instance holds a canvas and its own zrender event
     * handlers, and this is one page that swaps its whole view on
     * navigation -- so each visit to the costs page left two more instances
     * alive behind a detached element, with a window resize as the only
     * thing that ever pruned them. On a phone that is a tab left open all
     * day and never resized. Pruning at mount too bounds it at the charts
     * actually on screen.
     */
    function pruneCharts() {
      liveCharts = liveCharts.filter(function (chart) {
        if (chart.plot.isConnected) return true;
        chart.instance.dispose();
        return false;
      });
    }

    function mountEChart(chart, option) {
      /* Hung on the figure synchronously, before anything async starts, and
       * it is the only reason the charts are testable at all. ECharts draws
       * to a canvas, and jsdom has no canvas -- so `tests/browser` cannot
       * assert on a mark the way it did against hand-written SVG. What it
       * can assert on is the description this app hands the library, which
       * is now the whole of what this app decides about a chart. Reading a
       * rect's height was never testing the app's judgement anyway; it was
       * testing arithmetic that has since been deleted. */
      chart.option = option;
      chart.figure.chartOption = option;
      ensureECharts().then(function (echarts) {
        return new Promise(function (resolve) {
          requestAnimationFrame(function () { resolve(echarts); });
        });
      }).then(function (echarts) {
        if (!chart.plot.isConnected) return;
        pruneCharts();
        var instance = echarts.init(chart.plot, null, { renderer: "canvas" });
        instance.setOption(option);
        chart.instance = instance;
        liveCharts.push(chart);
      }).catch(function (err) {
        // Never a blank box. A chart that cannot draw says so, in the space
        // it would have used.
        if (chart.plot.childNodes.length) return;
        chart.plot.appendChild(el("p", "empty", "Chart could not load: " + err.message));
      });
    }

    window.addEventListener("resize", function () {
      pruneCharts();
      liveCharts.forEach(function (chart) { chart.instance.resize(); });
    });

    /* Full screen, unchanged in spirit from the version the owner asked for --
     * the phone-sized figure gets the whole window, which in landscape is a
     * much bigger picture and in portrait at least stops the tiles and the
     * other charts competing for it.
     *
     * The one thing it must now do that it did not before: tell the chart
     * its box changed. ECharts sizes its canvas at `init` and does not
     * watch the element, so without the `resize` the overlay would open on
     * a phone-width picture stretched across the screen.
     */
    var openFullChart = null;

    function setChartFullscreen(chart, on) {
      if (on && openFullChart && openFullChart !== chart) {
        setChartFullscreen(openFullChart, false);
      }
      chart.figure.classList.toggle("chart-full", on);
      document.body.classList.toggle("has-full-chart", on);
      var button = chart.figure.querySelector(".chart-tool-full");
      if (button) {
        button.textContent = on ? "Close" : "Full screen";
        // Two charts on the costs page means two buttons reading "Full
        // screen", and a screen reader announcing them identically is the
        // same failure as a bare priority glyph: the control does not say
        // what it acts on.
        button.setAttribute(
          "aria-label",
          (on ? "Close full screen: " : "Full screen: ") + (chart.title || "")
        );
        button.title = button.getAttribute("aria-label");
      }
      openFullChart = on ? chart : null;
      if (chart.instance) requestAnimationFrame(function () { chart.instance.resize(); });
    }

    function closeFullChart() {
      if (openFullChart) setChartFullscreen(openFullChart, false);
    }

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeFullChart();
    });

    /* A tooltip body, in the shape all three charts want: a stamp, then one
     * row per series with its own swatch. ECharts hands the formatter the
     * params for every series under the pointer; `rows` maps those to the
     * label and value this particular chart wants to print.
     */
    function tipHtml(when, rows) {
      var html = '<div class="chart-tip-when">' + escapeHtml(when) + "</div>";
      rows.forEach(function (row) {
        html += '<div class="chart-tip-row">'
          + '<span class="chart-tip-swatch" style="background:' + row.color + '"></span>'
          + '<span class="chart-tip-label">' + escapeHtml(row.label) + "</span>"
          + '<span class="chart-tip-value">' + escapeHtml(row.value) + "</span>"
          + "</div>";
      });
      return html;
    }

    function escapeHtml(text) {
      return String(text).replace(/[&<>"]/g, function (ch) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch];
      });
    }

    /* What one cycle costs, as a bar per cycle placed at the moment it ran.
     *
     * Placed by time rather than evenly spaced, which is the whole reason
     * this is worth looking at: the loop has been idle for days at a stretch
     * and run fourteen cycles in one, and a bar per cycle in a neat row
     * would draw both stretches identically. The gaps are the finding.
     */
    function renderCycleChart(payload, domain) {
      var rows = payload.cycles || [];
      var chart = chartFrame(
        "What a cycle costs",
        "Weighted tokens per cycle, placed when it ran"
      );
      if (!rows.length) {
        chart.plot.appendChild(el("p", "empty", "No cycles in the ledger yet."));
        return chart.figure;
      }
      // [when, minutes, turns, ?, weighted] -- the library wants [x, y] and
      // carries the rest through untouched for the tooltip.
      var data = rows.map(function (row) {
        return { value: [row[0], row[4]], minutes: row[1], turns: row[2] };
      });
      mountEChart(chart, baseOption({
        from: domain.from, to: domain.to,
        min: 0, max: null,
        yLabel: fmtTokens,
        series: [{
          type: "bar",
          name: "Weighted",
          data: data,
          itemStyle: { color: SERIES_A },
          // A bar per cycle placed at the moment it ran, not evenly spaced:
          // the loop has been idle for days at a stretch and run fourteen
          // cycles in one, and the gaps are the finding. On a time axis the
          // library sizes bars from the *smallest* gap in the data, which
          // used to be arithmetic this file did by hand; a minimum keeps a
          // lone cycle in a quiet week visible rather than sub-pixel.
          barMinWidth: 1,
          barMaxWidth: 14,
          large: true,
        }],
        tooltip: function (params) {
          var point = params[0];
          if (!point) return "";
          var extra = point.data || {};
          return tipHtml(fmtStamp(point.value[0]), [
            { color: SERIES_A, label: "Weighted", value: fmtTokens(point.value[1]) },
            { color: "transparent", label: "Ran for", value: extra.minutes + " min" },
            { color: "transparent", label: "Turns", value: String(extra.turns) },
          ]);
        },
      }));
      return chart.figure;
    }

    /* How much of each quota window has been spent, over time.
     *
     * Two series on one axis because both are a percentage of their own
     * window -- the comparison is the point, and it is the one comparison
     * this data supports without a second scale. The five-hour line sawtooths
     * because it resets five times a day; the seven-day line is the one that
     * decides whether the week runs out early.
     */
    function renderQuotaChart(payload, domain) {
      var rows = (payload.quota || []).filter(function (row) {
        return row[1] !== null || row[3] !== null;
      });
      var chart = chartFrame(
        "How much quota is left",
        "Percent of each window used, at every reading"
      );
      if (!rows.length) {
        chart.plot.appendChild(el("p", "empty", "No quota readings yet."));
        return chart.figure;
      }
      var series = [
        { index: 1, color: SERIES_A, label: "5-hour window" },
        { index: 3, color: SERIES_B, label: "7-day window" },
      ].map(function (spec) {
        return {
          type: "line",
          name: spec.label,
          // A reading that predates this field is a hole, not a zero, and
          // `connectNulls: false` is what stops the line dropping to the
          // axis and back -- which would read as the quota emptying.
          connectNulls: false,
          showSymbol: false,
          symbol: "circle",
          symbolSize: 6,
          lineStyle: { width: 2, color: spec.color },
          itemStyle: { color: spec.color },
          data: rows.map(function (row) {
            var value = row[spec.index];
            return [row[0], value === null || value === undefined ? null : value];
          }),
        };
      });
      mountEChart(chart, baseOption({
        from: domain.from, to: domain.to,
        min: 0, max: 100,
        yLabel: function (v) { return v + "%"; },
        series: series,
        tooltip: function (params) {
          if (!params.length) return "";
          return tipHtml(fmtStamp(params[0].value[0]), params.map(function (point) {
            return {
              color: point.color,
              label: point.seriesName,
              value: point.value[1] === null ? "—" : point.value[1] + "%",
            };
          }));
        },
      }));

      // Two series, so a legend is not optional -- identity must not rest on
      // colour alone.
      var legend = el("div", "chart-legend");
      [
        { color: SERIES_A, label: "5-hour window" },
        { color: SERIES_B, label: "7-day window" },
      ].forEach(function (spec) {
        var key = el("span", "legend-key");
        var swatch = el("span", "legend-swatch");
        swatch.style.background = spec.color;
        key.appendChild(swatch);
        key.appendChild(el("span", "legend-label", spec.label));
        legend.appendChild(key);
      });
      chart.figure.appendChild(legend);
      return chart.figure;
    }

    function statTile(label, value, note) {
      var tile = el("div", "tile");
      tile.appendChild(el("p", "tile-label", label));
      tile.appendChild(el("p", "tile-value", value));
      if (note) tile.appendChild(el("p", "tile-note", note));
      return tile;
    }

    /* What the cycles delegated, added up off the rows.
     *
     * A cycle's `weighted` column is what that session was charged and
     * deliberately excludes the subagents it spawned -- see `_subagent` in
     * `nova_costs.py`. So every tile and chart on this page understated a
     * delegating cycle until these columns existed, on the one page the
     * question "where does the money go" is asked on.
     *
     * Two things this deliberately does not do. It does not read
     * `summary.subagent_weighted`, which is computed from the transcripts on
     * disk now and counts orphans, so it disagrees with the rows the chart
     * plots. And it does not divide by the all-time total: rows older than
     * 2026-08-19 carry no attribution at all and arrive as `null`, so the
     * denominator is the parent spend of the rows that were actually
     * measured. Dividing by everything would report a share of a period
     * nobody was counting in, which is a smaller number and a false one.
     */
    function delegatedSpend(payload) {
      var rows = payload.cycles || [];
      var tokens = 0, parent = 0, counted = 0, from = null;
      rows.forEach(function (r) {
        if (r[6] === null || r[6] === undefined) return;
        if (from === null) from = r[0];
        counted += 1;
        tokens += r[6];
        parent += r[4] || 0;
      });
      if (!counted) return null;
      return {
        tokens: tokens,
        from: from,
        share: parent + tokens > 0 ? (100 * tokens) / (parent + tokens) : 0,
      };
    }

    function renderCostTiles(payload) {
      var summary = payload.summary || {};
      var quota = payload.quota || [];
      var latest = quota.length ? quota[quota.length - 1] : null;
      var row = el("div", "tiles");
      row.appendChild(statTile("Cycles", String(summary.cycles || (payload.cycles || []).length)));
      row.appendChild(statTile(
        "Median cycle", fmtTokens(summary.median_weighted), "weighted tokens"
      ));
      row.appendChild(statTile("Median length", fmtMinutes(summary.median_duration_seconds)));
      var delegated = delegatedSpend(payload);
      if (delegated) {
        row.appendChild(statTile(
          "Delegated",
          fmtTokens(delegated.tokens),
          delegated.share.toFixed(1) + "% of spend since " + fmtDay(delegated.from)
        ));
      }
      if (latest) {
        row.appendChild(statTile(
          "7-day used",
          (latest[3] === null ? "—" : latest[3] + "%"),
          latest[4] === null || latest[4] === undefined ? null : "pace " + latest[4]
        ));
      }
      return row;
    }

    /* Where the tokens actually go. Five shares of one total, which is a
     * table and not a chart: five slices would need five validated hues to
     * say what five rows say in one line each, and the ranking is the
     * finding (cache reads dominate, and they are the cheapest per token). */
    function renderCostShare(payload) {
      var share = (payload.summary || {}).cost_share;
      if (!share) return null;
      var names = {
        input_tokens: "Input",
        output_tokens: "Output",
        cache_read_tokens: "Cache read",
        cache_write_5m_tokens: "Cache write (5m)",
        cache_write_1h_tokens: "Cache write (1h)",
      };
      var wrap = el("section", "share");
      wrap.appendChild(el("h2", "share-title", "Where the cost goes"));
      Object.keys(share)
        .filter(function (key) { return share[key] > 0; })
        .sort(function (a, b) { return share[b] - share[a]; })
        .forEach(function (key) {
          var row = el("div", "share-row");
          row.appendChild(el("span", "share-label", names[key] || key));
          var track = el("span", "share-track");
          var fill = el("span", "share-fill");
          fill.style.width = Math.max(share[key], 0.5) + "%";
          fill.style.background = SERIES_A;
          track.appendChild(fill);
          row.appendChild(track);
          row.appendChild(el("span", "share-value", share[key].toFixed(1) + "%"));
          wrap.appendChild(row);
        });
      var weights = payload.weights || {};
      if (weights.output_tokens) {
        wrap.appendChild(el(
          "p", "share-note",
          "Weighted, not raw: output counts " + weights.output_tokens +
          "x an input token and a cache read " + weights.cache_read_tokens + "x."
        ));
      }
      return wrap;
    }

    /* The first and last moment either series knows about.
     *
     * Computed once and handed to both charts, because the comment above
     * says they share a time axis and until the reviewer checked, they did
     * not: each worked out its own domain from its own rows, and the two
     * series do not cover the same days -- the cycle ledger reaches back to
     * 08-03 and the quota history only to 08-08, so the same date sat at a
     * different x in the two stacked charts and any correlation a reader
     * drew between them was false. Sharing the domain also makes the
     * quota chart's empty left third say something true: nothing was
     * recorded there.
     */
    function timeDomain(payload) {
      var ends = [];
      [payload.cycles || [], payload.quota || []].forEach(function (rows) {
        if (rows.length) ends.push(rows[0][0], rows[rows.length - 1][0]);
      });
      if (!ends.length) return { from: 0, to: 1 };
      return { from: Math.min.apply(null, ends), to: Math.max.apply(null, ends) };
    }

    function renderCosts(payload) {
      stopPolling();
      markNav();
      var summary = payload.summary || {};
      statusEl.textContent = "";
      statusEl.appendChild(wordmark());
      statusEl.appendChild(el(
        "p", "status-line",
        "Costs — " + (summary.cycles || 0) + " cycles, "
          + fmtTokens(summary.total_weighted) + " weighted tokens all told"
      ));
      if (payload.replayed) statusEl.appendChild(savedCopyLine());
      feed.textContent = "";
      var domain = timeDomain(payload);
      feed.appendChild(renderCostTiles(payload));
      feed.appendChild(renderCycleChart(payload, domain));
      feed.appendChild(renderQuotaChart(payload, domain));
      var share = renderCostShare(payload);
      if (share) feed.appendChild(share);
      if (payload.generatedAt) {
        feed.appendChild(el(
          "p", "chart-sub", "Ledger published " + fmtStamp(payload.generatedAt)
        ));
      }
    }

    function loadCosts() {
      fetchPage("/api/costs")
        .then(function (payload) {
          // The same guard the board fetch carries: two taps in quick
          // succession leave two fetches in flight and the loser must not
          // paint over the winner.
          if (route(window.location.pathname).view !== "costs") return;
          renderCosts(payload);
        })
        .catch(function (err) {
          markNav();
          feed.textContent = "";
          feed.appendChild(el("p", "empty", "Could not load the costs: " + err));
        });
    }

    /* ---- The retrospective page (issues.md, 2026-08-13) ------------------
     *
     * the owner: "Rate yourself on a scale from 1 to 10 on how you feel its
     * going, how effective do you think you are, whats good, whats bad,
     * whats the overall feeling (which is the most important metric).
     * Actually note down data and compare it to previous retros (lets also
     * make a page that shows these data as graphs)."
     *
     * The comparison is the ask, so the chart is one chart with all three
     * lines on one 1-10 axis, not three charts side by side: the question
     * is whether they move together, and that is only readable when they
     * share an axis.
     */

    /* The third series colour, and it took a measurement to find.
     *
     * SERIES_A (blue) and SERIES_B (amber) already straddle the axis that
     * red-green deficiency collapses, so most third hues land on top of one
     * of them for somebody. Measured as CIEDE2000 between this and each of
     * the existing two, under normal vision and under simulated protanopia,
     * deuteranopia and tritanopia: teal #38a3a5 falls to 4.7, pink #c2739f
     * to 3.0, violet #b07de0 to 2.4 -- all indistinguishable from a
     * neighbour to a real reader. This one's worst adjacent delta is 16.8
     * (deuteranopia, against the amber) and its contrast against the app's
     * surface is 10.8:1. It is lighter than the pair (L* 79.7 against 56.7
     * and 61.2), which is what buys the separation and is a second channel
     * rather than a compromise. The legend below carries identity anyway --
     * three lines is past where colour alone should be asked to. */
    var SERIES_C = "#8fd694";

    /* Ordered to match nova_retro.SCORE_KEYS, and the overall feeling is
     * last because it is drawn last: three integer scores on a 1-10 axis
     * overlap exactly whenever two of them are equal, and the line he
     * called the most important metric should be the one on top. It is also
     * the thickest, for the same reason. */
    var RETRO_SERIES = {
      going: { color: SERIES_A, width: 2 },
      effectiveness: { color: SERIES_B, width: 2 },
      feeling: { color: SERIES_C, width: 3 },
    };

    function retroSeries(payload) {
      return (payload.scoreKeys || []).map(function (entry) {
        var style = RETRO_SERIES[entry.key] || { color: SERIES_A, width: 2 };
        return { key: entry.key, label: entry.label, color: style.color, width: style.width };
      });
    }

    function renderRetroChart(payload) {
      var rows = payload.retros || [];
      var series = retroSeries(payload);
      var chart = chartFrame(
        "How it has been going",
        "Each Friday's self-rating, 1 to 10"
      );
      if (!rows.length) {
        chart.plot.appendChild(el("p", "empty", "No retrospectives yet."));
        return chart.figure;
      }
      var range = payload.range || [1, 10];
      var lo = range[0];
      var hi = range[1];
      var from = rows[0].at;
      var to = rows[rows.length - 1].at;
      // One retro is a single moment, so the domain has no width. Give it a
      // week either side, which is what the axis would show once the second
      // retro lands.
      if (to === from) {
        from -= 3.5 * 24 * 3600 * 1000;
        to += 3.5 * 24 * 3600 * 1000;
      }
      mountEChart(chart, baseOption({
        from: from, to: to,
        min: lo, max: hi,
        // Five retros are five observations however wide the window is, so
        // the y axis is not something to zoom into -- it is a 1-to-10 scale
        // with ten possible values.
        zoomY: false,
        yLabel: function (v) { return String(v); },
        series: series.map(function (line) {
          return {
            type: "line",
            name: line.label,
            // Same rule as the quota chart: a missing score is a hole, not
            // a zero, and a line drawn down to the axis and back would read
            // as a week that went catastrophically.
            connectNulls: false,
            // A dot per retro as well as the line. With one retro there is
            // no line to see at all, and with five there are still only five
            // real observations -- marking them stops the eye reading the
            // segments between as data.
            showSymbol: true,
            symbol: "circle",
            symbolSize: line.width * 2.5,
            lineStyle: { width: line.width, color: line.color },
            itemStyle: { color: line.color },
            data: rows.map(function (row) {
              var value = (row.scores || {})[line.key];
              return [row.at, typeof value === "number" ? value : null];
            }),
          };
        }),
        tooltip: function (params) {
          if (!params.length) return "";
          return tipHtml(fmtDay(params[0].value[0]), params.map(function (point) {
            return {
              color: point.color,
              label: point.seriesName,
              value: point.value[1] === null ? "—" : point.value[1] + "/" + hi,
            };
          }));
        },
      }));

      var legend = el("div", "chart-legend");
      series.forEach(function (line) {
        var key = el("span", "legend-key");
        var swatch = el("span", "legend-swatch");
        swatch.style.background = line.color;
        key.appendChild(swatch);
        key.appendChild(el("span", "legend-label", line.label));
        legend.appendChild(key);
      });
      chart.figure.appendChild(legend);
      return chart.figure;
    }

    function renderRetroTiles(payload) {
      var rows = payload.retros || [];
      var latest = rows.length ? rows[rows.length - 1] : null;
      var row = el("div", "tiles");
      row.appendChild(statTile("Retros", String(rows.length)));
      if (!latest) return row;
      var hi = (payload.range || [1, 10])[1];
      retroSeries(payload).forEach(function (line) {
        var value = (latest.scores || {})[line.key];
        row.appendChild(statTile(
          line.label,
          typeof value === "number" ? value + "/" + hi : "—",
          latest.date
        ));
      });
      return row;
    }

    /* The one screen, drawn first (ideas.md #120).
     *
     * He asked for "one screen -- what shipped, what broke, what is still
     * stuck, and the one thing you would want to change", because a
     * chat-style report had read better to him than the journal did,
     * twice. So this is four short labelled paragraphs and nothing else:
     * no scores, no chart, no cycle numbers. Everything that answers "is
     * the loop getting better" is below it and is a different question.
     *
     * It is drawn from the newest retro that *has* a summary rather than
     * from the newest retro, because the three retros written before #120
     * have none, and skipping to the last real one is the difference
     * between an empty card and no card. Returns null when no retro has
     * written one yet -- the first is due the next time the retro runs. */
    function renderWeekCard(payload) {
      var rows = payload.retros || [];
      var row = null;
      for (var i = rows.length - 1; i >= 0; i--) {
        if (rows[i].week) { row = rows[i]; break; }
      }
      if (!row) return null;

      var card = el("article", "week-card");
      var head = el("header", "week-head");
      head.appendChild(el("h2", "week-title", "This week"));
      head.appendChild(el("p", "week-date", row.date));
      card.appendChild(head);
      (payload.weekKeys || []).forEach(function (part) {
        var text = row.week[part.key];
        if (!text) return;
        card.appendChild(el("h3", "week-sub", part.label));
        card.appendChild(el("p", "week-text", text));
      });
      return card;
    }

    /* One retro, in full. The chart answers "is it getting better"; this
     * answers "why", and the two are on one page because the score without
     * the sentence behind it is the thing he specifically did not ask for. */
    function renderRetroCard(payload, row) {
      var hi = (payload.range || [1, 10])[1];
      var card = el("article", "retro-card");
      var head = el("header", "retro-head");
      head.appendChild(el("h2", "retro-date", row.date));
      if (row.cycle) head.appendChild(el("p", "retro-cycle", "Cycle " + row.cycle));
      card.appendChild(head);

      var scores = el("div", "retro-scores");
      retroSeries(payload).forEach(function (line) {
        var value = (row.scores || {})[line.key];
        var pill = el("span", "retro-pill");
        var swatch = el("span", "legend-swatch");
        swatch.style.background = line.color;
        pill.appendChild(swatch);
        pill.appendChild(el("span", "retro-pill-label", line.label));
        pill.appendChild(el(
          "span", "retro-pill-value",
          typeof value === "number" ? value + "/" + hi : "—"
        ));
        scores.appendChild(pill);
      });
      card.appendChild(scores);

      if (row.overall) card.appendChild(el("p", "retro-overall", row.overall));
      [
        { label: "What is good", text: row.good },
        { label: "What is bad", text: row.bad },
      ].forEach(function (part) {
        if (!part.text) return;
        card.appendChild(el("h3", "retro-sub", part.label));
        card.appendChild(el("p", "retro-text", part.text));
      });
      if ((row.changes || []).length) {
        card.appendChild(el("h3", "retro-sub", "What I am changing"));
        var list = el("ul", "retro-changes");
        row.changes.forEach(function (change) {
          list.appendChild(el("li", "retro-change", change));
        });
        card.appendChild(list);
      }
      return card;
    }

    function renderRetro(payload) {
      stopPolling();
      markNav();
      var rows = payload.retros || [];
      var latest = rows.length ? rows[rows.length - 1] : null;
      statusEl.textContent = "";
      statusEl.appendChild(wordmark());
      statusEl.appendChild(el(
        "p", "status-line",
        rows.length
          ? "Retrospectives — " + rows.length + ", newest " + latest.date
          : "Retrospectives — none yet"
      ));
      if (payload.replayed) statusEl.appendChild(savedCopyLine());
      feed.textContent = "";
      if (!rows.length) {
        feed.appendChild(el(
          "p", "empty",
          "The first retrospective runs on a Friday morning. Nothing to compare yet."
        ));
        return;
      }
      var week = renderWeekCard(payload);
      if (week) feed.appendChild(week);
      feed.appendChild(renderRetroTiles(payload));
      feed.appendChild(renderRetroChart(payload));
      // Newest first, which is the opposite of the chart's left-to-right
      // and is right for both: a chart is read forwards and a feed is read
      // from the top.
      rows.slice().reverse().forEach(function (row) {
        feed.appendChild(renderRetroCard(payload, row));
      });
    }

    function loadRetro() {
      fetchPage("/api/retro")
        .then(function (payload) {
          // The same guard the board and costs fetches carry: two taps in
          // quick succession leave two fetches in flight and the loser must
          // not paint over the winner.
          if (route(window.location.pathname).view !== "retro") return;
          renderRetro(payload);
        })
        .catch(function (err) {
          markNav();
          feed.textContent = "";
          feed.appendChild(el("p", "empty", "Could not load the retrospectives: " + err));
        });
    }
    return {
      closeFullChart: closeFullChart,
      fmtStamp: fmtStamp,
      loadCosts: loadCosts,
      loadRetro: loadRetro,
    };
  };
})();
