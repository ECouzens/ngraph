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

# reuse from TF
from ngraph.frontends.tensorflow.tf_importer.ops_binary import OpsBinary as TFOpsBinary


class OpsBinary(TFOpsBinary):
    """
    Mix-in class element-wise binary ops.
    For now, reuse TF
    """
    # TBD: weird implementation of Sum of more than two inputs :) Should be modified?
    def _nested_add(self, op, inputs):
        if len(inputs) == 2:
            return self.Add(op, inputs)
        else:
            add_input = [inputs[0], self._nested_add(op, inputs[1:])]
            return self.Add(op, add_input)

    def Sum(self, op, inputs):
        return self._nested_add(op, inputs)
