// Box zoom: giữ Shift + kéo chuột trái để chọn vùng (div mờ phủ lên chart, không vẽ vào canvas), thả chuột thì phóng vùng.
// opts.priceOn() -> có đặt khoảng giá không; opts.apply(sel) nhận kết quả của ZM.boxSelection.
function attachZoomBox(chart, series, el, opts) {
  const box = document.createElement("div");
  box.className = "zoombox";
  box.hidden = true;
  el.appendChild(box);
  let start = null, cur = null;

  const area = () => {  // vùng chọn hợp lệ: bên trái trục giá, trong pane giá chính (không lấn pane RSI)
    let h = el.clientHeight;
    try { h = chart.paneSize(0).height; } catch (e) { /* bản cũ không có paneSize */ }
    return { w: el.clientWidth - chart.priceScale("right").width(), h };
  };
  const pos = (ev, clamp) => {
    const r = el.getBoundingClientRect(), a = area();
    const p = { x: ev.clientX - r.left, y: ev.clientY - r.top };
    return clamp ? { x: Math.min(Math.max(p.x, 0), a.w), y: Math.min(Math.max(p.y, 0), a.h) } : p;
  };
  function draw() {
    box.style.left = Math.min(start.x, cur.x) + "px";
    box.style.top = Math.min(start.y, cur.y) + "px";
    box.style.width = Math.abs(start.x - cur.x) + "px";
    box.style.height = Math.abs(start.y - cur.y) + "px";
  }
  function finish(apply) {
    window.removeEventListener("mousemove", onMove, true);
    window.removeEventListener("mouseup", onUp, true);
    chart.applyOptions({ handleScroll: true, handleScale: true });  // trả lại kéo/zoom của chart
    box.hidden = true;
    const s = start, e = cur;
    start = cur = null;
    if (!apply || !s || !e) return;
    const ts = chart.timeScale();
    const sel = ZM.boxSelection(s, e, (x) => ts.coordinateToLogical(x), (y) => series.coordinateToPrice(y), opts.priceOn());
    if (sel) opts.apply(sel);  // vùng quá nhỏ (< 5px) -> null -> bỏ qua
  }
  function onMove(ev) { cur = pos(ev, true); draw(); }
  function onUp(ev) { cur = pos(ev, true); finish(true); }
  function onDown(ev) {
    if (!ev.shiftKey || ev.button !== 0) return;
    const p = pos(ev, false), a = area();
    if (p.x > a.w || p.y > a.h) return;  // bấm vào trục giá / pane khác: để chart xử lý
    ev.preventDefault();
    ev.stopPropagation();  // chart không thấy mousedown nên không kéo theo
    start = cur = p;
    chart.applyOptions({ handleScroll: false, handleScale: false });
    box.hidden = false;
    draw();
    window.addEventListener("mousemove", onMove, true);
    window.addEventListener("mouseup", onUp, true);
  }
  el.addEventListener("mousedown", onDown, true);  // capture: chạy trước handler của chart
  return { cancel: () => { const was = start !== null; if (was) finish(false); return was; } };
}
