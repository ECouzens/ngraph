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
        if isinstance(data_arrays, dict):
            self.data_arrays = data_arrays
        elif isinstance(data_arrays, (list, tuple)):
            self.data_arrays = {k: x for k, x in enumerate(data_arrays)}
        else:
            self.data_arrays = {0: data_arrays}
        self.keys = list(self.data_arrays.keys())

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
        self.make_batch_buffers()

    def make_batch_buffers(self):
        self.shapes, self.batch_bufs = dict(), dict()
        for k, x in self.data_arrays.items():
            self.shapes[k] = x.shape[1:]
            self.batch_bufs[k] = np.empty(x.shape[1:] + (self.batch_size,), x.dtype)

    @property
    def nbatches(self):
        """
        Return the number of minibatches in this dataset.
        """
        return -((self.start - self.ndata) // self.batch_size)

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
            islice1, oslice1 = slice(0, bsz), slice(i1, i1 + bsz)
            islice2, oslice2 = None, None
            self.index += 1

            if self.batch_size > bsz:
                islice2, oslice2 = slice(bsz, None), slice(0, self.batch_size - bsz)

            for k in self.keys:
                src, dst = self.data_arrays[k], self.batch_bufs[k]

                dst[..., islice1] = np.rollaxis(src[oslice1], 0, src.ndim)
                if oslice2:
                    dst[..., islice2] = np.rollaxis(src[oslice2], 0, src.ndim)

            yield self.batch_bufs

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

        self.make_batch_buffers()

    def make_batch_buffers(self):
        self.shapes, self.batch_bufs = dict(), dict()
        for k, x in self.data_arrays.items():
            self.data_arrays[k] = x[:self.ntokens].reshape(
                self.batch_size, self.nbatches, self.time_steps)
            self.shapes[k] = (1, self.time_steps)
            self.batch_bufs[k] = np.empty((1, self.time_steps, self.batch_size), dtype=np.int32)

        if self.get_prev_target:
            self.batch_bufs['prev_tgt'] = np.empty((1, self.time_steps, self.batch_size),
                                                   dtype=np.int32)

    def __iter__(self):
        while self.index < self.total_iterations:
            idx = self.index % self.nbatches
            for k in self.keys:
                src, dst = self.data_arrays[k], self.batch_bufs[k]
                dst[:] = src[:, idx:(idx + 1), :].transpose(1, 2, 0)
            self.index += 1

            if self.reverse_target:
                self.batch_bufs['tgt_txt'][:] = self.batch_bufs['tgt_txt'][:, ::-1, :]

            if self.get_prev_target:
                self.batch_bufs['prev_tgt'][:, 0] = 0
                self.batch_bufs['prev_tgt'][:, 1:] = self.batch_bufs['tgt_txt'][:, :-1]

            yield self.batch_bufs
