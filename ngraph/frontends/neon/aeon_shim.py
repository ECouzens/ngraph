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
from __future__ import print_function
from builtins import object
import json
import ngraph as ng
from ngraph.frontends.neon import ax

try:
    from aeon import DataLoader
except ImportError:
    print(
        "\n"
        "Unable to import aeon module.\n"
        "Please see installation instructions at:\n"
        "*****************\n"
        "https://github.com/NervanaSystems/aeon/blob/rc1-master/README.md\n"
        "*****************\n"
    )
    import sys
    sys.exit(1)


class AeonDataLoader(object):

    def __init__(self, config, *args, **kwargs):
        self.config = config
        self._dataloader = DataLoader(json.dumps(config))

    def __next__(self):
        return next(self._dataloader)

    def __iter__(self):
        return self

    def make_placeholders(self, include_iteration=False):
        placeholders = {}
        ax.N.length = self._dataloader.batch_size
        for placeholder_name, axis_info in self._dataloader.axes_info.items():
            p_axes = ng.make_axes([ax.N])
            for nm, sz in axis_info.items():
                nm = "C" if nm == "channels" else nm
                p_axes += ng.make_axis(name=nm, length=sz)
            placeholders[placeholder_name] = ng.placeholder(p_axes)
        if include_iteration:
            placeholders['iteration'] = ng.placeholder(axes=())
        return placeholders
