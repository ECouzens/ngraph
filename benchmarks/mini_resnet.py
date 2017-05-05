# ----------------------------------------------------------------------------
# Copyright 2017 Nervana Systems Inc.
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
from __future__ import division
from __future__ import print_function
from benchmark import Benchmark
from fake_data_generator import generate_data
from ngraph.frontends.neon import Affine, Preprocess, Convolution, Pool2D, BatchNorm, Activation
from ngraph.frontends.neon import Sequential
from ngraph.frontends.neon import KaimingInit, Rectlin, Softmax, GradientDescentMomentum
from ngraph.frontends.neon import ax
from ngraph.frontends.neon import ArrayIterator
import ngraph as ng
import numpy as np


# TODO: Need to refactor and make it shareable with Alex's tests
def cifar_mean_subtract(x):
    bgr_mean = ng.persistent_tensor(
        axes=[x.axes.channel_axis()],
        initial_value=np.array([104., 119., 127.]))
    return (x - bgr_mean) / 255.


def conv_params(fsize, nfm, strides=1, relu=False, batch_norm=False):
    return dict(fshape=(fsize, fsize, nfm),
                strides=strides,
                padding=(1 if fsize > 1 else 0),
                activation=(Rectlin() if relu else None),
                filter_init=KaimingInit(),
                batch_norm=batch_norm)


class f_module(object):
    def __init__(self, nfm, first=False, strides=1, batch_norm=False):

        self.trunk = None
        self.side_path = None
        main_path = [Convolution(**conv_params(1, nfm, strides=strides)),
                     Convolution(**conv_params(3, nfm)),
                     Convolution(**conv_params(1, nfm * 4, relu=False, batch_norm=False))]

        if first or strides == 2:
            self.side_path = Convolution(
                **conv_params(1, nfm * 4, strides=strides, relu=False, batch_norm=False))
        else:
            if batch_norm:
                main_path = [BatchNorm(), Activation(Rectlin())] + main_path
            else:
                main_path = [Activation(Rectlin())] + main_path

        if strides == 2:
            if batch_norm:
                self.trunk = Sequential([BatchNorm(), Activation(Rectlin())])
            else:
                self.trunk = Sequential([Activation(Rectlin())])

        self.main_path = Sequential(main_path)

    def __call__(self, x):
        with ng.metadata(device_id=('1', '2'), parallel=ax.N):
            t_x = self.trunk(x) if self.trunk else x
            s_y = self.side_path(t_x) if self.side_path else t_x
            m_y = self.main_path(t_x)
            return s_y + m_y


class mini_residual_network(Sequential):
    def __init__(self, inputs, dataset, stage_depth, batch_norm=False, activation=True, preprocess=False):
        nfms = [2**(stage + 4) for stage in sorted(list(range(3)) * stage_depth)]
        strides = [1 if cur == prev else 2 for cur, prev in zip(nfms[1:], nfms[:-1])]
        layers = []
        if preprocess:
            layers = Preprocess(functor=cifar_mean_subtract)
        parallel_axis = inputs['image'].axes.batch_axes()
        with ng.metadata(device_id=('1', '2'), parallel=parallel_axis[0]):
            layers.append(Convolution(**conv_params(3, 16, batch_norm=batch_norm)))
            layers.append(f_module(nfms[0], first=True, batch_norm=batch_norm))

            for nfm, stride in zip(nfms[1:], strides):
                layers.append(f_module(nfm, strides=stride, batch_norm=batch_norm))

        if batch_norm:
            layers.append(BatchNorm())
        if activation:
            layers.append(Activation(Rectlin()))
        layers.append(Pool2D(8, strides=2, op='avg'))
        if dataset == 'cifar10':
            ax.Y.length = 10
            layers.append(Affine(axes=ax.Y, weight_init=KaimingInit(),
                                batch_norm=batch_norm, activation=Softmax()))
        elif dataset == 'i1k':
            ax.Y.length = 1000
            layers.append(Affine(axes=ax.Y, weight_init=KaimingInit(),
                                 batch_norm=batch_norm, activation=Softmax()))
        else:
            raise ValueError("Incorrect dataset provided")

        self.layers = layers


def get_mini_resnet(inputs, dataset, stage_depth=1, batch_norm=False, activation=False, preprocess=False):
    return mini_residual_network(inputs, dataset, stage_depth, batch_norm, activation, preprocess)


def get_fake_data(dataset, batch_size, n_iter):
    x_train, y_train = generate_data(dataset, batch_size)

    train_data = {'image': {'data': x_train, 'axes': ('batch', 'C', 'height', 'width')},
                  'label': {'data': y_train, 'axes': ('batch',)}}

    train_set = ArrayIterator(train_data, batch_size, total_iterations=n_iter)
    inputs = train_set.make_placeholders(include_iteration=True)
    return inputs, train_data, train_set


def run_cifar_benchmark(dataset='cifar10',n_iter=10, n_skip=1, batch_size=128,
                        transformer_type='cpu', bprop=False):
    inputs, data, train_set = get_fake_data(dataset, batch_size, n_iter)
    model = get_mini_resnet(inputs, dataset)

    # Running forward propagation
    fprop_computation_op = ng.computation(model(inputs['image']), 'all')
    benchmark_fprop = Benchmark(fprop_computation_op, train_set, inputs, transformer_type)
    Benchmark.print_benchmark_results(benchmark_fprop.time(n_skip, n_iter, dataset+'_msra_fprop'))

    if bprop:
        optimizer = GradientDescentMomentum(0.01, 0.9)
        train_loss = ng.cross_entropy_multi(model(inputs['image']),
                                            ng.one_hot(inputs['label'], axis=ax.Y))

        batch_cost = ng.sequential([optimizer(train_loss), ng.mean(train_loss, out_axes=())])
        batch_cost_computation_op = ng.computation(batch_cost, "all")

        benchmark = Benchmark(batch_cost_computation_op, train_set, inputs, transformer_type)
        Benchmark.print_benchmark_results(benchmark.time(n_skip, n_iter, dataset+'_msra_bprop'))


if __name__ == "__main__":
    run_cifar_benchmark(dataset='cifar10')
    run_cifar_benchmark(dataset='i1k')
