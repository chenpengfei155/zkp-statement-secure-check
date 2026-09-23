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
            hint.textContent = 'Engine: Picus · Checking environment…';
            fetch('/r1cs/engine').then(response => response.json()).then(info => {
                if (this.state !== state) return;
                hint.textContent = info.ready
                    ? `${info.platform === 'windows-native' ? 'Native Windows ' : ''}Picus + cvc5 · Analyze runs uniqueness, satisfiability and structural checks.`
                    : info.reason;
            }).catch(() => {
                if (this.state === state) hint.textContent = 'Could not check the Picus environment. Check the web service.';
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
    const outcomes = {
        safe: {style: 'success', level: 'Success', message: 'Output uniqueness verified.'},
        unsafe: {style: 'warning', level: 'Warning', message: 'Picus found an underconstrained circuit.'},
        unknown: {style: 'warning', level: 'Warning', message: 'Output uniqueness could not be determined.'},
        error: {style: 'error', level: 'Error', message: 'Analysis did not complete.'},
        cancelled: {style: 'info', level: 'Info', message: 'Analysis cancelled by the user.'},
        not_applicable: {style: 'info', level: 'Info', message: 'No public outputs to check.'},
        warning: {style: 'warning', level: 'Warning', message: 'Analysis completed with findings to review.'},
        unsatisfiable: {style: 'error', level: 'Error', message: 'The constraints have no solution.'},
    };
    const checks = Array.isArray(report.checks) ? report.checks : null;
    if (checks) {
        outcomes.safe.message = 'All six checks passed.';
        outcomes.unknown.message = 'Some checks could not reach a conclusion.';
        outcomes.not_applicable.message = 'No public outputs; see the remaining check results below.';
    }
    const verdict = Object.hasOwn(outcomes, report.verdict) ? report.verdict : 'error';
    const outcome = outcomes[verdict];
    container.className = `result ${outcome.style} r1cs-report`;
    container.replaceChildren();
    container.style.display = 'block';
    const element = (tag, text, className = '') => {
        const node = document.createElement(tag);
        node.textContent = text;
        node.className = className;
        return node;
    };
    const reasons = {
        timeout: 'The task time limit was reached without a conclusion.',
        solver_inconclusive: 'The solver could not reach a conclusion within its query budget.',
        no_outputs: 'Output uniqueness was not checked because this file has no public outputs.',
        invalid_output: 'Picus returned an invalid or incomplete response. See the run logs.',
        process_error: 'Picus or the solver failed. See the run logs.',
        supervisor_error: 'The Picus supervisor ended unexpectedly. See the run logs.',
        runtime_error: 'Could not run Picus. Check the installation and run logs.',
    };
    const logLine = (level, message) => `[${level}]`.padEnd(13) + message;
    const lines = [
        logLine('Info', `File: ${report.filename || 'Not provided'}`),
        logLine('Info', `Engine: Picus | Solver: ${report.solver || 'cvc5'}`),
        logLine('Info', checks ? 'Uniqueness, satisfiability and structural checks.'
            : 'Checking output uniqueness for identical public and private inputs.'),
    ];
    if (checks) {
        const levels = {pass: 'Success', fail: 'Warning', warning: 'Warning', unknown: 'Unknown',
            error: 'Error', cancelled: 'Cancelled', skipped: 'Skipped'};
        lines.push('');
        for (const check of checks) {
            const level = check.id === 'satisfiability' && check.status === 'fail' ? 'Error'
                : Object.hasOwn(levels, check.status) ? levels[check.status] : 'Error';
            lines.push(logLine(level, `${check.name}: ${check.message}`));
        }
        lines.push('');
    }
    lines.push(logLine(outcome.level, outcome.message));
    if (Object.hasOwn(reasons, report.reason)) lines.push(logLine(outcome.level, reasons[report.reason]));
    if (Number.isFinite(report.elapsed_seconds)) {
        lines.push(logLine('Timeit', `Elapsed time: ${report.elapsed_seconds} s`));
    }
    lines.push('', '='.repeat(50), `Result: ${verdict}`);
    container.append(element('h3', 'Analysis Result'));
    container.append(element('pre', lines.join('\n'), 'r1cs-result-output'));
    for (const check of checks || []) {
        if (!check.findings?.length) continue;
        const findingDetails = element('details', '', 'picus-logs');
        findingDetails.append(element('summary', `${check.name}: ${check.count} finding(s)`));
        const list = check.findings.map(item => item.wire != null ? `w${item.wire}`
            : item.duplicate_of != null ? `Constraint #${item.constraint + 1} repeats #${item.duplicate_of + 1}`
            : `Constraint #${item.constraint + 1} simplifies to 0 = 0`);
        findingDetails.append(element('pre', list.join('\n')));
        if (check.truncated) findingDetails.append(element('p', 'Showing the first 100 findings.'));
        if (!check.complete) findingDetails.append(element('p', 'Partial scan: structural-check work limit reached.'));
        container.append(findingDetails);
    }
    const counterexamples = checks ? checks.filter(check => check.verdict === 'unsafe')
        : verdict === 'unsafe' ? [{id: 'output_uniqueness', counterexample: report.counterexample}] : [];
    for (const check of counterexamples) {
        container.append(element('h4', check.id === 'signal_uniqueness'
            ? 'All-signal counterexample: same inputs, different signal values'
            : 'Counterexample: same inputs, different outputs'));
        const cex = check.counterexample;
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
                    values.forEach(value => tr.append(element('td', value == null ? 'Not provided' : value)));
                    body.append(tr);
                });
                node.append(head, body); wrap.append(node); container.append(wrap);
            };
            if (cex.inputs?.length) table(['Input wire', 'Shared input value'],
                cex.inputs.map(item => [`w${item.wire}`, item.value]));
            else container.append(element('p', 'No input values were listed in the counterexample.'));
            if (cex.outputs?.length) table(['Output wire', 'First output', 'Second output'],
                cex.outputs.map(item => [`w${item.wire}`, item.first, item.second]),
                values => values[1] != null && values[2] != null && values[1] !== values[2]);
            if (cex.internal?.length) table(['Internal wire', 'First value', 'Second value'],
                cex.internal.map(item => [`w${item.wire}`, item.first, item.second]),
                values => values[1] != null && values[2] != null && values[1] !== values[2]);
            if (cex.truncated) container.append(element('p', 'Only the first 500 variables in each group are shown.'));
        } else {
            container.append(element('p', 'Picus reported underconstraint, but the counterexample could not be formatted. See the raw run logs.'));
        }
    }
    const details = element('details', '', 'picus-logs');
    details.append(element('summary', 'Run details and raw logs'));
    const runDetails = [logLine('Info', 'Engine: Veridise/Picus')];
    if (report.revision) runDetails.push(logLine('Info', `Revision: ${report.revision}`));
    if (report.exit_code != null) runDetails.push(logLine('Info', `Exit code: ${report.exit_code}`));
    if (report.reason) runDetails.push(logLine('Info', `Reason: ${report.reason}`));
    details.append(element('pre', runDetails.join('\n')));
    details.append(element('pre', (report.logs || []).join('\n') || 'No run logs available.', 'picus-raw-log'));
    if (report.logs_truncated) details.append(element('p', 'Only the latest 1,000 log entries are retained, with up to 1,000 characters per entry.'));
    container.append(details);
}
