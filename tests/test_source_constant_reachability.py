"""Constant-constraint evidence is found without recursive graph traversal."""
from pathlib import Path
import random
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
for directory in ('src', 'src/cirverify/utils', 'src/cirverify/typecheck', 'src/cirverify/detect'):
    sys.path.insert(0, str(ROOT / directory))
from CDG import CircuitDependenceGraph, Edge, EdgeType, Node, NodeType, getEdgeName
from Detect import Detector
from StaticCheck import SignalType


def node(name, graph='Main', kind=NodeType.SIGNAL, signal=SignalType.INTERMEDIATE):
    return Node(None, name, kind, signal, graph)


def connect(graph, source, target, *, kind=EdgeType.CONSTRAINT, count=0):
    name = getEdgeName(kind, source, target)
    edge = Edge(source, target, kind, name, None, count_constraint=count)
    graph.edges[name] = edge
    source.flow_to.append(edge)
    target.flow_from.append(edge)


def graph_with(nodes, name='Main'):
    return CircuitDependenceGraph({}, {item.id: item for item in nodes}, name, {})


def recursive_reference(current):
    """Original rule on a small acyclic graph, without sharing global caches."""
    if any(edge.edge_type == EdgeType.CONSTRAINT and edge.count_constraint == 0
           and edge.node_to.node_type == NodeType.CONSTANT for edge in current.flow_to):
        return True
    for edge in current.flow_from:
        if edge.edge_type != EdgeType.CONSTRAINT:
            continue
        previous = edge.node_from
        if previous.node_type == NodeType.CONSTANT and edge.count_constraint == 0:
            return True
        if previous.is_signal() and previous.signal_type == SignalType.INTERMEDIATE:
            if recursive_reference(previous):
                return True
    return False


class ConstantReachabilityTests(unittest.TestCase):
    def test_random_acyclic_graphs_match_recursive_rule(self):
        rng = random.Random(2903)
        for _ in range(30):
            nodes = [node('constant', kind=NodeType.CONSTANT)] + [
                node(str(i), signal=SignalType.OUTPUT if i % 7 == 0 else SignalType.INTERMEDIATE)
                for i in range(1, 25)]
            graph = graph_with(nodes)
            for left in range(len(nodes)):
                for right in range(left + 1, len(nodes)):
                    if rng.random() < 0.12:
                        connect(graph, nodes[left], nodes[right],
                                kind=rng.choice(list(EdgeType)), count=rng.randrange(2))
            detector = Detector({'Main': graph})
            for current in nodes[1:]:
                self.assertEqual(detector.constrainted_as_const(graph, current),
                                 recursive_reference(current))

    def test_chain_beyond_python_recursion_limit_completes(self):
        nodes = [node(str(index)) for index in range(12_000)]
        constant = node('constant', kind=NodeType.CONSTANT)
        graph = graph_with(nodes + [constant])
        for left, right in zip(nodes, nodes[1:]):
            connect(graph, left, right)
            connect(graph, right, left)
        connect(graph, constant, nodes[0])
        detector = Detector({'Main': graph})
        self.assertTrue(detector.constrainted_as_const(graph, nodes[-1]))

    def test_cycles_do_not_cache_a_false_result_before_finding_evidence(self):
        a, b, c = (node(name) for name in ('a', 'b', 'c'))
        out = node('out', signal=SignalType.OUTPUT)
        constant = node('constant', kind=NodeType.CONSTANT)
        graph = graph_with([a, b, c, out, constant])
        connect(graph, a, out)
        connect(graph, c, a)
        connect(graph, b, a)
        connect(graph, a, b)
        connect(graph, constant, c)
        detector = Detector({'Main': graph})
        self.assertTrue(detector.constrainted_as_const(graph, out))
        self.assertTrue(detector.constrainted_as_const(graph, b))

    def test_cycle_without_constant_evidence_remains_unconstrained(self):
        nodes = [node(str(index)) for index in range(25)]
        graph = graph_with(nodes)
        for left, right in zip(nodes, nodes[1:] + nodes[:1]):
            connect(graph, left, right)
        detector = Detector({'Main': graph})
        for current in nodes:
            self.assertFalse(detector.constrainted_as_const(graph, current))

    def test_same_node_name_in_distinct_graphs_does_not_share_evidence(self):
        first_out = node('out', 'First', signal=SignalType.OUTPUT)
        first = graph_with([first_out], 'First')
        second_out = node('out', 'Second', signal=SignalType.OUTPUT)
        constant = node('constant', 'Second', kind=NodeType.CONSTANT)
        second = graph_with([second_out, constant], 'Second')
        connect(second, constant, second_out)
        detector = Detector({'First': first, 'Second': second})
        self.assertFalse(detector.constrainted_as_const(first, first_out))
        self.assertTrue(detector.constrainted_as_const(second, second_out))
        self.assertFalse(detector.constrainted_as_const(first, first_out))

    def test_dependency_edges_and_repeated_constant_constraints_stay_distinct(self):
        out = node('out', signal=SignalType.OUTPUT)
        constant = node('constant', kind=NodeType.CONSTANT)
        graph = graph_with([out, constant])
        connect(graph, constant, out, kind=EdgeType.DEPEND)
        connect(graph, out, constant, count=1)
        detector = Detector({'Main': graph})
        self.assertFalse(detector.constrainted_as_const(graph, out))

    def test_child_output_uses_its_own_constant_or_input_constraint_evidence(self):
        for tied_to_input in (False, True):
            with self.subTest(tied_to_input=tied_to_input):
                child_out = node('out', 'Child', signal=SignalType.OUTPUT)
                child_source = node('in' if tied_to_input else 'constant', 'Child',
                                    kind=NodeType.SIGNAL if tied_to_input else NodeType.CONSTANT,
                                    signal=SignalType.INPUT)
                child = graph_with([child_out, child_source], 'Child')
                connect(child, child_source, child_out)
                connect(child, child_out, child_source)
                parent_out = node('out', signal=SignalType.OUTPUT)
                instance_out = node('instance.out', 'Child|instance', signal=SignalType.OUTPUT)
                parent = graph_with([parent_out, instance_out])
                connect(parent, instance_out, parent_out)
                detector = Detector({'Main': parent, 'Child': child})
                self.assertTrue(detector.constrainted_as_const(parent, parent_out))


if __name__ == '__main__':
    unittest.main()
