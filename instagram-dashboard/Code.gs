/**
 * 인스타 계정 운영 대시보드 — 자동 수집 스크립트
 *
 * 하는 일
 *  - '계정' 탭에서 수집=ON 인 계정마다 Meta Graph API를 호출해
 *    일별지표 / 콘텐츠성과 / 팔로워구성 / 계정 탭을 갱신합니다.
 *  - 토큰은 시트가 아니라 [프로젝트 설정 → 스크립트 속성]에 저장합니다.
 *    (시트를 공유해도 토큰은 노출되지 않습니다)
 *
 * 처음 한 번
 *  1) 스크립트 속성에 토큰 저장: 키 = '계정' 탭의 [토큰 키 이름] (예: TOKEN_JJONDEUK)
 *  2) 메뉴 [📊 인스타 수집 → 지금 수집 실행] 으로 권한 승인 + 테스트
 *  3) 메뉴 [📊 인스타 수집 → 매일 자동 수집 켜기]
 */

const GRAPH = 'https://graph.facebook.com/v23.0';
const TZ = 'Asia/Seoul';
const DAILY_BACKFILL_DAYS = 3; // 인스타 지표는 하루 이틀 늦게 확정되므로 최근 3일을 매번 다시 씀
const MEDIA_LIMIT = 50;        // 계정당 최근 게시물 몇 개까지 성과를 갱신할지

const SHEET = {
  ACCOUNT: '계정',
  DAILY: '일별지표',
  CONTENT: '콘텐츠성과',
  DEMO: '팔로워구성',
};

/* ───────────── 메뉴 & 트리거 ───────────── */

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('📊 인스타 수집')
    .addItem('지금 수집 실행', 'dailyUpdate')
    .addItem('매일 자동 수집 켜기 (오전 6시)', 'setupDailyTrigger')
    .addItem('자동 수집 끄기', 'removeTriggers')
    .addToUi();
}

function setupDailyTrigger() {
  removeTriggers();
  ScriptApp.newTrigger('dailyUpdate')
    .timeBased().everyDays(1).atHour(6).inTimezone(TZ)
    .create();
  toast_('매일 오전 6시 자동 수집이 켜졌습니다.');
}

function removeTriggers() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'dailyUpdate')
    .forEach(t => ScriptApp.deleteTrigger(t));
}

/* ───────────── 메인 ───────────── */

function dailyUpdate() {
  const ss = SpreadsheetApp.getActive();
  const acc = ss.getSheetByName(SHEET.ACCOUNT);
  const last = acc.getLastRow();
  if (last < 2) return;
  const rows = acc.getRange(2, 1, last - 1, 11).getValues();
  const props = PropertiesService.getScriptProperties();
  const today = ymd_(new Date());

  rows.forEach((r, i) => {
    const handle = String(r[0]).trim();
    const igId = String(r[2]).trim();
    const tokenKey = String(r[4]).trim();
    const on = String(r[5]).trim().toUpperCase() === 'ON';
    const rowNo = i + 2;
    if (!on || !igId) return;

    const token = props.getProperty(tokenKey);
    if (!token) {
      acc.getRange(rowNo, 11).setValue(`오류: 스크립트 속성에 ${tokenKey} 없음`);
      return;
    }
    try {
      const prof = api_(`/${igId}`, { fields: 'username,followers_count,media_count' }, token);
      updateDaily_(ss, handle, igId, token);
      updateMedia_(ss, handle, igId, token, today);
      const krShare = updateDemographics_(ss, handle, igId, token, today);
      acc.getRange(rowNo, 7, 1, 4).setValues([[prof.followers_count, prof.media_count, krShare, toDate_(today)]]);
      acc.getRange(rowNo, 11).setValue('정상 수집');
    } catch (e) {
      acc.getRange(rowNo, 11).setValue('오류: ' + String(e.message).slice(0, 200));
    }
  });
  toast_('수집 완료');
}

/* ───────────── 일별 지표 ───────────── */

function updateDaily_(ss, handle, igId, token) {
  const sh = ss.getSheetByName(SHEET.DAILY);
  const metrics = 'reach,views,profile_views,accounts_engaged,total_interactions,shares,saves';

  // 신규 팔로우(일별 시계열). end_time 의 날짜 = 해당 한국 날짜
  const since0 = kstMidnight_(daysAgo_(DAILY_BACKFILL_DAYS));
  const fc = {};
  try {
    const f = api_(`/${igId}/insights`, {
      metric: 'follower_count', period: 'day',
      since: since0, until: Math.floor(Date.now() / 1000),
    }, token);
    (f.data[0].values || []).forEach(v => { fc[v.end_time.slice(0, 10)] = v.value; });
  } catch (e) { /* 팔로워 100명 미만 등은 제공 안 됨 */ }

  const newRows = [];
  for (let d = DAILY_BACKFILL_DAYS; d >= 1; d--) { // 오늘은 집계 미완료라 제외
    const day = daysAgo_(d);
    const s = kstMidnight_(day);
    const j = api_(`/${igId}/insights`, {
      metric: metrics, metric_type: 'total_value', period: 'day', since: s, until: s + 86400,
    }, token);
    const v = {};
    j.data.forEach(x => { v[x.name] = x.total_value ? x.total_value.value : ''; });
    const reach = Number(v.reach) || 0;
    newRows.push([
      toDate_(day), handle, v.reach, v.views, v.profile_views, fc[day] ?? '',
      v.accounts_engaged, v.total_interactions, v.shares, v.saves,
      reach ? round_(Number(v.profile_views) / reach) : '',
    ]);
  }
  upsert_(sh, newRows, r => `${r[0]}|${r[1]}`, 11, 1);
}

/* ───────────── 콘텐츠 성과 ───────────── */

function updateMedia_(ss, handle, igId, token, today) {
  const sh = ss.getSheetByName(SHEET.CONTENT);
  const list = api_(`/${igId}/media`, {
    fields: 'id,caption,media_type,media_product_type,timestamp,permalink',
    limit: MEDIA_LIMIT,
  }, token).data || [];

  const out = [];
  list.forEach(m => {
    const isReel = m.media_product_type === 'REELS';
    const base = 'reach,views,likes,comments,shares,saved,total_interactions';
    const metric = isReel ? base + ',ig_reels_avg_watch_time' : base + ',profile_visits';
    let v = {};
    try {
      v = insightsMap_(api_(`/${m.id}/insights`, { metric }, token));
    } catch (e) {
      try { // 오래된 게시물은 일부 지표가 없을 수 있어 최소 지표로 재시도
        v = insightsMap_(api_(`/${m.id}/insights`, { metric: 'reach,likes,comments,shares,saved' }, token));
      } catch (e2) { return; }
    }
    const reach = Number(v.reach) || 0;
    const rate = k => (reach ? round_((Number(v[k]) || 0) / reach) : '');
    let type = isReel ? '릴스' : (m.media_product_type === 'FEED' ? '피드' : m.media_product_type);
    if (m.media_type === 'CAROUSEL_ALBUM') type += '(캐러셀)';
    out.push([
      handle,
      toDate_(ymd_(new Date(m.timestamp))),
      type,
      String(m.caption || '').split('\n')[0].slice(0, 40),
      v.views ?? '', reach, v.likes ?? '', v.comments ?? '', v.shares ?? '', v.saved ?? '',
      v.ig_reels_avg_watch_time ? Math.round(v.ig_reels_avg_watch_time / 100) / 10 : '',
      rate('likes'), rate('shares'), rate('saved'),
      v.profile_visits ?? '',
      m.permalink,
      "'" + m.id,
      toDate_(today),
    ]);
  });
  // 키 = 미디어ID (Q열)
  upsert_(sh, out, r => String(r[16]).replace(/^'/, ''), 18, 2);
}

/* ───────────── 팔로워 구성 ───────────── */

function updateDemographics_(ss, handle, igId, token, today) {
  const sh = ss.getSheetByName(SHEET.DEMO);
  const labels = { age: '연령', gender: '성별', country: '국가', city: '도시' };
  const genderKo = { F: '여성', M: '남성', U: '미상' };
  const fresh = [];
  let krShare = '';

  Object.keys(labels).forEach(b => {
    let res;
    try {
      res = api_(`/${igId}/insights`, {
        metric: 'follower_demographics', period: 'lifetime', metric_type: 'total_value',
        breakdown: b, timeframe: 'this_month',
      }, token).data[0].total_value.breakdowns[0].results;
    } catch (e) { return; } // 팔로워 100명 미만이면 제공 안 됨
    const total = res.reduce((a, x) => a + x.value, 0) || 1;
    res.sort((a, z) => z.value - a.value).slice(0, 10).forEach(x => {
      const key = x.dimension_values[0];
      fresh.push([toDate_(today), handle, labels[b], genderKo[key] || key, x.value, round_(x.value / total)]);
      if (b === 'country' && key === 'KR') krShare = round_(x.value / total);
    });
  });
  if (!fresh.length) return krShare;

  // 해당 계정의 기존 행을 지우고 최신 스냅샷으로 교체
  const last = sh.getLastRow();
  const keep = last > 1
    ? sh.getRange(2, 1, last - 1, 6).getValues().filter(r => String(r[1]) !== handle && r[1] !== '')
    : [];
  if (last > 1) sh.getRange(2, 1, last - 1, 6).clearContent();
  const all = keep.concat(fresh);
  sh.getRange(2, 1, all.length, 6).setValues(all);
  return krShare;
}

/* ───────────── 공통 유틸 ───────────── */

function api_(path, params, token) {
  const q = Object.keys(params)
    .map(k => `${encodeURIComponent(k)}=${encodeURIComponent(params[k])}`)
    .join('&');
  const url = `${GRAPH}${path}?${q}&access_token=${encodeURIComponent(token)}`;
  const res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  const j = JSON.parse(res.getContentText());
  if (j.error) throw new Error(`${j.error.code} ${j.error.message}`);
  return j;
}

function insightsMap_(j) {
  const v = {};
  (j.data || []).forEach(x => { v[x.name] = x.values ? x.values[0].value : (x.total_value || {}).value; });
  return v;
}

/**
 * 키가 같은 행은 덮어쓰고, 없으면 아래에 추가한 뒤 날짜 내림차순 정렬
 * @param keyFn  행 → 키
 * @param width  열 개수
 * @param sortCol 정렬 기준 열(1부터)
 */
function upsert_(sh, newRows, keyFn, width, sortCol) {
  if (!newRows.length) return;
  const last = sh.getLastRow();
  const existing = last > 1 ? sh.getRange(2, 1, last - 1, width).getValues() : [];
  const norm = r => r.map(c => (c instanceof Date ? ymd_(c) : c));
  const index = {};
  existing.forEach((r, i) => { index[keyFn(norm(r))] = i; });
  const append = [];
  newRows.forEach(r => {
    const k = keyFn(norm(r));
    if (k in index) sh.getRange(index[k] + 2, 1, 1, width).setValues([r]);
    else append.push(r);
  });
  if (append.length) sh.getRange(sh.getLastRow() + 1, 1, append.length, width).setValues(append);
  const n = sh.getLastRow() - 1;
  if (n > 1) sh.getRange(2, 1, n, width).sort({ column: sortCol, ascending: false });
}

function ymd_(d) { return Utilities.formatDate(d, TZ, 'yyyy-MM-dd'); }

function daysAgo_(n) { return ymd_(new Date(Date.now() - n * 86400000)); }

/** 'yyyy-MM-dd' → 한국 자정 Date (시트에 날짜 값으로 기록됨) */
function toDate_(ymd) { return new Date(`${ymd}T00:00:00+09:00`); }

function kstMidnight_(ymd) { return Math.floor(new Date(`${ymd}T00:00:00+09:00`).getTime() / 1000); }

function round_(x) { return Math.round(x * 10000) / 10000; }

function toast_(msg) {
  try { SpreadsheetApp.getActive().toast(msg, '인스타 수집', 5); } catch (e) { /* 트리거 실행 시 무시 */ }
}
