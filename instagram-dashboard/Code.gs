/**
 * 인스타 계정 운영 대시보드 — 자동 수집 스크립트 (v2)
 *
 * 하는 일
 *  - '계정' 탭에서 수집=ON 인 계정마다 Meta Graph API를 호출해
 *    일별지표 / 콘텐츠성과 / 팔로워구성 / 계정 탭을 갱신합니다.
 *  - v2: 언팔로우·순증·비팔로워 도달, 리포스트·요일·게시시각·숏코드,
 *        28일 고유 도달·비팔로워 비중·릴스 조회 비중·25~44세·여성 비중 추가
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
  const last = lastRow_(acc);
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
      const demo = updateDemographics_(ss, handle, igId, token, today);
      const a28 = account28_(igId, token);
      acc.getRange(rowNo, 7, 1, 4).setValues([[prof.followers_count, prof.media_count, demo.krShare, toDate_(today)]]);
      acc.getRange(rowNo, 12, 1, 5).setValues([[a28.reach28, a28.nonFollowerShare, a28.reelViewShare, demo.age2544, demo.female]]);
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
    // 팔로우/언팔로우: FOLLOWER=신규 팔로우, NON_FOLLOWER=팔로우 취소
    const fu = breakdown_(igId, token, 'follows_and_unfollows', s);
    // 도달 중 비팔로워(탐색·추천으로 처음 본 사람)
    const rf = breakdown_(igId, token, 'reach', s);
    const follows = fu.FOLLOWER || 0;
    const unfollows = fu.NON_FOLLOWER || 0;
    newRows.push([
      toDate_(day), handle, v.reach, v.views, v.profile_views, follows,
      v.accounts_engaged, v.total_interactions, v.shares, v.saves,
      reach ? round_(Number(v.profile_views) / reach) : '',
      unfollows, follows - unfollows, rf.NON_FOLLOWER ?? '',
    ]);
  }
  upsert_(sh, newRows, r => `${r[0]}|${r[1]}`, 14, 1);
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
    const tries = isReel
      ? [base + ',ig_reels_avg_watch_time,reposts', base + ',ig_reels_avg_watch_time', 'reach,likes,comments,shares,saved']
      : [base + ',profile_visits', 'reach,likes,comments,shares,saved'];
    let v = null;
    for (const metric of tries) { // 오래된 게시물은 일부 지표가 없을 수 있어 단계적으로 재시도
      try { v = insightsMap_(api_(`/${m.id}/insights`, { metric }, token)); break; } catch (e) { }
    }
    if (!v) return;
    const posted = new Date(m.timestamp);
    const shortcode = String(m.permalink || '').replace(/\/$/, '').split('/').pop();
    const reach = Number(v.reach) || 0;
    const rate = k => (reach ? round_((Number(v[k]) || 0) / reach) : '');
    let type = isReel ? '릴스' : (m.media_product_type === 'FEED' ? '피드' : m.media_product_type);
    if (m.media_type === 'CAROUSEL_ALBUM') type += '(캐러셀)';
    out.push([
      handle,
      toDate_(ymd_(posted)),
      type,
      String(m.caption || '').split('\n')[0].slice(0, 40),
      v.views ?? '', reach, v.likes ?? '', v.comments ?? '', v.shares ?? '', v.saved ?? '',
      v.ig_reels_avg_watch_time ? Math.round(v.ig_reels_avg_watch_time / 100) / 10 : '',
      rate('likes'), rate('shares'), rate('saved'),
      v.profile_visits ?? '',
      m.permalink,
      "'" + m.id,
      toDate_(today),
      v.reposts ?? '',
      '일월화수목금토'.charAt(Number(Utilities.formatDate(posted, TZ, 'u')) % 7),
      "'" + Utilities.formatDate(posted, TZ, 'HH:mm'),
      shortcode,
    ]);
  });
  // 키 = 미디어ID (Q열). W열 이후는 시트 수식(시리즈·등급 등)이라 건드리지 않음
  upsert_(sh, out, r => String(r[16]).replace(/^'/, ''), 22, 2);
}

/* ───────────── 팔로워 구성 ───────────── */

function updateDemographics_(ss, handle, igId, token, today) {
  const sh = ss.getSheetByName(SHEET.DEMO);
  const labels = { age: '연령', gender: '성별', country: '국가', city: '도시' };
  const genderKo = { F: '여성', M: '남성', U: '미상' };
  const fresh = [];
  let krShare = '';
  let age2544 = '';
  let female = '';

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
      if (b === 'gender' && key === 'F') female = round_(x.value / total);
    });
    if (b === 'age') {
      const s = res.filter(x => ['25-34', '35-44'].includes(x.dimension_values[0])).reduce((a, x) => a + x.value, 0);
      age2544 = round_(s / total);
    }
  });
  const result = { krShare, age2544, female };
  if (!fresh.length) return result;

  // 해당 계정의 기존 행을 지우고 최신 스냅샷으로 교체
  const last = lastRow_(sh);
  const keep = last > 1
    ? sh.getRange(2, 1, last - 1, 6).getValues().filter(r => String(r[1]) !== handle && r[1] !== '')
    : [];
  if (last > 1) sh.getRange(2, 1, last - 1, 6).clearContent();
  const all = keep.concat(fresh);
  sh.getRange(2, 1, all.length, 6).setValues(all);
  return result;
}

/* ───────────── 계정 28일 요약 ───────────── */

function account28_(igId, token) {
  const until = kstMidnight_(daysAgo_(0));
  const since = until - 28 * 86400;
  const r = api_(`/${igId}/insights`, {
    metric: 'reach', period: 'day', metric_type: 'total_value', breakdown: 'follow_type', since, until,
  }, token).data[0].total_value;
  const v = api_(`/${igId}/insights`, {
    metric: 'views', period: 'day', metric_type: 'total_value', breakdown: 'media_product_type', since, until,
  }, token).data[0].total_value;
  const nf = resultsMap_(r).NON_FOLLOWER || 0;
  const vb = resultsMap_(v);
  const vt = Object.values(vb).reduce((a, x) => a + x, 0);
  return {
    reach28: r.value,
    nonFollowerShare: r.value ? round_(nf / r.value) : '',
    reelViewShare: vt ? round_((vb.REEL || 0) / vt) : '',
  };
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

function resultsMap_(tv) {
  const out = {};
  ((tv.breakdowns || [])[0] || {}).results?.forEach(x => { out[x.dimension_values[0]] = x.value; });
  return out;
}

function breakdown_(igId, token, metric, since) {
  try {
    const tv = api_(`/${igId}/insights`, {
      metric, period: 'day', metric_type: 'total_value', breakdown: 'follow_type', since, until: since + 86400,
    }, token).data[0].total_value;
    return resultsMap_(tv);
  } catch (e) { return {}; }
}

/** A열 기준 마지막 데이터 행 (오른쪽 ARRAYFORMULA 열이 getLastRow를 부풀리는 문제 회피) */
function lastRow_(sh) {
  const vals = sh.getRange('A:A').getValues();
  for (let i = vals.length - 1; i >= 0; i--) if (vals[i][0] !== '' && vals[i][0] !== null) return i + 1;
  return 1;
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
  const last = lastRow_(sh);
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
  if (append.length) sh.getRange(last + 1, 1, append.length, width).setValues(append);
  const n = last + append.length - 1;
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
