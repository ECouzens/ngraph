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

from ngraph.frontends.tensorflow.tf_importer.ops_base import OpsBase
from ngraph.frontends.tensorflow.tf_importer.utils_pos_axes import make_pos_axes
import ngraph as ng
import numpy as np


class OpsTransform(OpsBase):
    """
    Mix-in class for tensor transformation ops
    """

    def Rank(self, tf_node, inputs):
        """
        Returns the rank of a tensor.

        Arguments:
            tf_node: NodeDef object, the tensorflow node to convert.
            inputs: List of ngraph Ops as inputs to this node.

        Returns:
            A ngraph Op corresponding to the tensorflow node.

        Inputs to tf_node:
            input, name
        """
        # get inputs
        left = inputs[0]

        # get rank
        try:
            rank = len(left.axes.lengths)
        except:
            raise NotImplementedError("[NON-NATIVE] `Rank` op's axes must be "
                                      "pre-determined before execution.")
        # return
        return ng.constant(rank, ng.make_axes([])).named(tf_node.name)

    def Range(self, tf_node, inputs):
        """
        Creates a sequence of integers.

        Arguments:
            tf_node: NodeDef object, the tensorflow node to convert.
            inputs: List of ngraph Ops as inputs to this node.

        Returns:
            A ngraph Op corresponding to the tensorflow node.

        Inputs to tf_node:
            start, limit, delta, name
        """
        # get inputs
        start, limit, delta = inputs

        # get range
        try:
            range_val = np.arange(start.const, limit.const, delta.const)
        except:
            raise NotImplementedError("[NON-NATIVE] Input to `Range` must all "
                                      "be integer, dynamic allocation is not "
                                      "supported.")

        # return
        return ng.constant(range_val,
                           make_pos_axes(range_val.shape)).named(tf_node.name)

    def Size(self, tf_node, inputs):
        """
        Returns the size of a tensor.

        Arguments:
            tf_node: NodeDef object, the tensorflow node to convert.
            inputs: List of ngraph Ops as inputs to this node.

        Returns:
            A ngraph Op corresponding to the tensorflow node.

        Inputs to tf_node:
            input, name
        """
        # get inputs
        left = inputs[0]

        # get rank
        try:
            size = np.prod(left.axes.lengths)
        except:
            raise NotImplementedError("[NON-NATIVE] `Size` op's axes must be "
                                      "pre-determined before execution.")
        # return
        return ng.constant(size, ng.make_axes([])).named(tf_node.name)

    def Cast(self, tf_node, inputs):
        """
        Casts a tensor to a new type.

        Arguments:
            tf_node: NodeDef object, the tensorflow node to convert.
            inputs: List of ngraph Ops as inputs to this node.

        Returns:
            A ngraph Op corresponding to the tensorflow node.

        Inputs to tf_node:
            x, dtype, name
        """
        # TODO: now only a pass through
        # get src and dst datatypes
        # dst_type = tf_node.attr['DstT']
        # src_type = tf_node.attr['SrcT']
        return inputs[0]

    def Shape(self, tf_node, inputs):
        """
        Returns the shape of a tensor.

        Arguments:
            tf_node: NodeDef object, the tensorflow node to convert.
            inputs: List of ngraph Ops as inputs to this node.

        Returns:
            A ngraph Op corresponding to the tensorflow node.

        Inputs to tf_node:
            input, name
        """

        # get inputs
        left = inputs[0]

        # get shape
        try:
            shape = left.axes.lengths
        except:
            raise NotImplementedError("[NON-NATIVE] `Size` op's axes must be "
                                      "pre-determined before execution.")
        axes = ng.make_axes([ng.make_axis(len(left.axes.lengths)), ])

        # return
        return ng.constant(shape, axes).named(tf_node.name)

    def Reshape(self, tf_node, inputs):
        """
        Reshapes a tensor.

        Arguments:
            tf_node: NodeDef object, the tensorflow node to convert.
            inputs: List of ngraph Ops as inputs to this node.

        Returns:
            A ngraph Op corresponding to the tensorflow node.

        Inputs to tf_node:
            tensor, shape, name
        """
        # TODO: currently only support constants and flatten to 1d and 2d
        # get inputs
        tensor, shape = inputs

        def get_flatten_idx(shape_i, shape_o):
            """
            check if flattening shape is valid
            Args:
                shape_i: input tensor shape
                shape_o: output flattend tensor shape

            Returns:
                None if flatten not valid, otherwise the flatten_at index
            """
            return None

        # get input and output shape
        shape_i = tensor.shape.lengths
        shape_o = tuple(shape.const.astype(int))
        if np.prod(shape_i) != np.prod(shape_o):
            raise ValueError("Total size of input and output dimension "
                             "mismatch.")

        if tensor.const is not None:
            # reshape const
            np_val = np.reshape(tensor.const, shape_o)
            return ng.constant(np_val,
                               make_pos_axes(np_val.shape)).named(tf_node.name)
        else:
            ndims_o = len(shape_o)
            if ndims_o != 1 and ndims_o != 2:
                raise NotImplementedError("Reshape can only support flatten"
                                          "to 1d or 2d.")
            if ndims_o == 1:
                tensor = ng.flatten(tensor)
            else:
                cumprods = list(np.cumprod(shape_i))
                flatten_at_idx = cumprods.index(shape_o[0]) + 1
                tensor = ng.flatten_at(tensor, flatten_at_idx)
            res = ng.cast_axes(tensor, make_pos_axes(shape_o))
            return res.named(tf_node.name)

    def Tile(self, tf_node, inputs):
        """
        Constructs a tensor by tiling a given tensor.

        Arguments:
            tf_node: NodeDef object, the tensorflow node to convert.
            inputs: List of ngraph Ops as inputs to this node.

        Returns:
            A ngraph Op corresponding to the tensorflow node.

        Inputs to tf_node:
            tensor, shape, name
        """
        tensor, multiples = inputs

        # get inputs
        try:
            input_val = tensor.const
            multiples_val = multiples.const
        except:
            raise NotImplementedError(
                "Tile not supported in ngraph, "
                "currently only const tensor is supported.")

        # check shapes
        input_shape = input_val.shape
        input_ndims = len(input_shape)
        assert input_ndims >= 1 and input_ndims == len(multiples_val)

        output_val = np.tile(input_val, multiples_val.astype(int))

        # make new constants
        return ng.constant(output_val,
                           make_pos_axes(output_val.shape)).named(tf_node.name)

    def ExpandDims(self, tf_node, inputs):
        """
        Inserts a dimension of 1 into a tensor's shape.

        Arguments:
            tf_node: NodeDef object, the tensorflow node to convert.
            inputs: List of ngraph Ops as inputs to this node.

        Returns:
            A ngraph Op corresponding to the tensorflow node.

        Inputs to tf_node:
            input, dim, name
        """
        # get input
        tensor, dim = inputs[0], int(inputs[1].const)

        # check `-1-input.dims() <= dim <= input.dims()`
        input_ndims = len(tensor.axes.lengths)
        assert -1 - input_ndims <= dim <= input_ndims

        # deal with negative number
        if dim < 0:
            dim = input_ndims + 1 + dim

        # create new axis
        one_axis = ng.make_axis(length=1)

        # get output axis
        pre_axis = [axis for axis in tensor.axes[:dim]]  # avoid FlattenedAxis
        pos_axis = [axis for axis in tensor.axes[dim:]]  # avoid FlattenedAxis
        out_axis = ng.make_axes(pre_axis + [one_axis] + pos_axis)

        # broadcast
        return ng.broadcast(tensor, out_axis)
