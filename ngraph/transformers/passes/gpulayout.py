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

from ngraph.transformers.passes.passes import PeepholeGraphPass
from ngraph.util.generics import generic_method
from ngraph.op_graph.pooling import PoolingOp, BpropPoolOp
from ngraph.op_graph.op_graph import Op, ContiguousOp


class GPUTensorLayout(PeepholeGraphPass):
    """TODO."""
    @generic_method(dispatch_base_type=Op)
    def visit(self, op):
        """
        TODO.

        Arguments:
          op: TODO
        """
        pass

    @visit.on_type(PoolingOp)
    def visit(self, op):
        inputs = op.args[0]
        if not isinstance(inputs, ContiguousOp):
            new_op = PoolingOp(op.pool_params, ContiguousOp(inputs), axes=op.axes)
            self.replace_op(op, new_op)

    @visit.on_type(BpropPoolOp)
    def visit(self, op):
        deltas = op.args[0]
        if not isinstance(deltas, ContiguousOp):
            new_op = BpropPoolOp(ContiguousOp(deltas), op.inputs, op.fprop, axes=op.axes)
            self.replace_op(op, new_op)
