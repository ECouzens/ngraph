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

from ngraph.frontends.caffe2.c2_importer.ops_base import OpsBase
import ngraph as ng


class OpsNN(OpsBase):
    """
    Mix-in class for NN ops:
    """
    def FC(self, c2_op, inputs):
        """
        Multiplies matrix `a` by matrix `b`. The inputs must be two-dimensional,
        the inner dimensions must match (possibly after transpose).

        Arguments:
            c2_op: OperatorDef object, the caffe2 node to convert.
            inputs: List of ngraph Ops as inputs to this node.

        Returns:
            A ngraph Op corresponding to the caffe2 node.

        Inputs to c2_op:
            a, b, transpose_a, transpose_b, a_is_sparse, b_is_sparse, name
        """
        # get inputs
        left, right, bias = inputs

        # check shape
        assert left.axes[1].length == right.axes[0].length

        # cast axis
        left_casted = ng.cast_axes(left, [left.axes[0], right.axes[0] - 1])

        # add op
        add_op = ng.dot(left_casted, right)

        # cast bias axis
        bias_casted = ng.cast_axes(bias, [add_op.axes[-1]])

        # result op
        result_op = ng.add(add_op, bias_casted, name=c2_op.name)
        return result_op
