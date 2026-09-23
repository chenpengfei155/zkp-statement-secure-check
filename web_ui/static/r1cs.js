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
    container.append(element('h3', 'R1CS Analysis · 约束检查结果'));
    container.append(element('p', `${report.filename} · 已扫描 ${report.stats.constraints_scanned} / ${report.stats.constraints_total} 条约束 · ${report.elapsed_seconds}s`, 'report-meta'));
    const headline = report.status === 'partial' ? '部分检查未完成' : '可用检查已完成';
    container.append(element('p', `${headline} · ${report.counts.error} 项错误 · ${report.counts.warning} 项警告 · ${report.counts.info} 项提示`));
    container.append(element('p',
        '检测范围：未参与有效约束的输出和变量、恒成立的约束，以及常量或有界线性推导可确认的矛盾。' +
        '原有 9 类检测中，2 类仅能部分检查，7 类因缺少源码信息无法检查。未发现问题不代表电路安全；' +
        '本次未验证输出唯一性、全部非线性约束的可满足性或业务逻辑。', 'report-notice'));
    if (report.incomplete_reasons.length) {
        container.append(element('p', '达到计算预算或时间限制，以下范围未全部检查：'));
        const reasons = element('ul', '');
        for (const reason of report.incomplete_reasons) reasons.append(element('li', reason));
        container.append(reasons);
    }
    const titles = {
        unconstrained_output: '输出未参与有效约束', unused_wire: '变量未参与有效约束',
        ineffective_constraint: '无效约束：等价于 0 = 0',
        impossible_constraint: '约束矛盾：该约束无法满足',
        contradictory_linear_constraints: '线性约束矛盾：这些约束无法同时满足',
    };
    const severity = {error: '错误', warning: '警告', info: '提示'};
    const findings = element('div', '', 'report-findings');
    for (const finding of report.findings) {
        const card = element('div', '', `report-finding report-${finding.severity}`);
        card.append(element('strong', `${severity[finding.severity]} · ${titles[finding.code] || finding.code}`));
        card.append(element('p', finding.message));
        const evidence = [
            finding.wires.length ? `变量：${finding.wires.map(wire => 'w' + wire).join(', ')}` : '',
            finding.constraints.length ? `约束：${finding.constraints.map(index => '#' + (index + 1)).join(', ')}` : '',
        ].filter(Boolean).join(' · ');
        if (evidence) card.append(element('p', evidence + (finding.evidence_truncated
            ? ` …（共 ${finding.affected_count} 项，仅展示部分编号）` : ''), 'report-evidence'));
        findings.append(card);
    }
    if (!report.findings.length) findings.append(element('p', '本次可用检查未发现问题。请结合下方的检测覆盖范围解读此结果。'));
    if (report.findings_truncated) findings.append(element('p', '结果较多，仅显示前 200 条记录；上方计数包含其余记录。'));
    container.append(findings);
    container.append(element('h3', '原有检测项的覆盖范围'));
    const reasons = {
        unconstrained_output: '仅检查公开输出是否完全缺少有效约束；无法据此确认输出唯一。',
        unconstrained_component_input: 'R1CS 不保留组件边界和组件输入声明。',
        data_flow_constraint_discrepancy: 'R1CS 不保留原来的计算过程，无法与约束比较。',
        unused_component_output: 'R1CS 不保留组件边界及源码中的使用关系。',
        type_mismatch: 'R1CS 不保留源码中的类型和模板信息。',
        assignment_misuse: 'R1CS 不记录原来使用了 <-- 还是 <== 等赋值语句。',
        unused_signal: '仅检查保留下来的变量是否参与有效约束；无法还原被优化掉的信号声明。',
        divide_by_zero: 'R1CS 不保留原来的除法语句及计算过程。',
        nondeterministic_data_flow: 'R1CS 不保留原来的条件计算过程；本次也不检查输出唯一性。',
    };
    const wrap = element('div', '', 'report-table-wrap');
    const table = element('table', '');
    const head = element('thead', '');
    const header = element('tr', '');
    for (const label of ['检测项', '支持范围', '说明']) header.append(element('th', label));
    head.append(header);
    const body = element('tbody', '');
    for (const check of report.coverage) {
        const row = element('tr', '');
        row.append(element('td', check.name));
        row.append(element('td', check.status === 'unavailable' ? '无法检查' : '部分检查',
            `coverage-status coverage-${check.status}`));
        row.append(element('td', reasons[check.code] || check.reason));
        body.append(row);
    }
    table.append(head, body);
    wrap.append(table);
    container.append(wrap);
}
