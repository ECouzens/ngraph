# ----------------------------------------------------------------------------
# Copyright 2015-2016 Nervana Systems Inc.
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

'''
Test of the optimizers
'''
import copy
import itertools as itt

import numpy as np

import ngraph as ng
from ngraph.frontends.neon import GradientDescentMomentum
from ngraph.testing.execution import ExecutorFactory


def pytest_generate_tests(metafunc):
    if 'args' in metafunc.fixturenames:
        fargs = []
        lr = np.random.random(2)
        momentum = np.random.random(4)
        wdecay = [0.0005, 0.000, 0.001, 0.1]
        fargs = itt.product(lr, momentum, wdecay)
        metafunc.parametrize('args', fargs)


class DummyLayer(object):

    def __init__(self, p):
        self.p = p[0]

    def get_params(self):
        return self.p


def generate_data(C, N):
    x = np.random.rand(C, N).astype('float32')
    y = np.random.rand(N).astype('float32')

    return x, y


# xfail due to initial_value=nparray not working
# this test was working a previous commit of ngraph
# @pytest.mark.xfail(strict=True)
def test_gdm(args, transformer_factory):
    """
    Test the ngraph GradientDescentMomentum against the neon version across 10 update steps.
    """
    # set up parameters
    C = ng.make_axis(20, name="C")
    N = ng.make_axis(32, name="N", batch=True)

    # generate dummy data (to initialize values)
    w_init = np.random.rand(C.length).astype('float32')

    # set up nervana graph
    X = ng.placeholder([C, N]).named('X')
    Y = ng.placeholder([N]).named('Y')
    W = ng.variable([C - 1], initial_value=w_init).named('W')

    ex = ExecutorFactory()
    transformer = ex.transformer

    lrate, mom, wdecay = args
    gdm = GradientDescentMomentum(learning_rate=lrate, momentum_coef=mom, wdecay=wdecay)
    cost = ng.sum(Y - ng.dot(W, X), out_axis=())

    # to call ngraph gdm, use (ngraph_W, _) = ngraph_optimize(x, y)
    # where (x, y) are nparrays that fill the placeholders X and Y
    updates = gdm(cost)
    ngraph_optimize = transformer.computation([W, updates], X, Y)

    # set up the reference values for gradient descent
    w_ref = w_init.copy()
    vel_ref = np.zeros_like(w_ref)

    # store the weights with each minibatch for debugging
    ng_Ws = []

    # run for 20 minibatches
    for i, (x, y) in enumerate([generate_data(C.length, N.length) for _ in range(20)]):
        # obtain ngraph results
        (ng_W, _) = ngraph_optimize(x, y)
        gdm.update_learning_rate()
        ng_Ws.append(copy.deepcopy(ng_W))

        # obtain reference results
        dw = -1 * x.sum(axis=1) / N.length   # the gradients we compute analytically

        dw = dw + wdecay * w_ref
        if mom == 0:
            w_ref[:] = w_ref - lrate * dw
        else:
            vel_ref[:] = mom * vel_ref - lrate * dw
            w_ref[:] = w_ref + vel_ref

        ng.testing.assert_allclose(w_ref, ng_W, rtol=1e-3)
