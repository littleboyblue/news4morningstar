const state = { category: '全部', source: '全部', keyword: '', dateFrom: '', dateTo: '', guidance: '', page: 1, pageSize: 20 };
const $ = s => document.querySelector(s);

/* 静态数据（由 export_static.py 生成，Pages 托管） */
let ARTICLES = [];      // data/index.json：全部文章元数据，已按发布时间倒序
let DAY_COUNTS = {};    // data/day_counts.json：{ '2026-09-24': 15, ... }

async function api(url, opts) {
  const r = await fetch(url, opts);
  return r.json();
}

function toast(msg, type = 'info', ms = 3500) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = `toast ${type}`;
  setTimeout(() => t.classList.add('hidden'), ms);
}

/* ---------- 统计与筛选侧栏 ---------- */
async function loadStats() {
  const s = await api('data/stats.json');
  $('#totalCount').textContent = s.total;
  if (s.guidance_high != null) $('#starCount').textContent = s.guidance_high;
  if (s.last_crawl) {
    $('#lastCrawl').textContent =
      `上次抓取：${s.last_crawl.started_at}（${s.last_crawl.message || s.last_crawl.status}）`;
  }
  renderFilter('#categoryList', 'category', s.by_category);
  renderFilter('#sourceList', 'source', s.by_source);
}

function renderFilter(sel, key, rows) {
  // 「其他」始终排在最后
  if (key === 'category') {
    rows = [...rows].sort((a, b) => (a.category === '其他') - (b.category === '其他'));
  }
  const map = {};
  rows.forEach(r => map[key === 'category' ? r.category : r.source] = r.count);
  const totalCount = Object.values(map).reduce((a, b) => a + b, 0);
  const ul = $(sel);
  ul.innerHTML = '';
  const all = li('全部', totalCount, state[key] === '全部');
  all.onclick = () => { state[key] = '全部'; state.page = 1; refresh(); };
  ul.appendChild(all);
  Object.entries(map).forEach(([name, count]) => {
    const el = li(name, count, state[key] === name);
    el.onclick = () => { state[key] = name; state.page = 1; refresh(); };
    ul.appendChild(el);
  });
}

function li(name, count, active) {
  const el = document.createElement('li');
  el.className = active ? 'active' : '';
  el.innerHTML = `<span>${name}</span><span class="count">${count}</span>`;
  return el;
}

/* ---------- 文章列表（客户端筛选 + 分页） ---------- */
function filteredArticles() {
  const kw = state.keyword.toLowerCase();
  return ARTICLES.filter(a =>
    (state.category === '全部' || a.category === state.category) &&
    (state.source === '全部' || a.source === state.source) &&
    (!state.guidance || a.guidance_level === state.guidance) &&
    (!state.dateFrom || (a.publish_date || '') >= state.dateFrom) &&
    (!state.dateTo || (a.publish_date || '') <= state.dateTo) &&
    (!kw || (a.title || '').toLowerCase().includes(kw) ||
            (a.summary || '').toLowerCase().includes(kw))
  );
}

function loadArticles() {
  const filtered = filteredArticles();
  const items = filtered.slice((state.page - 1) * state.pageSize,
                               state.page * state.pageSize);
  const list = $('#articleList');
  list.innerHTML = '';
  $('#emptyState').classList.toggle('hidden', filtered.length > 0);
  items.forEach(a => {
    const gBadge = a.guidance_level === '高'
      ? '<span class="tag star">⭐ 高指导价值</span>'
      : (a.guidance_level === '中' ? '<span class="tag mid">☆ 值得跟踪</span>' : '');
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div class="card-title">${esc(a.title)}</div>
      <div class="card-summary">${esc(a.summary || '（暂无摘要，点击查看详情）')}</div>
      <div class="card-meta">
        ${gBadge}
        <span class="tag">${esc(a.category)}</span>
        <span class="tag src">${esc(a.source)}</span>
        <span class="date">${a.publish_date || ''}</span>
      </div>`;
    card.onclick = () => openDetail(a.id);
    list.appendChild(card);
  });
  renderPagination(filtered.length);
}

function renderPagination(total) {
  const pages = Math.max(1, Math.ceil(total / state.pageSize));
  const box = $('#pagination');
  box.innerHTML = '';
  if (pages <= 1) return;
  const mk = (label, page, cls = '', disabled = false) => {
    const b = document.createElement('button');
    b.textContent = label; b.className = cls; b.disabled = disabled;
    b.onclick = () => { state.page = page; loadArticles(); $('#articleList').scrollTop = 0; window.scrollTo(0, 0); };
    box.appendChild(b);
  };
  mk('‹ 上一页', state.page - 1, '', state.page <= 1);
  const lo = Math.max(1, state.page - 3), hi = Math.min(pages, state.page + 3);
  for (let i = lo; i <= hi; i++) mk(i, i, i === state.page ? 'cur' : '');
  mk('下一页 ›', state.page + 1, '', state.page >= pages);
}

/* ---------- 详情 ---------- */
async function openDetail(id) {
  const a = await api('data/articles/' + id + '.json');
  $('#mTitle').textContent = a.title;
  $('#mMeta').innerHTML =
    `<span class="tag">${esc(a.category)}</span>
     <span class="tag src">${esc(a.source)}</span>
     <span class="date" style="margin-left:0">${a.publish_date || ''} 发布 · ${a.fetched_at} 收录</span>`;
  const g = $('#mGuidance');
  if (a.guidance_level) {
    const icon = a.guidance_level === '高' ? '⭐' : (a.guidance_level === '中' ? '☆' : '·');
    g.textContent = `${icon} 指导价值【${a.guidance_level}】${a.guidance_reason ? '：' + a.guidance_reason : ''}`;
    g.style.display = 'block';
    g.style.borderLeftColor = a.guidance_level === '高' ? '#f59e0b' : '#94a3b8';
    g.style.background = a.guidance_level === '高' ? '#fffbeb' : '#f8fafc';
  } else {
    g.style.display = 'none';
  }
  $('#mSummary').textContent = a.summary ? '摘要：' + a.summary : '';
  $('#mSummary').style.display = a.summary ? '' : 'none';
  const mc = $('#mContent');
  mc.innerHTML = '';
  if (a.content_html) {
    mc.innerHTML = a.content_html;  // 服务端已白名单净化
    mc.classList.add('rich');
  } else if (a.content) {
    mc.classList.remove('rich');
    a.content.split(/\n+/).filter(Boolean).forEach(para => {
      const p = document.createElement('p');
      const isHead = /^([一二三四五六七八九十]+[、．.]|（[一二三四五六七八九十]+）)/.test(para);
      p.className = isHead ? 'sec-head' : '';
      p.textContent = para;
      mc.appendChild(p);
    });
  } else {
    mc.classList.remove('rich');
    mc.textContent = '（未能抓取到正文，请点击下方按钮查看原文）';
  }
  $('#mLink').href = a.url;
  // 附件下载（仿政府网站样式，新窗口打开）
  const at = $('#mAttach');
  at.innerHTML = '';
  let atts = [];
  try { atts = JSON.parse(a.attachments || '[]'); } catch (e) {}
  if (atts.length) {
    at.style.display = 'block';
    const t = document.createElement('div');
    t.className = 'm-attach-title';
    t.textContent = `附件下载（${atts.length}）`;
    at.appendChild(t);
    atts.forEach(x => {
      if (!/^https?:/.test(x.url)) return;
      const link = document.createElement('a');
      link.href = x.url;
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = '📎 ' + x.name;
      at.appendChild(link);
    });
  } else {
    at.style.display = 'none';
  }
  $('#modal').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  document.querySelector('.modal-scroll').scrollTop = 0;
}
function closeModal() {
  $('#modal').classList.add('hidden');
  document.body.style.overflow = '';
}

function esc(s) {
  return (s || '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function refresh() { loadStats(); loadArticles(); renderCalendar(); }

/* ---------- 侧栏月历 ---------- */
const cal = { month: null };  // 'YYYY-MM'

async function renderCalendar() {
  if (!cal.month) {
    const now = new Date();
    cal.month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  }
  const [y, m] = cal.month.split('-').map(Number);
  $('#calTitle').textContent = `${y} 年 ${m} 月`;
  const days = DAY_COUNTS;
  const lead = (new Date(y, m - 1, 1).getDay() + 6) % 7;  // 周一开头
  const dim = new Date(y, m, 0).getDate();
  const today = new Date();
  const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
  const box = $('#calDays');
  box.innerHTML = '';
  for (let i = 0; i < lead; i++) {
    const s = document.createElement('span');
    s.className = 'cal-day dim';
    box.appendChild(s);
  }
  for (let d = 1; d <= dim; d++) {
    const ds = `${cal.month}-${String(d).padStart(2, '0')}`;
    const el = document.createElement('span');
    el.className = 'cal-day';
    el.textContent = d;
    if (days[ds]) { el.classList.add('has-data'); el.title = `${days[ds]} 条记录`; }
    if (ds === todayStr) el.classList.add('today');
    if (state.dateFrom === ds && state.dateTo === ds) el.classList.add('sel');
    el.onclick = () => selectDate(ds);
    box.appendChild(el);
  }
}

function selectDate(ds) {
  if (state.dateFrom === ds && state.dateTo === ds) { clearDateFilter(); return; }
  state.dateFrom = ds;
  state.dateTo = ds;
  $('#dateFrom').value = ds;
  $('#dateTo').value = ds;
  state.page = 1;
  loadArticles();
  renderCalendar();
}

function clearDateFilter() {
  state.dateFrom = '';
  state.dateTo = '';
  $('#dateFrom').value = '';
  $('#dateTo').value = '';
  state.page = 1;
  loadArticles();
  renderCalendar();
}

$('#calPrev').onclick = () => {
  const [y, m] = cal.month.split('-').map(Number);
  cal.month = m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, '0')}`;
  renderCalendar();
};
$('#calNext').onclick = () => {
  const [y, m] = cal.month.split('-').map(Number);
  cal.month = m === 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, '0')}`;
  renderCalendar();
};
$('#clearDate').onclick = clearDateFilter;
$('#starOnly').addEventListener('change', e => {
  state.guidance = e.target.checked ? '高' : '';
  state.page = 1;
  loadArticles();
});

/* ---------- 事件 ---------- */
$('#searchBtn').onclick = () => {
  state.keyword = $('#searchInput').value.trim();
  state.dateFrom = $('#dateFrom').value;
  state.dateTo = $('#dateTo').value;
  state.page = 1;
  loadArticles();
  renderCalendar();  // 同步选中态
};
$('#searchInput').addEventListener('keydown', e => { if (e.key === 'Enter') $('#searchBtn').click(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

/* 启动：加载静态数据后渲染 */
(async () => {
  try {
    [ARTICLES, DAY_COUNTS] = await Promise.all([
      api('data/index.json'),
      api('data/day_counts.json'),
    ]);
  } catch (e) {
    toast('数据加载失败，请稍后再试', 'err', 6000);
    return;
  }
  refresh();
})();
