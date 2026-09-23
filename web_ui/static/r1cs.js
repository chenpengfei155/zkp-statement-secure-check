/* Display arithmetic modulo the file's own prime, without Number conversion. */
function formatR1CSCombination(terms, prime, signed = true) {
    const parts = [];
    // Put constants last so bit constraints read naturally as (w3 - 1).
    const ordered = [...terms.filter(t => t.wire !== 0), ...terms.filter(t => t.wire === 0)];
    for (const term of ordered) {
        let coefficient = BigInt(term.coefficient);
        if (signed && coefficient > prime / 2n) coefficient -= prime;
        if (coefficient === 0n) continue;
        const negative = coefficient < 0n;
        const magnitude = negative ? -coefficient : coefficient;
        const body = term.wire === 0 ? String(magnitude)
            : `${magnitude === 1n ? '' : magnitude + ' × '}w${term.wire}`;
        parts.push(`${parts.length ? (negative ? ' − ' : ' + ') : (negative ? '−' : '')}${body}`);
    }
    return parts.join('') || '0';
}

class R1CSViewer {
    constructor(panel) {
        this.panel = panel;
        this.state = null;
        this.request = null;
        this.revision = 0;
        this.previous = panel.querySelector('[data-action="previous"]');
        this.next = panel.querySelector('[data-action="next"]');
        this.mode = panel.querySelector('[data-action="coefficients"]');
        this.previous.onclick = () => this.load(Math.max(0, this.state.offset - 50));
        this.next.onclick = () => this.load(this.state.offset + 50);
        this.mode.onchange = () => {
            if (!this.state) return;
            this.state.signed = this.mode.value === 'signed';
            if (this.state.page) this.renderPage(this.state.page);
        };
    }

    hide() {
        this.revision++;
        if (this.request) this.request.abort();
        this.state = null;
        this.panel.hidden = true;
    }

    show(state) {
        this.hide();
        this.state = state;
        this.panel.hidden = false;
        const metadata = state.metadata;
        this.panel.querySelector('.r1cs-filename').textContent = metadata.filename;
        this.panel.querySelector('.r1cs-file-meta').textContent =
            `R1CS · Constraint View  /  ${metadata.size_bytes.toLocaleString()} bytes`;
        this.panel.querySelector('.r1cs-prime').textContent = metadata.prime;
        this.panel.querySelector('.r1cs-details').open = false;
        const summary = this.panel.querySelector('.r1cs-summary');
        summary.replaceChildren();
        for (const [label, value] of [
            ['Constraints', metadata.constraints], ['Variables', metadata.wires],
            ['Public outputs', metadata.public_outputs], ['Public inputs', metadata.public_inputs],
            ['Private inputs', metadata.private_inputs],
        ]) {
            const stat = document.createElement('div');
            stat.className = 'r1cs-stat';
            const name = document.createElement('dt');
            const content = document.createElement('dd');
            name.textContent = label;
            content.textContent = value;
            stat.append(name, content);
            summary.append(stat);
        }
        this.mode.value = state.signed ? 'signed' : 'raw';
        const hint = this.panel.querySelector('.r1cs-analysis-hint');
        if (hint) {
            hint.textContent = '检测引擎：Picus · 正在检查环境…';
            fetch('/r1cs/engine').then(response => response.json()).then(info => {
                if (this.state !== state) return;
                hint.textContent = info.ready
                    ? '检测引擎：Picus · 点击 Analyze 检查输出唯一性，结果显示在下方。'
                    : info.reason;
            }).catch(() => {
                if (this.state === state) hint.textContent = '无法查询 Picus 环境，请检查网页服务。';
            });
        }
        this.load(state.offset);
    }

    async load(offset) {
        if (!this.state) return;
        if (this.request) this.request.abort();
        const controller = new AbortController();
        this.request = controller;
        const revision = ++this.revision;
        const state = this.state;
        const status = this.panel.querySelector('.r1cs-status');
        this.previous.disabled = this.next.disabled = true;
        this.mode.disabled = true;
        status.textContent = 'Loading constraints…';
        status.classList.remove('error-text');
        this.panel.querySelector('.r1cs-constraints').replaceChildren();
        try {
            const response = await fetch(`/r1cs/${state.id}/constraints?offset=${offset}&limit=50`,
                {signal: controller.signal});
            const page = await response.json();
            if (!response.ok) throw new Error(page.error || 'Could not load constraints.');
            if (this.state !== state || revision !== this.revision) return;
            state.offset = offset;
            state.page = page;
            this.mode.disabled = false;
            this.renderPage(page);
        } catch (error) {
            if (error.name === 'AbortError' || this.state !== state || revision !== this.revision) return;
            status.textContent = error.message;
            status.classList.add('error-text');
        }
    }

    renderPage(page) {
        const state = this.state;
        const prime = BigInt(state.metadata.prime);
        const container = this.panel.querySelector('.r1cs-constraints');
        container.replaceChildren();
        for (const constraint of page.constraints) {
            const item = document.createElement('div');
            item.className = 'r1cs-constraint';
            const label = document.createElement('span');
            label.textContent = `#${constraint.index + 1}`;
            const formula = document.createElement('pre');
            const combination = name => formatR1CSCombination(constraint[name], prime, state.signed);
            formula.textContent = `(${combination('a')}) × (${combination('b')}) − (${combination('c')}) = 0`;
            item.append(label, formula);
            container.append(item);
        }
        const end = page.offset + page.constraints.length;
        this.panel.querySelector('.r1cs-status').textContent = page.total
            ? `Constraints ${page.offset + 1}–${end} of ${page.total}` : 'This file contains no constraints.';
        this.previous.disabled = page.offset === 0;
        this.next.disabled = end >= page.total;
    }
}

function renderR1CSReport(container, report) {
    container.className = 'result r1cs-report';
    container.replaceChildren();
    container.style.display = 'block';
    const element = (tag, text, className = '') => {
        const node = document.createElement(tag);
        node.textContent = text;
        node.className = className;
        return node;
    };
    const titles = {safe: '输出唯一性已验证', unsafe: 'Picus 发现欠约束',
        unknown: '无法确定', error: '分析未完成', cancelled: '本次分析已取消',
        not_applicable: '没有可检查的输出'};
    const reasons = {
        timeout: '达到整个任务的时间上限，未得出结论。',
        solver_inconclusive: '求解器未能在查询预算内得出结论。',
        no_outputs: '此文件没有公开输出，未执行输出唯一性检查。',
        invalid_output: 'Picus 返回了无法识别或不完整的结果，请查看运行日志。',
        process_error: 'Picus 或求解器运行失败，请查看日志。',
        supervisor_error: 'Picus 运行进程异常结束，请查看日志。',
        runtime_error: '无法完成 Picus 调用，请查看日志并检查安装。',
    };
    container.append(element('p', 'PICUS · R1CS ANALYSIS', 'report-eyebrow'));
    container.append(element('h3', titles[report.verdict] || '分析未完成', `picus-verdict verdict-${report.verdict}`));
    container.append(element('p', `${report.filename} · ${report.elapsed_seconds}s · cvc5`, 'report-meta'));
    container.append(element('p',
        '检测范围：全部输入（包括私有输入）相同时，公开输出是否唯一。' +
        '这不代表业务逻辑正确，也不代表所有安全问题都已排除。', 'report-notice'));
    if (reasons[report.reason]) container.append(element('p', reasons[report.reason]));
    if (report.verdict === 'unsafe') {
        container.append(element('h4', '反例 · 相同输入，不同输出'));
        const cex = report.counterexample;
        if (cex) {
            const table = (headers, rows, differs = () => false) => {
                const wrap = element('div', '', 'report-table-wrap');
                const node = element('table', '');
                const head = element('thead', '');
                const row = element('tr', '');
                headers.forEach(value => row.append(element('th', value)));
                head.append(row);
                const body = element('tbody', '');
                rows.forEach(values => {
                    const tr = element('tr', '', differs(values) ? 'picus-difference' : '');
                    values.forEach(value => tr.append(element('td', value == null ? '未提供' : value)));
                    body.append(tr);
                });
                node.append(head, body); wrap.append(node); container.append(wrap);
            };
            if (cex.inputs.length) table(['输入变量', '两组共同的输入值'],
                cex.inputs.map(item => [`w${item.wire}`, item.value]));
            else container.append(element('p', '反例未列出输入值。'));
            table(['输出变量', '第一组输出', '第二组输出'],
                cex.outputs.map(item => [`w${item.wire}`, item.first, item.second]),
                values => values[1] != null && values[2] != null && values[1] !== values[2]);
            if (cex.truncated) container.append(element('p', '反例较长，每组只展示前 500 个变量。'));
        } else {
            container.append(element('p', 'Picus 已报告欠约束，但反例未能整理，请展开原始日志查看。'));
        }
    }
    const details = element('details', '', 'picus-logs');
    details.append(element('summary', '运行详情与日志'));
    details.append(element('p', `引擎：Veridise/Picus · ${report.revision || ''}`, 'report-meta'));
    details.append(element('pre', (report.logs || []).join('\n') || '没有运行日志。'));
    if (report.logs_truncated) details.append(element('p', '仅保留最近 1000 条日志，每条最多显示 1000 个字符。'));
    container.append(details);
}
