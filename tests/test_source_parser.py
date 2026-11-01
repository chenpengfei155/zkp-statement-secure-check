"""Regression coverage for long Circom lists and included-source failures."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
for directory in [SOURCE] + [
    SOURCE / "cirverify" / name
    for name in ("target", "astgen", "utils", "typecheck", "cdggen", "detect")
]:
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from cirverify.core import detect, generate_ast
from AST import (AnonymousComponentExpr, ArrayInLine, Call, Declaration,
                 Number, Substitution, TupleExpr)
from Report import Report
from StaticCheck import TypeCheck


class SourceParserTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)

    def parse(self, source, files=None):
        for filename, contents in (files or {}).items():
            path = self.base / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")
        entry = self.base / "entry.circom"
        entry.write_text(source, encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            ast = generate_ast(str(entry))
        return ast, output.getvalue()

    def parse_success(self, source, files=None):
        ast, output = self.parse(source, files)
        self.assertIsNotNone(ast, output)
        return ast

    def test_large_literal_array_preserves_values_and_source_locations(self):
        values = list(range(3000))
        source = "pragma circom 2.0.0;\nfunction Constants() {\nreturn [\n"
        source += ",\n".join(map(str, values)) + "\n];\n}\n"
        ast = self.parse_success(source)
        array = ast.definitions[0].body.stmts[0].value
        self.assertIsInstance(array, ArrayInLine)
        self.assertEqual([item.value for item in array.values], values)
        self.assertEqual(array.values[0].locate.start.line, 4)
        self.assertEqual(array.values[-1].locate.start.line, 3003)
        self.assertEqual(array.values[-1].locate.path, str(self.base / "entry.circom"))

    def test_multidimensional_arrays_keep_row_boundaries(self):
        rows = [list(range(1200)), [7, 11, 13], [17, 19]]
        literal = "[" + ",".join("[" + ",".join(map(str, row)) + "]" for row in rows) + "]"
        ast = self.parse_success("function Matrix() { return " + literal + "; }")
        matrix = ast.definitions[0].body.stmts[0].value
        self.assertTrue(all(isinstance(row, ArrayInLine) for row in matrix.values))
        self.assertEqual([[item.value for item in row.values] for row in matrix.values], rows)

    def test_long_function_parameters_and_call_arguments_preserve_order(self):
        names = ["arg" + str(index) for index in range(1200)]
        source = "function Pick(" + ",".join(names) + ") { return arg1199; }\n"
        source += "template Main() { var result = Pick(" + ",".join(map(str, range(1200))) + "); }\n"
        source += "component main = Main();"
        ast = self.parse_success(source)
        self.assertEqual(ast.definitions[0].args, names)
        call = ast.definitions[1].body.stmts[0].initializations[1].rhe
        self.assertIsInstance(call, Call)
        self.assertEqual([arg.value for arg in call.args], list(range(1200)))

    def test_long_declaration_and_statement_lists_preserve_order(self):
        names = ["value" + str(index) for index in range(1200)]
        source = "template Main() {\nvar " + ",".join(names) + ";\n"
        source += "\n".join(name + " = " + str(index) + ";" for index, name in enumerate(names))
        source += "\n}\ncomponent main = Main();"
        ast = self.parse_success(source)
        statements = ast.definitions[0].body.stmts
        declarations = statements[0].initializations
        self.assertTrue(all(isinstance(item, Declaration) for item in declarations))
        self.assertEqual([item.name for item in declarations], names)
        self.assertEqual([item.var for item in statements[1:]], names)
        self.assertEqual([item.rhe.value for item in statements[1:]], list(range(1200)))

    def test_long_tuple_and_simple_symbol_lists_preserve_order(self):
        names = ["value" + str(index) for index in range(1200)]
        source = "template Main() { var (" + ",".join(names) + ") = ("
        source += ",".join(map(str, range(1200))) + "); } component main = Main();"
        ast = self.parse_success(source)
        initializations = ast.definitions[0].body.stmts[0].initializations
        self.assertEqual([item.name for item in initializations[:-1]], names)
        assignment = initializations[-1]
        self.assertIsInstance(assignment.rhe, TupleExpr)
        self.assertEqual([item.name for item in assignment.lhe.values], names)
        self.assertEqual([item.value for item in assignment.rhe.values], list(range(1200)))

    def test_long_signal_and_log_lists_preserve_order(self):
        names = ["input" + str(index) for index in range(1200)]
        source = "template Main() { signal input " + ",".join(names) + "; log("
        source += '",",' + ",".join(names) + "); } component main = Main();"
        ast = self.parse_success(source)
        statements = ast.definitions[0].body.stmts
        self.assertEqual([item.name for item in statements[0].initializations], names)
        self.assertEqual(statements[1].args[0].value, '","')
        self.assertEqual([item.expr.name for item in statements[1].args[1:]], names)

    def test_long_definition_list_and_repeated_include_list(self):
        count = 1100
        source = 'include "shared.circom";\n' * count
        source += "\n".join("function F" + str(index) + "() { return " + str(index) + "; }"
                            for index in range(count))
        ast = self.parse_success(source, {"shared.circom": "function Shared() { return 1; }"})
        self.assertEqual(len(ast.includes), count)
        self.assertEqual([item.name_field for item in ast.definitions],
                         ["F" + str(index) for index in range(count)] + ["Shared"])

    def test_anonymous_component_positional_named_and_empty_inputs(self):
        cases = [
            ("Pair(2)(3, 5)", [3, 5], [None, None]),
            ("Pair(2)(left <== 3, right <-- 5)", [3, 5], [("<==", "left"), ("<--", "right")]),
            ("Pair(2)()", [], []),
        ]
        for expression, values, names in cases:
            with self.subTest(expression=expression):
                ast = self.parse_success("template Main() { var result = " + expression + "; }")
                expression_ast = ast.definitions[0].body.stmts[0].initializations[1].rhe
                self.assertIsInstance(expression_ast, AnonymousComponentExpr)
                self.assertEqual(expression_ast.id, "Pair")
                self.assertEqual([item.value for item in expression_ast.params], [2])
                self.assertEqual([item.value for item in expression_ast.signals], values)
                self.assertEqual(expression_ast.names, names)

    def test_small_circuit_keeps_main_publics_and_operator_precedence(self):
        source = """pragma circom 2.0.0;
template Main() {
    signal input left, right;
    signal output out;
    out <== left + right * 2;
}
component main {public [left, right]} = Main();
"""
        ast = self.parse_success(source)
        self.assertEqual(ast.compile_version, (2, 0, 0))
        self.assertEqual(ast.main_component.publics, ["left", "right"])
        self.assertEqual(ast.main_component.expr.id, "Main")
        assignment = ast.definitions[0].body.stmts[2]
        self.assertIsInstance(assignment, Substitution)
        self.assertEqual((assignment.var, assignment.op), ("out", "<=="))
        self.assertEqual(assignment.rhe.infix_op, "+")
        self.assertEqual(assignment.rhe.rhe.infix_op, "*")
        self.assertIsInstance(assignment.rhe.rhe.rhe, Number)
        self.assertEqual(assignment.rhe.rhe.rhe.value, 2)

    def test_missing_nested_include_reports_original_failure(self):
        ast, output = self.parse('include "outer.circom";', {
            "outer.circom": 'include "nested/inner.circom";',
            "nested/inner.circom": 'include "missing.circom";',
        })
        self.assertIsNone(ast)
        self.assertIn("missing.circom", output)
        self.assertIn("not found", output)
        self.assertNotIn("NoneType", output)
        self.assertNotIn("has no attribute", output)

    def test_syntax_error_in_nested_include_reports_original_failure(self):
        ast, output = self.parse('include "outer.circom";', {
            "outer.circom": 'include "broken.circom";',
            "broken.circom": "template Broken() { signal input ; }",
        })
        self.assertIsNone(ast)
        self.assertIn("broken.circom", output)
        self.assertIn("Error on line", output)
        self.assertNotIn("NoneType", output)
        self.assertNotIn("has no attribute", output)

    def test_invalid_character_in_root_source_is_not_silently_skipped(self):
        ast, output = self.parse("template Main() { @ } component main = Main();")
        self.assertIsNone(ast)
        self.assertIn("entry.circom", output)
        self.assertIn("token recognition error", output)
        self.assertIn("@", output)
        self.assertNotIn("NoneType", output)

    def test_invalid_character_in_included_source_is_not_silently_skipped(self):
        ast, output = self.parse('include "outer.circom";', {
            "outer.circom": 'include "broken.circom";',
            "broken.circom": "template Broken() { @ }",
        })
        self.assertIsNone(ast)
        self.assertIn("broken.circom", output)
        self.assertIn("token recognition error", output)
        self.assertIn("@", output)
        self.assertNotIn("NoneType", output)

    def test_component_array_can_reference_previous_loop_iteration(self):
        source = """template Unit() {
    signal input in;
    signal output out;
    out <== in;
}
template Chain() {
    component stages[3];
    for (var i = 0; i < 3; i++) {
        if (i > 0) { stages[i - 1].out === 1; }
        stages[i] = Unit();
        stages[i].in <== 1;
    }
}
component main = Chain();
"""
        checked = TypeCheck(self.parse_success(source))
        checked.check()
        self.assertEqual(checked.global_env[0]["main"].mtype.name, "Chain")
        self.assertEqual(checked.list_template["Unit"].mtype.signals_in, ["in"])
        self.assertEqual(checked.list_template["Unit"].mtype.signals_out, ["out"])

    def test_component_array_with_conflicting_template_types_is_rejected(self):
        source = """template Unit() {
    signal input in;
    signal output out;
    out <== in;
}
template Other() {
    signal input in;
    signal output out;
    out <== in + 1;
}
template Chain() {
    component stages[2];
    stages[0] = Unit();
    stages[0].in <== 1;
    stages[1] = Other();
    stages[1].in <== 1;
}
component main = Chain();
"""
        checked = TypeCheck(self.parse_success(source))
        with self.assertRaises(Report) as failure:
            checked.check()
        message = str(failure.exception)
        self.assertIn("stages", message)
        self.assertIn("Unit", message)
        self.assertIn("Other", message)

    def test_component_type_inference_respects_block_shadowing(self):
        source = """template Unit() {
    signal input in;
    signal output out;
    out <== in;
}
template Other() {
    signal input in;
    signal output different;
    different <== in;
}
template Chain() {
    component stages[3];
    for (var i = 0; i < 3; i++) {
        if (i > 0) { stages[i - 1].out === 1; }
        stages[i] = Unit();
        stages[i].in <== 1;
    }
    {
        component stages[3];
        for (var j = 0; j < 3; j++) {
            if (j > 0) { stages[j - 1].different === 2; }
            stages[j] = Other();
            stages[j].in <== 2;
        }
    }
    stages[2].out === 1;
}
component main = Chain();
"""
        checked = TypeCheck(self.parse_success(source))
        checked.check()
        self.assertEqual(checked.global_env[0]["main"].mtype.name, "Chain")
        self.assertEqual(checked.list_template["Unit"].mtype.signals_out, ["out"])
        self.assertEqual(checked.list_template["Other"].mtype.signals_out, ["different"])

    def test_scalar_port_access_before_template_assignment_is_rejected(self):
        source = """template Unit() { signal output out; out <== 1; }
template Main() {
    component unit;
    unit.out === 1;
    unit = Unit();
}
component main = Main();
"""
        checked = TypeCheck(self.parse_success(source))
        with self.assertRaises(Report) as failure:
            checked.check()
        self.assertIn("unit", str(failure.exception))
        self.assertIn("out", str(failure.exception))

    def test_array_port_access_before_template_assignment_outside_loop_is_rejected(self):
        source = """template Unit() { signal output out; out <== 1; }
template Main() {
    component units[1];
    units[0].out === 1;
    units[0] = Unit();
}
component main = Main();
"""
        checked = TypeCheck(self.parse_success(source))
        with self.assertRaises(Report) as failure:
            checked.check()
        self.assertIn("units", str(failure.exception))
        self.assertIn("out", str(failure.exception))

    def test_inferred_component_type_does_not_skip_constructor_argument_check(self):
        source = """template Unit(value) { signal output out; out <== value; }
template Main() {
    component units[2];
    for (var i = 0; i < 2; i++) {
        if (i > 0) { units[i - 1].out === 1; }
        units[i] = Unit();
    }
}
component main = Main();
"""
        checked = TypeCheck(self.parse_success(source))
        with self.assertRaises(Report) as failure:
            checked.check()
        message = str(failure.exception)
        self.assertIn("Unit", message)
        self.assertIn("1 arguments", message)
        self.assertIn("0 were provided", message)

    def test_detection_rejects_reading_current_component_before_initialization(self):
        source = """template Unit() { signal output out; out <== 1; }
template Main() {
    component stages[2];
    for (var i = 0; i < 2; i++) {
        stages[i].out === 1;
        stages[i] = Unit();
    }
}
component main = Main();
"""
        self.parse_success(source)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            graphs, reports = detect(str(self.base / "entry.circom"))
        self.assertIsNone(graphs, output.getvalue())
        self.assertIsNone(reports, output.getvalue())

    def test_component_port_typo_reports_missing_port(self):
        source = """template Unit() {
    signal input in;
    signal output out;
    out <== in;
}
template Chain() {
    component stages[1];
    stages[0] = Unit();
    stages[0].in <== 1;
    stages[0].missing === 1;
}
component main = Chain();
"""
        checked = TypeCheck(self.parse_success(source))
        with self.assertRaises(Report) as failure:
            checked.check()
        message = str(failure.exception)
        self.assertIn("missing", message)
        self.assertIn("Unit", message)


if __name__ == "__main__":
    unittest.main()
