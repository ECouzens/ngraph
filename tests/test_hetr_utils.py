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
import pytest
from ngraph.util.hetr_utils import comm_path_exists, update_comm_deps, find_recvs
from ngraph.op_graph.comm_nodes import SendOp, ScatterSendOp, GatherSendOp
from ngraph.op_graph.comm_nodes import RecvOp, ScatterRecvOp, GatherRecvOp
import ngraph as ng


ax_A = ng.make_axis(length=10, name='A')
ax_B = ng.make_axis(length=15, name='B')
ax_C = ng.make_axis(length=20, name='C')
axes = ng.make_axes([ax_A, ax_B, ax_C])


def create_graph():
    with ng.metadata(device=None, device_id=None, transformer=None, host_transformer=None):
        from_node = ng.placeholder(axes)
        to_node = ng.placeholder(axes)
    send_x = SendOp(from_node=from_node)
    recv_x = RecvOp(to_node=to_node, send_node=send_x)

    with ng.metadata(device=None, device_id=None, transformer=None, host_transformer=None):
        x_plus_one = recv_x + 1

    send_x_plus_one = SendOp(from_node=x_plus_one)
    recv_x_plus_one = RecvOp(to_node=to_node, send_node=send_x_plus_one)

    with ng.metadata(device=None, device_id=None, transformer=None, host_transformer=None):
        z = recv_x_plus_one + 2
    return z, recv_x, recv_x_plus_one, send_x, x_plus_one, from_node, send_x_plus_one


def create_scatter_gather_graph():
    with ng.metadata(parallel=ax_B, device=(0, 1), device_id=(0, 1),
                     transformer=None, host_transformer=None):
        from_node = ng.placeholder(axes)
        to_node = ng.placeholder(axes)
    scatter_send_x = ScatterSendOp(from_node=from_node, to_node=to_node)
    scatter_recv_a = ScatterRecvOp(to_node=to_node, send_node=scatter_send_x)
    scatter_recv_b = ScatterRecvOp(to_node=to_node, send_node=scatter_send_x)
    gather_send_a = GatherSendOp(from_node=scatter_recv_a)
    gather_send_b = GatherSendOp(from_node=scatter_recv_b)
    gather_recv_x_plus_one = GatherRecvOp(from_node=from_node, to_node=to_node,
                                          send_node=gather_send_a)
    return scatter_send_x, scatter_recv_a, scatter_recv_b, \
        gather_send_a, gather_send_b, gather_recv_x_plus_one


def test_find_recvs():
    z, recv_x, recv_x_plus_one, send_x, x_plus_one, from_node, send_x_plus_one = create_graph()

    assert set([recv_x]) == set(find_recvs(x_plus_one))
    assert set([recv_x]) == set(find_recvs(recv_x))
    assert len(find_recvs(from_node)) == 0
    assert set([recv_x]) == set(find_recvs(send_x_plus_one))
    assert set([recv_x_plus_one, recv_x]) == set(find_recvs(recv_x_plus_one))
    assert set([recv_x_plus_one, recv_x]) == set(find_recvs(z))


def test_find_recvs_scatter_gather():
    scatter_send_x, scatter_recv_a, scatter_recv_b, gather_send_a, gather_send_b, \
        gather_recv_x_plus_one = create_scatter_gather_graph()

    assert set([scatter_recv_a]) == set(find_recvs(gather_send_a))
    assert set([scatter_recv_b]) == set(find_recvs(gather_send_b))
    assert len(find_recvs(scatter_send_x)) == 0
    assert set([gather_recv_x_plus_one, scatter_recv_a]) == set(find_recvs(gather_recv_x_plus_one))
    assert set([scatter_recv_a]) == set(find_recvs(scatter_recv_a))


def test_comm_path_exists():
    with ng.metadata(device=None, device_id=None, transformer=None, host_transformer=None):
        from_node = ng.placeholder(axes)
        to_node = ng.placeholder(axes)
    send_x = SendOp(from_node=from_node)
    recv_x = RecvOp(to_node=to_node, send_node=send_x)

    with ng.metadata(device=None, device_id=None, transformer=None, host_transformer=None):
        x_plus_one = recv_x + 1

    assert comm_path_exists(recv_x, send_x)
    assert comm_path_exists(x_plus_one, send_x)


def test_comm_path_exists_scatter_gather():
    scatter_send_x, scatter_recv_a, scatter_recv_b, gather_send_a, gather_send_b, \
        gather_recv_x_plus_one = create_scatter_gather_graph()

    assert comm_path_exists(scatter_recv_a, scatter_send_x)
    assert comm_path_exists(gather_recv_x_plus_one, gather_send_a)
    assert comm_path_exists(gather_recv_x_plus_one, scatter_send_x)
    assert comm_path_exists(scatter_recv_b, scatter_send_x)
    assert not comm_path_exists(gather_recv_x_plus_one, gather_send_b)
    assert not comm_path_exists(gather_send_a, gather_recv_x_plus_one)


def test_update_comm_deps():
    with ng.metadata(transformer='cpu0'):
        z, recv_x, recv_x_plus_one, send_x, x_plus_one, from_node, send_x_plus_one = create_graph()
    update_comm_deps((z, send_x))
    assert recv_x_plus_one in z.all_deps


def test_update_comm_deps_scatter_gather():
    parallel_metadata = dict(parallel=ax_B, device_id=(0, 1),
                             transformer=None, host_transformer=None, device=None)
    with ng.metadata(transformer='cpu0'):
        with ng.metadata(**parallel_metadata):
            from_node_a = ng.placeholder(axes)
            to_node_a = ng.placeholder(axes)
        scatter_send_x = ScatterSendOp(from_node=from_node_a, to_node=to_node_a)
        scatter_recv_a = ScatterRecvOp(to_node=to_node_a, send_node=scatter_send_x)
        with ng.metadata(**parallel_metadata):
            x_plus_one_a = scatter_recv_a + 1
        gather_send_x_plus_one_a = GatherSendOp(from_node=x_plus_one_a)

    with ng.metadata(transformer='cpu1'):
        with ng.metadata(**parallel_metadata):
            to_node_b = ng.placeholder(axes)
        scatter_recv_b = ScatterRecvOp(to_node=to_node_b, send_node=scatter_send_x)
        with ng.metadata(**parallel_metadata):
            x_plus_one_b = scatter_recv_b + 1
        gather_send_x_plus_one_b = GatherSendOp(from_node=x_plus_one_b)

    with ng.metadata(transformer='cpu0'):
        with ng.metadata(**parallel_metadata):
            gather_recv_x_plus_one_a = GatherRecvOp(from_node=from_node_a, to_node=to_node_a,
                                                    send_node=gather_send_x_plus_one_a)
            z_a = gather_recv_x_plus_one_a + 1

    update_comm_deps((scatter_send_x, gather_send_x_plus_one_a, z_a))
    update_comm_deps((gather_send_x_plus_one_b,))

    assert set([scatter_send_x]) == set(scatter_recv_a.all_deps)
    assert set([scatter_send_x, gather_send_x_plus_one_a]) == \
        set(gather_recv_x_plus_one_a.all_deps)

def assert_axes_eq_len(expected_axes, actual_axes):
    for exp, act in zip(expected_axes, actual_axes):
        assert exp.length == act.length

# TODO don't define these globally
ax_A = ng.make_axis(64)
ax_B = ng.make_axis(128)
ax_C = ng.make_axis(256)
@pytest.mark.parametrize('config', [
        {
            'axes': ng.make_axes([ax_A]),
            'parallel_axis': ax_A,
            'slices': [[slice(0, 32, 1)], [slice(32, 64, 1)]],
            'device_id': (0, 1)
        },
        {
            'axes': ng.make_axes([ax_A, ax_B]),
            'parallel_axis': ax_A,
            'slices': [[slice(0, 16, 1), slice(None)],
                       [slice(16, 32, 1), slice(None)],
                       [slice(32, 48, 1), slice(None)],
                       [slice(48, 64, 1), slice(None)]],
            'device_id': (0, 1, 2, 3)
        },
        {
            'axes': ng.make_axes([ax_A, ax_B, ax_C]),
            'parallel_axis': ax_A,
            'slices': [[slice(0, 16, 1), slice(None), slice(None)],
                       [slice(16, 32, 1), slice(None), slice(None)],
                       [slice(32, 48, 1), slice(None), slice(None)],
                       [slice(48, 64, 1), slice(None), slice(None)]],
            'device_id': (0, 1, 2, 3)
        },
        {
            'axes': ng.make_axes([ax_A, ax_B, ax_C]),
            'parallel_axis': ax_C,
            'slices': [[slice(None), slice(None), slice(0, 128, 1)],
                       [slice(None), slice(None), slice(128, 256, 1)]],
            'device_id': (0, 1)
        }
    ])


def test_scatter_gather_node_axes(config):
    t = config
  
    with ng.metadata(device=None, device_id='0', transformer='cpu0', host_transformer=None):
        from_node = ng.placeholder(axes=t['axes'])
        to_node = ng.placeholder(axes=t['axes'])

    with ng.metadata(device=None, device_id=t['device_id'], transformer=None,
                     parallel=t['parallel_axis'], host_transformer=None):
        par_node = ng.placeholder(axes=t['axes'])

    scatter_send_op = ScatterSendOp(from_node=from_node,
                                    to_node=par_node)
    assert t['axes'] == scatter_send_op.axes
    assert t['slices'] == scatter_send_op.slices

    scatter_recv_op = ScatterRecvOp(to_node=par_node,
                                    send_node=scatter_send_op)

    for sct_a, a in zip(scatter_recv_op.axes, t['axes']):
        if sct_a == t['parallel_axis']:
            assert sct_a.length == a.length / len(t['device_id'])
        else:
            assert sct_a.length == a.length

    gather_send_op = GatherSendOp(from_node=scatter_recv_op)
    assert_axes_eq_len(scatter_recv_op.axes, gather_send_op.axes)

    gather_recv_op = GatherRecvOp(from_node=par_node,
                                  to_node=to_node,
                                  send_node=gather_send_op)
    assert_axes_eq_len(t['axes'], gather_recv_op.axes)

    assert t['slices'] == gather_recv_op.slices


