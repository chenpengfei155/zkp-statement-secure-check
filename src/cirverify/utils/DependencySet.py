"""Persistent unions of expression leaves without copying every SSA prefix."""


class DependencySet:
    """An immutable union DAG with exact, non-recursive set iteration.

    Each update shares its previous operands. Iteration expands shared nodes once
    and emits each leaf once; it deliberately does not cache expanded sets on
    every historical variable version.
    """

    __slots__ = ('_left', '_right', '_item')

    def __init__(self, item=None, *, left=None, right=None):
        self._left = left
        self._right = right
        self._item = item

    def __or__(self, other):
        if not isinstance(other, DependencySet):
            return NotImplemented
        if self is other:
            return self
        if self._item is None and self._left is None:
            return other
        if other._item is None and other._left is None:
            return self
        return DependencySet(left=self, right=other)

    def with_item(self, item):
        """Return a new union; leave this version and shared ancestors intact."""
        return self | DependencySet(item)

    def __iter__(self):
        pending = [self]
        visited = set()
        emitted = set()
        while pending:
            node = pending.pop()
            identity = id(node)
            if identity in visited:
                continue
            visited.add(identity)
            if node._item is not None:
                if node._item not in emitted:
                    emitted.add(node._item)
                    yield node._item
            elif node._left is not None:
                pending.append(node._right)
                pending.append(node._left)
