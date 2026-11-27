// Chú giải OHLC theo crosshair + tooltip cho markers tín hiệu/lệnh.
function legendHtml(bar, vol, mas) {
  if (!bar) return `<b>${esc(CH.symbol)}</b> · ${esc(CH.tf)}`;
  const chg = bar.close - bar.open;
  const cls = fmt.cls(chg);
  const pct = bar.open ? (chg / bar.open) * 100 : 0;
  let html = `<b>${esc(CH.symbol)}</b> · ${esc(CH.tf)} &nbsp; ` +
    `O <span class="${cls}">${fmt.num(bar.open)}</span> H <span class="${cls}">${fmt.num(bar.high)}</span> ` +
    `L <span class="${cls}">${fmt.num(bar.low)}</span> C <span class="${cls}">${fmt.num(bar.close)}</span> ` +
    `<span class="${cls}">${chg >= 0 ? "+" : ""}${fmt.num(chg)} (${fmt.num(pct)}%)</span>`;
  if (vol !== undefined) html += `<br>Vol ${fmt.num(vol, 0)}`;
  for (const [k, v] of Object.entries(mas)) {
    if (v !== undefined) html += ` &nbsp; <span style="color:${MA_COLORS[k]}">${k.toUpperCase()} ${fmt.num(v)}</span>`;
  }
  return html;
}

function tooltipHtml(items) {
  return items.map((m) => {
    const t = m.tooltip || {};
    if (m.kind === "fill") {
      return `<b>${t.side === "buy" ? "Mua" : "Bán"} khớp</b> ${esc(t.qty)} @ ${fmt.num(t.price)}<br>` +
        `<span class="muted">${esc(t.filled_at)}</span>`;
    }
    if (m.kind === "myfill") {  // lệnh đã khớp của tài khoản Alpaca
      const pnl = t.pnl === null || t.pnl === undefined ? "" :
        `<br>P/L <span class="${fmt.cls(t.pnl)}">${t.pnl >= 0 ? "+" : ""}${fmt.num(t.pnl)}</span>`;
      return `<b>${t.side === "buy" ? "Mua" : "Bán"} khớp</b> ${esc(FM.q(t.qty))} @ ${esc(FM.p2(t.price))}<br>` +
        `<span class="muted">${esc(nyDay(t.time) + " " + nyClock(t.time))} (${esc(TZ_CHOICES[CH.tz])})</span><br>` +
        `Loại ${esc(t.type)} · Nguồn ${esc(t.source_text)}${pnl}`;
    }
    const prob = t.prob === null || t.prob === undefined ? "-" : fmt.num(t.prob, 3);
    return `<b>Tín hiệu ${esc(t.action)}</b> (${esc(t.strategy)})<br>` +
      `giá ${fmt.num(t.price)} · confidence ${fmt.num(t.confidence, 2)} · prob ${prob}<br>` +
      (t.veto ? `<span class="err">VETO: ${esc(t.veto_reason)}</span><br>` : "") +
      `<span class="muted">${esc(t.reason)}</span><br><span class="muted">${esc(t.asof)}</span>`;
  }).join("<hr>");
}

function lastBarInfo() {
  const last = D.bars[D.bars.length - 1];
  const vol = D.volume[D.volume.length - 1];
  const mas = {};
  for (const k of Object.keys(MA_COLORS)) {
    const arr = D.ind[k];
    mas[k] = arr && arr.length ? arr[arr.length - 1].value : undefined;
  }
  return { bar: last, vol: vol ? vol.value : undefined, mas };
}

function refreshLegend() {
  const { bar, vol, mas } = lastBarInfo();
  document.getElementById("legend").innerHTML = legendHtml(bar, vol, mas);
}

function onCrosshair(param) {
  const tip = document.getElementById("tooltip");
  const bar = param.time ? param.seriesData.get(CH.candles) : null;
  if (!bar) { refreshLegend(); tip.hidden = true; return; }
  const vol = param.seriesData.get(CH.volume);
  const mas = {};
  for (const k of Object.keys(MA_COLORS)) {
    const p = param.seriesData.get(CH.ma[k]);
    mas[k] = p ? p.value : undefined;
  }
  document.getElementById("legend").innerHTML = legendHtml(bar, vol ? vol.value : undefined, mas);
  const items = CH.markerByTime.get(param.time);
  if (!items || !param.point) { tip.hidden = true; return; }
  tip.innerHTML = tooltipHtml(items);
  tip.hidden = false;
  const wrap = document.getElementById("chart-wrap");
  tip.style.left = Math.min(param.point.x + 16, wrap.clientWidth - 260) + "px";
  tip.style.top = Math.min(param.point.y + 16, wrap.clientHeight - tip.offsetHeight - 8) + "px";
}

function initLegend() {
  CH.chart.subscribeCrosshairMove(onCrosshair);
  refreshLegend();
}
