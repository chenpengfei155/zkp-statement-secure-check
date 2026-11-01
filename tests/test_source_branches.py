"""Witness conditionals preserve both data paths without inventing constraints."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from cirverify.core import detect
from web_ui.source_analysis import analyze_source


class SourceBranchTests(unittest.TestCase):
    def analyze(self, body, definitions=''):
        source = '''pragma circom 2.0.0;
''' + definitions + '''
template Main() {
    signal input cond;
    signal input a;
    signal input b;
    signal input c;
    signal output out;
''' + body + '''
}
component main = Main();
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'conditional.circom'
            path.write_text(source, encoding='utf-8')
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                graphs, reports = detect(str(path))
        return graphs, reports, output.getvalue()

    def assert_dependencies(self, body, expected):
        graphs, reports, output = self.analyze(body)
        self.assertIsNotNone(graphs, output)
        self.assertIsNotNone(reports, output)
        graph = graphs['Main']
        actual = {name for name in ('cond', 'a', 'b', 'c')
                  if graph.has_path_depend(graph.nodes[name], graph.nodes['out'])}
        self.assertEqual(actual, set(expected))

    def test_unknown_single_branch_keeps_previous_value_and_condition(self):
        self.assert_dependencies('var x = a; if (cond) x = b; out <-- x;',
                                 {'cond', 'a', 'b'})

    def test_unknown_if_else_merges_both_scalar_assignments(self):
        self.assert_dependencies('var x = 0; if (cond) { x = a; } else { x = b; } out <-- x;',
                                 {'cond', 'a', 'b'})

    def test_unknown_ternary_preserves_both_rhs_dependencies(self):
        self.assert_dependencies('out <-- cond ? a : b;', {'cond', 'a', 'b'})

    def test_nested_known_ternaries_use_their_own_conditions(self):
        self.assert_dependencies('out <-- 1 ? (0 ? a : b) : c;', {'b'})
        self.assert_dependencies('out <-- cond ? (1 ? a : c) : b;', {'cond', 'a', 'b'})

    def test_known_if_evaluates_only_its_selected_branch(self):
        self.assert_dependencies('var x = 0; if (1) x = a; else x = 1 / 0; out <-- x;', {'a'})
        self.assert_dependencies('var x = 0; if (0) x = 1 / 0; else x = b; out <-- x;', {'b'})

    def test_known_ternary_evaluates_only_its_selected_branch(self):
        self.assert_dependencies('out <-- 1 ? a : 1 / 0;', {'a'})
        self.assert_dependencies('out <-- 0 ? 1 / 0 : b;', {'b'})

    def test_unknown_assignment_does_not_retain_an_old_constant(self):
        self.assert_dependencies('var x = 1; x = cond; var y = a; if (x) y = b; out <-- y;',
                                 {'cond', 'a', 'b'})

    def test_self_referential_rhs_uses_the_old_condition_value(self):
        self.assert_dependencies('var x = 1; x = x ? a : b; out <-- x;', {'a'})

    def test_unknown_branch_with_constraints_is_not_silently_merged(self):
        graphs, _, output = self.analyze('if (cond) out <== a; else out <== b;')
        self.assertIsNone(graphs)
        self.assertIn('unsupported branch merging', output)

    def test_unknown_component_branch_is_not_silently_merged(self):
        graphs, _, output = self.analyze('''
component unit;
if (cond) unit = Unit(); else unit = Unit();
unit.in <== a;
out <== unit.out;
''', 'template Unit() { signal input in; signal output out; out <== in; }')
        self.assertIsNone(graphs)
        self.assertIn('unsupported branch merging', output)

    def test_unknown_multi_assignment_branch_remains_incomplete(self):
        graphs, _, output = self.analyze('var x = a; if (cond) { x = b; x = c; } out <-- x;')
        self.assertIsNone(graphs)
        self.assertIn('unsupported branch merging', output)

    @unittest.skipUnless((ROOT / 'benchmarks/entries/circomlib-cff5ab6__Bits2Point_Strict@pointbits.circom').is_file(),
                         'requires the Bits2Point benchmark fixture')
    def test_real_bits_to_point_witness_conditional_completes(self):
        path = ROOT / 'benchmarks/entries/circomlib-cff5ab6__Bits2Point_Strict@pointbits.circom'
        result = analyze_source(path, filename=path.name, timeout_seconds=30)
        self.assertEqual(result['status'], 'completed', result)
        self.assertNotIn(result.get('reason'), ('graph_error', 'timeout'))


if __name__ == '__main__':
    unittest.main()
