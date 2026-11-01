"""Constraint connectivity stays equivalent to BFS while graph edges are added."""
from pathlib import Path
import random
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
for directory in ('src', 'src/cirverify/utils', 'src/cirverify/typecheck'):
    sys.path.insert(0, str(ROOT / directory))
from CDG import CircuitDependenceGraph, Edge, EdgeType, Node, NodeType, getEdgeName
from StaticCheck import SignalType


def add_edge(edges, source, destination, kind=EdgeType.CONSTRAINT, **details):
    name = getEdgeName(kind, source, destination)
    edge = Edge(source, destination, kind, name, details.get('ast'),
                count_constraint=details.get('count_constraint', 0))
    edges[name] = edge
    source.flow_to.append(edge)
    destination.flow_from.append(edge)
    return edge


def make_graph(size, pairs=(), name='Main'):
    nodes = {str(i): Node(None, str(i), NodeType.SIGNAL, SignalType.INTERMEDIATE, name)
             for i in range(size)}
    edges = {}
    for a, b in pairs:
        add_edge(edges, nodes[str(a)], nodes[str(b)])
        add_edge(edges, nodes[str(b)], nodes[str(a)])
    return CircuitDependenceGraph(edges, nodes, name, {})


def bfs(source, destination):
    visited = set()
    stack = [source]
    while stack:
        current = stack.pop()
        if current.id in visited:
            continue
        if current.id == destination.id:
            return True
        visited.add(current.id)
        stack.extend(edge.node_to for edge in current.flow_to
                     if edge.edge_type == EdgeType.CONSTRAINT)
    return False


class ConstraintConnectivityTests(unittest.TestCase):
    def assert_matches_bfs(self, graph):
        for source in graph.nodes.values():
            for destination in graph.nodes.values():
                self.assertEqual(graph.has_path_constraint(source, destination), bfs(source, destination),
                                 (source.id, destination.id))

    def assert_edge_index_matches_adjacency(self, graph):
        actual = [edge for node in graph.nodes.values() for edge in node.flow_to]
        self.assertEqual(len(actual), len(graph.edges))
        self.assertEqual({id(edge) for edge in actual}, {id(edge) for edge in graph.edges.values()})
        for name, edge in graph.edges.items():
            self.assertEqual(name, edge.name)
            self.assertEqual(name, getEdgeName(edge.edge_type, edge.node_from, edge.node_to))
            self.assertIn(edge, edge.node_to.flow_from)

    def test_random_small_undirected_graphs_match_original_bfs(self):
        rng = random.Random(20261101)
        for size in range(1, 25):
            with self.subTest(size=size):
                pairs = [(a, b) for a in range(size) for b in range(a + 1, size)
                         if rng.random() < 0.12]
                graph = make_graph(size, pairs)
                # Data-flow edges never establish a constraint path.
                for a in range(size - 1):
                    add_edge(graph.edges, graph.nodes[str(a)], graph.nodes[str(a + 1)], EdgeType.DEPEND)
                self.assert_matches_bfs(graph)

    def test_dynamic_relations_update_previously_negative_queries(self):
        rng = random.Random(71)
        graph = make_graph(18)
        self.assert_matches_bfs(graph)  # Build the index before adding relations.
        pairs = [(a, b) for a in range(18) for b in range(a + 1, 18)]
        rng.shuffle(pairs)
        for first, second in pairs[:28]:
            graph._add_constraint_relation(graph.nodes[str(first)], graph.nodes[str(second)])
            self.assert_matches_bfs(graph)
            self.assert_edge_index_matches_adjacency(graph)

    def test_dependency_only_paths_do_not_merge_constraint_components(self):
        graph = make_graph(3, [(0, 1)])
        add_edge(graph.edges, graph.nodes['1'], graph.nodes['2'], EdgeType.DEPEND)
        self.assertTrue(graph.has_path_constraint(graph.nodes['0'], graph.nodes['1']))
        self.assertFalse(graph.has_path_constraint(graph.nodes['0'], graph.nodes['2']))
        self.assertFalse(graph.has_path_constraint(graph.nodes['2'], graph.nodes['0']))

    def test_relation_insertion_keeps_both_keys_and_is_idempotent(self):
        graph = make_graph(2)
        first, second = graph.nodes['0'], graph.nodes['1']
        original = add_edge(graph.edges, first, second, ast='original constraint', count_constraint=3)
        add_edge(graph.edges, second, first, ast='original constraint', count_constraint=3)
        graph._add_constraint_relation(first, second)
        graph._add_constraint_relation(first, second)
        self.assertIs(graph.edges[getEdgeName(EdgeType.CONSTRAINT, first, second)], original)
        self.assertEqual(original.ast, 'original constraint')
        self.assertEqual(original.count_constraint, 3)
        self.assertEqual(len(graph.edges), 2)
        self.assert_edge_index_matches_adjacency(graph)

    def test_transitive_export_preserves_forward_and_reverse_edges(self):
        graph = make_graph(3, [(0, 1), (1, 2)])
        graph.build_constraint_recursive({'Main': graph}, graph.nodes['0'], graph.nodes['2'], True)
        self.assertEqual(len(graph.edges), 6)
        self.assertIn(getEdgeName(EdgeType.CONSTRAINT, graph.nodes['0'], graph.nodes['2']), graph.edges)
        self.assertIn(getEdgeName(EdgeType.CONSTRAINT, graph.nodes['2'], graph.nodes['0']), graph.edges)
        self.assert_matches_bfs(graph)
        self.assert_edge_index_matches_adjacency(graph)

    def test_child_component_relation_unions_existing_parent_index(self):
        child = make_graph(3, [(0, 1), (1, 2)], name='Child')
        first = Node(None, 'unit.0', NodeType.SIGNAL, SignalType.INPUT, 'Child|id=0')
        second = Node(None, 'unit.2', NodeType.SIGNAL, SignalType.OUTPUT, 'Child|id=0')
        parent = CircuitDependenceGraph({}, {first.id: first, second.id: second}, 'Parent', {})
        self.assertFalse(parent.has_path_constraint(first, second))
        for _ in range(2):
            parent.build_constraint_recursive({'Child': child}, first, second, False)
        self.assertTrue(parent.has_path_constraint(first, second))
        self.assertTrue(parent.has_path_constraint(second, first))
        self.assertEqual(len(parent.edges), 2)
        self.assert_edge_index_matches_adjacency(parent)

    def test_many_queries_keep_a_linear_connectivity_index(self):
        size = 1000
        graph = make_graph(size, [(0, index) for index in range(1, size)])
        target = graph.nodes[str(size - 1)]
        for source in graph.nodes.values():
            self.assertTrue(graph.has_path_constraint(source, target))
        # The previous DFS retained ~1M frontier references for this 2K-edge star.
        self.assertLessEqual(len(graph._constraint_parent), size)
        self.assertLessEqual(len(graph._constraint_rank), size)
        self.assertEqual(len(graph.edges), 2 * (size - 1))
        self.assertFalse(hasattr(graph, '_visit_state'))


if __name__ == '__main__':
    unittest.main()
