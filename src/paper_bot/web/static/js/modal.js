// Hộp xác nhận dùng chung: chỉ nút "Gửi" mới bật khi người dùng gõ ĐÚNG "XAC NHAN" (phân biệt hoa/thường, không cắt khoảng trắng).
const CONFIRM_WORD = "XAC NHAN";
let _modalBusy = false;

// Trả Promise<string|null>: chuỗi đã gõ (luôn = CONFIRM_WORD) nếu đồng ý, null nếu huỷ bỏ.
function askConfirm(title, bodyHtml) {
  if (_modalBusy) return Promise.resolve(null);
  _modalBusy = true;
  const root = document.getElementById("modal");
  const input = document.getElementById("modal-input");
  const ok = document.getElementById("modal-ok");
  const cancel = document.getElementById("modal-cancel");
  document.getElementById("modal-title").textContent = title;
  document.getElementById("modal-body").innerHTML = bodyHtml;  // bodyHtml phải được esc() từ phía gọi
  input.value = ""; ok.disabled = true; root.hidden = false; input.focus();

  return new Promise((resolve) => {
    const done = (value) => {
      root.hidden = true; _modalBusy = false;
      input.removeEventListener("input", onInput); input.removeEventListener("keydown", onKey);
      ok.removeEventListener("click", onOk); cancel.removeEventListener("click", onCancel);
      root.removeEventListener("mousedown", onBackdrop);
      resolve(value);
    };
    const onInput = () => { ok.disabled = input.value !== CONFIRM_WORD; };
    const onOk = () => { if (input.value === CONFIRM_WORD) done(input.value); };
    const onCancel = () => done(null);
    const onKey = (ev) => {
      if (ev.key === "Enter") onOk();
      else if (ev.key === "Escape") onCancel();
    };
    const onBackdrop = (ev) => { if (ev.target === root) onCancel(); };
    input.addEventListener("input", onInput); input.addEventListener("keydown", onKey);
    ok.addEventListener("click", onOk); cancel.addEventListener("click", onCancel);
    root.addEventListener("mousedown", onBackdrop);
  });
}

// Bảng tóm tắt "nhãn: giá trị" cho hộp xác nhận
function summaryTable(rows) {
  return "<table>" + rows.map(([k, v]) =>
    `<tr><td class="muted">${esc(k)}</td><td><b>${esc(v)}</b></td></tr>`).join("") + "</table>";
}
