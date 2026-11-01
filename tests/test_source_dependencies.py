"""Shared expression unions preserve the eager dependency-set semantics."""
import contextlib
import io
from pathlib import Path
import random
import sys
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
for directory in ('parser', 'astgen', 'typecheck', 'utils', 'cdggen', 'detect', 'target'):
    sys.path.append(str(ROOT / 'src' / 'cirverify' / directory))
from cirverify.core import detect
from cirverify.source_report import build_source_report
from cirverify.utils.DependencySet import DependencySet
import CDGGeneration as generation


class EagerDependencySet(set):
    """Independent reference using the former copied-set representation."""
    def __or__(self, other):
        return EagerDependencySet(super().__or__(other))

    def with_item(self, item):
        return self | {item}


class DependencySetTests(unittest.TestCase):
    def test_updates_do_not_mutate_historical_versions(self):
        initial = DependencySet().with_item('a')
        branch = initial.with_item('b')
        later = initial.with_item('c')
        merged = branch | later
        self.assertEqual(set(initial), {'a'})
        self.assertEqual(set(branch), {'a', 'b'})
        self.assertEqual(set(later), {'a', 'c'})
        self.assertEqual(set(merged), {'a', 'b', 'c'})
        self.assertEqual(len(list(merged | merged)), 3)

    def test_random_shared_unions_match_independent_sets(self):
        rng = random.Random(1729)
        versions = [DependencySet()]
        reference = [set()]
        for _ in range(300):
            left = rng.randrange(len(versions))
            right = rng.randrange(len(versions))
            leaf = str(rng.randrange(30))
            versions.append((versions[left] | versions[right]).with_item(leaf))
            reference.append(reference[left] | reference[right] | {leaf})
        for actual, expected in zip(versions, reference):
            leaves = list(actual)
            self.assertEqual(set(leaves), expected)
            self.assertEqual(len(leaves), len(expected))

    def test_long_chain_iteration_does_not_recurse(self):
        value = DependencySet()
        for index in range(20_000):
            value = value.with_item(str(index))
        self.assertEqual(set(value), {str(index) for index in range(20_000)})

    def test_retaining_ssa_prefixes_has_bounded_memory(self):
        tracemalloc.start()
        try:
            versions = [DependencySet()]
            for index in range(2000):
                versions.append(versions[-1].with_item(str(index)))
            self.assertEqual(len(set(versions[-1])), 2000)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        # Eagerly retaining these prefixes needs tens of MiB of copied set tables.
        self.assertLess(peak, 12 * 1024 * 1024)

    def analyze(self, path, representation):
        output = io.StringIO()
        generation.FindNode.node_number = 0
        with patch.object(generation, 'DependencySet', representation):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                graphs, reports = detect(str(path))
        self.assertIsNotNone(graphs, output.getvalue())
        self.assertIsNotNone(reports, output.getvalue())
        shape = {name: {
            'nodes': set(graph.nodes),
            'edges': {(edge.node_from.id, edge.node_to.id, edge.edge_type.name)
                      for edge in graph.edges.values()},
        } for name, graph in graphs.items()}
        report = build_source_report(graphs, reports, filename=path.name)
        for check in report['checks']:
            check['findings'].sort(key=lambda item: (item['location'], item['message']))
        return shape, report

    def test_generated_graphs_and_findings_match_eager_sets(self):
        cases = [
            '''var acc = in[0];
for (var i = 1; i < 6; i++) acc = acc + in[i];
var chosen = gate ? acc : in[0];
out <== chosen;
acc === in[5];''',
            '''var a[2];
a[0] = in[0]; a[1] = in[1];
var b[2]; b = a;
var x = b[0] + b[1];
var y = in[2] + in[3];
x === y;
out <-- x + y;''',
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dependencies.circom'
            for body in cases:
                with self.subTest(body=body):
                    path.write_text('''pragma circom 2.0.0;
template Main() {
signal input in[6];
signal input gate;
signal output out;
''' + body + '''
}
component main = Main();
''', encoding='utf-8')
                    actual = self.analyze(path, DependencySet)
                    expected = self.analyze(path, EagerDependencySet)
                    self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()
