// Trục giá: chế độ Thường/Log/% + Auto + khoảng giá thủ công (box zoom). Nhớ chế độ trong localStorage.
const PS_KEY = "paperbot.priceMode";

function attachPriceScale(chart, series, ui) {
  const scale = () => series.priceScale();
  let mode = "normal";
  try { mode = localStorage.getItem(PS_KEY) || "normal"; } catch (e) { /* localStorage bị chặn thì dùng mặc định */ }
  if (!(mode in ZM.MODES)) mode = "normal";

  function syncUi() {  // hiện trạng thái thật lên nút (kéo trục giá làm thư viện tự tắt auto)
    const auto = scale().options().autoScale;
    if (ui.mode) ui.mode.value = mode;
    if (ui.auto) { ui.auto.classList.toggle("active", !!auto); ui.auto.textContent = auto ? "Auto" : "Tay"; }
  }
  function setMode(m) {
    if (!(m in ZM.MODES)) return;
    mode = m;
    try { localStorage.setItem(PS_KEY, m); } catch (e) { /* bỏ qua */ }
    scale().applyOptions({ mode: ZM.MODES[m] });
    syncUi();
  }
  function setAuto(on) { scale().applyOptions({ autoScale: !!on }); syncUi(); }
  // Đặt khoảng giá tay. Chỉ chắc đúng ở chế độ Thường (Log/% thư viện dùng đơn vị nội bộ) -> trả false nếu bỏ qua
  function setManual(range) {
    if (mode !== "normal" || !range) return false;
    scale().setVisibleRange({ from: range.from, to: range.to });  // tự tắt autoScale
    syncUi();
    return true;
  }

  if (ui.mode) ui.mode.onchange = () => setMode(ui.mode.value);
  if (ui.auto) ui.auto.onclick = () => setAuto(!scale().options().autoScale);
  setMode(mode);
  return { setMode, setAuto, setManual, syncUi, getMode: () => mode, toggleLog: () => setMode(mode === "log" ? "normal" : "log") };
}
