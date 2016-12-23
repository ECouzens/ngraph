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
import numpy as np
import ngraph as ng
from functools import partial
from ngraph.frontends.neon import ar


class GaussianInit(object):
    def __init__(self, mean=0.0, var=0.01):
        self.functor = partial(np.random.normal, mean, var)

    def __call__(self, out_axes):
        return self.functor(out_axes.lengths)


class UniformInit(object):
    def __init__(self, low=-0.01, high=0.01):
        self.functor = partial(np.random.uniform, low, high)

    def __call__(self, out_axes):
        return self.functor(out_axes.lengths)


class ConstantInit(object):
    def __init__(self, val=0.0):
        self.val = val

    def __call__(self, out_axes):
        return self.val


def get_input_output_axes(w_axes):
    dual_axes = ng.make_axes([a for a in w_axes if a.dual_level != 0])

    if len(dual_axes) == 0:
        axes_o = w_axes.role_axes(ar.features_output)
        axes_i = w_axes - axes_o
    else:
        # Note that this would be flipped if we used positive duals
        # Doesn't work at all if we have mixed duals (+/-)
        axes_i = dual_axes
        axes_o = w_axes - axes_i
        if not all([a.dual_level == -1 for a in dual_axes]):
            raise ValueError("Expecting only duals of -1 in weight initialization")

    return (axes_i, axes_o)


class GlorotInit(object):
    def __call__(self, out_axes):
        ax_i, ax_o = get_input_output_axes(out_axes)
        scale = np.sqrt(6. / (np.prod(ax_i.lengths) + np.prod(ax_o.lengths)))
        return np.random.uniform(-scale, scale, out_axes.lengths)

class XavierInit(object):
    def __call__(self, out_axes):
        ax_i, _ = get_input_output_axes(out_axes)
        scale = np.sqrt(3. / np.prod(ax_i.lengths))
        return np.random.uniform(-scale, scale, out_axes.lengths)


class KaimingInit(object):
    def __call__(self, out_axes):
        ax_i, _ = get_input_output_axes(out_axes)
        scale = np.sqrt(2. / np.prod(ax_i.lengths))
        return np.random.normal(0, scale, out_axes.lengths)

