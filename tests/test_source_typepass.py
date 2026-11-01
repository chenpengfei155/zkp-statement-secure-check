"""Bare control-flow bodies use the declaration pass without early port checks."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
for directory in ('parser', 'astgen', 'typecheck', 'utils', 'cdggen', 'detect', 'target'):
    sys.path.append(str(ROOT / 'src' / 'cirverify' / directory))
from cirverify.core import detect, generate_ast
from Report import Report
from StaticCheck import PrimeField, TypeCheck


UNIT = 'template Unit() { signal output out; out <== 1; }'


class TypePassTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'typepass.circom'

    def source(self, body, definitions=UNIT):
        self.path.write_text('pragma circom 2.0.0;\n' + definitions
                             + '\ntemplate Main() {\n' + body
                             + '\n}\ncomponent main = Main();\n', encoding='utf-8')
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            ast = generate_ast(str(self.path))
        self.assertIsNotNone(ast, output.getvalue())
        return ast

    def assert_complete(self, body, definitions=UNIT):
        checked = TypeCheck(self.source(body, definitions))
        checked.check()
        self.assertEqual(checked.count_visited, 2)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            graphs, reports = detect(str(self.path))
        self.assertIsNotNone(graphs, output.getvalue())
        self.assertIsNotNone(reports, output.getvalue())
        return checked

    def test_bare_if_without_else_checks_ports_after_component_assignment(self):
        self.assert_complete('component unit = Unit(); if (1) unit.out === 1;')

    def test_bare_if_else_in_nested_loops_accepts_array_component_ports(self):
        definitions = '''template Matrix() {
signal output out[2][2];
for (var i = 0; i < 2; i++) for (var j = 0; j < 2; j++) {
    out[i][j] <== (i == 0 && j == 0) ? 1 : 0;
}
}'''
        self.assert_complete('''component matrix = Matrix();
for (var i = 0; i < 2; i++) for (var j = 0; j < 2; j++) {
    if (i == 0 && j == 0) matrix.out[i][j] === 1;
    else matrix.out[i][j] === 0;
}''', definitions)

    def test_bare_while_body_is_deferred_to_the_checking_pass(self):
        # A constant-false loop is finite but its body still needs type checking.
        self.assert_complete('component unit = Unit(); while (0) unit.out === 1;')

    def test_first_pass_still_collects_bare_and_nested_function_returns(self):
        definitions = '''function Choose(flag) {
if (flag) return 1; else return 0;
}
function Nested(flag) {
if (flag) {
    var values[2];
    values[0] = 1;
    values[1] = 2;
    return values[0];
} else return 0;
}
function WhileReturn(flag) {
while (flag) return 1;
return 0;
}'''
        checked = self.assert_complete('''signal output out;
out <== Choose(1) + Nested(1) + WhileReturn(0);''', definitions)
        self.assertEqual(set(checked.list_function), {'Choose', 'Nested', 'WhileReturn'})

    def test_min_max_infer_parameter_return_types_from_conditions(self):
        for operator in ('<', '>'):
            for otherwise in ('return b;', 'else return b;'):
                with self.subTest(operator=operator, otherwise=otherwise):
                    definitions = ('function Pick(a, b) { if (a ' + operator
                                   + ' b) return a; ' + otherwise + ' }')
                    checked = self.assert_complete(
                        'signal output out; out <== Pick(2, 3);', definitions)
                    function = checked.list_function['Pick'].mtype
                    self.assertIsInstance(function.return_type, PrimeField)
                    self.assertTrue(all(isinstance(arg, PrimeField) for arg in function.params))

    def test_while_condition_infers_returned_function_parameters(self):
        checked = self.assert_complete('signal output out; out <== Pick(2, 3);',
            'function Pick(a, b) { while (a < b) return a; return b; }')
        function = checked.list_function['Pick'].mtype
        self.assertIsInstance(function.return_type, PrimeField)
        self.assertTrue(all(isinstance(arg, PrimeField) for arg in function.params))

    def test_bare_function_parameter_conditions_infer_scalar_returns(self):
        for statement in ('if (value) return value; else return 0;',
                          'while (value) return value; return 0;'):
            with self.subTest(statement=statement):
                checked = self.assert_complete('signal output out; out <== Pick(1);',
                                               'function Pick(value) { ' + statement + ' }')
                function = checked.list_function['Pick'].mtype
                self.assertIsInstance(function.return_type, PrimeField)
                self.assertIsInstance(function.params[0], PrimeField)

    def test_template_conditions_wait_until_component_types_are_available(self):
        self.assert_complete('''component unit = Unit();
var value = 0;
if (unit.out) value = 1;
signal output out;
out <-- value;''')

    def test_nested_declarations_are_collected_once_without_else(self):
        self.assert_complete('''if (1) {
    var value = 1;
    component unit = Unit();
    unit.out === value;
}''')

    def test_invalid_ports_in_bare_if_and_while_fail_in_second_pass(self):
        for statement in ('if (1) unit.missing === 1;',
                          'if (0) unit.out === 1; else unit.missing === 1;',
                          'while (0) unit.missing === 1;'):
            with self.subTest(statement=statement):
                checked = TypeCheck(self.source('component unit = Unit(); ' + statement))
                with self.assertRaises(Report) as failure:
                    checked.check()
                self.assertEqual(checked.count_visited, 1)
                self.assertIn('missing', str(failure.exception))
                self.assertIn('Unit', str(failure.exception))

    def test_scalar_component_read_before_initialization_stays_invalid(self):
        for statement in ('if (1) unit.out === 1;', 'while (0) unit.out === 1;'):
            with self.subTest(statement=statement):
                checked = TypeCheck(self.source('component unit; ' + statement + ' unit = Unit();'))
                with self.assertRaises(Report) as failure:
                    checked.check()
                self.assertEqual(checked.count_visited, 1)
                self.assertIn('unit', str(failure.exception))

    def test_loop_array_inference_does_not_hide_read_before_initialization(self):
        self.source('''component units[2];
for (var i = 0; i < 2; i++) {
    if (i == 0) units[i].out === 1;
    units[i] = Unit();
}''')
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            graphs, reports = detect(str(self.path))
        self.assertIsNone(graphs, output.getvalue())
        self.assertIsNone(reports, output.getvalue())

    def test_invalid_condition_types_are_still_checked(self):
        checked = TypeCheck(self.source('component unit = Unit(); if (unit) unit.out === 1;'))
        with self.assertRaises(Report) as failure:
            checked.check()
        self.assertEqual(checked.count_visited, 1)
        self.assertIn('Found component', str(failure.exception))

    def test_bare_return_in_a_template_remains_invalid(self):
        checked = TypeCheck(self.source('if (1) return 0;'))
        with self.assertRaises(Report) as failure:
            checked.check()
        self.assertIn('Return statement outside of a function', str(failure.exception))


if __name__ == '__main__':
    unittest.main()
