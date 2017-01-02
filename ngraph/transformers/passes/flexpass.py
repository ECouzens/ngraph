from ngraph.transformers.passes.passes import GraphPass, PeepholeGraphPass
from ngraph.util.generics import generic_method
from ngraph.op_graph.op_graph import Op, tdcache
from autoflex.flexgpu import gpuflex16


class FlexPass(PeepholeGraphPass):
    @generic_method(dispatch_base_type=Op)
    def visit(self, op):
        # TODO currently hard coded gpuflex16
        op.dtype = gpuflex16


# unused after most recent merge from master (12/30)
class ClearTensorDescriptions(GraphPass):
    def do_pass(self, ops):
        tdcache.tensor_description_cache.clear()
