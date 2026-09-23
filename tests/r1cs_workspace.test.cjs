// State integration tests with a small DOM/CodeMirror double, no browser or npm packages.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
    constructor(tag = 'div') {
        this.tag = tag; this.children = []; this.style = {}; this.dataset = {};
        this.className = ''; this.value = ''; this.hidden = false; this.listeners = {};
        this.classList = {
            add: name => { this.className += ' ' + name; },
            remove: name => { this.className = this.className.split(' ').filter(n => n !== name).join(' '); },
        };
    }
    append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
    appendChild(node) { this.append(node); return node; }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(n => n !== this); }
    set innerHTML(value) { this.children = []; this.html = value; }
    get innerHTML() { return this.html || this.textContent || ''; }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    scrollIntoView() { this.scrolledIntoView = true; }
    querySelectorAll(selector) {
        return this.children.flatMap(child => [child, ...child.querySelectorAll('*')]).filter(child => {
            if (selector === '*') return true;
            if (selector.startsWith('.')) return child.className.split(' ').includes(selector.slice(1));
            const action = selector.match(/^\[data-action="(.+)"\]$/);
            return action ? child.dataset.action === action[1] : child.tag === selector;
        });
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function harness() {
    const root = new Element();
    const ids = new Map();
    const document = {
        createElement: tag => new Element(tag),
        getElementById: id => {
            if (!ids.has(id)) { const node = new Element(); root.append(node); ids.set(id, node); }
            return ids.get(id);
        },
        querySelectorAll: selector => root.querySelectorAll(selector),
    };
    const panel = document.getElementById('r1csPanel');
    for (const name of ['r1cs-summary', 'r1cs-status', 'r1cs-constraints', 'r1cs-filename',
                        'r1cs-file-meta', 'r1cs-prime', 'r1cs-details']) {
        const node = new Element(); node.className = name; panel.append(node);
    }
    for (const name of ['previous', 'next', 'coefficients']) {
        const node = new Element(); node.dataset.action = name; panel.append(node);
    }
    let code = '';
    const wrapper = new Element();
    const handlers = {};
    const editor = {
        on: (name, fn) => { handlers[name] = fn; },
        setValue: text => { code = text; if (handlers.change) handlers.change(); },
        getValue: () => code, getWrapperElement: () => wrapper,
        refresh() {}, setOption() {},
    };
    const requests = [];
    const streams = [];
    const context = vm.createContext({document, CodeMirror: {fromTextArea: () => editor},
        File: class File {}, AbortController, console,
        setInterval: () => 1, clearInterval() {},
        EventSource: class {
            constructor(url) { this.url = url; streams.push(this); }
            close() { this.closed = true; }
            emit(message) { this.onmessage({data: JSON.stringify(message)}); }
        },
        fetch: (url, options = {}) => {
            if (options.method === 'DELETE') { requests.push({url, deleted: true}); return Promise.resolve({ok: true}); }
            return new Promise(resolve => requests.push({url, options, resolve}));
        },
    });
    const js = fs.readFileSync(path.join(__dirname, '../web_ui/static/r1cs.js'), 'utf8');
    const html = fs.readFileSync(path.join(__dirname, '../web_ui/templates/index.html'), 'utf8');
    const inline = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace('{{ examples | tojson }}', '{}');
    vm.runInContext(js + '\n' + inline, context);
    const run = code => vm.runInContext(code, context);
    const r1cs = (id = 'file-a', count = 60) => ({kind: 'r1cs', id,
        metadata: {filename: 'demo.r1cs', size_bytes: 100, constraints: count, wires: 2,
            public_outputs: 1, public_inputs: 0, private_inputs: 0, prime: '17'}});
    const open = async (name, file, replace = false) => {
        context.fixture = file;
        await run(`openFile(${JSON.stringify(name)}, fixture, ${replace})`);
    };
    const respond = async (request, offset = 0, total = 60) => {
        request.resolve({ok: true, json: async () => ({offset, total, limit: 50,
            constraints: [{index: offset, a: [{wire: 0, coefficient: '16'}, {wire: 1, coefficient: '1'}],
                b: [{wire: 1, coefficient: '1'}], c: []}]})});
        await new Promise(resolve => setImmediate(resolve));
    };
    return {run, context, editor, panel, document, requests, streams, open, r1cs, respond};
}

test('source edits survive R1CS switching, same-name replacement and clear release uploads', async () => {
    const h = harness();
    await h.open('source.circom', {kind: 'circom', content: 'original'});
    h.editor.setValue('edited source');
    await h.open('demo.r1cs', h.r1cs());
    assert.equal(h.document.getElementById('analyzeBtn').hidden, false);
    await h.respond(h.requests.at(-1));
    assert.match(h.panel.querySelector('.r1cs-constraints').children[0].children[1].textContent, /w1 − 1/);
    h.run("switchToFile('source.circom')");
    assert.equal(h.editor.getValue(), 'edited source');
    assert.equal(h.panel.hidden, true);
    await h.open('demo.r1cs', h.r1cs('file-b'), true);
    assert.ok(h.requests.some(r => r.deleted && r.url === '/r1cs/file-a'));
    assert.equal(h.run("openFiles['demo.r1cs'].id"), 'file-b');
    assert.equal(h.document.getElementById('editorTabs').children.length, 2);
    h.run('clearAll()');
    assert.ok(h.requests.some(r => r.deleted && r.url === '/r1cs/file-b'));
    assert.equal(h.document.getElementById('editorTabs').children.length, 0);
    assert.equal(h.editor.getValue(), '');
    assert.equal(h.document.getElementById('analyzeBtn').hidden, false);
});

test('late page responses do not replace the active file, and paging/mode persist', async () => {
    const h = harness();
    await h.open('a.r1cs', h.r1cs('a'));
    const old = h.requests.at(-1);
    await h.open('b.r1cs', h.r1cs('b'));
    await h.respond(h.requests.at(-1));
    await h.respond(old, 50);
    assert.equal(h.panel.querySelector('.r1cs-status').textContent, 'Constraints 1–1 of 60');
    h.panel.querySelector('[data-action="next"]').onclick();
    assert.match(h.requests.at(-1).url, /offset=50/);
    await h.respond(h.requests.at(-1), 50);
    const mode = h.panel.querySelector('[data-action="coefficients"]');
    mode.value = 'raw'; mode.onchange();
    assert.match(h.panel.querySelector('.r1cs-constraints').children[0].children[1].textContent, /w1 \+ 16/);
    h.run("switchToFile('a.r1cs'); switchToFile('b.r1cs')");
    assert.match(h.requests.at(-1).url, /offset=50/);
    assert.equal(mode.value, 'raw');
    const pending = h.requests.at(-1);
    h.run('clearAll()');
    await h.respond(pending, 50);
    assert.equal(h.panel.hidden, true);
});

test('expiry messages remain visible and leave paging disabled', async () => {
    const h = harness();
    await h.open('demo.r1cs', h.r1cs());
    h.requests.at(-1).resolve({ok: false, json: async () => ({error: 'File expired. Please upload it again.'})});
    await new Promise(resolve => setImmediate(resolve));
    assert.match(h.panel.querySelector('.r1cs-status').textContent, /expired/);
    assert.equal(h.panel.querySelector('[data-action="next"]').disabled, true);
});

function reportFixture() {
    return {kind: 'r1cs', engine: 'picus', verdict: 'safe', reason: 'completed',
        elapsed_seconds: 0.01, revision: '138b151', logs: ['The circuit is properly constrained'],
        counterexample: null};
}

async function startAnalysis(h) {
    const pending = h.run('analyze()');
    const request = h.requests.at(-1);
    request.resolve({ok: true, json: async () => ({session_id: 'analysis-1'})});
    await pending;
    return request;
}

test('R1CS Analyze dispatches by id and preserves reports across tab switches without false passes', async () => {
    const h = harness();
    await h.open('demo.r1cs', h.r1cs());
    const request = await startAnalysis(h);
    assert.equal(request.url, '/r1cs/file-a/analyze');
    assert.deepEqual(JSON.parse(request.options.body), {});
    const stream = h.streams.at(-1);
    stream.emit({type: 'progress', data: {message: 'Solving', elapsed_seconds: 3}});
    assert.equal(h.document.getElementById('progressBar').textContent, 'Picus');
    assert.match(h.document.getElementById('progressText').textContent, /Solving.*3s/);
    await h.open('source.circom', {kind: 'circom', content: 'edited source'});
    stream.emit({type: 'complete', result: reportFixture()});
    assert.equal(h.document.getElementById('result').style.display, 'none');
    assert.equal(h.run("openFiles['demo.r1cs'].result.type"), 'r1cs');
    h.run("switchToFile('demo.r1cs')");
    const result = h.document.getElementById('result');
    assert.equal(result.className, 'result success r1cs-report');
    assert.equal(result.querySelectorAll('.coverage-unavailable').length, 0);
    assert.equal(result.querySelector('h3').textContent, 'Analysis Result');
    assert.match(result.querySelector('.r1cs-result-output').textContent, /\[Success\]\s+Output uniqueness verified/);
    assert.ok(stream.closed);
    assert.equal(h.run('analysisBusy'), false);
    assert.equal(h.editor.getValue(), 'edited source');
});

test('replacement and Clear cannot receive stale R1CS analysis results', async () => {
    const h = harness();
    await h.open('demo.r1cs', h.r1cs());
    await startAnalysis(h);
    const first = h.streams.at(-1);
    await h.open('demo.r1cs', h.r1cs('replacement'), true);
    first.emit({type: 'complete', result: reportFixture()});
    assert.equal(h.run("openFiles['demo.r1cs'].result"), null);
    assert.equal(h.document.getElementById('result').style.display, 'none');
    await startAnalysis(h);
    const second = h.streams.at(-1);
    h.run('clearAll()');
    second.emit({type: 'complete', result: reportFixture()});
    assert.equal(h.document.getElementById('result').style.display, 'none');
    assert.equal(h.run('analysisBusy'), false);
    assert.ok(second.closed);
    assert.ok(h.requests.some(r => r.url === '/stop/analysis-1'));
});

test('expired R1CS analysis requests show the error and re-enable Analyze', async () => {
    const h = harness();
    await h.open('demo.r1cs', h.r1cs());
    const pending = h.run('analyze()');
    h.requests.at(-1).resolve({ok: false, json: async () => ({error: 'File expired. Please upload it again.'})});
    await pending;
    assert.equal(h.run('analysisBusy'), false);
    assert.equal(h.document.getElementById('analyzeBtn').disabled, false);
    assert.match(h.document.getElementById('result').innerHTML, /File expired/);
});

test('Circom Analyze still sends edited source and displays its text report', async () => {
    const h = harness();
    await h.open('source.circom', {kind: 'circom', content: 'original'});
    h.editor.setValue('edited');
    const request = await startAnalysis(h);
    assert.equal(request.url, '/analyze');
    assert.deepEqual(JSON.parse(request.options.body), {code: 'edited'});
    h.streams.at(-1).emit({type: 'complete', result: 'Total warnings: 2'});
    assert.match(h.document.getElementById('result').innerHTML, /Total warnings: 2/);
});

test('Picus counterexamples preserve decimal strings, highlight differences and never fill missing values', async () => {
    const h = harness();
    const big = '21888242871839275222246405745257275088548364400416034343698204186575808495616';
    h.context.picusFixture = {...reportFixture(), verdict: 'unsafe', logs: ['<script>bad()</script>'],
        counterexample: {inputs: [{wire: 3, value: big}],
            outputs: [{wire: 1, first: '1', second: big}, {wire: 2, first: '2', second: null}]}};
    h.run("renderR1CSReport(document.getElementById('result'), picusFixture)");
    const result = h.document.getElementById('result');
    const values = result.querySelectorAll('td').map(node => node.textContent);
    assert.ok(values.includes(big));
    assert.ok(values.includes('Not provided'));
    assert.equal(result.className, 'result warning r1cs-report');
    assert.match(result.querySelector('.r1cs-result-output').textContent, /\[Warning\]\s+Picus found an underconstrained circuit/);
    assert.equal(result.querySelectorAll('.picus-difference').length, 1);
    assert.equal(result.querySelector('.picus-raw-log').textContent, '<script>bad()</script>');
    assert.equal(result.querySelectorAll('script').length, 0);
});

test('closing an R1CS tab before the start response cancels its late session', async () => {
    const h = harness();
    await h.open('demo.r1cs', h.r1cs());
    const pending = h.run('analyze()');
    const request = h.requests.at(-1);
    h.run('clearAll()');
    request.resolve({ok: true, json: async () => ({session_id: 'late-session'})});
    await pending;
    assert.ok(h.requests.some(r => r.url === '/stop/late-session'));
    assert.equal(h.streams.length, 0);
    assert.equal(h.run('analysisBusy'), false);
});
