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
from ngraph.frontends.neon import ax
import collections


class ArrayIterator(object):

    def __init__(self, data_arrays, batch_size, total_iterations=None, time_steps=1):
        """
        During initialization, the input data will be converted to backend tensor objects
        (e.g. CPUTensor or GPUTensor). If the backend uses the GPU, the data is copied over to the
        device.

        Args:
            data_arrays (ndarray, shape: [# examples, feature size]): Input features of the
                dataset.
            batch_size (int): number of examples in each minibatch
            total_iterations (int): number of minibatches to cycle through on this iterator.
                                    If not provided, it will cycle through all of the data once.
        """
        # Treat singletons like list so that iteration follows same syntax
        self.batch_size = batch_size
        self.time_steps = time_steps
        self.axis_names = None
        if isinstance(data_arrays, dict):
            self.data_arrays = {k: v['data'] for k, v in data_arrays.items()}
            self.axis_names = {k: v['axes'] for k, v in data_arrays.items()}
        elif isinstance(data_arrays, collections.Sequence):
            self.data_arrays = {k: x for k, x in enumerate(data_arrays)}
        else:
            self.data_arrays = {0: data_arrays}

        self.keys = list(self.data_arrays.keys())

        if not self.axis_names:
            self.axis_names = {k: None for k in self.keys}

        # just get an arbitrary element for len
        self.ndata = len(self.data_arrays[self.keys[0]])
        if self.time_steps != 1:
            self.ndata = self.ndata // (self.batch_size * self.time_steps) * self.batch_size
        self.ntokens = self.ndata * self.time_steps

        if self.ndata < self.batch_size:
            raise ValueError('Number of examples is smaller than the batch size')

        self.start = 0
        self.index = 0

        self.total_iterations = self.nbatches if total_iterations is None else total_iterations

    @property
    def nbatches(self):
        """
        Return the number of minibatches in this dataset.
        """
        return -((self.start - self.ndata) // self.batch_size)

    def make_placeholders(self):
        placeholders = {}
        ax.N.length = self.batch_size
        for k, axnm in self.axis_names.items():
            p_axes = ng.make_axes([ax.N])
            for i, sz in enumerate(self.data_arrays[k].shape[1:], 1):
                name = axnm[i] if axnm else None
                p_axes += ng.make_axis(name=name, length=sz)
            placeholders[k] = ng.placeholder(p_axes)
        return placeholders

    def reset(self):
        """
        Resets the starting index of this dataset to zero. Useful for calling
        repeated evaluations on the dataset without having to wrap around
        the last uneven minibatch. Not necessary when data is divisible by batch size
        """
        self.start = 0
        self.index = 0

    def __iter__(self):
        """
        Returns a new minibatch of data with each call.

        Yields:
            tuple: The next minibatch which includes both features and labels.
        """
        i1 = self.start
        while self.index < self.total_iterations:
            i1 = (self.start + self.index * self.batch_size) % self.ndata
            bsz = min(self.batch_size, self.ndata - i1)
            oslice1 = slice(i1, i1 + bsz)
            self.index += 1

            if self.batch_size > bsz:
                batch_bufs = {k: np.concatenate([src[oslice1], src[:self.batch_size - bsz]])
                              for k, src in self.data_arrays.viewitems()}
            else:
                batch_bufs = {k: src[oslice1] for k, src in self.data_arrays.viewitems()}

            yield batch_bufs

        self.start = (self.start + self.total_iterations * self.batch_size) % self.ndata


class SequentialArrayIterator(ArrayIterator):

    def __init__(self, data_arrays, time_steps, batch_size,
                 total_iterations=None, reverse_target=False, get_prev_target=False):
        self.get_prev_target = get_prev_target
        self.reverse_target = reverse_target

        super(SequentialArrayIterator, self).__init__(
            data_arrays=data_arrays,
            batch_size=batch_size,
            total_iterations=total_iterations,
            time_steps=time_steps,
        )

        self.data_arrays = {k: x[:self.ntokens].reshape(
                                self.batch_size,
                                self.nbatches,
                                self.time_steps
                            ) for k, x in self.data_arrays.viewitems()}

        if self.reverse_target:
            self.data_arrays['tgt_txt'][:] = self.data_arrays['tgt_txt'][:, :, ::-1]

        # if self.get_prev_target:
        #     self.data_arrays['prev_tgt'] =

    def make_placeholders(self):
        placeholders = {}
        ax.N.length = self.batch_size
        for k, axnm in self.data_arrays.items():
            p_axes = ng.make_axes([ax.N])
            # p_axes += ng.make_axis(name=)
            for i, sz in enumerate(self.data_arrays[k].shape[1:], 1):
                name = axnm[i] if axnm else None
                p_axes += ng.make_axis(name=name, length=sz)
            placeholders[k] = ng.placeholder(p_axes)
        return placeholders

    #     self.make_batch_buffers()

    # def make_batch_buffers(self):
    #     self.shapes, self.batch_bufs = dict(), dict()
    #     for k, x in self.data_arrays.items():
    #         self.data_arrays[k] = x[:self.ntokens].reshape(
    #             self.batch_size, self.nbatches, self.time_steps)
    #         self.shapes[k] = (1, self.time_steps)
    #         self.batch_bufs[k] = np.empty((1, self.time_steps, self.batch_size), dtype=np.int32)

    def __iter__(self):
        while self.index < self.total_iterations:
            idx = self.index % self.nbatches
            self.index += 1

            batch_bufs = {k: x[:, idx:(idx + 1), :] for k, x in self.data_arrays.viewitems()}

            if self.get_prev_target:
                batch_bufs['prev_tgt'] = np.concatenate(
                    np.zeros((self.batch_size, 1, 1), dtype=np.int32),
                    batch_bufs['tgt_txt'][:, :, 1:],
                    axis=2
                )

            yield batch_bufs
