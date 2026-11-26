// Tạo job nền rồi poll tới khi xong. onTick(job) gọi mỗi lần poll (để vẽ tiến độ).
// Trạng thái chung của trang: kết quả run hiện tại + chỗ để các tab khác đăng ký phản ứng
// onResult: [fn(result)] gọi khi có kết quả run mới; onShow: {tab: fn()} gọi khi mở tab
const TESTER = { opts: null, result: null, jobId: null, onResult: [], onShow: {} };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const POLL_MS = 600;

async function runJob(url, body, onTick) {
  const { job_id } = await sendJSON("POST", url, body);
  let failures = 0;
  for (;;) {
    await sleep(POLL_MS);
    let job;
    try {
      job = await getJSON("/api/tester/jobs/" + job_id);
      failures = 0;
    } catch (e) {
      if (e.status === 404 || ++failures >= 5) throw e;  // mạng chập chờn thì thử lại vài lần
      continue;
    }
    if (onTick) onTick(job);
    if (job.status === "error") throw new Error(job.error);
    if (job.status === "done") return job;
  }
}

// Thanh tiến độ: total = 0 -> chạy vô định (run_tester không báo tiến độ)
function showProgress(barId, job) {
  const bar = $(barId);
  bar.hidden = false;
  const { done, total } = job ? job.progress : { done: 0, total: 0 };
  bar.classList.toggle("busy", !total);
  bar.firstElementChild.style.width = total ? (100 * done / total) + "%" : "30%";
}
