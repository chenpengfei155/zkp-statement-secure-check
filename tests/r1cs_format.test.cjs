// Run with node --test tests/r1cs_format.test.cjs (no npm dependencies).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../web_ui/static/r1cs.js'), 'utf8'), context);
const format = context.formatR1CSCombination;
const prime = 21888242871839275222246405745257275088548364400416034343698204186575808495617n;

test('signed coefficients preserve precision and keep constants last', () => {
    const terms = [{wire: 0, coefficient: String(prime - 1n)}, {wire: 3, coefficient: '1'}];
    assert.equal(format(terms, prime), 'w3 − 1');
    assert.equal(format(terms, prime, false), `w3 + ${prime - 1n}`);
    assert.equal(terms[0].coefficient, String(prime - 1n));
});
test('empty, negative, zero, and non-default fields', () => {
    assert.equal(format([], 17n), '0');
    assert.equal(format([{wire: 1, coefficient: '16'}, {wire: 3, coefficient: '2'}], 17n), '−w1 + 2 × w3');
    assert.equal(format([{wire: 1, coefficient: '0'}], 17n), '0');
    assert.equal(format([{wire: 0, coefficient: '8'}], 17n), '8');
    assert.equal(format([{wire: 0, coefficient: '9'}], 17n), '−8');
});
