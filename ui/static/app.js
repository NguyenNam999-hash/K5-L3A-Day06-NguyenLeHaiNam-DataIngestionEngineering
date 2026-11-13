'use strict';

/* The Ingestion Line — client for ui/server.py
   Every number on screen comes from a real module call on the server. */

const STEPS = [
  {
    n: 1,
    eyebrow: 'Intake',
    spine: 'Source',
    title: 'Load, clean & gate the corpus',
    lede:
      'Read the 39 normalized text units, measure what normalize_text actually removed from the raw source, and run every manifest/benchmark gate before anything is chunked.',
    chips: ['data/pages.jsonl', 'cleaning audit', 'manifest + benchmark gates'],
  },
  {
    n: 2,
    eyebrow: 'Splitting',
    spine: 'Chunks',
    title: 'Cut the text three ways',
    lede:
      'Build A with fixed windows, B by paragraph structure, and C as B plus a metadata filter. Then compare measured Hit@ scores across the three retrieval configurations.',
    chips: ['A · fixed 1600 / 200', 'B · paragraph ≤ 1000', 'C · B + section filter'],
  },
  {
    n: 3,
    eyebrow: 'Vectors',
    spine: 'Embed',
    title: 'Embed with BGE-base',
    lede:
      'Run the real local ONNX model over both chunk sets. C reuses B\'s vectors because only the query-time filter differs. Embedding on CPU takes about a minute; the model also warms in the background at startup.',
    chips: ['BAAI/bge-base-en-v1.5', '768 dimensions', 'local · no API cost'],
  },
  {
    n: 4,
    eyebrow: 'Index',
    spine: 'Store',
    title: 'Upsert into pgvector',
    lede:
      'Persist every chunk with document, source, page and section metadata. Re-running the upsert must keep row counts stable.',
    chips: ['PostgreSQL + pgvector', 'exact cosine scan', 'stable upsert'],
  },
  {
    n: 5,
    eyebrow: 'Evaluation',
    spine: 'Score',
    title: 'Save results & test retrieval',
    lede:
      'Score all 20 frozen questions with the strict evidence-hit rule, save the artifacts, then interrogate the index with sample questions.',
    chips: ['20 questions · 15 dev / 5 test', 'strict hit', 'artifacts/ui_runs'],
  },
];

const STATUS_LABEL = { pending: 'Queued', running: 'Running', done: 'Complete', error: 'Failed' };
const state = {
  boot: null,
  statuses: {},
  results: {},
  running: null,
  metricBlocks: new Map(),
  startTimes: {},
  activeQuestion: null,
  retrieving: false,
  foreignRun: null,
  autoTested: false,
  timers: {},
};

/* --------------------------------------------------------------- utilities */

const fmtNum = (value) => Number(value ?? 0).toLocaleString('en-US');
const fmtPct = (rate) => (rate === null || rate === undefined ? '—' : `${(rate * 100).toFixed(1)}%`);

function fmtDate(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'style') node.setAttribute('style', value);
      else if (key === 'on') {
        for (const [event, handler] of Object.entries(value)) node.addEventListener(event, handler);
      } else if (value === true) node.setAttribute(key, '');
      else node.setAttribute(key, String(value));
    }
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

function status(n) {
  return state.statuses[n] || 'pending';
}

function stepEl(n) {
  return document.querySelector(`.step[data-step="${n}"]`);
}

function resultHost(n) {
  return document.getElementById(`result-${n}`);
}

function canRun(n) {
  if (state.running !== null || state.foreignRun !== null) return false;
  if (n > 1 && status(n - 1) !== 'done') return false;
  for (let later = n + 1; later <= 5; later += 1) {
    if (status(later) !== 'pending') return false;
  }
  return true;
}

function runHint(n) {
  if (state.foreignRun !== null) return `Step ${state.foreignRun} is still running on the server.`;
  if (state.running !== null) return 'A step is already running.';
  if (n > 1 && status(n - 1) !== 'done') return `Run step ${n - 1} first.`;
  for (let later = n + 1; later <= 5; later += 1) {
    if (status(later) !== 'pending') return 'Start over to re-run this step.';
  }
  return '';
}

/* ------------------------------------------------------------------ shell */

function buildSteps() {
  const host = document.getElementById('steps');
  host.replaceChildren();

  STEPS.forEach((def) => {
    state.statuses[def.n] = 'pending';

    const progress = el(
      'div',
      { class: 'progress', id: `progress-${def.n}`, hidden: true },
      el(
        'div',
        { class: 'progress__row' },
        el('span', { class: 'progress__msg', id: `msg-${def.n}` }, 'Waiting for operator'),
        el(
          'span',
          { class: 'progress__meta' },
          el('span', { class: 'progress__elapsed', id: `elapsed-${def.n}` }, '0.0s'),
          el('span', { class: 'progress__pct', id: `pct-${def.n}` }, '0%'),
        ),
      ),
      el('div', { class: 'progress__track', role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': 0, 'aria-label': `Step ${def.n} progress` }, el('i', { id: `bar-${def.n}` })),
    );

    const log = el(
      'details',
      { class: 'log', id: `log-${def.n}`, hidden: true },
      el(
        'summary',
        {},
        'Event log',
        el('span', { class: 'log__count', id: `logcount-${def.n}` }, '0'),
      ),
      el('ol', { id: `loglist-${def.n}` }),
    );

    const article = el(
      'article',
      { class: 'step', 'data-step': def.n, 'data-status': 'pending', style: `--i:${def.n}` },
      el(
        'div',
        { class: 'step__spine' },
        el('span', { class: 'step__num' }, String(def.n).padStart(2, '0')),
        el('span', { class: 'step__spine-label' }, def.spine),
      ),
      el(
        'div',
        { class: 'step__body' },
        el(
          'div',
          { class: 'step__head' },
          el(
            'div',
            {},
            el('p', { class: 'step__eyebrow' }, `Step ${def.n}`, el('i', {}, '·'), def.eyebrow),
            el('h2', {}, def.title),
          ),
          el(
            'div',
            { class: 'step__actions' },
            el('span', { class: 'status', id: `status-${def.n}`, 'data-status': 'pending', 'aria-live': 'polite' }, STATUS_LABEL.pending),
            el(
              'button',
              { class: 'run', id: `run-${def.n}`, type: 'button', on: { click: () => runStep(def.n) } },
              el('span', { class: 'run__label' }, 'Run'),
              el('span', { class: 'arrow', 'aria-hidden': 'true' }, '→'),
            ),
          ),
        ),
        el('p', { class: 'step__lede' }, def.lede),
        el('div', { class: 'chips' }, def.chips.map((chip) => el('span', { class: 'chip' }, chip))),
        progress,
        log,
        el('div', { class: 'result', id: `result-${def.n}`, hidden: true }),
      ),
    );

    host.append(article);
  });
}

function buildRail() {
  const rail = document.getElementById('rail');
  rail.replaceChildren();
  STEPS.forEach((def) => {
    rail.append(
      el(
        'div',
        { class: 'rail__tick', 'data-state': 'pending' },
        el('span', { class: 'rail__id' }, String(def.n).padStart(2, '0')),
        el('span', { class: 'rail__name' }, def.eyebrow),
      ),
    );
  });
  updateRail();
}

function updateRail() {
  const ticks = document.querySelectorAll('.rail__tick');
  ticks.forEach((tick, index) => {
    const value = status(index + 1);
    tick.dataset.state = value === 'done' ? 'done' : value === 'running' ? 'running' : value === 'error' ? 'error' : 'pending';
  });
}

function setStatus(n, value) {
  state.statuses[n] = value;
  const pill = document.getElementById(`status-${n}`);
  if (pill) {
    pill.dataset.status = value;
    pill.textContent = STATUS_LABEL[value];
  }
  const article = stepEl(n);
  if (article) article.dataset.status = value;
  updateRail();
}

function refreshButtons() {
  STEPS.forEach((def) => {
    const button = document.getElementById(`run-${def.n}`);
    if (!button) return;
    const allowed = canRun(def.n);
    button.disabled = !allowed;
    const running = state.running === def.n;
    button.dataset.running = String(running);
    const label = button.querySelector('.run__label');
    const arrow = button.querySelector('.arrow');
    if (label) {
      label.textContent = running
        ? 'Running'
        : status(def.n) === 'done'
          ? allowed
            ? 'Run again'
            : 'Locked'
          : status(def.n) === 'error'
            ? allowed
              ? 'Retry'
              : 'Locked'
            : allowed
              ? 'Run'
              : 'Locked';
    }
    if (arrow) arrow.textContent = running ? '⟳' : '→';
    button.title = allowed ? (status(def.n) === 'done' ? 'Re-run this step' : 'Run this step') : runHint(def.n);
  });
}

/* ------------------------------------------------------------------- logs */

function clearLog(n) {
  const list = document.getElementById(`loglist-${n}`);
  const count = document.getElementById(`logcount-${n}`);
  if (list) list.replaceChildren();
  if (count) count.textContent = '0';
}

function startTimer(n) {
  stopTimer(n);
  const target = document.getElementById(`elapsed-${n}`);
  if (target) target.textContent = '0.0s';
  state.timers[n] = window.setInterval(() => {
    const started = state.startTimes[n];
    if (!started || !target) return;
    target.textContent = `${((performance.now() - started) / 1000).toFixed(1)}s`;
  }, 100);
}

function stopTimer(n) {
  if (state.timers[n]) {
    window.clearInterval(state.timers[n]);
    delete state.timers[n];
  }
}

function addLog(n, message, kind = 'info') {
  const list = document.getElementById(`loglist-${n}`);
  if (!list) return;
  const started = state.startTimes[n] || performance.now();
  const seconds = ((performance.now() - started) / 1000).toFixed(1);
  const item = el(
    'li',
    { 'data-kind': kind },
    el('time', {}, `+${seconds}s`),
    el('span', {}, message),
  );
  list.append(item);
  const count = document.getElementById(`logcount-${n}`);
  if (count) count.textContent = String(list.children.length);
  while (list.children.length > 160) list.firstElementChild.remove();
  list.scrollTop = list.scrollHeight;
}

/* ------------------------------------------------------------------ runner */

async function runStep(n) {
  if (!canRun(n) || state.running !== null) return;
  state.running = n;
  state.startTimes[n] = performance.now();
  setStatus(n, 'running');
  refreshButtons();

  const progress = document.getElementById(`progress-${n}`);
  const bar = document.getElementById(`bar-${n}`);
  const pct = document.getElementById(`pct-${n}`);
  const msg = document.getElementById(`msg-${n}`);
  const log = document.getElementById(`log-${n}`);
  const host = resultHost(n);

  if (progress) {
    progress.hidden = false;
    progress.dataset.running = 'true';
  }
  if (bar) bar.style.width = '0%';
  if (pct) pct.textContent = '0%';
  if (msg) msg.textContent = `Starting step ${n}…`;
  if (log) {
    log.hidden = false;
    log.open = true;
    clearLog(n);
  }
  if (host) {
    host.hidden = true;
    host.replaceChildren();
  }
  startTimer(n);
  addLog(n, `POST /api/steps/${n}/run`);

  try {
    const response = await fetch(`/api/steps/${n}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `Request failed with HTTP ${response.status}`);
    }
    await consumeStream(response, n);
  } catch (error) {
    failStep(n, error.message || String(error));
  } finally {
    state.running = null;
    stopTimer(n);
    refreshButtons();
  }
}

function consumeStream(response, n) {
  return new Promise((resolve) => {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let settled = false;

    const finish = () => {
      settled = true;
      resolve();
    };

    const pump = async () => {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary = buffer.indexOf('\n\n');
        while (boundary >= 0) {
          const raw = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const event = parseEvent(raw);
          if (event) handleEvent(n, event, finish);
          boundary = buffer.indexOf('\n\n');
        }
      }
      if (!settled) failStep(n, 'The stream ended before the step reported completion.');
      finish();
    };

    pump().catch((error) => {
      failStep(n, error.message || String(error));
      finish();
    });
  });
}

function parseEvent(raw) {
  let event = 'message';
  const dataLines = [];
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;
  try {
    return { event, data: JSON.parse(dataLines.join('\n')) };
  } catch {
    return null;
  }
}

function handleEvent(n, event, finish) {
  if (event.event === 'progress') {
    const { percent, message } = event.data;
    const bar = document.getElementById(`bar-${n}`);
    const pct = document.getElementById(`pct-${n}`);
    const msg = document.getElementById(`msg-${n}`);
    if (typeof percent === 'number') {
      if (bar) bar.style.width = `${percent}%`;
      if (pct) pct.textContent = `${Math.round(percent)}%`;
      if (bar?.parentElement) bar.parentElement.setAttribute('aria-valuenow', String(Math.round(percent)));
    }
    if (message) {
      if (msg) msg.textContent = message;
      addLog(n, message, percent === 100 ? 'done' : 'info');
    }
  } else if (event.event === 'done') {
    completeStep(n, event.data);
    finish();
  } else if (event.event === 'error') {
    failStep(n, event.data.error || 'Unknown server error');
    finish();
  }
}

function completeStep(n, payload) {
  const { result, seconds } = payload;
  state.results[n] = result;
  setStatus(n, 'done');
  stopTimer(n);

  const progress = document.getElementById(`progress-${n}`);
  const bar = document.getElementById(`bar-${n}`);
  const pct = document.getElementById(`pct-${n}`);
  const msg = document.getElementById(`msg-${n}`);
  const elapsed = document.getElementById(`elapsed-${n}`);
  if (progress) progress.dataset.running = 'false';
  if (bar) bar.style.width = '100%';
  if (bar?.parentElement) bar.parentElement.setAttribute('aria-valuenow', '100');
  if (pct) pct.textContent = '100%';
  if (msg) msg.textContent = `Complete in ${seconds}s`;
  if (elapsed) elapsed.textContent = `${seconds}s`;
  addLog(n, `Step ${n} complete in ${seconds}s`, 'done');

  renderResult(n, result);
  refreshButtons();
}

function failStep(n, message) {
  if (status(n) === 'done') return;
  stopTimer(n);
  setStatus(n, 'error');
  const progress = document.getElementById(`progress-${n}`);
  if (progress) progress.dataset.running = 'false';
  const msg = document.getElementById(`msg-${n}`);
  if (msg) msg.textContent = message;
  addLog(n, message, 'error');
  const host = resultHost(n);
  if (host) {
    host.hidden = false;
    host.replaceChildren(
      el('div', { class: 'notice notice--error' }, `Step ${n} failed: ${message}`),
    );
  }
  refreshButtons();
}

/* --------------------------------------------------------------- renderers */

function stat(value, label, sub) {
  return el(
    'div',
    { class: 'stat' },
    el('div', { class: 'stat__value' }, value),
    el('div', { class: 'stat__label' }, label),
    sub ? el('div', { class: 'stat__sub' }, sub) : null,
  );
}

function sectionTitle(text) {
  return el('h3', { class: 'section-title' }, text);
}

function badge(passed) {
  return el('span', { class: `badge${passed ? '' : ' badge--warn'}` }, passed ? 'pass' : 'fail');
}

function strategyShort(key, config) {
  if (key === 'A') return `fixed ${config.chunk_size}/${config.overlap}`;
  if (key === 'C') return 'paragraph + filter';
  return `paragraph ≤${config.chunk_size}`;
}

function histogram(lengths, bins = 12) {
  if (!lengths || !lengths.length) return [];
  const min = Math.min(...lengths);
  const max = Math.max(...lengths);
  const counts = new Array(bins).fill(0);
  if (max === min) {
    counts[Math.floor(bins / 2)] = lengths.length;
  } else {
    for (const value of lengths) {
      const index = Math.min(bins - 1, Math.floor(((value - min) / (max - min)) * bins));
      counts[index] += 1;
    }
  }
  const peak = Math.max(...counts, 1);
  return counts.map((count) => Math.max(count ? 8 : 2, Math.round(Math.sqrt(count / peak) * 100)));
}

function renderResult(n, result) {
  const host = resultHost(n);
  if (!host) return;
  host.replaceChildren();
  host.hidden = false;

  if (n === 1) renderIngest(host, result);
  else if (n === 2) renderChunking(host, result);
  else if (n === 3) renderEmbedding(host, result);
  else if (n === 4) renderStore(host, result);
  else if (n === 5) renderEvaluation(host, result);
}

function renderIngest(host, result) {
  host.append(
    el(
      'div',
      { class: 'stat-grid' },
      stat(fmtNum(result.page_count), 'content units', '≥ 30 required'),
      stat(fmtNum(result.content_chars), 'normalized chars', `${result.min_chars}–${result.max_chars} per unit`),
      stat(fmtNum(result.sections), 'sections', 'filter values from source'),
      stat(fmtNum(result.benchmark.questions), 'benchmark questions', `${result.benchmark.dev} dev · ${result.benchmark.test} test`),
      stat(fmtNum(result.benchmark.filter_questions), 'filter questions', '≥ 1 in test'),
    ),
    sectionTitle('First units, kept traceable'),
    el(
      'div',
      { class: 'table-wrap' },
      el(
        'table',
        { class: 'data-table' },
        el(
          'thead',
          {},
          el(
            'tr',
            {},
            el('th', {}, 'Unit'),
            el('th', {}, 'Section'),
            el('th', {}, 'Chars'),
            el('th', {}, 'Normalized preview'),
          ),
        ),
        el(
          'tbody',
          {},
          result.preview.map((row) =>
            el(
              'tr',
              {},
              el('td', { class: 'num' }, `p${String(row.page).padStart(2, '0')}`),
              el('td', {}, row.section || '—'),
              el('td', { class: 'num' }, fmtNum(row.chars)),
              el('td', { class: 'trunc' }, row.preview),
            ),
          ),
        ),
      ),
    ),
    el(
      'div',
      { class: 'chips', style: 'margin-top:12px' },
      el('span', { class: 'chip' }, el('b', {}, 'document'), result.document_ids.join(', ')),
      el('span', { class: 'chip' }, el('b', {}, 'source'), 'gutenberg.org/files/11'),
      el('span', { class: 'chip' }, el('b', {}, 'license'), 'public domain'),
      result.manifest?.accessed_at
        ? el('span', { class: 'chip' }, el('b', {}, 'accessed'), result.manifest.accessed_at)
        : null,
    ),
  );

  const cleaning = result.cleaning;
  if (cleaning) {
    host.append(
      sectionTitle('Cleaning applied by normalize_text'),
      el(
        'div',
        { class: 'table-wrap' },
        el(
          'table',
          { class: 'data-table' },
          el(
            'thead',
            {},
            el(
              'tr',
              {},
              el('th', {}, 'Rule, in run order'),
              el('th', {}, 'Occurrences in raw body'),
              el('th', {}, 'Effect'),
            ),
          ),
          el(
            'tbody',
            {},
            (cleaning.rules || []).map((row) =>
              el(
                'tr',
                {},
                el('td', {}, row.rule),
                el('td', { class: 'num' }, fmtNum(row.found)),
                el('td', {}, row.action),
              ),
            ),
          ),
        ),
      ),
      el(
        'p',
        { class: 'fineprint', style: 'margin-top:10px' },
        cleaning.raw_available
          ? `Counted on ${cleaning.raw_source} (book body, ${fmtNum(cleaning.body_chars)} chars) — not on the already-normalized pages. Zero means this corpus never needed the rule; it still runs for the PDF path.`
          : `Raw source ${cleaning.raw_source} not found; only the runtime page checks below apply.`,
        el('br'),
        cleaning.idempotent
          ? 'normalize_text is idempotent: re-normalizing an already-clean unit changes nothing, so the load step cannot drift the corpus.'
          : 'WARNING: normalize_text is not idempotent — a second pass would change the text.',
      ),
      el(
        'div',
        { class: 'chips', style: 'margin-top:10px' },
        (cleaning.residual || []).map((row) =>
          el('span', { class: 'chip' }, el('b', {}, fmtNum(row.found)), ' ', row.rule),
        ),
      ),
    );
  }

  if (result.checks) {
    const failed = result.checks.filter((row) => !row.passed).length;
    host.append(
      sectionTitle('Error checks that gate the run'),
      el(
        'div',
        { class: 'table-wrap' },
        el(
          'table',
          { class: 'data-table' },
          el(
            'thead',
            {},
            el(
              'tr',
              {},
              el('th', {}, 'Check'),
              el('th', {}, 'Scope'),
              el('th', {}, 'Observed'),
              el('th', {}, 'Required'),
              el('th', {}, 'Status'),
            ),
          ),
          el(
            'tbody',
            {},
            result.checks.map((row) =>
              el(
                'tr',
                {},
                el('td', {}, row.label),
                el('td', { class: 'tagcell' }, row.scope),
                el('td', { class: 'num' }, row.observed),
                el('td', {}, row.expected),
                el('td', {}, badge(row.passed)),
              ),
            ),
          ),
        ),
      ),
      el(
        'p',
        { class: 'fineprint', style: 'margin-top:10px' },
        failed
          ? `${failed} of ${result.checks.length} checks failed. In src.run the same conditions raise and stop the pipeline before ingest — nothing is silently repaired.`
          : `All ${result.checks.length} checks passed. The same conditions raise in src.run, and a bad page record, empty unit or non-string text raises at load time — no fallback, no repaired-on-the-fly corpus.`,
      ),
    );
  }
}

function renderChunking(host, result) {
  const letters = ['A', 'B', 'C'];
  host.append(
    sectionTitle('Three cuts, measured'),
    el(
      'div',
      { class: 'chunk-grid' },
      letters.map((letter) => {
        const item = result.strategies[letter];
        const bars = histogram(item.lengths, 12);
        const sample = item.samples && item.samples[0];
        return el(
          'div',
          { class: 'chunk-card' },
          el(
            'div',
            { class: 'chunk-card__top' },
            el('span', { class: 'chunk-card__tag' }, letter),
            el('span', { class: 'chunk-card__name' }, strategyShort(letter, item.config)),
          ),
          el('div', { class: 'chunk-card__count' }, fmtNum(item.chunks), el('small', {}, 'chunks')),
          el(
            'div',
            { class: 'hist', title: 'Chunk length distribution', 'aria-label': `Strategy ${letter} chunk length distribution` },
            bars.map((height) => el('i', { style: `height:${height}%` })),
          ),
          el(
            'div',
            { class: 'chunk-card__facts' },
            el('span', {}, 'avg ', el('b', {}, `${fmtNum(Math.round(item.avg_chars))} ch`)),
            el('span', {}, 'max ', el('b', {}, `${fmtNum(item.max_chars)} ch`)),
            el('span', {}, 'pages ', el('b', {}, item.pages_covered)),
            el('span', {}, 'per page ', el('b', {}, item.chunks_per_page)),
          ),
          el('p', { class: 'chunk-card__note' }, item.note),
          sample
            ? el(
                'div',
                { class: 'chunk-sample' },
                el('span', { class: 'chunk-sample__id' }, `${sample.chunk_id} · p${sample.page} · ${fmtNum(sample.chars)} ch`),
                sample.text,
              )
            : null,
        );
      }),
    ),
    sectionTitle('Retrieval quality by strategy'),
    el('div', { id: 'metric-step2', class: 'metric-block' }),
    el(
      'p',
      { class: 'fineprint', style: 'margin-top:10px' },
      'Hit@ scores come from the frozen 20-question benchmark with the strict rule: gold document + gold page + the full verbatim evidence quote inside one chunk. A–B isolates chunking; B–C isolates the metadata filter.',
    ),
  );

  const comparison = result.comparison || {};
  if (comparison.metrics) {
    paintMetrics('metric-step2', comparison.metrics, {
      title: 'Hit@ by strategy',
      source: comparison.source || 'results/metrics.json',
      generatedAt: comparison.generated_at,
    });
  } else {
    const host2 = document.getElementById('metric-step2');
    if (host2) {
      host2.replaceChildren(
        el('div', { class: 'notice' }, 'No saved metrics were found. Finish step 5 to measure Hit@ for this run.'),
      );
    }
  }
}

function renderEmbedding(host, result) {
  const max = Math.max(...result.fingerprint.map((v) => Math.abs(v)), 0.0001);
  host.append(
    el(
      'div',
      { class: 'stat-grid' },
      stat(result.dimensions, 'dimensions', result.model),
      stat(fmtNum(result.strategies.A.vectors + result.strategies.B.vectors), 'vectors', 'C reuses B'),
      stat(fmtNum(result.corpus_tokens), 'corpus tokens', 'model tokenizer'),
      stat(`${result.total_seconds}s`, 'embedding time', 'local ONNX'),
      stat('$0.00', 'estimated cost', 'no API calls'),
    ),
    sectionTitle('Model & throughput'),
    el(
      'div',
      { class: 'table-wrap' },
      el(
        'table',
        { class: 'data-table' },
        el(
          'thead',
          {},
          el(
            'tr',
            {},
            el('th', {}, 'Strategy'),
            el('th', {}, 'Documents'),
            el('th', {}, 'Dimensions'),
            el('th', {}, 'Tokens'),
            el('th', {}, 'Seconds'),
          ),
        ),
        el(
          'tbody',
          {},
          ['A', 'B', 'C'].map((letter) => {
            const item = result.strategies[letter];
            return el(
              'tr',
              {},
              el('td', { class: 'tagcell' }, letter),
              el('td', { class: 'num' }, fmtNum(item.chunks)),
              el('td', { class: 'num' }, fmtNum(item.dimensions)),
              el('td', { class: 'num' }, fmtNum(item.model_tokens)),
              el('td', { class: 'num' }, letter === 'C' ? '0 · reused' : `${item.embedding_seconds}s`),
            );
          }),
        ),
      ),
    ),
    sectionTitle('A real slice of one vector'),
    el(
      'div',
      { class: 'fingerprint', title: 'First 28 dimensions of the first strategy-A document vector' },
      el('span', { class: 'fingerprint__label' }, 'first vector · dims 0–27'),
      result.fingerprint.map((value) =>
        el('i', { style: `height:${Math.max(4, Math.round((Math.abs(value) / max) * 100))}%`, title: value.toFixed(5) }),
      ),
    ),
  );
}

function renderStore(host, result) {
  const letters = ['A', 'B', 'C'];
  const stable = result.reingest_stable;
  host.append(
    el(
      'div',
      { class: 'stat-grid' },
      stat(fmtNum(result.total_rows), 'rows in pgvector', 'strategy + chunk_id key'),
      stat('3', 'strategies indexed', 'A · B · C'),
      stat(result.metric, 'distance metric', 'exact scan'),
      stat(stable ? 'stable' : 'drift', 're-ingest check', stable ? 'before = after' : 'row counts changed'),
    ),
    sectionTitle('Upsert ledger'),
    el(
      'div',
      { class: 'table-wrap' },
      el(
        'table',
        { class: 'data-table' },
        el(
          'thead',
          {},
          el(
            'tr',
            {},
            el('th', {}, 'Strategy'),
            el('th', {}, 'Rows before'),
            el('th', {}, 'Rows after'),
            el('th', {}, 'Avg chars'),
            el('th', {}, 'Upsert'),
          ),
        ),
        el(
          'tbody',
          {},
          letters.map((letter) => {
            const item = result.strategies[letter];
            return el(
              'tr',
              {},
              el('td', { class: 'tagcell' }, letter),
              el('td', { class: 'num' }, fmtNum(item.before.chunks)),
              el('td', { class: 'num' }, fmtNum(item.after.chunks)),
              el('td', { class: 'num' }, fmtNum(Math.round(item.after.avg_chars))),
              el(
                'td',
                {},
                el('span', { class: item.stable ? 'badge' : 'badge badge--warn' }, item.stable ? 'no duplicates' : 'changed'),
                el('span', { class: 'mono', style: 'margin-left:8px;color:var(--muted)' }, `${item.seconds}s`),
              ),
            );
          }),
        ),
      ),
    ),
    el(
      'p',
      { class: 'fineprint', style: 'margin-top:10px' },
      'Every row keeps document_id, source, page and section. The table is shared across strategies; only the strategy column separates them.',
    ),
  );
}

function renderEvaluation(host, result) {
  host.append(
    el(
      'div',
      { class: 'stat-grid' },
      stat(fmtNum(result.counts.questions), 'questions scored', `${result.counts.dev} dev · ${result.counts.test} test`),
      stat(fmtNum(result.counts.traces), 'traces saved', '3 strategies × 20'),
      stat(`${result.evaluation_seconds}s`, 'evaluation time', 'query + search + hit check'),
      stat(result.run_id, 'run id', 'artifacts/ui_runs'),
    ),
    sectionTitle('Measured on this run'),
    el('div', { id: 'metric-step5', class: 'metric-block' }),
    sectionTitle('Saved artifacts'),
    el(
      'div',
      { class: 'artifacts' },
      result.artifacts.map((path) =>
        el(
          'div',
          { class: 'artifact' },
          el('span', {}, 'file'),
          el('code', {}, path),
        ),
      ),
    ),
    el(
      'p',
      { class: 'fineprint', style: 'margin-top:8px' },
      'These are UI-session artifacts. The reported snapshot in results/ is never overwritten by this console.',
    ),
  );

  paintMetrics('metric-step5', result.summary, {
    title: 'Hit@ by strategy',
    source: 'evaluated in this session · step 5',
    generatedAt: null,
  });

  if (state.results[2] && document.getElementById('metric-step2')) {
    paintMetrics('metric-step2', result.summary, {
      title: 'Hit@ by strategy',
      source: 'evaluated in this session · step 5',
      generatedAt: null,
    });
  }

  host.append(buildTester());
  const first = activeSampleId();
  if (first && !state.autoTested) {
    state.autoTested = true;
    state.activeQuestion = first;
    syncChips();
    runRetrieval(first);
  }
}

/* ------------------------------------------------------------- metric block */

function paintMetrics(hostId, metrics, opts) {
  const container = document.getElementById(hostId);
  if (!container || !metrics) return;
  const previous = state.metricBlocks.get(hostId);
  const split = previous?.split || 'overall';
  state.metricBlocks.set(hostId, { metrics, opts: opts || {}, split });

  const tabs = ['overall', 'dev', 'test'].map((key) =>
    el(
      'button',
      {
        type: 'button',
        'data-metric-split': key,
        'data-block': hostId,
        'data-active': String(split === key),
        on: {
          click: () => {
            const entry = state.metricBlocks.get(hostId);
            if (!entry || entry.split === key) return;
            entry.split = key;
            paintMetrics(hostId, entry.metrics, entry.opts);
          },
        },
      },
      key === 'overall' ? 'All' : key,
    ),
  );

  const rows = ['A', 'B', 'C'].flatMap((letter) => {
    const row = metrics[letter]?.[split];
    if (!row) return [];
    const config = state.boot?.strategies?.[letter] || {};
    return [
      el(
        'div',
        { class: 'metric-row' },
        el(
          'div',
          { class: 'metric-id' },
          el('span', { class: 'letter' }, letter),
          el('span', { class: 'desc' }, strategyShort(letter, config)),
        ),
        el(
          'div',
          { class: 'metric-bars' },
          metricBar('Hit@1', row.hit1, row.n, row.hit1_rate, 'hit1'),
          metricBar('Hit@3', row.hit3, row.n, row.hit3_rate, 'hit3'),
        ),
      ),
    ];
  });

  container.className = 'metric-block';
  container.replaceChildren(
    el(
      'div',
      { class: 'metric-block__head' },
      el(
        'div',
        {},
        el('div', { class: 'metric-block__title' }, opts?.title || 'Hit@ by strategy'),
        el(
          'div',
          { class: 'metric-block__source' },
          `${opts?.source || ''}${opts?.generatedAt ? ` · ${fmtDate(opts.generatedAt)}` : ''}`,
        ),
      ),
      el('div', { class: 'split-tabs', role: 'group', 'aria-label': 'Benchmark split' }, tabs),
    ),
    el('div', { class: 'metric-body' }, rows),
    el(
      'div',
      { class: 'metric-legend' },
      el('span', {}, el('i'), 'Hit@1'),
      el('span', {}, el('i', { class: 'acc' }), 'Evidence Hit@3'),
      metrics.A?.[split] ? el('span', {}, `n = ${metrics.A[split].n} questions`) : null,
    ),
  );

  requestAnimationFrame(() => {
    container.querySelectorAll('.metric-bar__fill').forEach((fill) => {
      fill.style.width = `${fill.dataset.width}%`;
    });
  });
}

function metricBar(label, hits, total, rate, kind) {
  const width = rate === null || rate === undefined ? 0 : Math.max(rate > 0 ? 2 : 0, rate * 100);
  return el(
    'div',
    { class: 'metric-bar' },
    el('span', { class: 'metric-bar__label' }, label),
    el('div', { class: 'metric-bar__track' }, el('i', { class: 'metric-bar__fill', 'data-kind': kind, 'data-width': width, style: 'width:0%' })),
    el('span', { class: 'metric-bar__val' }, `${hits}/${total} · ${fmtPct(rate)}`),
  );
}

/* ------------------------------------------------------------- test bench */

function activeSampleId() {
  const samples = state.boot?.samples || [];
  return state.activeQuestion || samples[0]?.id || state.boot?.questions?.[0]?.id || null;
}

function findQuestion(id) {
  return (state.boot?.questions || []).find((question) => question.id === id) || null;
}

function buildTester() {
  const samples = state.boot?.samples || [];
  const questions = state.boot?.questions || [];
  const section = el('section', { class: 'tester', id: 'tester' });

  const chips = el(
    'div',
    { class: 'picker' },
    samples.map((question) =>
      el(
        'button',
        {
          type: 'button',
          class: 'qchip',
          'data-qid': question.id,
          title: question.question,
          on: { click: () => selectQuestion(question.id) },
        },
        el('span', { class: 'qid' }, question.id),
        `${question.type}${question.filter ? ' · filter' : ''} · ${question.split}`,
      ),
    ),
    el(
      'select',
      {
        class: 'picker__select',
        'aria-label': 'All benchmark questions',
        on: {
          change: (event) => {
            if (event.target.value) selectQuestion(event.target.value);
          },
        },
      },
      el('option', { value: '' }, 'All 20 questions…'),
      ['dev', 'test'].map((split) =>
        el(
          'optgroup',
          { label: `${split} split` },
          questions
            .filter((question) => question.split === split)
            .map((question) =>
              el('option', { value: question.id }, `${question.id} · ${question.type}${question.filter ? ' · filter' : ''}`),
            ),
        ),
      ),
    ),
  );

  section.append(
    el(
      'div',
      { class: 'tester__head' },
      el(
        'div',
        {},
        el('p', { class: 'eyebrow' }, 'Retrieval test'),
        el('h3', {}, 'Ask the index a frozen question'),
        el(
          'p',
          {},
          'The same query vector runs against A, B and C. C applies the section filter when the question carries one; A and B do not. Gold is shown so you can judge the hits yourself.',
        ),
      ),
    ),
    chips,
    el('div', { class: 'test-stage', id: 'test-stage' }),
  );

  return section;
}

function syncChips() {
  document.querySelectorAll('.qchip').forEach((chip) => {
    chip.dataset.active = String(chip.dataset.qid === state.activeQuestion);
  });
  const select = document.querySelector('.picker__select');
  if (select && state.activeQuestion) {
    const option = [...select.options].find((item) => item.value === state.activeQuestion);
    if (option) select.value = state.activeQuestion;
  }
}

function selectQuestion(id) {
  if (!id || state.retrieving) return;
  state.activeQuestion = id;
  syncChips();
  runRetrieval(id);
}

async function runRetrieval(id) {
  const stage = document.getElementById('test-stage');
  if (!stage) return;
  state.retrieving = true;
  stage.replaceChildren(
    el(
      'div',
      { class: 'skeleton' },
      el('i'),
      el('i'),
      el('i'),
    ),
  );

  try {
    const response = await fetch('/api/retrieve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) throw new Error(data.error || `Request failed with HTTP ${response.status}`);
    if (state.activeQuestion !== id) return;
    renderRetrieval(data);
  } catch (error) {
    stage.replaceChildren(
      el('div', { class: 'notice notice--error' }, `Retrieval failed: ${error.message || error}`),
    );
  } finally {
    state.retrieving = false;
  }
}

function renderRetrieval(data) {
  const stage = document.getElementById('test-stage');
  if (!stage) return;
  const question = data.question;

  const goldFacts = [
    el('span', {}, 'answer ', el('b', {}, question.expected_answer)),
    el('span', {}, 'gold ', el('b', {}, `${question.gold_source_id} · p${question.gold_page}`)),
    el('span', {}, 'split ', el('b', {}, question.split)),
    el('span', {}, 'type ', el('b', {}, question.type)),
    question.filter
      ? el(
          'span',
          {},
          'filter ',
          el('b', {}, Object.entries(question.filter).map(([key, value]) => `${key}=${value}`).join(', ')),
        )
      : el('span', {}, 'filter ', el('b', {}, 'none')),
  ];

  const gold = el(
    'div',
    { class: 'gold' },
    el('div', { class: 'gold__q' }, question.question),
    el('div', { class: 'gold__facts' }, goldFacts),
    el('blockquote', {}, `“${question.evidence_quote}”`),
  );

  const grid = el(
    'div',
    { class: 'test-grid' },
    ['A', 'B', 'C'].map((letter) => renderStrategyColumn(letter, data.strategies[letter], question)),
  );

  stage.replaceChildren(
    gold,
    grid,
    el(
      'p',
      { class: 'fineprint', style: 'margin-top:10px' },
      `Query embedded and searched in ${data.elapsed_seconds}s. Scores are cosine similarity; a hit still requires the full evidence quote, not just a nearby page.`,
    ),
  );
}

function renderStrategyColumn(letter, payload, question) {
  const config = state.boot?.strategies?.[letter] || {};
  const header = el(
    'div',
    { class: 'strat-col__head' },
    el('span', { class: 'strat-col__letter' }, letter),
    el('span', { class: 'strat-col__name' }, strategyShort(letter, config)),
    el(
      'span',
      { class: 'strat-col__flags' },
      el('span', { class: payload['hit@1'] ? 'badge' : 'badge badge--warn' }, `H@1 ${payload['hit@1'] ? '✓' : '✗'}`),
      el('span', { class: payload['hit@3'] ? 'badge' : 'badge badge--warn' }, `H@3 ${payload['hit@3'] ? '✓' : '✗'}`),
    ),
  );

  const hits = payload.hits.map((hit) => {
    const text = el('p', { class: 'hit__text' }, highlight(hit.text, hit.evidence_span));
    const card = el(
      'div',
      { class: `hit${hit.hit ? ' hit--hit' : ''}` },
      el(
        'div',
        { class: 'hit__top' },
        el('span', { class: 'hit__rank' }, `#${hit.rank}`),
        el('span', { class: hit.hit ? 'badge' : 'badge badge--neutral' }, hit.hit ? 'evidence' : 'no evidence'),
        el('span', { class: 'hit__score' }, hit.score.toFixed(4)),
      ),
      el(
        'div',
        { class: 'hit__meta' },
        `${hit.chunk_id} · p${hit.page}${hit.section ? ` · ${hit.section}` : ''}`,
      ),
      text,
    );
    if (hit.hit) {
      requestAnimationFrame(() => {
        const mark = text.querySelector('mark');
        if (!mark) return;
        const offset = mark.getBoundingClientRect().top - text.getBoundingClientRect().top;
        text.scrollTop = Math.max(0, text.scrollTop + offset - 18);
      });
    }
    return card;
  });

  return el(
    'div',
    { class: 'strat-col' },
    header,
    el('div', { class: 'hit-list' }, hits),
    payload.filter
      ? el(
          'div',
          { class: 'fineprint', style: 'padding:0 11px 10px' },
          `filter applied: ${Object.entries(payload.filter).map(([key, value]) => `${key}=${value}`).join(', ')}`,
        )
      : el('div', { class: 'fineprint', style: 'padding:0 11px 10px' }, 'no filter applied'),
  );
}

function highlight(text, span) {
  if (!span || !Array.isArray(span)) return document.createTextNode(text);
  const start = Math.max(0, Math.min(Number(span[0]) || 0, text.length));
  const end = Math.max(start, Math.min(Number(span[1]) || 0, text.length));
  if (end <= start) return document.createTextNode(text);
  const fragment = document.createDocumentFragment();
  fragment.append(document.createTextNode(text.slice(0, start)));
  const mark = document.createElement('mark');
  mark.textContent = text.slice(start, end);
  fragment.append(mark);
  fragment.append(document.createTextNode(text.slice(end)));
  return fragment;
}

/* --------------------------------------------------------------- bootstrap */

async function bootstrap() {
  buildSteps();
  buildRail();
  refreshButtons();

  try {
    const response = await fetch('/api/bootstrap');
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    state.boot = data;

    const label = document.getElementById('corpus-label');
    if (label) {
      label.textContent = `${fmtNum(data.corpus.page_count)} units · ${data.corpus.document_ids.join(', ')}`;
    }
    restoreSnapshot(data.state);
  } catch (error) {
    document.getElementById('steps').prepend(
      el(
        'div',
        { class: 'notice notice--error', style: 'margin-bottom:24px' },
        `Could not reach the pipeline server: ${error.message || error}. Start it with starter/.venv/bin/python ui/server.py.`,
      ),
    );
  }
}

function restoreSnapshot(snapshot) {
  if (!snapshot) return;
  const steps = snapshot.steps || {};
  let runningStep = null;

  for (const def of STEPS) {
    const entry = steps[String(def.n)];
    if (!entry) continue;
    if (entry.status === 'running') {
      runningStep = def.n;
      state.statuses[def.n] = 'running';
      setStatus(def.n, 'running');
      const progress = document.getElementById(`progress-${def.n}`);
      if (progress) progress.hidden = false;
      const msg = document.getElementById(`msg-${def.n}`);
      if (msg) msg.textContent = 'Running on the server…';
      continue;
    }
    if (entry.status === 'done' && entry.result) {
      state.results[def.n] = entry.result;
      setStatus(def.n, 'done');
      const progress = document.getElementById(`progress-${def.n}`);
      const bar = document.getElementById(`bar-${def.n}`);
      const pct = document.getElementById(`pct-${def.n}`);
      const msg = document.getElementById(`msg-${def.n}`);
      const elapsed = document.getElementById(`elapsed-${def.n}`);
      if (progress) {
        progress.hidden = false;
        progress.dataset.running = 'false';
      }
      if (bar) bar.style.width = '100%';
      if (pct) pct.textContent = '100%';
      if (msg) msg.textContent = `Complete in ${entry.seconds}s`;
      if (elapsed) elapsed.textContent = `${entry.seconds}s`;
      renderResult(def.n, entry.result);
    } else if (entry.status === 'error') {
      setStatus(def.n, 'error');
      const host = resultHost(def.n);
      if (host) {
        host.hidden = false;
        host.replaceChildren(
          el('div', { class: 'notice notice--error' }, `Last attempt failed: ${entry.error || 'unknown error'}`),
        );
      }
    }
  }

  if (runningStep !== null) {
    state.foreignRun = runningStep;
    pollForeignRun();
  }

  if (state.results[5] && state.results[2] && document.getElementById('metric-step2')) {
    paintMetrics('metric-step2', state.results[5].summary, {
      title: 'Hit@ by strategy',
      source: 'evaluated in this session · step 5',
      generatedAt: null,
    });
  }

  if (state.results[5]) {
    const first = activeSampleId();
    if (first && !state.autoTested) {
      state.autoTested = true;
      state.activeQuestion = first;
      syncChips();
      runRetrieval(first);
    }
  }

  refreshButtons();
}

function pollForeignRun() {
  window.setTimeout(async () => {
    try {
      const response = await fetch('/api/bootstrap');
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error('bootstrap failed');
      const entry = data.state?.steps?.[String(state.foreignRun)];
      if (entry && entry.status === 'running') {
        pollForeignRun();
        return;
      }
      state.foreignRun = null;
      state.boot = { ...state.boot, ...data };
      restoreSnapshot(data.state);
      refreshButtons();
    } catch {
      pollForeignRun();
    }
  }, 2000);
}

/* -------------------------------------------------------------------- reset */

async function resetAll() {
  if (state.running !== null) return;
  const confirmed = window.confirm(
    'Clear the in-memory pipeline progress and return every step to Queued? Saved artifacts stay on disk.',
  );
  if (!confirmed) return;
  try {
    const response = await fetch('/api/reset', { method: 'POST' });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || 'Reset failed');
    window.location.reload();
  } catch (error) {
    window.alert(`Could not reset: ${error.message || error}`);
  }
}

/* --------------------------------------------------------------------- init */

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('reset')?.addEventListener('click', resetAll);
  bootstrap();
});
