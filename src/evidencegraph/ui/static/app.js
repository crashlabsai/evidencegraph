/* Evidencegraph viewer: a read-only reading surface over one case.
   No framework and no build step. All text is inserted as text nodes, never as HTML. */
'use strict';

const ENTITY_RE = /^e-[0-9a-f]{16,}$/;
const CITATION_RE = /^ref-[0-9a-f]{16,}$/;
const RELATION_RE = /^r-[0-9a-f]{16,}$/;
const WITNESS_RE = /^w-[0-9a-f]{12,}$/;
const HASH_RE = /^[0-9a-f]{64}$/;
const TIME_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

const OUTCOMES = {
  supported: { glyph: '✓', cls: 'good' },
  ambiguous: { glyph: '?', cls: 'warning' },
  contradicted: { glyph: '✕', cls: 'critical' },
  unmatched: { glyph: '∅', cls: 'serious' },
  not_assessable: { glyph: '–', cls: 'neutral' },
};
const STATUS_CLASSES = {
  exact: 'good', within_tolerance: 'warning', differs: 'critical', not_computable: 'neutral',
  independent: 'good', same: 'neutral', unknown: 'warning',
};
const GLYPHS = { good: '✓', warning: '?', critical: '✕', serious: '∅', neutral: '–' };

const state = { caseInfo: null, title: 'Evidencegraph' };

/* ---------- DOM helpers ---------- */
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === 'class') el.className = value;
      else if (key === 'style') Object.assign(el.style, value);
      else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
      else if (value === true) el.setAttribute(key, '');
      else el.setAttribute(key, String(value));
    }
  }
  append(el, children);
  return el;
}
function append(el, children) {
  for (const child of children) {
    if (child == null || child === false) continue;
    if (Array.isArray(child)) append(el, child);
    else el.append(child.nodeType ? child : String(child));
  }
  return el;
}
function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
function $(selector) { return document.querySelector(selector); }
function text(cls, ...children) { return h('span', { class: cls }, ...children); }
function mono(value) { return h('span', { class: 'mono' }, value); }
function code(value) { return h('code', null, value); }

async function api(path, options) {
  const response = await fetch(path, options);
  const raw = await response.text();
  let data = null;
  try { data = raw ? JSON.parse(raw) : null; } catch { data = { error: raw }; }
  if (!response.ok) {
    const error = new Error((data && data.error) || `${response.status} ${response.statusText}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

/* ---------- formatting ---------- */
function titleCase(key) {
  return String(key).replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
}
function shortId(id) { return id.length > 14 ? id.slice(0, 14) + '…' : id; }
function fmtInt(n) { return Number(n).toLocaleString('en-US'); }
function fmtNum(value, key) {
  if (Number.isInteger(value)) return fmtInt(value);
  const k = String(key || '');
  if (/share|rate|precision|recall|estimate|fraction|level/.test(k) && value >= 0 && value <= 1) {
    return (value * 100).toFixed(1) + '%';
  }
  return String(Number(value.toFixed(4)));
}
function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KiB`;
  return `${(n / 1024 / 1024).toFixed(1)} MiB`;
}
function pct(x) { return (x * 100).toFixed(1) + '%'; }

function copyButton(value, label = 'copy') {
  return h('button', {
    class: 'btn small copy', type: 'button', title: 'Copy to clipboard',
    onclick: async (event) => {
      event.stopPropagation();
      try { await navigator.clipboard.writeText(value); event.target.textContent = 'copied'; }
      catch { event.target.textContent = 'copy failed'; }
      setTimeout(() => { event.target.textContent = label; }, 1200);
    },
  }, label);
}
function hashSpan(value) {
  return h('span', null, h('span', { class: 'hash', title: value }, value.slice(0, 12) + '…'), copyButton(value));
}

/* ---------- status vocabulary ---------- */
function badge(value, meanings) {
  const outcome = OUTCOMES[value];
  const cls = outcome ? outcome.cls : (STATUS_CLASSES[value] || 'neutral');
  const glyph = outcome ? outcome.glyph : GLYPHS[cls];
  return h('span', { class: `badge ${cls}`, title: (meanings && meanings[value]) || '' },
    h('span', { class: 'glyph', 'aria-hidden': 'true' }, glyph), String(value).replace(/_/g, ' '));
}
function methodChip(method, ctx) {
  const hint = ctx && ctx.hints && ctx.hints[method];
  return h('span', { class: 'chip method', title: hint || method }, method, hint ? text('dim', ' ⓘ') : null);
}
function entityLink(id, ctx) {
  const meta = ctx && ctx.entities && ctx.entities[id];
  const label = meta ? [h('span', { class: 'kind' }, meta.subkind), meta.natural_key] : [shortId(id)];
  return h('a', { class: 'entity', href: '#/entities/' + encodeURIComponent(id), title: id }, ...label);
}
function witnessLink(id, ctx) {
  const meta = ctx && ctx.witnesses && ctx.witnesses[id];
  return h('a', { class: 'entity', href: '#/witnesses/' + encodeURIComponent(id), title: id },
    meta ? [h('span', { class: 'kind' }, meta.trust_domain), meta.filename] : shortId(id));
}
function citeLabel(id, ctx) {
  const c = ctx && ctx.citations && ctx.citations[id];
  if (!c) return shortId(id);
  const w = ctx.witnesses && ctx.witnesses[c.witness_id];
  const file = w ? w.filename : shortId(c.witness_id);
  switch (c.locator_kind) {
    case 'jsonl_line': case 'csv_row': return `${file}:${c.locator}`;
    case 'json_path': return `${file} ${c.locator}`;
    case 'event': return `${c.transcript_id} · event ${c.locator.slice(0, 8)}`;
    case 'message': return `${c.transcript_id} · message ${c.locator.slice(0, 8)}`;
    case 'file': return file;
    default: return shortId(id);
  }
}
function citeChip(id, ctx) {
  const preview = ctx && ctx.citations && ctx.citations[id] && ctx.citations[id].preview;
  return h('button', {
    class: 'chip cite', type: 'button', title: preview ? `${id}\n${preview}` : id,
    onclick: (event) => { event.stopPropagation(); openCitation(id); },
  }, h('span', { class: 'chip-glyph', 'aria-hidden': 'true' }, '¶'), citeLabel(id, ctx));
}

/* ---------- generic value rendering ---------- */
function isObj(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
function isRelation(row) { return isObj(row) && 'relation_id' in row && 'subject_id' in row && 'object_id' in row; }

function cell(key, value, ctx) {
  const k = String(key || '').toLowerCase();
  if (value == null) return text('dim', '—');
  if (typeof value === 'boolean') return text(value ? 'yes' : 'no', value ? '✓ yes' : '✕ no');
  if (typeof value === 'number') return text('num', fmtNum(value, k));
  if (typeof value === 'string') {
    if (ENTITY_RE.test(value)) return entityLink(value, ctx);
    if (CITATION_RE.test(value)) return citeChip(value, ctx);
    if (k === 'outcome' || k === 'status' || k === 'trust_domain_relation' || k === 'configuration' || k === 'analyzer') return badge(value, ctx && ctx.meanings);
    if (k === 'method') return methodChip(value, ctx);
    if (HASH_RE.test(value)) return hashSpan(value);
    if (WITNESS_RE.test(value)) return witnessLink(value, ctx);
    if (RELATION_RE.test(value)) return h('a', { class: 'entity', href: '#/relations?q=' + encodeURIComponent(value), title: value }, shortId(value));
    if (k.endsWith('_id') || k === 'id' || k === 'locator' || TIME_RE.test(value)) return mono(value);
    if ((value.startsWith('{') || value.startsWith('[')) && value.length > 1) {
      try { return renderValue(key, JSON.parse(value), ctx); } catch { /* plain string */ }
    }
    if (value.length > 160 || value.includes('\n')) {
      return h('details', { class: 'inline-details' }, h('summary', null, value.slice(0, 100).replace(/\s+/g, ' ') + '…'), h('pre', { class: 'wrap' }, value));
    }
    return h('span', null, value);
  }
  if (Array.isArray(value)) {
    if (!value.length) return text('dim', '—');
    if (value.every(v => typeof v === 'string')) {
      if (value.some(v => v.length > 60)) return h('ul', null, value.map(v => h('li', null, v)));
      return h('span', { class: 'chips' }, value.map(v => cell(k.replace(/s$/, ''), v, ctx)));
    }
    if (value.every(v => typeof v === 'number')) return text('num', value.map(v => fmtNum(v, k)).join(' – '));
    return jsonDetails(value);
  }
  return jsonDetails(value);
}

function jsonDetails(value) {
  const summary = Array.isArray(value) ? `[${value.length} items]` : `{${Object.keys(value).length} fields}`;
  return h('details', { class: 'inline-details' }, h('summary', null, summary), jsonTree(value, 1));
}

function jsonTree(value, depth = 0) {
  if (value === null) return text('json z', 'null');
  if (typeof value === 'string') {
    return value.length > 400 ? h('details', null, h('summary', null, text('s', value.slice(0, 80) + '…')), h('pre', { class: 'wrap' }, value)) : text('s', JSON.stringify(value));
  }
  if (typeof value === 'number') return text('n', String(value));
  if (typeof value === 'boolean') return text('b', String(value));
  const entries = Array.isArray(value) ? value.map((v, i) => [i, v]) : Object.entries(value);
  if (!entries.length) return text('z', Array.isArray(value) ? '[]' : '{}');
  const list = h('ul', null, entries.map(([k, v]) => h('li', null, text('k', String(k)), ': ', jsonTree(v, depth + 1))));
  if (depth < 2) return h('div', { class: depth === 0 ? 'json' : '' }, list);
  return h('details', { open: depth < 3 }, h('summary', null, Array.isArray(value) ? `[${entries.length}]` : `{${entries.length}}`), list);
}

function renderValue(key, value, ctx) {
  const k = String(key || '');
  if (value == null) return text('dim', '—');
  if (Array.isArray(value)) {
    if (!value.length) return text('dim', 'none');
    if (k === 'coverage' && isObj(value[0]) && 'n_population' in value[0]) return coverageCards(value, ctx);
    if (isObj(value[0])) return isRelation(value[0]) ? relationTable(value, ctx) : genericTable(value, ctx);
    return cell(k, value, ctx);
  }
  if (isObj(value)) {
    const keys = Object.keys(value);
    const values = Object.values(value);
    if (keys.length && keys.every(x => x in OUTCOMES) && values.every(v => typeof v === 'number')) return outcomeStrip(value, ctx);
    if (keys.length && values.every(isObj)) {
      return genericTable(keys.map(x => ({ [k.replace(/s$/, '') || 'key']: x, ...value[x] })), ctx);
    }
    return kvTable(value, ctx);
  }
  return cell(k, value, ctx);
}

function kvTable(obj, ctx) {
  return h('div', { class: 'table-wrap' }, h('table', { class: 'data kv' }, h('tbody', null,
    Object.entries(obj).map(([k, v]) => h('tr', null, h('td', null, titleCase(k)), h('td', null, isObj(v) || (Array.isArray(v) && v.some(isObj)) ? renderValue(k, v, ctx) : cell(k, v, ctx)))))));
}

function genericTable(rows, ctx, options = {}) {
  const columns = options.columns || [...new Set(rows.flatMap(r => Object.keys(r)))].filter(c => !(options.hide || []).includes(c));
  const numeric = new Set(columns.filter(c => rows.every(r => r[c] == null || typeof r[c] === 'number')));
  return h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
    h('thead', null, h('tr', null, columns.map(c => h('th', { class: numeric.has(c) ? 'num' : null }, titleCase(c))))),
    h('tbody', null, rows.map(row => h('tr', null, columns.map(c => h('td', { class: numeric.has(c) ? 'num' : null }, row[c] && row[c].nodeType ? row[c] : cell(c, row[c], ctx))))))));
}

function relationTable(rows, ctx) {
  const kinds = new Set(rows.map(r => r.kind));
  const columns = [];
  if (kinds.size > 1) columns.push(['Kind', r => h('span', null, r.kind)]);
  columns.push(['Subject', r => subjectCell(r.subject_id, r.subject_subkind, r.subject_key, ctx)]);
  columns.push(['Outcome', r => badge(r.outcome, ctx && ctx.meanings)]);
  columns.push(['Object', r => {
    if (r.subject_id === r.object_id && (!r.candidates || !r.candidates.length)) return text('dim', 'no matching action');
    const link = subjectCell(r.object_id, r.object_subkind, r.object_key, ctx);
    if (r.candidates && r.candidates.length > 1) {
      return h('div', null, link, h('div', { class: 'small dim' }, `${r.candidates.length} candidates: `, h('span', { class: 'chips' }, r.candidates.map(c => entityLink(c, ctx)))));
    }
    return link;
  }]);
  columns.push(['Method', r => methodChip(r.method, ctx)]);
  columns.push(['Trust', r => badge(r.trust_domain_relation || 'unknown')]);
  columns.push(['Rationale', r => clampText(r.rationale)]);
  columns.push(['Cites', r => h('span', { class: 'chips' }, (r.citation_ids || []).map(id => citeChip(id, ctx)))]);
  return h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
    h('thead', null, h('tr', null, columns.map(([name]) => h('th', null, name)))),
    h('tbody', null, rows.map(r => h('tr', { title: `${r.relation_id} · run ${r.run_id}` }, columns.map(([, render]) => h('td', null, render(r))))))));
}
function subjectCell(id, subkind, key, ctx) {
  if (subkind && key) return h('a', { class: 'entity', href: '#/entities/' + encodeURIComponent(id), title: id }, h('span', { class: 'kind' }, subkind), key);
  return entityLink(id, ctx);
}
function clampText(value) {
  const el = h('div', { class: 'clamp', title: 'Click to expand', onclick: () => el.classList.toggle('open') }, value);
  return el;
}

function outcomeStrip(counts, ctx) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const order = Object.keys(OUTCOMES).filter(k => counts[k]);
  const strip = h('div', { class: 'strip', role: 'img', 'aria-label': order.map(k => `${k} ${counts[k]}`).join(', ') },
    order.map(k => h('div', { class: `seg ${OUTCOMES[k].cls}`, style: { flexGrow: String(counts[k]) }, title: `${k}: ${counts[k]} of ${total}` })));
  const legend = h('div', { class: 'legend' }, order.map(k => h('span', null, h('span', { class: `swatch seg ${OUTCOMES[k].cls}` }), badge(k, ctx && ctx.meanings), ' ', text('num', fmtInt(counts[k])))), text('dim num', `${fmtInt(total)} total`));
  return h('div', null, strip, legend);
}

function coverageCards(rows, ctx) {
  return h('div', null, rows.map(row => {
    const population = typeof row.population === 'string' ? JSON.parse(row.population) : row.population;
    const estimate = row.estimate;
    const interval = row.interval;
    const meter = h('div', { class: 'meter', role: 'img', 'aria-label': `estimate ${estimate == null ? 'unavailable' : pct(estimate)}` });
    if (interval) meter.append(h('div', { class: 'band', style: { left: pct(interval[0]), width: pct(Math.max(0, interval[1] - interval[0])) } }));
    if (estimate != null) meter.append(h('div', { class: 'fill', style: { width: pct(estimate) } }), h('div', { class: 'mark', style: { left: `calc(${pct(estimate)} - 1px)` } }));
    return h('div', { class: 'coverage-card' },
      h('div', { class: 'kicker' }, `${row.kind} · sampling unit: ${row.sampling_unit}`, population && population.id ? ` · population ${population.id}` : ''),
      h('div', { class: 'headline-num' }, `${fmtInt(row.n_supported)} of ${fmtInt(row.n_sampled)} = ${estimate == null ? 'unavailable' : pct(estimate)}`),
      meter,
      h('p', { class: 'small' }, interval ? `${Math.round(row.interval_level * 100)}% interval ${pct(interval[0])} – ${pct(interval[1])}. ` : 'No interval available. ', row.design),
      h('p', { class: 'small muted' }, `Ambiguous: ${row.ambiguous_handling}. Not assessable: ${row.not_assessable_handling}. `, (row.exclusions || []).join('; ')),
      row.assumptions && row.assumptions.length ? h('ul', { class: 'small' }, row.assumptions.map(a => h('li', null, a))) : null,
      population && population.description ? h('p', { class: 'small dim' }, population.description) : null,
      h('p', { class: 'small dim' }, 'Reference witness ', witnessLink(row.reference_source, ctx)));
  }));
}

function callout(cls, title, ...body) {
  return h('div', { class: `callout ${cls}` }, h('div', { class: 'callout-title' }, GLYPHS[cls] ? text('glyph', GLYPHS[cls]) : null, title), ...body);
}
function errorBox(error) {
  return callout('critical', 'Request failed', h('p', null, error.message || String(error)));
}
function emptyState(title, message, actions = []) {
  return h('div', { class: 'empty' }, h('h2', null, title), message ? h('p', null, message) : null,
    actions.length ? h('div', { class: 'actions' }, actions.map(([label, href]) => h('a', { class: 'btn', href }, label))) : null);
}
function tile(label, value, sub, cls) {
  return h('div', { class: 'tile' }, h('div', { class: 'label' }, label), h('div', { class: `value ${cls || ''}` }, value), sub ? h('div', { class: 'sub' }, sub) : null);
}

/* ---------- routing ---------- */
function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/docket';
  const [path, query] = raw.split('?');
  const params = {};
  for (const [k, v] of new URLSearchParams(query || '')) params[k] = v;
  return { path, params };
}
function navigate(path, params) {
  const query = new URLSearchParams(Object.entries(params || {}).filter(([, v]) => v !== '' && v != null)).toString();
  location.hash = '#' + path + (query ? '?' + query : '');
}
function startPage(title, route) {
  document.title = `${title} · ${state.title}`;
  for (const link of document.querySelectorAll('.nav a')) link.classList.toggle('active', link.dataset.route === route);
  const main = clear($('#main'));
  main.append(h('div', { class: 'spinner' }, 'Loading…'));
  return main;
}
const ROUTES = [
  [/^\/?$/, () => pageDocket()],
  [/^\/docket(?:\/([^/]+))?$/, m => pageDocket(m[1])],
  [/^\/witnesses$/, () => pageWitnesses()],
  [/^\/witnesses\/([^/]+)$/, m => pageWitness(decodeURIComponent(m[1]))],
  [/^\/relations$/, (m, params) => pageRelations(params)],
  [/^\/entities$/, (m, params) => pageEntities(params)],
  [/^\/entities\/([^/]+)$/, m => pageEntity(decodeURIComponent(m[1]))],
  [/^\/query$/, () => pageQuery()],
  [/^\/case$/, () => pageCase()],
  [/^\/cite\/([^/]+)$/, async m => { await pageDocket(); openCitation(decodeURIComponent(m[1])); }],
];
async function route() {
  const { path, params } = parseHash();
  for (const [pattern, handler] of ROUTES) {
    const match = path.match(pattern);
    if (!match) continue;
    try { await handler(match, params); }
    catch (error) { clear($('#main')).append(errorBox(error)); }
    return;
  }
  clear($('#main')).append(emptyState('No such page', path, [['Docket', '#/docket']]));
}

/* ---------- docket ---------- */
async function pageDocket(focus) {
  const main = startPage('Docket', 'docket');
  let data;
  try { data = await api('/api/docket'); } catch (error) {
    if (error.status === 404) {
      clear(main).append(emptyState('No docket rendered yet', error.message, [['See the case pipeline', '#/case'], ['Browse witnesses', '#/witnesses']]));
      return;
    }
    throw error;
  }
  const { docket, relevant } = data;
  const ctx = { entities: data.entity_index, citations: data.citation_index, witnesses: data.witness_index, meanings: data.outcome_meanings, hints: data.method_hints };
  const showAll = localStorage.getItem('eg.showAll') === '1';
  const answers = docket.answers.filter(a => showAll || relevant.includes(a.question_id));
  clear(main);
  main.append(h('header', { class: 'page-head' },
    h('div', null, h('h1', null, docket.title),
      h('p', { class: 'muted lede' }, 'Evidence docket · prototype · source assertions and independently corroborated actions remain distinct. Each supported line lists the declarations it rests on; treat them as part of the finding.')),
    h('div', { class: 'page-meta' },
      data.rendered_at ? text('small muted', 'Rendered ', mono(data.rendered_at)) : null,
      data.verified
        ? h('span', { class: 'badge good', title: 'docket.json bytes match the hash recorded in manifest.json' }, text('glyph', '✓'), 'docket.json hash verified')
        : h('span', { class: 'badge critical' }, text('glyph', '✕'), 'docket.json hash differs from manifest'),
      h('a', { class: 'btn small', href: '/api/reports/DOCKET.md' }, 'DOCKET.md'),
      h('a', { class: 'btn small', href: '/api/reports/docket.json' }, 'docket.json'))));
  if (isStale(data)) main.append(staleCallout(data.stale, data.stale_analyzer));

  const counts = {};
  for (const a of answers) counts[a.outcome] = (counts[a.outcome] || 0) + 1;
  main.append(h('section', { class: 'tiles', 'aria-label': 'Question statuses' },
    tile('Questions', answers.length, showAll ? 'all sixteen frozen questions' : 'relevant to the ingested evidence'),
    Object.keys(OUTCOMES).map(o => tile(badge(o, ctx.meanings), counts[o] || 0, ctx.meanings[o].split('.')[0] + '.'))));

  const toggle = h('input', { type: 'checkbox', checked: showAll || null, onchange: (e) => { localStorage.setItem('eg.showAll', e.target.checked ? '1' : '0'); route(); } });
  main.append(h('div', { class: 'toolbar' },
    h('label', null, toggle, 'Show all sixteen questions, including those the ingested evidence cannot address'),
    h('button', { class: 'btn small', type: 'button', onclick: () => { for (const d of main.querySelectorAll('details.question')) d.open = true; } }, 'Expand all'),
    h('button', { class: 'btn small', type: 'button', onclick: () => { for (const d of main.querySelectorAll('details.question')) d.open = false; } }, 'Collapse all')));

  const list = h('section', { class: 'questions' });
  for (const a of answers) list.append(questionCard(a, data.questions[a.question_id], ctx, a.question_id === focus));
  main.append(list);

  if (docket.validation) main.append(validationSection(docket.validation));
  main.append(witnessInventory(docket.witnesses));
  main.append(gapsSection(answers));
  main.append(h('p', { class: 'muted small' }, 'Click any citation to resolve it against the immutable snapshot with its hash re-verified, or run ', code('eg cite CASE REF_ID'), '. The full graph is queryable on the Query page.'));
  if (focus) {
    const target = document.getElementById('q-' + focus);
    if (target) target.scrollIntoView({ block: 'start' });
  }
}

function questionCard(answer, question, ctx, open) {
  const details = h('details', { class: 'question', id: 'q-' + answer.question_id, open: open || null });
  details.append(h('summary', null,
    h('span', { class: 'qid' }, answer.question_id),
    h('span', { class: 'qlabel' }, question ? question.label : ''),
    badge(answer.outcome, ctx.meanings),
    h('span', { class: 'headline' }, answer.headline)));
  const body = h('div', { class: 'qbody' });
  if (question) body.append(h('p', { class: 'question-text' }, question.text));
  body.append(h('p', { class: 'small muted' }, h('strong', null, answer.outcome.replace(/_/g, ' ')), ': ', ctx.meanings[answer.outcome]));
  if (answer.assumptions && answer.assumptions.length) {
    body.append(callout('warning', 'Assumptions this line rests on', h('ul', null, answer.assumptions.map(a => h('li', null, a)))));
  }
  if (answer.gaps && answer.gaps.length) {
    body.append(callout('neutral', 'Gaps: what is missing or would change the answer', h('ul', null, answer.gaps.map(g => h('li', null, g)))));
  }
  if (answer.numbers && Object.keys(answer.numbers).length) {
    body.append(h('h3', null, 'Evidence'), renderNumbers(answer.numbers, ctx));
  }
  if (answer.coverage_ids && answer.coverage_ids.length) {
    body.append(h('p', { class: 'small muted' }, 'Coverage estimates: ', h('span', { class: 'chips' }, answer.coverage_ids.map(id => h('span', { class: 'chip' }, id)))));
  }
  if (answer.citation_ids && answer.citation_ids.length) {
    body.append(h('div', { class: 'cites' }, h('span', { class: 'label' }, 'Citations'), answer.citation_ids.map(id => citeChip(id, ctx))));
  }
  details.append(body);
  details.addEventListener('toggle', () => { if (details.open) history.replaceState(null, '', '#/docket/' + answer.question_id); });
  return details;
}

function renderNumbers(numbers, ctx) {
  const wrap = h('div', { class: 'numbers' });
  for (const [key, value] of Object.entries(numbers)) {
    wrap.append(h('div', { class: 'number-block' }, h('h4', null, titleCase(key)), renderValue(key, value, ctx)));
  }
  return wrap;
}

function validationSection(validation) {
  const section = h('section', null, h('h2', null, 'Validation against private truth'),
    h('p', { class: 'small muted' }, 'A software check on this staged fixture, scored against host records the graph never reads. Not a real-world accuracy estimate.'));
  const tiles = [];
  if (validation.produced) {
    const p = validation.produced;
    tiles.push(tile('Produced precision', fmtNum(p.precision, 'precision'), `${p.correct} correct of ${p.supported} supported`));
    tiles.push(tile('Produced recall', fmtNum(p.recall, 'recall'), `${p.identifiable_with_captured_transcripts} identifiable with captured transcripts`));
    tiles.push(tile('Confident errors', p.confident_errors, 'wrong supported attributions'));
  }
  if (validation.spoofed) {
    const s = validation.spoofed;
    tiles.push(tile('Fabricated receipts', `${s.contradicted} / ${s.captured}`, `contradicted of captured; ${s.false_positives} false positives, ${s.missed} missed`));
  }
  if (tiles.length) section.append(h('div', { class: 'tiles' }, tiles));
  section.append(kvTable(validation, {}));
  section.append(h('p', { class: 'small' }, h('a', { href: '/api/reports/validation.json' }, 'validation.json')));
  return section;
}

function witnessInventory(witnesses) {
  const groups = new Map();
  for (const w of witnesses) {
    const category = HASH_RE.test(w.filename) ? 'Content-addressed artifacts' : w.filename.endsWith('.eval') ? 'Inspect transcripts' : 'Source files';
    const key = `${w.trust_domain}|${category}`;
    if (!groups.has(key)) groups.set(key, { trust_domain: w.trust_domain, category, names: [] });
    groups.get(key).names.push(w.filename);
  }
  const rows = [...groups.values()].sort((a, b) => a.trust_domain.localeCompare(b.trust_domain) || a.category.localeCompare(b.category)).map(g => ({
    sources: g.category === 'Source files' && g.names.length <= 3 ? g.names.join(', ') : g.category,
    trust_domain: g.trust_domain,
    files: g.names.length,
  }));
  return h('section', null, h('h2', null, 'Witness inventory'),
    h('p', { class: 'small muted' }, `${witnesses.length} witnesses, each hashed before parsing. `, h('a', { href: '#/witnesses' }, 'Full inventory with hashes and coverage claims')),
    genericTable(rows, {}));
}

function gapsSection(answers) {
  const gaps = new Map();
  for (const a of answers) for (const gap of a.gaps || []) {
    if (!gaps.has(gap)) gaps.set(gap, []);
    gaps.get(gap).push(a.question_id);
  }
  if (!gaps.size) return null;
  return h('section', null, h('h2', null, 'Known gaps'),
    h('ul', null, [...gaps.entries()].map(([gap, ids]) => h('li', null, gap, ' ', text('dim small', ids.join(', '))))));
}

function staleCallout(stale, staleAnalyzer) {
  const wrap = h('div');
  if (stale && stale.length) {
    wrap.append(callout('critical', 'Case declarations changed after these stages ran',
      h('p', null, 'Trust and clock declarations are inputs to every conclusion. Re-run ', code('eg ingest'), ' and the derived stages before trusting, rendering or exporting this docket.'),
      h('p', { class: 'small' }, 'Stale stages: ', h('span', { class: 'chips' }, stale.map(s => h('span', { class: 'chip' }, s))))));
  }
  if (staleAnalyzer && staleAnalyzer.length) {
    wrap.append(callout('critical', 'The analyzer changed after these stages ran',
      h('p', null, 'An upgraded rule must not present conclusions computed by the old one. Re-run ', code('eg ingest'), ' and the derived stages with the current analyzer before trusting, rendering or exporting this docket.'),
      h('p', { class: 'small' }, 'Stages from an earlier analyzer: ', h('span', { class: 'chips' }, staleAnalyzer.map(s => h('span', { class: 'chip' }, s))))));
  }
  return wrap;
}
function isStale(info) { return (info.stale && info.stale.length) || (info.stale_analyzer && info.stale_analyzer.length); }

/* ---------- citation drawer ---------- */
async function openCitation(id) {
  const drawer = $('#drawer');
  drawer.hidden = false;
  $('#backdrop').hidden = false;
  $('#drawer-title').textContent = 'Citation';
  const body = clear($('#drawer-body'));
  body.append(h('div', { class: 'spinner' }, 'Resolving against the immutable snapshot…'));
  $('#drawer-close').focus();
  try {
    const data = await api('/api/citations/' + encodeURIComponent(id));
    clear(body).append(citationView(data));
  } catch (error) {
    clear(body).append(errorBox(error));
  }
}
function closeDrawer() {
  $('#drawer').hidden = true;
  $('#backdrop').hidden = true;
}
function citationView(data) {
  const { citation, witness } = data;
  const verification = data.verified
    ? callout('good', 'Hash re-verified', h('p', { class: 'small' }, 'The snapshot still has the size and SHA-256 recorded at acquisition, and the cited fragment hashes to the value stored in the citation.'))
    : callout('critical', 'Verification failed', h('p', { class: 'small' }, data.error));
  const fields = h('table', { class: 'data kv' }, h('tbody', null,
    row('Citation', mono(citation.citation_id), copyButton(citation.citation_id)),
    row('Witness', witnessLink(witness.witness_id, { witnesses: { [witness.witness_id]: witness } }), ' ', text('dim small', witness.witness_id)),
    row('Trust domain', witness.trust_domain),
    row('Adapter', `${witness.adapter} (${witness.kind})`),
    row('Locator', `${citation.locator_kind} `, mono(citation.locator)),
    citation.transcript_id ? row('Transcript', mono(citation.transcript_id)) : null,
    row('Fragment SHA-256', hashSpan(citation.content_sha256)),
    row('Witness SHA-256', hashSpan(witness.sha256)),
    row('Snapshot', mono(witness.snapshot_path)),
    row('Origin', mono(witness.origin))));
  const source = data.source_row == null
    ? h('p', { class: 'muted' }, 'Source bytes withheld because verification failed.')
    : h('div', null, jsonTree(data.source_row), h('details', null, h('summary', { class: 'small' }, 'Raw JSON'), h('pre', { class: 'wrap' }, JSON.stringify(data.source_row, null, 2))));
  return h('div', null,
    verification,
    h('div', { class: 'table-wrap' }, fields),
    h('h3', null, 'Source fragment'),
    citation.preview ? h('p', { class: 'small dim' }, 'Preview at acquisition: ', mono(citation.preview)) : null,
    source,
    h('h3', null, 'Used by'),
    data.entities.length ? h('ul', null, data.entities.map(e => h('li', null, entityLink(e.entity_id, { entities: { [e.entity_id]: e } })))) : h('p', { class: 'muted small' }, 'No entity is built directly on this fragment.'),
    data.relation_count ? h('p', { class: 'small' }, h('a', { href: '#/relations?citation=' + encodeURIComponent(citation.citation_id), onclick: closeDrawer }, `${data.relation_count} relation${data.relation_count === 1 ? '' : 's'} cite this fragment`)) : null,
    h('h3', null, 'Reproduce'),
    h('pre', { class: 'wrap' }, data.command),
    h('p', { class: 'small' }, copyButton(data.command, 'copy command'), ' ', h('a', { class: 'btn small', href: '#/cite/' + encodeURIComponent(citation.citation_id) }, 'permalink')));
}
function row(label, ...values) { return h('tr', null, h('td', null, label), h('td', null, ...values)); }

/* ---------- witnesses ---------- */
async function pageWitnesses() {
  const main = startPage('Witnesses', 'witnesses');
  const data = await api('/api/witnesses');
  clear(main);
  main.append(h('header', { class: 'page-head' }, h('div', null, h('h1', null, 'Witnesses'),
    h('p', { class: 'muted lede' }, 'One witness per acquired evidence file, hashed and snapshotted before anything parsed it. Only pairs of domains declared independent can yield a supported cross-source match.'))));
  const byDomain = new Map();
  for (const w of data.witnesses) {
    if (!byDomain.has(w.trust_domain)) byDomain.set(w.trust_domain, []);
    byDomain.get(w.trust_domain).push(w);
  }
  if (!byDomain.size) { main.append(emptyState('No witnesses acquired', 'Add evidence with eg witness add.', [['Case', '#/case']])); return; }
  for (const [domain, witnesses] of byDomain) {
    const declared = data.trust_domains[domain];
    main.append(h('h2', null, domain, declared ? text('muted', ` · ${declared.label}`) : null));
    if (declared) {
      const relations = Object.entries(declared.related_to || {});
      main.append(h('p', { class: 'small muted' },
        relations.length ? ['Declared ', relations.map(([other, rel], i) => [i ? '; ' : '', badge(rel), ` of ${other}`])] : 'No trust relation declared with another domain.',
        declared.authentic_records ? [' · ', badge('supported'), ' declared authentic: its recorder was outside the investigated actors\' control'] : ' · not declared authentic',
        declared.exclusive_receipt_tokens ? [' · ', badge('supported'), ' exclusive receipt tokens declared'] : ' · receipt tokens not declared exclusive'));
    }
    main.append(h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
      h('thead', null, h('tr', null, ['File', 'Kind', 'Adapter', 'Size', 'Citations', 'SHA-256', 'Acquired', 'Coverage claim', 'Ingested'].map(c => h('th', null, c)))),
      h('tbody', null, witnesses.map(w => h('tr', { class: 'clickable', onclick: () => navigate('/witnesses/' + encodeURIComponent(w.witness_id)) },
        h('td', null, h('a', { href: '#/witnesses/' + encodeURIComponent(w.witness_id), title: w.filename, class: HASH_RE.test(w.filename) ? 'mono' : null }, HASH_RE.test(w.filename) ? w.filename.slice(0, 16) + '…' : w.filename), h('div', { class: 'tiny dim mono' }, w.witness_id)),
        h('td', null, w.kind), h('td', null, w.adapter), h('td', { class: 'num' }, fmtBytes(w.size_bytes)),
        h('td', { class: 'num' }, w.row_count == null ? '—' : fmtInt(w.row_count)),
        h('td', null, hashSpan(w.sha256)), h('td', null, mono(w.acquired_at)),
        h('td', { class: 'small' }, w.coverage_claim),
        h('td', null, w.ingested ? text('yes', '✓ yes') : text('no', '✕ not yet'))))))));
  }
}

async function pageWitness(id) {
  const main = startPage('Witness', 'witnesses');
  const data = await api('/api/witnesses/' + encodeURIComponent(id));
  const w = data.witness;
  clear(main);
  main.append(h('header', { class: 'page-head' }, h('div', null, h('p', { class: 'kicker' }, h('a', { href: '#/witnesses' }, 'Witnesses'), ' / ', w.trust_domain), h('h1', null, w.filename), h('p', { class: 'muted mono' }, w.witness_id))));
  main.append(data.verified
    ? callout('good', 'Snapshot verified', h('p', { class: 'small' }, 'The snapshot still has the size and SHA-256 recorded at acquisition.'))
    : callout('critical', 'Snapshot verification failed', h('p', { class: 'small' }, data.error)));
  main.append(h('div', { class: 'table-wrap' }, h('table', { class: 'data kv' }, h('tbody', null,
    row('Trust domain', w.trust_domain, data.trust_domain ? text('muted', ` · ${data.trust_domain.label}`) : null, data.trust_domain && data.trust_domain.authentic_records ? [' · ', badge('supported'), ' declared authentic'] : null, data.trust_domain && data.trust_domain.exclusive_receipt_tokens ? [' · ', badge('supported'), ' exclusive receipt tokens'] : null),
    row('Kind', w.kind), row('Adapter', `${w.adapter} ${w.adapter_version}`),
    row('SHA-256', hashSpan(w.sha256)), row('Size', fmtBytes(w.size_bytes)),
    row('Acquired', mono(w.acquired_at)), row('Origin', mono(w.origin)), row('Snapshot', mono(w.snapshot_path)),
    row('Coverage claim', w.coverage_claim),
    row('Citations', w.row_count == null ? '—' : fmtInt(w.row_count), text('dim small', ` (${fmtInt(data.citations)} in the graph)`)),
    row('Clocks', w.clock_ids && w.clock_ids.length ? h('span', { class: 'chips' }, w.clock_ids.map(c => h('span', { class: 'chip' }, c))) : text('dim', 'none')),
    row('Ingested', data.ingested ? text('yes', '✓ yes') : text('no', '✕ not yet'))))));
  main.append(h('h2', null, 'Entities from this witness'));
  if (data.entities.length) {
    main.append(h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
      h('thead', null, h('tr', null, h('th', null, 'Kind'), h('th', null, 'Subkind'), h('th', { class: 'num' }, 'Rows'), h('th', { class: 'num' }, 'With time'))),
      h('tbody', null, data.entities.map(e => h('tr', null, h('td', null, e.kind),
        h('td', null, h('a', { href: `#/entities?witness_id=${encodeURIComponent(id)}&subkind=${encodeURIComponent(e.subkind)}` }, e.subkind)),
        h('td', { class: 'num' }, fmtInt(e.rows)), h('td', { class: 'num' }, fmtInt(e.timed))))))));
  } else main.append(h('p', { class: 'muted' }, 'No native entities; ingest the witness first.'));
  if (data.clocks.length) { main.append(h('h2', null, 'Clocks'), genericTable(data.clocks, {}, { hide: ['witness_id'] })); }
  if (data.relations.length) {
    main.append(h('h2', null, 'Relations citing this witness'),
      h('p', { class: 'small' }, h('a', { href: '#/relations?witness=' + encodeURIComponent(id) }, 'Browse them')),
      genericTable(data.relations, {}));
  }
}

/* ---------- relations ---------- */
function filterBar(fields, params, apply) {
  const bar = h('form', { class: 'filters', onsubmit: (e) => { e.preventDefault(); apply(collect()); } });
  const inputs = {};
  for (const field of fields) {
    if (field.options) {
      const select = h('select', { name: field.name, onchange: () => apply(collect()) },
        h('option', { value: '' }, 'any'),
        field.options.map(o => h('option', { value: o.value, selected: params[field.name] === o.value || null }, `${o.value} (${fmtInt(o.n)})`)));
      inputs[field.name] = select;
      bar.append(h('label', null, field.label, select));
    } else {
      const input = h('input', { type: 'search', name: field.name, value: params[field.name] || '', placeholder: field.placeholder || '' });
      inputs[field.name] = input;
      bar.append(h('label', null, field.label, input));
    }
  }
  bar.append(h('button', { class: 'btn small', type: 'submit' }, 'Apply'), h('button', { class: 'btn small', type: 'button', onclick: () => apply({}) }, 'Reset'));
  function collect() { const out = {}; for (const [k, el] of Object.entries(inputs)) if (el.value) out[k] = el.value; return out; }
  return bar;
}
function pager(data, params, path) {
  const from = data.total ? data.offset + 1 : 0;
  const to = Math.min(data.offset + data.rows.length, data.total);
  return h('div', { class: 'pager' },
    h('button', { class: 'btn small', type: 'button', disabled: data.offset === 0 || null, onclick: () => navigate(path, { ...params, offset: Math.max(0, data.offset - data.limit) }) }, '‹ Previous'),
    text('num', `${fmtInt(from)}–${fmtInt(to)} of ${fmtInt(data.total)}`),
    h('button', { class: 'btn small', type: 'button', disabled: to >= data.total || null, onclick: () => navigate(path, { ...params, offset: data.offset + data.limit }) }, 'Next ›'));
}

async function pageRelations(params) {
  const main = startPage('Relations', 'relations');
  const data = await api('/api/relations?' + new URLSearchParams(params));
  const ctx = { entities: data.entity_index, citations: data.citation_index, witnesses: data.witness_index, meanings: data.outcome_meanings, hints: data.method_hints };
  clear(main);
  main.append(h('header', { class: 'page-head' }, h('div', null, h('h1', null, 'Relations'),
    h('p', { class: 'muted lede' }, 'Cited edges between witness-local entities. Each carries an outcome, the method that decided it, the candidates it weighed and the trust relation between the witnesses involved. Hover a method for what would change it.'))));
  main.append(filterBar([
    { name: 'kind', label: 'Kind', options: data.facets.kind },
    { name: 'outcome', label: 'Outcome', options: data.facets.outcome },
    { name: 'method', label: 'Method', options: data.facets.method },
    { name: 'run_id', label: 'Run', options: data.facets.run_id },
    { name: 'q', label: 'Search rationale, ids or keys', placeholder: 'e.g. receipt' },
  ], params, next => navigate('/relations', { ...next, entity: params.entity, witness: params.witness, citation: params.citation })));
  const pinned = ['entity', 'witness', 'citation'].filter(k => params[k]);
  if (pinned.length) main.append(h('p', { class: 'small muted' }, 'Pinned to ', pinned.map(k => [`${k} `, mono(params[k]), ' ']), h('a', { href: '#/relations' }, 'clear')));
  if (!data.total) { main.append(emptyState('No relations match', params.kind || params.outcome || params.q ? 'Loosen the filters.' : 'Ingest witnesses and run a reconcile stage to derive relations.')); return; }
  main.append(pager(data, params, '/relations'), relationTable(data.rows, ctx), pager(data, params, '/relations'));
}

/* ---------- entities ---------- */
async function pageEntities(params) {
  const main = startPage('Entities', 'entities');
  const data = await api('/api/entities?' + new URLSearchParams(params));
  const ctx = { citations: data.citation_index, witnesses: data.witness_index };
  clear(main);
  main.append(h('header', { class: 'page-head' }, h('div', null, h('h1', null, 'Entities'),
    h('p', { class: 'muted lede' }, 'Witness-local observations: tool events, ledger records, reads, revisions, claimed handles. Entities from different witnesses are never merged silently.'))));
  main.append(filterBar([
    { name: 'subkind', label: 'Subkind', options: data.facets.subkind },
    { name: 'kind', label: 'Kind', options: data.facets.kind },
    { name: 'witness_id', label: 'Witness', options: data.facets.witness_id.map(o => ({ value: o.value, n: o.n })) },
    { name: 'q', label: 'Search key, attributes or id', placeholder: 'e.g. probe.py' },
  ], params, next => navigate('/entities', next)));
  if (!data.total) { main.append(emptyState('No entities match', 'Loosen the filters, or ingest witnesses first.')); return; }
  const table = h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
    h('thead', null, h('tr', null, ['Subkind', 'Natural key', 'Kind', 'Time', 'Grade', 'Witness', 'Cite'].map(c => h('th', null, c)))),
    h('tbody', null, data.rows.map(e => h('tr', { class: 'clickable', onclick: () => navigate('/entities/' + encodeURIComponent(e.entity_id)) },
      h('td', null, e.subkind),
      h('td', null, h('a', { class: 'entity', href: '#/entities/' + encodeURIComponent(e.entity_id) }, e.natural_key)),
      h('td', null, e.kind),
      h('td', null, e.time_lower ? [mono(e.time_lower), e.time_upper && e.time_upper !== e.time_lower ? h('div', { class: 'tiny dim mono' }, '→ ' + e.time_upper) : null] : text('dim', '—')),
      h('td', null, e.time_grade || text('dim', '—')),
      h('td', null, witnessLink(e.witness_id, ctx)),
      h('td', null, citeChip(e.citation_id, ctx)))))));
  main.append(pager(data, params, '/entities'), table, pager(data, params, '/entities'));
}

async function pageEntity(id) {
  const main = startPage('Entity', 'entities');
  const data = await api('/api/entities/' + encodeURIComponent(id));
  const e = data.entity;
  const ctx = { entities: data.entity_index, citations: data.citation_index, witnesses: data.witness_index, meanings: data.outcome_meanings, hints: data.method_hints };
  clear(main);
  main.append(h('header', { class: 'page-head' }, h('div', null,
    h('p', { class: 'kicker' }, h('a', { href: '#/entities' }, 'Entities'), ' / ', h('a', { href: '#/entities?subkind=' + encodeURIComponent(e.subkind) }, e.subkind)),
    h('h1', null, e.natural_key), h('p', { class: 'muted mono' }, e.entity_id))));
  main.append(h('div', { class: 'table-wrap' }, h('table', { class: 'data kv' }, h('tbody', null,
    row('Kind', `${e.kind} · ${e.subkind}`),
    row('Witness', witnessLink(e.witness_id, ctx)),
    row('Citation', citeChip(e.citation_id, ctx)),
    row('Time', e.time_lower ? [mono(e.time_lower), e.time_upper !== e.time_lower ? [' → ', mono(e.time_upper)] : null] : text('dim', 'no time claim')),
    e.time_grade ? row('Time grade', e.time_grade, e.time_uncertainty_s != null ? text('dim', ` · ±${e.time_uncertainty_s}s`) : null, e.winning_clock_id ? text('dim', ` · clock ${e.winning_clock_id}`) : null) : null,
    data.questions.length ? row('Serves questions', h('span', { class: 'chips' }, data.questions.map(q => h('a', { class: 'chip', href: '#/docket/' + q }, q)))) : null))));
  main.append(h('h2', null, 'Attributes'), Object.keys(e.attrs || {}).length ? kvTable(e.attrs, ctx) : h('p', { class: 'muted' }, 'No attributes.'));
  if (data.time_claims.length) main.append(h('h2', null, 'Time claims'), genericTable(data.time_claims, ctx, { hide: ['entity_id', 'witness_id'] }));
  main.append(h('h2', null, `As subject (${data.outgoing.length})`), data.outgoing.length ? relationTable(data.outgoing, ctx) : h('p', { class: 'muted' }, 'No relations with this entity as subject.'));
  main.append(h('h2', null, `As object (${data.incoming.length})`), data.incoming.length ? relationTable(data.incoming, ctx) : h('p', { class: 'muted' }, 'No relations with this entity as object.'));
  main.append(h('p', { class: 'small' }, h('a', { href: '#/relations?entity=' + encodeURIComponent(id) }, 'All relations involving this entity')));
}

/* ---------- query ---------- */
async function pageQuery() {
  const main = startPage('Query', 'query');
  const meta = await api('/api/tables');
  clear(main);
  main.append(h('header', { class: 'page-head' }, h('div', null, h('h1', null, 'Query'),
    h('p', { class: 'muted lede' }, 'One read-only SELECT over the published graph, in DuckDB SQL. The same restrictions as ', code('eg query'), ' apply: no writes, no file or network access.'))));
  const editor = h('textarea', { class: 'sql', spellcheck: 'false', 'aria-label': 'SQL' });
  editor.value = localStorage.getItem('eg.sql') || meta.examples[0].sql;
  const limit = h('select', { class: 'plain', 'aria-label': 'Row limit' }, [100, 500, 2000, 5000].map(n => h('option', { value: n, selected: n === 500 || null }, `${fmtInt(n)} rows`)));
  const examples = h('select', { class: 'plain', 'aria-label': 'Examples', onchange: (ev) => { if (ev.target.value) { editor.value = meta.examples[Number(ev.target.value)].sql; ev.target.value = ''; run(); } } },
    h('option', { value: '' }, 'Examples…'), meta.examples.map((ex, i) => h('option', { value: i }, ex.title)));
  const status = h('div', { class: 'status-line' }, 'Ctrl+Enter or ⌘+Enter runs the query.');
  const results = h('div');
  const runButton = h('button', { class: 'btn primary', type: 'button', onclick: () => run() }, 'Run');
  const downloads = h('span', { class: 'chips' });
  async function run() {
    const sql = editor.value.trim();
    if (!sql) return;
    localStorage.setItem('eg.sql', sql);
    runButton.disabled = true;
    status.textContent = 'Running…';
    clear(results); clear(downloads);
    const started = performance.now();
    try {
      const data = await api('/api/query', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sql, limit: Number(limit.value) }) });
      const elapsed = ((performance.now() - started) / 1000).toFixed(2);
      status.textContent = `${fmtInt(data.rows.length)} row${data.rows.length === 1 ? '' : 's'}${data.truncated ? ` (truncated to ${fmtInt(data.limit)}; raise the limit or narrow the query)` : ''} · ${elapsed}s`;
      if (data.rows.length) {
        results.append(genericTable(data.rows, {}, { columns: data.columns }));
        downloads.append(h('button', { class: 'btn small', type: 'button', onclick: () => download('results.csv', toCsv(data.columns, data.rows), 'text/csv') }, 'Download CSV'),
          h('button', { class: 'btn small', type: 'button', onclick: () => download('results.json', JSON.stringify(data.rows, null, 2), 'application/json') }, 'Download JSON'));
      } else results.append(h('p', { class: 'muted' }, 'No rows.'));
    } catch (error) {
      status.textContent = 'Query failed.';
      results.append(errorBox(error));
    } finally { runButton.disabled = false; }
  }
  editor.addEventListener('keydown', (ev) => { if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') { ev.preventDefault(); run(); } });
  const tables = h('div', { class: 'tables-list' },
    h('h4', null, 'Tables'),
    meta.tables.map(t => tableEntry(t, editor, run)),
    h('h4', null, 'Views'),
    meta.views.map(t => tableEntry(t, editor, run)));
  main.append(h('div', { class: 'query-layout' }, tables,
    h('div', null, editor, h('div', { class: 'query-actions' }, runButton, limit, examples, downloads), status, results)));
  run();
}
function tableEntry(t, editor, run) {
  const use = h('button', { class: 'btn small use', type: 'button', onclick: (ev) => { ev.preventDefault(); editor.value = `SELECT * FROM ${t.name} LIMIT 100`; run(); } }, 'select *');
  return h('details', null,
    h('summary', null, h('span', null, mono(t.name), ' ', text('dim small', fmtInt(t.rows))), use),
    h('div', { class: 'cols' }, t.note ? h('div', { class: 'dim' }, t.note) : null, t.question ? h('div', { class: 'dim' }, `serves ${t.question}`) : null, t.columns.join(', ')));
}
function toCsv(columns, rows) {
  const escape = (v) => { const s = v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
  return [columns.join(','), ...rows.map(r => columns.map(c => escape(r[c])).join(','))].join('\n') + '\n';
}
function download(name, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = h('a', { href: url, download: name });
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ---------- case ---------- */
async function pageCase() {
  const main = startPage('Case', 'case');
  const data = await api('/api/case');
  state.caseInfo = data;
  const config = data.config;
  clear(main);
  main.append(h('header', { class: 'page-head' }, h('div', null, h('h1', null, data.title),
    h('p', { class: 'muted lede' }, 'The declarations every conclusion is bound to, and which stages have run under them.')),
    h('div', { class: 'page-meta' }, h('a', { class: 'btn small', href: '/api/reports/case.json' }, 'case.json'), h('a', { class: 'btn small', href: '/api/reports/manifest.json' }, 'manifest.json'))));
  if (isStale(data)) main.append(staleCallout(data.stale, data.stale_analyzer));

  main.append(h('h2', null, 'Trust domains'));
  main.append(h('div', { class: 'cards' }, config.trust_domains.map(d => h('div', { class: 'card' },
    h('h3', null, d.id, text('muted', ` · ${d.label}`)),
    h('p', { class: 'small' }, d.authentic_records
      ? [badge('supported'), ' Declared authentic: its records were written by a recorder the investigated actors could not control. A unique matching receipt from this domain can be supported without a receipt token.']
      : [badge('ambiguous'), ' Not declared authentic: a native event in this domain could have been written or edited by the investigated actors.']),
    h('p', { class: 'small' }, d.exclusive_receipt_tokens
      ? [badge('supported'), ' Declared exclusive receipt tokens: a genuine token could not be relayed or copied into another native event, so a matching token can support attribution. A false declaration produces a wrong attribution; hashes cannot verify it.']
      : [badge('ambiguous'), ' Receipt tokens not declared exclusive: a matching token shows possession only and the attribution stays ambiguous (receipt_possession_only).']),
    h('p', { class: 'small' }, Object.keys(d.related_to || {}).length
      ? Object.entries(d.related_to).map(([other, rel], i) => [i ? '; ' : '', badge(rel), ` of ${other}`])
      : text('muted', 'No relation declared with another domain; cross-domain matches cannot become supported.')),
    d.sources && d.sources.length ? h('p', { class: 'small dim' }, 'Sources: ', d.sources.join(', ')) : null))));
  if (!config.trust_domains.length) main.append(h('p', { class: 'muted' }, 'No trust domains declared.'));

  main.append(h('h2', null, 'Clock bounds'));
  main.append(config.clock_bounds.length
    ? genericTable(config.clock_bounds.map(b => ({ clocks: `${b.clock_a} : ${b.clock_b}`, bound_seconds: b.bound_seconds, source: b.source, note: b.note })), {})
    : callout('warning', 'No clock bound declared', h('p', { class: 'small' }, 'Timing questions and absence contradictions stay not assessable without a runner:container bound. A declared bound is an assumption; a measured one is evidence.')));

  main.append(h('h2', null, 'Other declarations'));
  main.append(kvTable({
    populations: config.populations.length ? config.populations : 'none declared in case.json (registry populations arrive as witnesses)',
    handle_patterns: config.handle_patterns,
    family_confidence: config.family_confidence,
    schema_version: config.schema_version,
    'case.json sha256': data.config_sha256,
    'current analyzer build': data.analyzer_build_id,
  }, {}));

  main.append(h('h2', null, 'Pipeline'), h('p', { class: 'small muted' }, 'Each stage records the case.json hash it ran under; a changed declaration makes every earlier stage stale.'));
  main.append(h('ol', { class: 'steps' }, data.pipeline.map(step => stepItem(step))));
  main.append(h('h3', null, 'Optional stages'), h('ol', { class: 'steps' }, data.optional.map(step => stepItem(step))));

  const stages = Object.entries(data.stages).sort((a, b) => a[1].completed_at.localeCompare(b[1].completed_at));
  if (stages.length) {
    main.append(h('h2', null, 'Stage history'), genericTable(stages.map(([name, s]) => ({
      stage: name, completed_at: s.completed_at, analyzer_build: s.analyzer_build_id,
      configuration: data.stale.includes(name) ? 'differs' : 'exact',
      analyzer: data.stale_analyzer.includes(name) ? 'differs' : 'exact',
      parameters: Object.fromEntries(Object.entries(s.parameters || {}).filter(([k]) => k !== 'case_config_sha256')),
    })), { meanings: { exact: 'Matches the current case.json and analyzer build', differs: 'Ran under an earlier case.json or analyzer build; re-run it' } }));
  }
  main.append(h('h2', null, 'Reports'));
  const reports = Object.entries(data.reports);
  main.append(reports.length ? genericTable(reports.map(([name, r]) => ({ report: h('a', { href: '/api/reports/' + name }, name), path: r.path, sha256: r.sha256, verified: r.verified })), {}) : h('p', { class: 'muted' }, 'No docket rendered.'));
  if (data.validation) main.append(h('p', { class: 'small' }, 'Validation against private truth recorded: ', h('a', { href: '/api/reports/validation.json' }, 'validation.json'), ' · ', h('a', { href: '#/docket' }, 'summary on the docket')));
  main.append(h('h2', null, 'Graph tables'), genericTable(Object.entries(data.table_rows).sort().map(([table, rows]) => ({ table, rows })), {}));
}
function stepItem(step) {
  return h('li', { class: `step ${step.done ? 'done' : 'todo'}` },
    h('span', { class: 'mark', 'aria-label': step.done ? 'done' : 'not run' }, step.done ? '✓' : '○'),
    h('span', { class: 'name' }, step.step),
    h('span', null, step.detail ? h('div', { class: 'small' }, step.detail) : null, h('code', { class: 'small' }, step.command)));
}

/* ---------- shell ---------- */
function applyTheme() {
  const theme = localStorage.getItem('eg.theme') || 'auto';
  if (theme === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', theme);
  $('#theme-toggle').textContent = `Theme: ${theme}`;
}
async function loadCase() {
  try {
    const info = await api('/api/case');
    state.caseInfo = info;
    state.title = info.title;
    $('#case-title').textContent = info.title;
    $('#case-root').textContent = info.root;
    const flags = clear($('#case-flags'));
    if (info.bundle) flags.append(h('span', { class: 'chip', title: 'Serving the data/ directory of an exported BagIt bundle' }, 'bundle'));
    flags.append(h('span', { class: 'chip', title: 'The viewer never mutates the case' }, 'read-only'));
    const banner = $('#stale-banner');
    if (isStale(info)) { clear(banner).append(staleCallout(info.stale, info.stale_analyzer)); banner.hidden = false; } else banner.hidden = true;
  } catch (error) {
    $('#case-title').textContent = 'Case unavailable';
    $('#case-root').textContent = error.message;
  }
}
function init() {
  applyTheme();
  $('#theme-toggle').addEventListener('click', () => {
    const order = ['auto', 'light', 'dark'];
    const current = localStorage.getItem('eg.theme') || 'auto';
    localStorage.setItem('eg.theme', order[(order.indexOf(current) + 1) % order.length]);
    applyTheme();
  });
  $('#drawer-close').addEventListener('click', closeDrawer);
  $('#backdrop').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && !$('#drawer').hidden) closeDrawer(); });
  window.addEventListener('hashchange', route);
  loadCase().then(route);
}
document.addEventListener('DOMContentLoaded', init);
