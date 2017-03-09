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
from ngraph.util.hetr_utils import comm_path_exists, update_comm_deps, find_recvs
from ngraph.factory.comm_nodes import SendOp, ScatterSendOp, GatherSendOp
from ngraph.factory.comm_nodes import RecvOp, ScatterRecvOp, GatherRecvOp
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

    assert set([recv_x]) == find_recvs(x_plus_one)
    assert set([recv_x]) == find_recvs(recv_x)
    assert len(find_recvs(from_node)) == 0
    assert set([recv_x]) == find_recvs(send_x_plus_one)
    assert set([recv_x_plus_one, recv_x]) == find_recvs(recv_x_plus_one)
    assert set([recv_x_plus_one, recv_x]) == find_recvs(z)


def test_find_recvs_scatter_gather():
    scatter_send_x, scatter_recv_a, scatter_recv_b, gather_send_a, gather_send_b, \
        gather_recv_x_plus_one = create_scatter_gather_graph()

    assert set([scatter_recv_a]) == find_recvs(gather_send_a)
    assert set([scatter_recv_b]) == find_recvs(gather_send_b)
    assert len(find_recvs(scatter_send_x)) == 0
    assert set([gather_recv_x_plus_one, scatter_recv_a]) == find_recvs(gather_recv_x_plus_one)
    assert set([scatter_recv_a]) == find_recvs(scatter_recv_a)


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
    with ng.metadata(transformer='numpy0'):
        z, recv_x, recv_x_plus_one, send_x, x_plus_one, from_node, send_x_plus_one = create_graph()
    update_comm_deps((z, send_x))
    assert recv_x_plus_one in z.control_deps


def test_update_comm_deps_scatter_gather():
    parallel_metadata = dict(parallel=ax_B, device_id=(0, 1),
                             transformer=None, host_transformer=None, device=None)
    with ng.metadata(transformer='numpy0'):
        with ng.metadata(**parallel_metadata):
            from_node_a = ng.placeholder(axes)
            to_node_a = ng.placeholder(axes)
        scatter_send_x = ScatterSendOp(from_node=from_node_a, to_node=to_node_a)
        scatter_recv_a = ScatterRecvOp(to_node=to_node_a, send_node=scatter_send_x)
        with ng.metadata(**parallel_metadata):
            x_plus_one_a = scatter_recv_a + 1
        gather_send_x_plus_one_a = GatherSendOp(from_node=x_plus_one_a)

    with ng.metadata(transformer='numpy1'):
        with ng.metadata(**parallel_metadata):
            to_node_b = ng.placeholder(axes)
        scatter_recv_b = ScatterRecvOp(to_node=to_node_b, send_node=scatter_send_x)
        with ng.metadata(**parallel_metadata):
            x_plus_one_b = scatter_recv_b + 1
        gather_send_x_plus_one_b = GatherSendOp(from_node=x_plus_one_b)

    with ng.metadata(transformer='numpy0'):
        with ng.metadata(**parallel_metadata):
            gather_recv_x_plus_one_a = GatherRecvOp(from_node=from_node_a, to_node=to_node_a,
                                                    send_node=gather_send_x_plus_one_a)
            z_a = gather_recv_x_plus_one_a + 1

    update_comm_deps((scatter_send_x, gather_send_x_plus_one_a, z_a))
    update_comm_deps((gather_send_x_plus_one_b,))

    assert set([scatter_send_x]) == scatter_recv_a.control_deps
    assert set([scatter_send_x, gather_send_x_plus_one_a]) == \
        gather_recv_x_plus_one_a.control_deps
