// 사용성 E2E: 실제 사용자 흐름을 브라우저로 따라가며 '불편 신호'를 측정한다.
//   node tests/e2e/flows.js <harness.json> <결과 폴더> <가로> <세로>
// 측정: 콘솔 오류, 실패한 요청, 가로 넘침, 핵심 버튼이 화면 안에 보이는지, 단계별 소요 시간, 입력 중 단축키 오작동.
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const [harnessPath, outDir, W, H] = process.argv.slice(2);
const harness = JSON.parse(fs.readFileSync(harnessPath, "utf8"));
const BASE = `http://127.0.0.1:${harness.port}`;
const tag = `${W}x${H}`;
fs.mkdirSync(outDir, { recursive: true });

const results = { viewport: tag, checks: [], timings: {}, errors: [], screenshots: [] };
const check = (name, ok, detail = "") => { results.checks.push({ name, ok: !!ok, detail: String(detail) }); console.log(`${ok ? "PASS" : "FAIL"} [${tag}] ${name} ${detail}`); };
const t0 = () => Date.now();
const shot = async (page, name) => { const f = `${tag}-${name}.png`; await page.screenshot({ path: path.join(outDir, f) }); results.screenshots.push(f); };

async function layout(page, where, selectors) {
  const r = await page.evaluate((sels) => {
    const vw = innerWidth, vh = innerHeight;
    const over = document.documentElement.scrollWidth - vw;
    const vis = {};
    for (const s of sels) {
      const el = document.querySelector(s);
      if (!el) { vis[s] = "없음"; continue; }
      const b = el.getBoundingClientRect();
      vis[s] = b.width > 0 && b.height > 0 && b.left >= -1 && b.top >= -1 && b.right <= vw + 1 && b.bottom <= vh + 1 ? "ok" : `밖(${Math.round(b.left)},${Math.round(b.top)},${Math.round(b.right)},${Math.round(b.bottom)})`;
    }
    return { over, vis };
  }, selectors);
  check(`${where}: 가로 스크롤 없음`, r.over <= 1, `넘침 ${r.over}px`);
  for (const [s, v] of Object.entries(r.vis)) check(`${where}: ${s} 화면 안에 보임`, v === "ok", v);
}

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH || undefined,
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
  });
  const ctx = await browser.newContext({ viewport: { width: +W, height: +H }, permissions: ["microphone"], acceptDownloads: true });
  const page = await ctx.newPage();
  page.on("pageerror", e => results.errors.push("pageerror: " + e.message));
  page.on("console", m => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) results.errors.push("console: " + m.text()); });
  page.on("response", r => { if (r.status() >= 400 && !r.url().includes("favicon")) results.errors.push(`HTTP ${r.status()} ${r.url().replace(BASE, "")}`); });
  page.on("dialog", d => d.accept());

  try {
    // 1) 시작 화면
    let s = t0();
    await page.goto(BASE + "/");
    await page.waitForSelector("#drop");
    results.timings.home_load_ms = Date.now() - s;
    check("시작 화면: 설치 문제 배너 없음", !(await page.$(".banner:not(.upd)")));
    await layout(page, "시작 화면", ["#drop", "#side"]);
    await shot(page, "01-home");

    // 2) 새 프로젝트: 업로드 → 채널(와이프) → 시작 → 자동으로 편집기 열림
    s = t0();
    // 경로 대신 내용+이름으로 넣는다(Playwright는 작은따옴표·한글 경로를 조용히 무시함). 서버는 원래 이름 그대로 받는다.
    await page.setInputFiles("#filePick", { name: path.basename(harness.webm), mimeType: "video/webm", buffer: fs.readFileSync(harness.webm) });
    await page.waitForSelector("#goBtn:not([disabled])", { timeout: 60000 });
    results.timings.upload_ms = Date.now() - s;
    await layout(page, "새 프로젝트 창", ["#goBtn", "#mCancel"]);
    await page.click('[data-prof="wife"]');
    check("새 프로젝트: 채널 고르면 스타일 자동 선택(유머)", await page.$('[data-style="funny"].on'));
    await shot(page, "02-new-project");
    s = t0();
    await page.click("#goBtn");
    await page.waitForURL("**/edit", { timeout: 240000 });
    results.timings.auto_edit_and_open_ms = Date.now() - s;
    await page.waitForSelector(".b-main", { timeout: 60000 });
    await page.waitForTimeout(800);

    // 3) 편집기 배치
    await layout(page, "편집기", ["#exportBtn", "#playBtn", "#bSplit", "#homeBtn", '#lnav [data-nav="ai"]', "#tracks .b-main", "#score"]);
    const frameH = await page.evaluate(() => document.querySelector("#frame").getBoundingClientRect().height);
    check("편집기: 플레이어 높이 220px 이상", frameH >= 220, `${Math.round(frameH)}px`);
    const tlH = await page.evaluate(() => document.querySelector("#tscroll").getBoundingClientRect().height);
    check("편집기: 타임라인 보이는 높이 150px 이상", tlH >= 150, `${Math.round(tlH)}px`);
    check("편집기: 자동 캡션이 들어감", await page.evaluate(() => (diag.captions || []).length > 3));
    await shot(page, "03-editor");

    // 4) CapCut 조작: 분할·왼쪽 삭제·되돌리기
    const st = () => page.evaluate(() => ({ clips: plan.clips.length, total: +L.total.toFixed(2), undo: undo.length }));
    const a = await st();
    await page.evaluate(() => seek(L.clips[1].out + Math.min(1, L.clips[1].dur / 2)));
    await page.keyboard.press("Control+b");
    const b = await st();
    check("Ctrl+B 분할", b.clips === a.clips + 1, `${a.clips}→${b.clips}`);
    await page.keyboard.press("q");
    await page.keyboard.press("Control+z");
    await page.keyboard.press("Control+z");
    check("되돌리기 2번이면 원래대로", (await st()).clips === a.clips && Math.abs((await st()).total - a.total) < 0.02);

    // 5) 입력 중 단축키 오작동 없음
    await page.click('[data-nav="voice"]');
    await page.waitForSelector("#narText");
    const before = await st();
    await page.fill("#narText", "");
    await page.type("#narText", "q w 이번 영상 핵심만 정리합니다");
    check("문장 입력 중 q/w 단축키가 작동하지 않음", (await st()).total === before.total);

    // 6) 나레이션(가짜 음성 엔진)
    await page.waitForFunction(() => document.querySelectorAll("#narVoice option[value^='voicestudio:']").length > 0, null, { timeout: 15000 });
    check("음성 탭: VoiceStudio 연결 표시·기본 엔진 VoxCPM2", (await page.textContent("#lpanel")).includes("VoiceStudio 연결됨") && (await page.inputValue("#vsModel")) === "voxcpm2");
    await layout(page, "음성 탭", ["#vsModel", "#narAdd"]);
    const clipped = await page.evaluate(() => { const P = document.querySelector("#lpanel").getBoundingClientRect();
      return [...document.querySelectorAll("#lpanel button, #lpanel select, #lpanel .note")].filter(el => { const b = el.getBoundingClientRect(); return b.width && (b.right > P.right + 1 || (el.matches(".note") && b.width < 40)); }).map(el => el.id || el.textContent.trim().slice(0, 12)); });
    check("음성 탭: 버튼·문구가 패널 밖으로 잘리거나 세로로 찌그러지지 않음", clipped.length === 0, clipped.join(", "));
    s = t0();
    await page.click("#narAdd");
    await page.waitForFunction(() => plan.narrations.length > 0 && plan.narrations[0].audio, null, { timeout: 30000 });
    results.timings.narration_ms = Date.now() - s;
    check("나레이션 블록 표시", await page.$(".b-nar"));
    await shot(page, "03b-voice");
    // VoiceStudio가 꺼진 상황: 안내와 '다시 연결'이 보이고, 다시 켜면 이어서 쓸 수 있어야 한다
    const vsUrl = await page.evaluate(() => S.voice.url);
    await page.evaluate(async () => { S.voice = (await api("/api/settings", {voicestudio_url: "http://127.0.0.1:9"})).voice; STOCK = null; renderLeft(); });
    await page.waitForSelector("#vsRetry");
    await layout(page, "음성 탭(VoiceStudio 꺼짐)", ["#vsRetry"]);
    await shot(page, "03c-voice-off");
    await page.evaluate(async (u) => { await api("/api/settings", {voicestudio_url: u}); }, vsUrl);
    await page.click("#vsRetry");
    await page.waitForFunction(() => S.voice.ready, null, { timeout: 15000 });
    check("VoiceStudio를 다시 켜면 '다시 연결'로 복구", !(await page.$("#vsRetry")));

    // 7) 텍스트 추가 + 자동 저장
    await page.click('[data-nav="text"]');
    await page.click('[data-addtext="basic"]');
    await page.waitForSelector("#saveState:has-text('자동 저장됨')", { timeout: 10000 });
    check("자동 저장", true);

    // 8) 최종 내보내기 → v1 + 검수표 + 진행률
    await page.keyboard.press("Control+e");
    await page.waitForSelector("#mGo");
    await layout(page, "내보내기 창", ["#mGo"]);
    s = t0();
    await page.click("#mGo");
    const seen = new Set();
    for (let i = 0; i < 240; i++) {
      await page.waitForTimeout(500);
      const w = await page.evaluate(() => document.querySelector("#prog i")?.style.width || "");
      if (w) seen.add(w);
      if (/완료|실패|오류/.test(await page.textContent("#mstate"))) break;
    }
    results.timings.export_final_ms = Date.now() - s;
    const mstate = await page.textContent("#mstate");
    check("최종 내보내기 완료", /완료/.test(mstate), mstate.slice(0, 80));
    check("내보내기 진행률이 실제로 움직임", seen.size >= 3, [...seen].join(" "));
    check("v1 검수표 링크", await page.$("text=검수표 열기"));
    const sheet = await page.evaluate(async () => (await fetch("/media/review/" + encodeURIComponent("검수표_v1.html"))).text());
    check("검수표에 체크리스트·업로드 문구·AI 음성 표시", /컨펌 체크리스트/.test(sheet) && /업로드 문구/.test(sheet) && /AI 음성 사용/.test(sheet));
    check("검수표에 음성 엔진·라이선스 표시", /VoxCPM2/.test(sheet) && /Apache-2\.0/.test(sheet) && !/비상업용 — 사용 불가/.test(sheet));
    await shot(page, "04-export");
    await page.click("#mClose");

    // 9) 수정 요청: 메신저 내용 붙여 넣기 → 시간 눌러 이동
    await page.click("#statusChip");
    await page.waitForSelector("#rvMemo");
    check("내보낸 뒤 상태가 '컨펌 요청'", (await page.textContent("#statusChip")).includes("컨펌 요청"));
    await page.fill("#rvMemo", "0:03 자막 오타 있어요\n5초 부분 컷 빼주세요\n마지막 문구 짧게");
    await page.waitForSelector("[data-rqt]", { timeout: 5000 });
    check("수정 요청 시간 2개 인식", (await page.$$("[data-rqt]")).length === 2);
    await page.click("[data-rqt]");
    const pt = await page.evaluate(() => playT);
    check("시간 누르면 그 위치로 이동", Math.abs(pt - 3) < 0.2, pt.toFixed(2));
    await page.click("[data-rqdone='0']");
    await page.selectOption("#rvStatus", "수정 요청");
    await shot(page, "05-review");

    // 10) 버전·백업 탭
    await page.click('#rtabs [data-rt="ver"]');
    check("버전 탭에 v1", (await page.textContent("#rbody")).includes("v1"));

    // 11) 홈으로 → 카드에 상태·버전·표지
    await page.click("#homeBtn");
    await page.waitForSelector(".pc");
    await page.waitForTimeout(800);
    const card = await page.evaluate(() => { const c = document.querySelector(".pc"); return { st: c.querySelector(".st").textContent, ver: c.querySelector(".ver")?.textContent, cover: getComputedStyle(c.querySelector(".cv")).backgroundImage }; });
    check("홈 카드: 상태 '수정 요청'", card.st === "수정 요청", card.st);
    check("홈 카드: 버전 v1·표지 이미지", card.ver === "v1" && card.cover.includes("cover"), JSON.stringify(card));
    await shot(page, "06-home-after");

    // 12) 설정·점검
    await page.click('#side [data-page="settings"]');
    await page.waitForSelector(".chk");
    check("설정·점검 화면", (await page.$$(".chk")).length >= 6);
    await shot(page, "07-settings");

    // 13) 업데이트: 새 버전 알림 → 지금 업데이트 → 다시 시작 뒤 화면 자동 새로고침 → 되돌리기 버튼
    await page.click('#side [data-page="home"]');
    await page.waitForSelector(".banner.upd", { timeout: 20000 });
    check("업데이트: 시작 화면에 새 버전 알림과 바뀐 점", (await page.textContent(".banner.upd")).includes("9.9.0") && (await page.textContent(".banner.upd")).includes("새 기능 A"));
    await layout(page, "업데이트 알림", ["#updGo"]);
    await shot(page, "08-update-banner");
    s = t0();
    await page.click("#updGo");
    await page.waitForEvent("load", { timeout: 60000 });
    await page.waitForSelector("#drop");
    results.timings.update_ms = Date.now() - s;
    await page.waitForTimeout(800);
    check("업데이트 뒤 알림이 사라짐(최신 버전)", !(await page.$(".banner.upd")));
    await page.click('#side [data-page="settings"]');
    await page.waitForSelector("#updBox #updBack", { timeout: 10000 });
    check("설정: 지금 버전 9.9.0·이전 버전 되돌리기 버튼", (await page.textContent("#updBox")).includes("지금 버전 9.9.0") && (await page.textContent("#updBack")).includes("되돌리기"));
    await shot(page, "09-update-settings");
    await layout(page, "설정 화면", ["#sSave"]);
  } catch (e) {
    check("흐름 중단 없이 끝까지 진행", false, e.message.split("\n")[0]);
    await shot(page, "zz-failure").catch(() => {});
  }
  check("콘솔·요청 오류 없음", results.errors.length === 0, results.errors.slice(0, 5).join(" | "));
  fs.writeFileSync(path.join(outDir, `result-${tag}.json`), JSON.stringify(results, null, 1));
  await browser.close();
})();
