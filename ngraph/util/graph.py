# ----------------------------------------------------------------------------
# Copyright 2016 Nervana Systems Inc.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ----------------------------------------------------------------------------

from collections import defaultdict
from ngraph.op_graph.op_graph import OrderedSet

try:
    import graphviz
except:
    graphviz = None


class Digraph(object):
    """
    Base class for Directed graph.
    Includes Graphviz visualization, DFS, topsort

    Arguments:

    Returns:

    """

    def __init__(self, successors):
        """
        Initialize directed graph from successors dict

        Arguments:
          successors (dict: op => set(op)): dict that map each op to all its users
        """
        self.successors = successors

    def _graphviz(self, name=''):
        """
        Export the current Digraph to Graphviz

        Arguments:
          name (str): Name of the resulting graph

        Returns:
          pygraphviz object
        """
        dot = graphviz.Digraph(name)
        for node, nexts in list(self.successors.items()):
            dot.node(node.name, node.graph_label, node.style)
            for next in nexts:
                dot.node(next.name, next.graph_label, next.style)
                dot.edge(node.name, next.name)
        return dot

    @staticmethod
    def _invert(adjacency):
        """
        Returns the invert of the given adjacency dict (e.g., successors to predecessors)

        Arguments:
          adjacency: TODO

        Returns:
          Result
        """
        result = defaultdict(OrderedSet)
        for x, others in list(adjacency.items()):
            for y in others:
                result[y].add(x)
        return result

    def render(self, fpath, view=True):
        """
        Renders to a graphviz file

        Arguments:
          fpath: str): file to write too
          view: TODO
        """
        self._graphviz().render(fpath, view=view)

    def view(self):
        """View the graph. Requires pygraphviz."""
        self._graphviz().view()

    def dfs(self, starts, fun, reverse=False):
        """
        Performs DFS, applying the provided function to each node.

        Arguments:
          starts: nodes to start from
          fun: Function): Function to apply to each visited node
          reverse: bool): whether to do DFS on the reversed graph
        """
        if reverse:
            nexts = Digraph._invert(self.successors)
        else:
            nexts = self.successors

        # TODO This is almost identical to Op's visit_input_closure, which we should switch
        # to using when we can.
        available = OrderedSet()
        counts = dict()
        parents = defaultdict(list)
        ready = OrderedSet()

        available.update(node.forwarded for node in starts)
        while available:
            node = available.pop()
            node.update_forwards()

            if node in counts:
                continue

            children = [child.forwarded for child in nexts[node]]
            if children:
                counts[node] = len(children)
                for child in children:
                    parents[child].append(node)
                available.update(children)
            else:
                ready.add(node)

        while ready:
            node = ready.pop()
            fun(node)
            for p in parents.get(node, []):
                count = counts[p] - 1
                if count == 0:
                    ready.add(p)
                    del counts[p]
                else:
                    counts[p] = count

    @property
    def inputs(self):
        """TODO."""

        predecessors = Digraph._invert(self.successors)
        return [u for u, vs in iter(list(predecessors.items())) if len(vs) == 0]

    def can_reach(self, outs, order=None):
        """
        Computes the vertices that can reach the nodes specified in outs
        ordered as specified

        Arguments:
          outs: list): nodes to reach
          order: list): list specifying the order of the result nodes

        Returns:
          Ordered dependencies of outs
        """
        result = set()
        self.dfs(outs, lambda x: result.add(x.forwarded), reverse=True)
        if order is not None:
            result = [x for x in order if x in result]
        return result

    def topsort(self):
        """
        Topological sort of the nodes.

        Returns:
          Sorted list of nodes.
        """
        result = []
        self.dfs(self.inputs, lambda x: result.insert(0, x))
        return result


class UndirectedGraph(object):
    """
    Base class for Undirected graph.
    Includes Graphviz visualization
    """

    def __init__(self, neighbors):
        self.neighbors = neighbors

    def _graphviz(self, name=''):
        """
        TODO.

        Arguments:
          name: TODO

        Returns:

        """
        try:
            from graphviz import Graph
        except:
            pass
        dot = Graph()
        processed = set()
        for na, _ in list(self.neighbors.items()):
            dot.node(na.name, na.graph_label, na.style)
            for nb in _:
                dot.node(nb.name, nb.graph_label, nb.style)
                if (nb, na) not in processed:
                    dot.edge(na.name, nb.name)
                    processed.add((na, nb))
        return dot

    def render(self, fpath, view=True):
        """
        TODO.

        Arguments:
          fpath: TODO
          view: TODO
        """
        self._graphviz().render(fpath, view=view)

    def view(self):
        """TODO."""
        self._graphviz().view()
