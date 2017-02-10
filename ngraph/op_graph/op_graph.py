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
# ----------------------------------------------------------------------------
from __future__ import division

from contextlib import contextmanager
import collections

import inspect
import cachetools
import numpy as np
from builtins import object
from functools import wraps
from collections import defaultdict

from ngraph.op_graph.axes import TensorDescription, \
    make_axis, make_axes, Axes, FlattenedAxis, PaddedAxis, SlicedAxis, default_dtype, \
    default_int_dtype, casting_axis
from ngraph.util.names import NameableValue
from ngraph.util.threadstate import get_thread_state
from ngraph.util.ordered import OrderedSet


def tensor_descriptions(args):
    """
    A list of tensor descriptions for Ops.

    Arguments:
      args: A list of Ops.

    Returns:
      A list of the Op's tensor descriptions.
    """
    return (arg.tensor_description() for arg in args)


def tdcache():
    """
    Decorator to mark tensor description method as cached.

    Returns:
        Cache decorator set to use a particular cache.

    """
    return cachetools.cached(cache=tdcache.tensor_description_cache)

tdcache.tensor_description_cache = {}


@contextmanager
def metadata(**metadata):
    """
    Capture all Ops created within the context. Hides ops created in this
    context from parent contexts.
    """
    with Op.all_ops() as ops:
        yield
    for op in ops:
        op.metadata.update(metadata)


def with_op_metadata(f, metadata=None):
    """
    Decorator to add metadata to all ops created inside the decorated function.
    If this decorator is applied to a method of a class with a class
    variable `metadata` defined as a dictionary then we add that to the
    op metadata to attach.
    """
    metadata = metadata or dict()
    assert isinstance(metadata, dict), "Metadata must be dict, not {}".format(type(metadata))

    @wraps(f)
    def wrapper(*args, **kwargs):
        with Op.all_ops() as ops:
            result = f(*args, **kwargs)
        # If this decorator is applied to a method of a class with a class
        # variable called `metadata` then we add that to the
        if len(args) > 0 and hasattr(type(args[0]), 'metadata'):
            metadata.update(type(args[0]).metadata)
        for op in ops:
            op.metadata.update(metadata)
        return result
    return wrapper


class DebugInfo(object):
    """Mixin that captures file/line location of an object's creation."""

    def __init__(self, **kwargs):
        # TODO This is a good first cut for debugging info, but it would be nice to
        # TODO be able to reliably walk the stack back to user code rather than just
        # TODO back past this constructor
        super(DebugInfo, self).__init__(**kwargs)
        frame = None
        try:
            frame = inspect.currentframe()
            while frame.f_locals.get('self', None) is self:
                frame = frame.f_back
            while frame:
                filename, lineno, function, code_context, index = inspect.getframeinfo(
                    frame)
                if -1 == filename.find('ngraph/op_graph'):
                    break
                frame = frame.f_back

            self.filename = filename
            self.lineno = lineno
            self.code_context = code_context
        finally:
            del frame

    @property
    def file_info(self):
        """
        Return file location that created the node.

        Returns:
            String with file location that created the node.

        """
        return 'File "{filename}", line {lineno}'.format(
            filename=self.filename, lineno=self.lineno)


class Op(NameableValue, DebugInfo):
    """
    Any operation that can be in an AST.

    Arguments:
        args: Values used by this node.
        const: The value of a constant Op, or None,
        constant (bool): The Op is constant.  Default False.
        forward: If not None, the node to use instead of this node.
        initializers: List of one-time initializations to run before the op.
        persistent (bool): The value will be retained from computation to computation and
            not shared.  Default False.
        metadata: String key value dictionary for frontend metadata.
        trainable (bool): The value is trainable.  Default False.
        kwargs: Args defined in related classes.

    Attributes:
        const: The value of a constant.
        constant (bool): The value is constant.
        initializers (list): Additional Ops to run before this Op is run the first time.
        control_deps (OrderedSet): Ops in addtion to args that must run before this op.
        persistent (bool): The value will be retained from computation to computation and
            not shared.  Always True if reference is set.
        schemas: Information about how the Op was generated.
        metadata: Dictionary with of string keys and values used for attaching
            arbitrary metadata to nodes.
        trainable: The value is trainable.
    """

    # Default is to not collect Ops as they are created
    @staticmethod
    def _get_thread_ops():
        """
        :return: The stack of Ops being collected.
        """
        try:
            ops = get_thread_state().ops
        except AttributeError:
            ops = [None]
            get_thread_state().ops = ops
        return ops

    @staticmethod
    @contextmanager
    def captured_ops(ops=None):
        """
        Capture all Ops created within the context. Hides ops created in this
        context from parent contexts.
        """
        if ops is None:
            ops = []
        try:
            Op._get_thread_ops().append(ops)
            yield (ops)
        finally:
            Op._get_thread_ops().pop()

    @staticmethod
    def get_all_ops():
        try:
            all_ops = get_thread_state().all_ops
        except AttributeError:
            all_ops = [None]
            get_thread_state().all_ops = all_ops
        return all_ops

    # We need to create another stack here because all_ops and captured_ops
    # have different semantics that don't work with a shared stack
    @staticmethod
    @contextmanager
    def all_ops(ops=None, isolate=False):
        """
        Collects all Ops created within the context. Does not hide ops created
        in this context from parent contexts unless isolate is True.
        """
        if ops is None:
            ops = []
        try:
            all_ops = Op.get_all_ops()
            all_ops.append(ops)
            yield (ops)
        finally:
            all_ops.pop()
            parent = all_ops[-1]
            if not isolate and parent is not None:
                parent.extend(ops)

    @staticmethod
    def ordered_ops(results):
        """
        depth-first, post-order "Bottom Up" traversal of Ops in results.

        Ops will only appear once in result.

        Arguments:
          results: a list of ops which are the roots of the graph traversal

        Returns:
          list of Ops in depth-first, post-order
        """
        ordered_ops = []
        Op.visit_input_closure(results, lambda o: ordered_ops.append(o))
        return ordered_ops

    @staticmethod
    def visit_input_closure(roots, fun):
        """
        Topological sort order traversal of root and their inputs.

        Nodes will only be visited once, even if there are multiple routes to the
        same Node.

        Arguments:
            roots: root set of nodes to visit
            fun: Function to call on each visited node

        Returns:
            None
        """
        available = OrderedSet()
        counts = dict()
        parents = defaultdict(OrderedSet)
        ready = OrderedSet()

        available.update(root.forwarded for root in roots)
        while available:
            node = available.pop()
            node.update_forwards()

            if node in counts:
                continue

            children = OrderedSet((child.forwarded for child in node.control_deps))
            if children:
                counts[node] = len(children)
                for child in children:
                    parents[child].add(node)
                available.update(children)
            else:
                ready.add(node)

        while ready:
            node = ready.pop()
            fun(node)
            for p in parents.get(node, []):
                count = counts[p] - 1
                if count == 0:
                    ready.add(p)
                    del counts[p]
                else:
                    counts[p] = count
        if len(counts) > 0:
            raise ValueError("Graph not a DAG")

    def __init__(self,
                 args=(),
                 metadata=None,
                 const=None,
                 constant=False,
                 persistent=True,
                 trainable=False,
                 initializers=None,
                 **kwargs):
        super(Op, self).__init__(**kwargs)
        self.__args = tuple(as_op(arg) for arg in args)
        self.metadata = dict()

        if metadata is not None:
            if not isinstance(metadata, dict):
                raise ValueError("Metadata must be of type dict,"
                                 "not {} of {}".format(type(metadata), metadata))
            self.metadata.update(metadata)

        # List to keep generation deterministic
        self.__control_deps = OrderedSet()
        self.schemas = []
        self.const = const
        self.__is_constant = constant
        self.__is_persistent = persistent
        self.__is_trainable = trainable
        self.initializers = OrderedSet()
        if initializers is not None:
            for initializer in initializers:
                self.add_initializer(initializer)

        # Add this op to both the captured op and all op accounting lists
        ops = Op._get_thread_ops()[-1]
        if ops is not None:
            ops.append(self)
        all_ops = Op.get_all_ops()[-1]
        if all_ops is not None:
            all_ops.append(self)

        self.style = {}
        self.__forward = None

    @property
    def tensor(self):
        """

        Returns: The op providing the value.

        """
        return self

    @property
    def states_read(self):
        """

        Returns: All state read by this op.

        """
        return OrderedSet()

    @property
    def states_written(self):
        """

        Returns: All state written by this op.

        """
        return OrderedSet()

    def __str__(self):
        return self.graph_label

    def __repr__(self):
        return '<{cl}({gl}):{id}>'.format(
            cl=self.__class__.__name__,
            gl=self.graph_label_type,
            id=id(self)
        )

    @property
    def is_constant(self):
        return self.__is_constant

    @property
    def is_persistent(self):
        return self.__is_persistent

    @property
    def is_trainable(self):
        return self.__is_trainable

    @property
    def is_tensor_op(self):
        return False

    @property
    def is_scalar(self):
        return 0 == len(self.axes)

    @property
    def is_device_op(self):
        """

        Returns:
            True if the Op executes on the device.
        """
        return True

    @property
    def scalar_op(self):
        """
        Returns the scalar op verion of this op.  Will be overridden by subclasses
        """
        if not self.is_scalar:
            raise ValueError()
        return self

    @property
    def args(self):
        """All the inputs to this node."""
        return self.__args

    @property
    def forward(self):
        """
        If not None, self has been replaced with forward.

        When set, invalidates cached tensor descriptions.

        Returns:
             None or the replacement.
        """
        return self.__forward

    @forward.setter
    def forward(self, value):
        self.update_forwards()
        value.update_forwards()

        # Make sure everything that is supposed to happen
        # before this op still happens
        for dep in self.__control_deps:
            value.add_control_dep(dep)
        self.__forward = value
        tdcache.tensor_description_cache.clear()
        value.metadata.update(self.metadata)

    @property
    def forwarded(self):
        """
        Finds the op that handles this op.

        Returns:
             Follows forwarding to the op that shoud handle this op.
        """
        result = self
        while True:
            if not result.__forward:
                return result
            result = result.__forward

    @property
    def control_deps(self):
        """

        Returns:
            Ops that must execute before this one can.
        """
        return self.__control_deps + self.args

    def add_control_dep(self, dep):
        """
        Add an op that needs to run before this op.

        Args:
            dep: The op.

        """
        dep = dep.forwarded
        if dep is not self and dep not in self.control_deps:
            self.__control_deps.add(dep)

    def remove_control_dep(self, dep):
        """
        Remove an op from the list of ops that need to run before this op.

        Args:
            dep: The op.

        """
        self.update_forwards()
        self.__control_deps.remove(dep.forwarded)

    def add_initializer(self, init):
        self.initializers.add(init)

    def update_forwards(self):
        """
        Replaces internal op references with their forwarded versions.

        Any subclass that uses ops stored outside of args, control_deps, and initializers
        needs to override this method to update those additional ops.

        This is mainly to reduce the number of places that need to explicitly check
        for forwarding.

        """

        for op in self.control_deps:
            if op.forward is not None:
                self.__args = tuple(arg.forwarded for arg in self.__args)
                control_deps = self.__control_deps
                self.__control_deps = OrderedSet()
                for op in control_deps:
                    self.add_control_dep(op.forwarded)
                break
        for op in self.initializers:
            if op.forward is not None:
                self.initializers = OrderedSet(op.forwarded for op in self.initializers)
                break

    def replace_self(self, rep):
        self.forward = rep

    def add_schema(self, schema, set_generate_adjoints=True):
        """
        Adds a description of some op substructure.

        When a function generates a groups of nodes, it can add a schema
        describing the roles of these nodes.  The schema may include its
        own generate_adjoints.

        Arguments:
          schema: param set_generate_adjoints: Whether to override the node's generate_adjoints
        with the version from the schema.
          set_generate_adjoints: TODO

        Returns:
          TODO
        """
        self.schemas.insert(0, schema)
        if set_generate_adjoints:
            # generate_adjoints is normally called with *args, but for a
            # schema we call it with the associated node.
            def generate_adjoints(adjoints, adjoint, *ignore):
                """
                TODO.

                Arguments:
                  adjoints: TODO
                  adjoint: TODO
                  *ignore: TODO
                """
                schema.generate_adjoints(adjoints, adjoint, self)
            # Replace generate_adjoints for self
            self.generate_adjoints = generate_adjoints

    def find_schema(self, t):
        """
        Find a schema of particular type.

        Searches added schema for one of type t.

        Arguments:
          t: The type of schema desired.

        Returns:
          A schema of type t, or None.
        """
        for schema in self.schemas:
            if isinstance(schema, t):
                return schema
        return None

    @property
    def defs(self):
        """
        Returns:
            For liveness analysis.  The storage associated with everything
            in the returned list is modified when the Op is executed.

        """
        return [self]

    def variables(self, filter=None):
        """
        Return all trainable Ops used in computing this node.

        Arguments:
            filter: Boolean filter of op, defaults to trainable.

        Returns:
            Set of trainable Ops.
        """
        params = OrderedSet()

        if filter is None:
            filter = lambda op: op.is_trainable

        def visitor(node):
            """
            TODO.

            Arguments:
              node: TODO
            """
            if filter(node.tensor):
                params.add(node.tensor)

        Op.visit_input_closure([self], visitor)

        return params

    @cachetools.cached({})
    def adjoints(self, error):
        """
        Returns a map containing the adjoints of this op with respect to other
        ops.

        Creates the map if it does not already exist.

        Arguments:
            error (TensorOp, optional): The tensor holding the error value
                the derivative will be computed at. Must have the same axes as dependent.


        Returns:
            Map from Op to dSelf/dOp.
        """
        adjoints = {
            self: error,
        }

        # visit ops in reverse depth first post-order. it is important that
        # ordered_ops returns a copy of this traversal order since the graph
        # may change as we generate adjoints and we don't want to visit those
        # new ops.
        for o in reversed(Op.ordered_ops([self])):
            if o in adjoints:
                adjoint = adjoints[o]
                if o.scale is not None:
                    adjoint = adjoint * o.scale

                o.generate_adjoints(adjoints, adjoint, *o.args)

        return adjoints

    def tensor_description(self):
        return None

    @cachetools.cached({})
    def call_info(self):
        """
        Creates the TensorDescriptions (of this op or its arguments)
        required to evaluate it.

        The list is used to allocate buffers (in the transformers) and supply
        values to the transform method (in the transform_call_info) method.

        Only TensorDescriptions of the arguments are necessary.  A
        TensorDescription of the output is generate by calling
        self.tensor_description()
        """
        return list(tensor_descriptions(self.args))


def as_op(x):
    """
    Finds an Op appropriate for x.

    If x is an Op, it returns x. Otherwise, constant(x) is returned.

    Arguments:
      x: Some value.

    Returns:
      Op:
    """
    if isinstance(x, AssignableTensorOp):
        return TensorValueOp(x)

    if isinstance(x, Op):
        return x

    return constant(x)


def as_ops(xs):
    """
    Converts an iterable of values to a tuple of Ops using as_op.

    Arguments:
        xs: An iterable of values.

    Returns:
        A tuple of Ops.
    """
    return tuple(as_op(x) for x in xs)


def init_tensor(tensor, valfun):
    """
    Initializes a device tensor from a CPU tensor.

    Arguments:
        tensor: Tensor to be intialized.
        valfun: Function that performs initialization

    Returns:
        InitTensorOp: The tensor initialization.

    """
    return InitTensorOp(tensor, valfun)


class AssignOp(Op):
    """
    tensor[...] = val.

    Arguments:
        tensor (AssignableTensorOp): An assignable TensorOp.
        val: The value to assign.
        force (bool): Override constant check on tensor.
        **kwargs: Args for related classes.
    """

    def __init__(self, tensor, val, force=False, **kwargs):
        tensor, val = as_ops((tensor, val))
        if not force and tensor.is_constant:
            raise ValueError("{} is not assignable.".format(tensor))
        val = broadcast(val, tensor.axes)
        super(AssignOp, self).__init__(args=(tensor, val), **kwargs)
        self.force = force

    @property
    def states_written(self):
        return self.args[0].states_read


class AssignOneDOp(Op):
    """
    Assign a value to a 1d tensor.

    Arguments:
        tensor (AssignableTensorOp): The value to assign to.
        value (TensorOp): The value.
    """

    def __init__(self, tensor, val, force=False, **kwargs):
        if val.is_scalar:
            val = val.scalar_op
        super(AssignOneDOp, self).__init__(args=(tensor, val), **kwargs)
        self.force = force

    @property
    def states_written(self):
        return self.args[0].states_read


class AssignTwoDOp(AssignOneDOp):
    """
    Assign a value to a 2d tensor

    Arguments:
        tensor (AssignableTensorOp): The value to assign to.
        value (TensorOp): The value.
    """
    pass


def assign(lvalue, rvalue):
    """
    Assignment; lvalue <= rvalue

    Arguments:
        lvalue: Tensor to assign to.
        rvalue: Value to be assigned.
        item (optional):
    """
    return AssignOp(lvalue, rvalue)


class SetItemOp(Op):
    """
    tensor[item] = val

    This is a stub and has no frontend support at this time.

    Arguments:
        tensor (AssignableTensorOp): An assignable tensor.
        item: An index into the tensor.
        val (TensorOp): A value to assign.

    """

    def __init__(self, tensor, item, val, **kwargs):
        super(SetItemOp, self).__init__(args=(tensor, val), **kwargs)
        self.item = tuple(item)

    @property
    def states_written(self):
        return self.args[0].states_read


class ControlBlockOp(Op):
    """
    An Op that affects execution sequencing.
    """
    def __init__(self, **kwargs):
        super(ControlBlockOp, self).__init__(**kwargs)

    @property
    def is_device_op(self):
        """

        Returns:
            False, because this is handled by the transformer.
        """
        return False


class ParallelOp(ControlBlockOp):
    """
    Compute every Op in all in any order compatible with existing dependencies.

    Arguments:
        all: Ops to be computed.
        **kwargs: Args for related classes.
    """
    def __init__(self, all, **kwargs):
        super(ParallelOp, self).__init__(**kwargs)
        for op in all:
            self.add_control_dep(op)


def doall(all):
    return ParallelOp(all)


class ComputationOp(ParallelOp):
    """
    Represents a host-callable graph computation.

    Arguments:
        returns: Values returned by the computation. A list, set, or op.
        *args: Inputs to the computation.

    Parameters:
        returns: Ops returned.
        parameters: Parameter ops.
    """
    def __init__(self, returns, *args, **kwargs):
        args = tuple(as_op(arg) for arg in args)
        for arg in args:
            if not isinstance(arg.tensor, AssignableTensorOp) or not arg.tensor.input:
                raise ValueError((
                    'The arguments to a computation must all be Ops with property '
                    'input=True, but the op passed had input=False.  In most '
                    'cases you want to pass placeholder ops in as arguments.  '
                    '{op} was passed in, of type {op_type}.'
                ).format(
                    op=arg,
                    op_type=arg.__class__.__name__,
                ))

        if isinstance(returns, collections.Container):
            all = type(returns)(as_op(ret) for ret in returns)
        elif isinstance(returns, Op):
            all = [as_op(returns)]
        elif returns is not None:
            raise ValueError()
        else:
            all = []

        self.returns = returns
        super(ComputationOp, self).__init__(all=all, **kwargs)
        self.parameters = args
        for arg in args:
            self.add_control_dep(arg)


def computation(returns, *args):
    """
    Defines a host-callable graph computation.

    Arguments:
        returns: Values returned by the computation. A list, set, or op.
        *args: Inputs to the computation.

    Returns:
        A computation op.
    """

    return ComputationOp(returns, *args)


class Fill(Op):
    """
    Fill a tensor with a scalar value.

    Arguments:
        tensor (AssignableTensorOp): An assignable TensorOp.
        scalar: A scalar value.
        force (bool): Disable constant check on tensor.
    """

    def __init__(self, tensor, scalar, force=False, **kwargs):
        super(Fill, self).__init__(args=(tensor,), **kwargs)
        if not force and tensor.is_constant:
            raise ValueError("{} is not assignable.".format(tensor))
        if isinstance(scalar, TensorOp):
            if scalar.is_constant:
                scalar = scalar.const
            else:
                raise ValueError("{} is not a scalar constant".format(scalar))
        else:
            npscalar = np.asarray(scalar, dtype=tensor.dtype)
            if 0 != len(npscalar.shape):
                raise ValueError("{} is not a scalar".format(scalar))
            scalar = npscalar[()]

        self.scalar = scalar

    @property
    def states_written(self):
        return self.args[0].states_read


class TensorOp(Op):
    """
    Super class for all Ops whose value is a Tensor.

    Arguments:
        axes: The axes of the tensor.
        dtype: The element type of the tensor.
        scale: If specified, a scaling factor applied during updates.
        is_value_op: If specified, the normal dtype/axes/scale defaulting is disabled
          since those values will be supplied by a subclass, such as ValueOp.
        **kwargs: Arguments for related classes.
    """

    def __init__(self, dtype=None, axes=None, scale=None, is_value_op=None, **kwargs):
        super(TensorOp, self).__init__(**kwargs)
        if not is_value_op:
            self.dtype = default_dtype(dtype)
            if axes is not None:
                axes = make_axes(axes)
            self.__axes = axes
            self.scale = scale

    @property
    def is_tensor_op(self):
        return True

    def generate_add_delta(self, adjoints, delta):
        """
        Adds delta to the backprop contribution..

        Arguments:
            adjoints: dy/dOp for all Ops used to compute y.
            delta: Backprop contribute.
        """
        if not self.axes.has_same_axes(delta.axes):
            raise ValueError(
                'delta axes {} do not match adjoint axes {}'
                .format(delta.axes, self.axes)
            )
        if self not in adjoints:
            adjoints[self] = delta
        else:
            adjoints[self] = delta + adjoints[self]

    # Magic methods for builtin operations we want to use for creating nodes
    def __neg__(self):
        return negative(self)

    def __pos__(self):
        return self

    def __abs__(self):
        return absolute(self)

    def __add__(self, val):
        return add(self, val)

    def __radd__(self, val):
        return add(val, self)

    def __sub__(self, val):
        return subtract(self, val)

    def __rsub__(self, val):
        return subtract(val, self)

    def __mul__(self, val):
        return multiply(self, val)

    def __rmul__(self, val):
        return multiply(val, self)

    def __div__(self, val):
        return divide(self, val)

    def __mod__(self, val):
        return mod(self, val)

    def __truediv__(self, val):
        return divide(self, val)

    def __rtruediv__(self, val):
        return divide(val, self)

    def __rdiv__(self, val):
        return divide(val, self)

    def __pow__(self, val):
        return power(self, val)

    def __rpow__(self, val):
        return power(val, self)

    # Python always uses eq for comparing keys, so if we override __eq__ we
    # cannot have sets of tensors, or using them as dictionary keys.  So,
    # we must use Equal explicitly in transform.  defmod and define __eq__
    # if it can ensure that its nodes do not need to be used as keys.
    # def __eq__(self, val):
    #    return equal(self, val)

    # def __ne__(self, val):
    #    return not_equal(self, val)

    def __lt__(self, val):
        return less(self, val)

    def __gt__(self, val):
        return greater(self, val)

    def __le__(self, val):
        return less_equal(self, val)

    def __ge__(self, val):
        return greater_equal(self, val)

    def __setitem__(self, key, val):
        if key == slice(None) or key is Ellipsis:
            return assign(self, val)
        raise ValueError("Setting {} is not supported yet".format(key))

    # Only works when capturing ops
    def __iadd__(self, val):
        return assign(self, self + val)

    # Only works when capturing ops
    def __isub__(self, val):
        return assign(self, self - val)

    # Only works when capturing ops
    def __imul__(self, val):
        return assign(self, self * val)

    # Only works when capturing ops
    def __idiv__(self, val):
        return assign(self, self / val)

    def __getitem__(self, item):
        if isinstance(item, slice) and len(self.axes) > 1:
            item = (item,)
        item += tuple(slice(None) for _ in range(len(self.axes) - len(item)))
        return tensor_slice(self, item)

    def __axes__(self):
        return self.axes

    @tdcache()
    def tensor_description(self):
        """
        Returns a TensorDescription describing the output of this TensorOp

        Returns:
          TensorDescription for this op.
        """
        return TensorDescription(self.axes, dtype=self.dtype).named(self.name)

    @property
    def axes(self):
        """

        Returns: The axes of the tensor.

        """
        if self.__axes is not None:
            return self.__axes
        else:
            raise NotImplementedError

    @axes.setter
    def axes(self, value):
        if self.__axes is not None:
            raise ValueError()
        self.__axes = value

    @property
    def has_axes(self):
        """

        Returns: True if axes have been set.

        """
        return self.__axes is not None

    def insert_axis(self, index, axis):
        """
        Inserts an axis
        Arguments:
            index   : Index to insert at
            axis    : The Axis object to insert
        """
        if self.__axes is None:
            raise ValueError()
        self.__axes.insert(index, axis)

    def append_axis(self, axis):
        if self.__axes is None:
            raise ValueError()
        self.__axes.append(axis)

    def generate_adjoints(self, adjoints, delta, *args):
        """
        With delta as the computation for the adjoint of this Op, incorporates delta into the
        adjoints for thr args.

        Args:
            adjoints: dy/dOp for all ops involved in computing y.
            delta: Backprop amount for this Op.
            *args: The args of this Op.
        """
        pass

    @property
    def shape(self):
        """
        This is required for parameter initializers in legacy neon code.  It
        expects layers to implement a shape that it can use to pass through
        layers.

        Returns: self.axes
        """
        return self.axes

    def shape_dict(self):
        """
        Retuns: shape of this tensor as a dictionary
        """
        return self.axes.shape_dict()

    def mean(self, reduction_axes=None, out_axes=None):
        """
        Used in Neon front end.

        Returns: mean(self)

        """
        return mean(self, reduction_axes=reduction_axes, out_axes=out_axes)

    @property
    def value(self):
        """
        Returns a handle to the device tensor.

        The transformer must have been initialized.

        :return: A handle to the device tensor.
        """
        return self.forwarded.tensor_description().value


class ValueOp(TensorOp, ControlBlockOp):
    """
    Mixin class for ops whose value is another op.

    """
    def __init__(self, tensor=None, **kwargs):
        super(ValueOp, self).__init__(args=(), is_value_op=True, **kwargs)
        self.__tensor = tensor

    def tensor_description(self):
        return self.tensor.tensor_description()

    @property
    def tensor(self):
        """
        The op that supplies the value.

        Returns:
            The op that supplies the value.

        """
        return self.__tensor

    @tensor.setter
    def tensor(self, tensor):
        self.__tensor = tensor

    @property
    def is_tensor_op(self):
        return self.tensor.is_tensor_op

    @property
    def value(self):
        return self.tensor.value

    @property
    def axes(self):
        return self.tensor.axes

    @property
    def dtype(self):
        return self.tensor.dtype

    @dtype.setter
    def dtype(self, dtype):
        self.tensor.dtype = dtype

    @property
    def scale(self):
        return self.tensor.scale

    @property
    def states_read(self):
        return self.tensor.states_read

    @property
    def states_written(self):
        return self.tensor.states_written

    def generate_adjoints(self, adjoints, delta):
        self.tensor.generate_add_delta(adjoints, delta)

    def generate_add_delta(self, adjoints, delta):
        self.tensor.generate_add_delta(adjoints, delta)


class InitTensorOp(ValueOp):
    """
    Initializes a device tensor from a CPU tensor.

    Arguments:
        tensor: Tensor to be intialized.
        valfun: Function that performs initialization
        kwargs: Other op args.

    Attributes:
        valfun: A CPU function that produces the initial value for the tensor.

    """

    def __init__(self, tensor, valfun, **kwargs):
        super(InitTensorOp, self).__init__(tensor=tensor, **kwargs)
        self.valfun = valfun

    @property
    def states_written(self):
        return OrderedSet([self.tensor])


class SequentialOp(ValueOp):
    """
    Given a list of ops, ensure that every op that has not already been executed is executed in
    the given order. The value of the last op is the value of this op.

    Ops will only be executed once, so to return the value of an earlier op, just add it again at
    the end of the list.

    Control dependencies are not computed until after the graph is computed, i.e. after derivatives
    are expanded.

    Arguments:
        ops: Sequence of ops to compute. If not specified, set the attribute ops when known. This
            is useful for subclassing.

    Attributes:
        ops: The list of ops to be computed. The last op is the returned value.
    """
    def __init__(self, ops=None, **kwargs):
        super(SequentialOp, self).__init__(**kwargs)
        self.tensor = None
        self.__ops = None
        self.control_dependencies_computed = False
        if ops is not None:
            self.ops = ops

    @property
    def ops(self):
        return self.__ops

    @ops.setter
    def ops(self, ops):
        self.__ops = list(as_op(op).forwarded for op in ops)

        for op in self.__ops:
            self.add_control_dep(op)
        self.tensor = self.__ops[-1]
        self.control_dependencies_computed = False

    def compute_control_dependencies(self):
        # Called in passes after graph expansion, such as derivatives, has been
        # performed.
        if self.control_dependencies_computed:
            return

        # Ops that have already executed.
        done_ops = set()

        # State => op_tops that have written state
        writers = defaultdict(OrderedSet)
        # State => op_tops that have read state
        readers = defaultdict(OrderedSet)
        for op_top in self.__ops:
            ordered_ops = Op.ordered_ops([op_top])
            # Make ops that read/write state execute after the op_tops that last read/wrote
            # the state.
            for op in ordered_ops:
                if op in done_ops:
                    # The op already ran, so it doesn't run here
                    continue
                for state in op.states_read:
                    for write_op in writers[state]:
                        op.add_control_dep(write_op)
                for state in op.states_written:
                    for read_op in readers[state]:
                        op.add_control_dep(read_op)
            # Register this op_top with each state it read/wrote.
            for op in ordered_ops:
                if op in done_ops:
                    # The op already ran, so it doesn't run here
                    continue
                for state in op.states_written:
                    writers[state].add(op_top)
                for state in op.states_read:
                    readers[state].add(op_top)
            done_ops.update(ordered_ops)
        self.control_dependencies_computed = True


def sequential(ops=None):
    """
    Compute every op in order, compatible with existing dependencies, returning last value.

    Ops will only be executed once, so to return the value of an earlier op, just add it again at
    the end of the list.

    Arguments:
        ops: Sequence of ops to compute.

    """
    return SequentialOp(ops)


class TensorValueOp(ValueOp):
    """
    A read of an AssignableTensorOp.

    This provides a way to maintain different control information on different
    versions of state.

    """
    def __init__(self, tensor, **kwargs):
        super(TensorValueOp, self).__init__(tensor=tensor, **kwargs)

        for key in ['device', 'device_id', 'parallel']:
            if key in tensor.metadata:
                self.metadata[key] = tensor.metadata[key]

    @property
    def states_read(self):
        return OrderedSet([self.tensor])


class ReshapeOp(TensorOp):

    def __init__(self, x, **kwargs):
        super(ReshapeOp, self).__init__(
            args=(x,),
            dtype=x.dtype,
            **kwargs
        )

    @property
    def is_scalar(self):
        """
        Reshape adds shape information, but we retain being a scalar.

        Returns:
            True if the value comes from a scalar.

        """
        return self.args[0].is_scalar

    @property
    def scalar_op(self):
        return self.args[0].scalar_op

    @property
    def is_device_op(self):
        """
        Returns:
            False, because this is handled by the transformer.
        """
        return False


class Transpose(ReshapeOp):
    """
    Used to reverse the axes of a tensor.

    Arguments:
        x: A tensor.
    """

    def __init__(self, x, **kwargs):
        super(Transpose, self).__init__(
            x,
            axes=reversed(x.axes),
            **kwargs
        )

    @tdcache()
    def tensor_description(self):
        return self.args[0].tensor_description().transpose().named(self.name)

    def generate_adjoints(self, adjoints, delta, x):
        x.generate_add_delta(adjoints, Transpose(delta))


class AxesCastOp(ReshapeOp):
    """
    Used to label a tensor with known axes, without altering its value

    Arguments:
        x: A tensor.
        axes: The new axes.

    """

    def __init__(self, x, axes, **kwargs):
        axes = make_axes(axes)
        if not x.is_scalar and x.axes.lengths != axes.lengths:
            raise ValueError("casting axes {} must have the same length as original axes {}"
                             .format(axes, x.axes))
        if len(x.axes) > 0:
            aliasing_axes = []
            for new_axis, old_axis in zip(axes, x.axes):
                aliasing_axes.append(casting_axis(new_axis, old_axis))
            axes = make_axes(aliasing_axes)

        super(AxesCastOp, self).__init__(x, axes=axes, **kwargs)

    @tdcache()
    def tensor_description(self):
        return self.args[0].tensor_description().cast(self.axes).named(self.name)

    def generate_adjoints(self, adjoints, delta, x):
        x.generate_add_delta(adjoints, cast_axes(delta, x.axes))


def cast_axes(tensor, axes):
    """
    Cast the axes of a tensor to new axes.

    Args:
        tensor (TensorOp): The tensor.
        axes (Axes): The new axes.

    Returns:
        TensorOp: The tensor with new axes.
    """
    return AxesCastOp(tensor, axes)


class ExpandDims(ReshapeOp):
    """
    Adds additional axes into a tensor.
    Arguments:
        x: The tensor.
        axis: The additional axis.
        dim: The position to add the axes.
    """

    def __init__(self, x, axis, dim, **kwargs):
        axes = []
        axes.extend(x.axes[:dim])
        axes.append(axis)
        axes.extend(x.axes[dim:])
        axes = make_axes(axes)
        super(ExpandDims, self).__init__(x, axes=axes, **kwargs)

    @tdcache()
    def tensor_description(self):
        """
        TODO.
        Arguments:
        Returns:
          TODO
        """
        x, = tensor_descriptions(self.args)
        return x.broadcast(self.axes)

    def generate_adjoints(self, adjoints, delta, x):
        """
        TODO.
        Arguments:
          adjoints: TODO
          delta: TODO
          x: TODO
        Returns:
          TODO
        """
        x.generate_add_delta(
            adjoints,
            sum(delta, reduction_axes=delta.axes - x.axes)
        )


def expand_dims(x, axis, dim):
    """
    Adds additional axes into a tensor.
    Arguments:
        x: The tensor.
        axis: The additional axis.
        dim: The position to add the axes.
    """
    if axis in x.axes:
        return x
    return ExpandDims(x, axis, dim)


class BroadcastOp(ReshapeOp):
    """
    Used to add additional axes for a returned derivative.

    Arguments:
        x: The tensor to broadcast.
        axes: The new axes.
    """

    def __init__(self, x, axes, **kwargs):
        Axes.assert_valid_broadcast(x.axes, axes)
        super(BroadcastOp, self).__init__(
            x, axes=axes, **kwargs
        )

    @tdcache()
    def tensor_description(self):
        """
        TODO.

        Arguments:

        Returns:
          TODO
        """
        td, = tensor_descriptions(self.args)
        return td.broadcast(self.axes).named(self.name)

    def generate_adjoints(self, adjoints, delta, x):
        x.generate_add_delta(adjoints, sum(
            delta,
            reduction_axes=delta.axes - x.axes,
            out_axes=x.axes
        ))


def broadcast(x, axes):
    """
    Broadcast the axes of x.

    Args:
        x (TensorOp): The tensor.
        axes: New axes.

    Returns:
        TensorOp: Tensor with additional axes.
    """
    axes = make_axes(axes)
    if x.axes == axes:
        return x
    return BroadcastOp(x, axes)


def axes_with_role_order(x, roles):
    """
    Return a tensor with a different axes order according to
    specified roles.  Will expand dims as necessary with inferred
    axes for missing roles

    Args:
        x (TensorOp): The tensor.
        roles (sequence, AxisRoles): A permutation of the roles
                                     of axes of the tensor.

    Returns:
        TensorOp: The new tensor.

    """
    reordered_axes = make_axes()
    y = x
    for r in roles:
        ax_i = y.axes.role_axes(r)
        if len(ax_i) == 0:
            ax_i = make_axis(length=1, roles=[r])
        elif len(ax_i) == 1:
            ax_i = ax_i[0]
        else:
            raise ValueError("Unable to handle multiple axes with role {}".format(r.name))
        reordered_axes += ax_i
        # This will only add the missing axes to the front
        y = expand_dims(y, ax_i, 0)

    # Ensure that axes of x are a subset of y
    if not x.axes.intersect(y.axes).has_same_axes(x.axes):
        raise ValueError("Input axes contain roles not encompassed by role list: {}".format(
            x.axes - x.axes.intersect(y.axes)
        ))

    return axes_with_order(y, reordered_axes)


def axes_with_order(x, axes):
    """
    Return a tensor with a different axes order.

    Args:
        x (TensorOp): The tensor.
        axes (Axes): A permutation of the axes of the tensor.

    Returns:
        TensorOp: The new tensor.

    """
    axes = make_axes(axes)
    if x.axes == axes:
        return x
    return ReorderAxes(x, axes)


class ReorderAxes(ReshapeOp):
    """
    Reorders the axes of a tensor, without making a copy.

    Arguments:
        x: The tensor whose axes to reorder.
        axes: The new axes.
    """

    def __init__(self, x, axes, **kwargs):
        if not x.axes.has_same_axes(axes):
            raise ValueError(
                'The input and output axes must have the same elements.'
            )
        super(ReorderAxes, self).__init__(
            x, axes=axes, **kwargs
        )

    @tdcache()
    def tensor_description(self):
        """
        TODO.

        Arguments:

        Returns:
          TODO
        """
        td, = tensor_descriptions(self.args)
        return td.reorder(self.axes).named(self.name)

    def generate_adjoints(self, adjoints, delta, x):
        x.generate_add_delta(adjoints, axes_with_order(
            delta,
            x.axes
        ))


def tensor_slice(x, slices, axes=None):
    """
    Creates a sliced version of a tensor.

    Args:
        x: The tensor.
        slices: One slice for each dimension in x.
        axes: Axes for the result.  If not specified, axes will be generated.

    Returns:
        A sliced view of the tensor.
    """
    return TensorSliceOp(x, slices, axes)


class TensorSliceOp(ReshapeOp):
    """
    Creates a sliced version of a tensor.

    Arguments:
        x: The tensor.
        slices: One slice for each dimension in x.
        axes: Axes for the result.  If not specified, axes will be generated.
    """

    def __init__(self, x, slices, axes=None, **kwargs):
        slices = tuple(slices)
        if len(slices) != len(x.shape):
            raise ValueError((
                'There should be one slice in slices for each dimension in '
                'input tensor.  Input tensor had {tensor_dim} dimensions, '
                'but slices has length {slices_len}.'
            ).format(
                tensor_dim=len(x.shape),
                slices_len=len(slices),
            ))

        if axes is None:
            axes = []
            for axis, s in zip(x.axes, slices):
                # if s is an int, we are doing a getitem, for example y = x[1]
                # and so this axis will no longer exist in the result.
                if not isinstance(s, int):
                    # if nop slice, don't slice the axis
                    if s == slice(None, None, None):
                        axes.append(axis)
                    else:
                        axes.append(SlicedAxis(axis, s))

            axes = make_axes(axes)

        super(TensorSliceOp, self).__init__(
            x,
            axes=axes,
            **kwargs
        )

        self.slices = slices

    @tdcache()
    def tensor_description(self):
        """
        TODO.

        Arguments:

        Returns:
          TODO
        """
        x, = tensor_descriptions(self.args)
        return x.slice(self.slices, self.axes).named(self.name)

    def generate_adjoints(self, adjoints, delta, x):
        """
        TODO.

        Arguments:
          adjoints: TODO
          delta: TODO
          x: TODO

        Returns:
          TODO
        """
        x.generate_add_delta(
            adjoints,
            _unslice(delta, self.slices, x.axes)
        )


def slice_along_axis(x, axis, idx):
    """
    Returns a slice of a tensor constructed by indexing into a single axis
    at a single position. If the axis occurs multiple times in the dimensions
    of the input tensor, we select only on the first occurrence.
    Arguments:
        x: input tensor
        axis: axis along which to slice
        idx: index to select from the axis
    Returns:
        y: a slice of x
    """
    pos = x.axes.index(axis)
    ss = tuple(idx if i == pos else slice(None) for i in range(len(x.axes)))
    axes = x.axes[:pos] + x.axes[pos + 1:]
    return tensor_slice(x, ss, axes=axes)


class Flatten(ReshapeOp):

    def __init__(self, x, axes, **kwargs):
        x = ContiguousOp(axes_with_order(x, x.axes))
        super(Flatten, self).__init__(x, axes=axes, **kwargs)

    @tdcache()
    def tensor_description(self):
        x, = tensor_descriptions(self.args)
        return x.flatten(self.axes).named(self.name)

    def generate_adjoints(self, adjoints, delta, x):
        x.generate_add_delta(adjoints, unflatten(
            delta,
            axes=x.axes
        ))


def flatten(x, axes=None, **kwargs):
    if axes is None:
        if len(x.axes) == 1:
            return x
        else:
            axes = make_axes((FlattenedAxis(x.axes),))

    if x.is_scalar:
        return x

    if isinstance(x, Flatten) and x.axes == axes:
        return x
    return Flatten(x, axes=axes, **kwargs)


def flatten_at(x, idx):
    if idx == 0 or idx == len(x.axes):
        return flatten(x)
    else:
        return flatten(x, make_axes((
            make_axes(x.axes[:idx]).flatten(),
            make_axes(x.axes[idx:]).flatten()
        )))


class Unflatten(ReshapeOp):

    def __init__(self, x, axes=None, **kwargs):
        if axes is None:
            axes = []
            for axis in x.axes:
                axes.extend(axis.axes)
        axes = make_axes(axes)
        Axes.assert_valid_unflatten(x.axes, axes)
        super(Unflatten, self).__init__(x, axes=axes, **kwargs)

    @tdcache()
    def tensor_description(self):
        x, = tensor_descriptions(self.args)
        return x.unflatten(self.axes).named(self.name)

    def generate_adjoints(self, adjoints, delta, x):
        x.generate_add_delta(adjoints, flatten(
            delta,
            axes=x.axes
        ))


def unflatten(x, axes=None, **kwargs):
    if axes is None:
        axes = []
        for axis in x.axes:
            axes.extend(axis.axes)
    axes = Axes(axes)
    if axes == x.axes:
        return x
    return Unflatten(x, axes=axes, **kwargs)


class AssignableTensorOp(TensorOp):
    """
    Value comes directly from storage.

    Arguments:
        input: The storage is used as an input from the CPU. Implies persistent.
        initial_value: If callable, a function that generates an Op whose tensor should be
            used as the initial value.  Otherwise an Op that should be used as the initial
            value.

    Attributes:
        input (bool): The storage is used as an input.
    """

    def __init__(
            self,
            initial_value=None,
            input=False,
            persistent=False,
            **kwargs):
        if input:
            persistent = True
        super(AssignableTensorOp, self).__init__(persistent=persistent, **kwargs)
        self.input = input

        if callable(initial_value):
            self.add_initializer(assign(self, initial_value(self.axes)))
        elif initial_value is not None:
            self.add_initializer(assign(self, initial_value))

    @property
    def defs(self):
        """

        Returns:
            AssignableTensorOp is not executed, so its appearance in the instruction stream does
            not affect liveness of its value.

        """
        return []

    @property
    def is_device_op(self):
        """

        Returns:
            False, because this is handled by the transformer.

        """
        return False

    def add_control_dep(self, op):
        """
        Allocations happen before executed ops, so control_deps are ignored.

        Args:
            op:

        Returns:

        """
        pass


def value_of(tensor):
    """
    Capture the value of a tensor.

    Args:
        tensor: The value to be captured.

    Returns:
        A copy of the value.

    """
    if tensor.is_constant:
        return tensor
    temp = temporary(axes=tensor.axes, dtype=tensor.dtype, constant=True)
    return sequential([
        AssignOp(temp, tensor, force=True),
        temp
    ])


def constant(const, axes=None, dtype=None):
    """
    Makes a constant scalar/tensor.  For a tensor, constant provides the opportunity
        to supply axes.  Scalar/NumPytensor arguments are usually automatically converted to
        tensors, but constant may be used to supply axes or in the rare cases where constant
        is not automatically provided.

    Args:
        const: The constant, a scalar or a NumPy array.
        axes: The axes for the constant.
        dtype (optional): The dtype to use.
    Returns:
        An AssignableTensorOp for the constant.
    """
    graph_label_type = "<Const({})>".format(const)
    val = AssignableTensorOp(axes=axes, constant=True, persistent=True,
                             trainable=False, graph_label_type=graph_label_type,
                             dtype=dtype)

    nptensor = np.asarray(const, dtype=val.dtype)

    if not val.has_axes:
        val.axes = make_axes([make_axis(x, match_on_length=True) for x in nptensor.shape])

    if np.isscalar(const):
        nptensor = np.zeros(val.axes.lengths, dtype=val.dtype) + const

    if nptensor.shape != val.axes.lengths:
        raise ValueError((
            "Tried to initialize constant with numpy array of "
            "shape {np_shape} though gave axes with a different "
            "shape {axes_shape}."
        ).format(
            np_shape=nptensor.shape,
            axes_shape=val.axes.lengths,
        ))

    val_tensor = nptensor
    if len(val.axes) == 0:
        val_tensor = nptensor[()]
    val.const = val_tensor

    def value_fun(tensor):
        return val_tensor

    val.add_initializer(init_tensor(val, value_fun))

    return val


def is_constant(value):
    """
    Test an Op to see if it is a constant.

    Args:
        value: An Op

    Returns: True if value is a constant.

    """
    return isinstance(value, AssignableTensorOp) and value.is_constant


def is_constant_scalar(value):
    """
    Tests an Op to see if it is a constant scalar.

    Args:
        value: An Op.

    Returns: True if value is a constant scalar.

    """
    return value.is_constant and value.is_scalar


def constant_value(value):
    """
    Returns the constant value of an Op.

    Args:
        value (TensorOp): A constant op.

    Returns: The constant value.

    """
    if not is_constant(value):
        raise ValueError()
    return value.const


def constant_storage(axes, dtype=None, initial_value=None):
    """
    A tensor that is supposed to remain constant.

    Args:
        axes (Axes): The axes of the constant storage.
        dtype (optional): The dtype of the storage.
        name (String, optional): A name for the storage.
        initial_value: A host constant or callable. If a callable, will be called
            to produce the value.


    Returns:
        AssignableTensorOp: The constant storage.
    """

    return AssignableTensorOp(graph_label_type="constant",
                              constant=True, persistent=True, trainable=False,
                              axes=axes, dtype=dtype,
                              initial_value=initial_value)


def placeholder(axes, dtype=None, initial_value=None):
    """
    A persistent tensor to be initialized from the CPU.

    Args:
        axes (Axes): The axes of the placeholder.
        dtype (optional): The dtype of the placeholder.
        initial_value (optional): A host constant or callable. If callable, will
            be called to generate an initial value.

    Returns:
        AssignableTensorOp: The placeholder.

    """
    return AssignableTensorOp(graph_label_type="placeholder",
                              constant=False, persistent=True, trainable=False,
                              input=True,
                              axes=axes, dtype=dtype,
                              initial_value=initial_value)


def temporary(axes, dtype=None, initial_value=None, constant=False):
    """
    Temporary storage.

    Statically allocates storage that may be reused outside of the scope of the values.

    Args:
        axes (Axes): The axes of the storage.
        dtype (optional): The dtype of the storage.
        initial_value (optional): A host constant or callable. If callable, will
            be called to generate an initial value.
        constant (optional): Once initialization is complete, this tensor should not change.

    Returns:
        AssignableTensorOp: The placeholder.

    """
    return AssignableTensorOp(graph_label_type="Temp",
                              constant=constant, persistent=True, trainable=False,
                              axes=axes, dtype=dtype,
                              initial_value=initial_value)


def persistent_tensor(axes, dtype=None, initial_value=None):
    """
    Persistent storage.

    Storage that will retain its value from computation to computation.

    Args:
        axes (Axes): The axes of the persistent storage.
        dtype (optional): The dtype of the persistent storage.
        initial_value (optional): A host constant or callable. If callable, will
            be called to generate an initial value.

    Returns:
        AssignableTensorOp: The persistent storage.

    """
    return AssignableTensorOp(graph_label_type="Persistent",
                              constant=False, persistent=True, trainable=False,
                              axes=axes, dtype=dtype,
                              initial_value=initial_value)


def variable(axes, dtype=None, initial_value=None):
    """
    A trainable tensor.

    Args:
        axes (Axes): Axes for the variable.
        dtype (optional): The dtype for the tensor.
        initial_value: A constant or callable. If a callable, the callable
            will be called to provide an initial value.

    Returns:
        AssignableTensorOp: The variable.

    """
    return AssignableTensorOp(graph_label_type="Variable",
                              constant=False, persistent=True, trainable=True,
                              axes=axes, dtype=dtype,
                              initial_value=initial_value)


class StackOp(SequentialOp):
    """
    Joins a list of identically-axed tensors along a new axis.

    Assign each argument into the appropriate slice of the storage associated
    with this op.

    Arguments:
        x_list: A list of identically-axed tensors to join.
        axis: The axis to select joined tensors.
        pos: The position within the axes of the x_list tensors to insert axis in the result.
        **kwargs: Other args for TensorOp.

    Parameters:
        pos: The position of the join axis.
    """

    def __init__(self, x_list, axis, pos=0, **kwargs):
        super(StackOp, self).__init__(**kwargs)
        self.pos = pos
        self.x_list = tuple(as_op(arg) for arg in x_list)
        if axis.length != len(x_list):
            raise ValueError("Axis must have the same length as x_list")
        arg_axes = self.x_list[0].axes
        axes_0 = arg_axes[:pos]
        axes_1 = arg_axes[pos:]
        # Axis layout for the result
        result_axes = make_axes(tuple(axes_0) + (axis,) + tuple(axes_1))

        # With axes, we should be able to just setitem into a tensor shaped like the
        # result, but things don't quite work that way so we use a temp that would have
        # each arg in its own contiguous section, setitem into that, and reshape the result.
        storage_axes = make_axes((axis,) + tuple(arg_axes))
        self.storage = temporary(axes=storage_axes, dtype=self.x_list[0].dtype, constant=True)
        slices = [slice(None)] * len(arg_axes)

        self.ops = [
            doall([SetItemOp(self.storage, [i] + slices, arg)
                   for i, arg in enumerate(self.x_list)
                   ]),
            axes_with_order(self.storage, result_axes)
        ]

    def generate_adjoints(self, adjoints, delta):
        s = [slice(None)] * len(self.storage.axes)
        for i, x in enumerate(self.x_list):
            s[self.pos] = i
            x.generate_add_delta(
                adjoints,
                axes_with_order(tensor_slice(delta, tuple(s)), x.axes)
            )


def stack(x_list, axis, pos=0):
    """

    Args:
        x_list: A list of identically-axed tensors to join.
        axis: The axis to select joined tensors.
        pos: The position within the axes of the x_list tensors to insert axis in the result.

    Returns:
        TensorOp: The joined tensors.

    """
    return StackOp(x_list, axis, pos)


class ConcatOp(SequentialOp):
    """
    Concatenates a list of tensors along specific axis. The axis can be different among each
    tensor, but must have a common role. All other axes should be identical.

    Args:
        x_list (list of TensorOps): A list of nearly identically-axed tensors to concatenate.
                                    They can have at most one axis that is different, and it must
                                    have a common role.
        axis_list (list of Axis): A list of Axis objects that will be concatenated along, one for
                                  each tensor in x_list.
    """

    def __init__(self, x_list, axis_list, **kwargs):
        super(ConcatOp, self).__init__(**kwargs)
        self.x_list = tuple(as_op(arg) for arg in x_list)
        # Get common axes from first tensor in list
        x_axes = self.x_list[0].axes
        ax = axis_list[0]
        common_axes = x_axes - ax

        # Create long axis for concatenated tens1or
        concat_axis = make_axis(batch=ax.is_batch,
                                recurrent=ax.is_recurrent,
                                roles=ax.roles).named("Concat")

        # Store the axes order equivalent to the first tensor
        ind = x_axes.index(ax)
        axes_0 = x_axes[:ind]
        axes_1 = x_axes[ind + 1:]
        self._axes_order = axes_0 + concat_axis + axes_1

        # To do the assignments we must first make sure that every slice of the larger tensor is
        # contiguous in memory. This is most easily achieved by flattening, with concat_axis as
        # either the first or last axis.
        axes = make_axes((concat_axis,) + tuple(common_axes))
        self.storage = temporary(axes=axes, dtype=self.x_list[0].dtype, constant=True)
        self._axis_list = axis_list

        # Since the concatenation axis is first, we'll flatten the rest.
        flat = flatten_at(self.storage, 1)
        slices = [slice(None)] * len(flat.axes)
        assign_op = AssignTwoDOp if len(flat.axes) == 2 else AssignOneDOp

        start = 0
        deps = []
        for ii, (x, ax) in enumerate(zip(self.x_list, axis_list)):
            if len(x.axes - common_axes) > 1:
                raise RuntimeError("Tensor {} has more than 1 axis not in common with"
                                   " other tensors".format(ii))
            if ax.length is None:
                raise RuntimeError("Tensor {} axis must have a specified length".format(ii))

            slices[0] = slice(start, start + ax.length)
            slice_op = tensor_slice(flat, slices)

            # Make sure the args are in the same order as our axes
            ordered_axes = make_axes((ax,) + tuple(common_axes))
            ordered_arg = axes_with_order(x, ordered_axes)

            # Flatten the arg so that it is the same shape as the slice
            flat_arg = flatten_at(ordered_arg, 1)

            # Assign into the slice
            deps.append(assign_op(slice_op, flat_arg, force=True))
            start += ax.length

        concat_axis.length = start
        self.ops = [doall(deps), axes_with_order(self.storage, self._axes_order)]

    def generate_adjoints(self, adjoints, delta):
        delta = axes_with_order(delta, self.storage.axes)
        s = [slice(None)] * len(self.storage.axes)
        start = 0
        for i, (x, ax) in enumerate(zip(self.x_list, self._axis_list)):
            arg_axes = make_axes((ax,) + tuple(self.storage.axes[1:]))
            s[0] = slice(start, start + ax.length)
            x.generate_add_delta(adjoints,
                                 axes_with_order(tensor_slice(delta,
                                                              tuple(s),
                                                              axes=arg_axes),
                                                 x.axes))
            start += ax.length


def concat_along_axis(x_list, axis):
    """
    Concatenates a list of tensors along specific axis. The axis must appear in every tensor in the
    list.

    Args:
        x_list (list of TensorOps): A list of identically-axed tensors to concatenate
        axis (Axis): Axis to concatenate along

    Returns:
        The concatenated tensor op. Axes are ordered the same as in the first tensor in x_list.

    Examples:
        ax = ng.make_name_scope("ax")
        ax.H = ng.make_axis(length=5)
        ax.W = ng.make_axis(length=4)
        axes = ng.make_axes([ax.H, ax.W])
        x = ng.constant(np.ones(axes.full_lengths), axes=axes)
        y = ng.constant(np.ones(axes.full_lengths), axes=axes)
        c = ng.concat_along_axis([x, y], ax.H)
    """

    if len(x_list) < 1:
        return x_list

    return ConcatOp(x_list, [axis for _ in range(len(x_list))])


def concat_role_axis(x_list, role):
    """
    Concatenates a list of tensors along an axis with the specified role. All other axes in each
    tensor should be identical.

    Args:
        x_list (list of TensorOps): A list of identically-axed tensors to concatenate
        role (AxisRole): Axis role common to every tensor in x_list

    Returns:
        The concatenated tensor op. Axes are ordered the same as in the first tensor in x_list.

    Examples:
        role = ng.make_axis_role("Concat")
        ax = ng.make_name_scope("ax")
        ax.H1 = ng.make_axis(length=5, roles=[role])
        ax.H2 = ng.make_axis(length=8, roles=[role])
        ax.W = ng.make_axis(length=4)
        x = ng.constant(np.ones((ax.H1.length, ax.W.length)), axes=[ax.H1, ax.W])
        y = ng.constant(np.ones((ax.H2.length, ax.W.length)), axes=[ax.H2, ax.W])
        c = ng.concat_role_axis([x, y], role)
    """
    if len(x_list) < 1:
        return x_list

    def get_role_axis(axes, role):
        ax = axes.role_axes(role)
        if len(ax) > 1:
            raise RuntimeError("Multiple axes have role {}".format(role.name))
        elif len(ax) == 0:
            raise RuntimeError("No axis with role {}".format(role.name))
        else:
            return ax[0]

    return ConcatOp(x_list, [get_role_axis(x.axes, role) for x in x_list])


class UnsliceOp(SequentialOp):
    def __init__(self, x, slices, axes, **kwargs):
        super(UnsliceOp, self).__init__(**kwargs)
        self.x = x
        self.slices = slices
        temp = temporary(axes=axes, dtype=x.dtype).named('unslice')
        self.ops = [
            Fill(temp, 0),
            SetItemOp(temp, slices, x),
            temp
        ]

    def generate_adjoints(self, adjoints, delta):
        self.x.generate_add_delta(adjoints, tensor_slice(delta, self.slices, axes=self.x.axes))


def _unslice(x, slices, axes):
    """
    A computation to reverse a slicing operation.
    Used internally to implement expansions of tensors
    such as the derivative of a slice and a padding function.

    Arguments:
        x: The tensor.
        slices: slices to be unsliced.
        axes: axes of result.

    Attributes:
        slices: The slices.
        input_axes: The axes of the input x.
    """
    return UnsliceOp(x, slices, axes)


class RngOp(TensorOp):

    def __init__(self, distribution, params, x, *args, **kwargs):
        """
        Arguments:
            x  : input tensor.
            distribution : either 'uniform' or 'normal'
            params: dict for specifying parameters of distribution
        Return:
        """
        if distribution not in ('uniform', 'normal'):
            raise ValueError((
                'unsupported distribution: {}'
            ).format(distribution))

        self.distribution = distribution
        self.params = params

        super(RngOp, self).__init__(
            args=(x,), axes=x.axes, *args, **kwargs
        )

    def generate_adjoints(self, adjoints, delta, x):
        x.generate_add_delta(adjoints, 0)


def uniform(x, low=0.0, high=1.0):
    """
    Fills x with uniform distribution between low and high.

    Args:
        x (TensorOp): A tensor.
        low (float): lower limit of distribution range
        high (float): upper limit of distribution range

    Returns:
        TensorOp: The  value of x.

    """
    return RngOp(distribution='uniform', params=dict(low=low, high=high), x=x)


def normal(x, loc=0.0, scale=1.0):
    """
    Fills x with normal distribution centered around loc and scaled by scale

    Args:
        x (TensorOp): A tensor.
        loc (float): mean of distribution
        scale (float): standard deviation of distribution

    Returns:
        TensorOp: The  value of x.

    """
    return RngOp(distribution='normal', params=dict(loc=loc, scale=scale), x=x)


class AllReduce(Op):
    """TODO."""

    def __init__(self, x, **kwargs):
        super(AllReduce, self).__init__(args=(x,), **kwargs)


class ElementWise(TensorOp):
    pass


class UnaryElementwiseAxesOp(ElementWise):
    """
    Handles initialization and 1d shaping for unary elementwise operations.
    """
    one_d_class = None

    def __init__(self, x):
        super(UnaryElementwiseAxesOp, self).__init__(args=(x,), axes=x.axes)

    def reduce_to_one_d(self):
        """
        Flatten the argument, do the op, and then unflatten the result.

        The class attribure one_d_class should hold the Op class for the flattened operation.

        Returns:
            Flattened computation.

        """
        return unflatten(self.__class__.one_d_class(flatten(self.args[0])).named(self.name))


class UnaryElementwiseOneDOp(ElementWise):
    """
    Handles initialization for unary operations.
    """

    def __init__(self, x):
        super(UnaryElementwiseOneDOp, self).__init__(args=(x,), axes=x.axes)


class StopGradientOneDOp(UnaryElementwiseOneDOp):
    """
    1d stop gradient.
    """
    pass


class StopGradient(UnaryElementwiseAxesOp):
    """ TODO """
    one_d_class = StopGradientOneDOp

    @tdcache()
    def tensor_description(self):
        return self.tensor.tensor_description()

    @property
    def is_tensor_op(self):
        return False

    @property
    def value(self):
        return self.tensor.value

    @property
    def axes(self):
        return self.tensor.axes

    def generate_adjoints(self, adjoints, delta, x):
        x.generate_add_delta(adjoints, 0.)


def stop_gradient(x):
    """ TODO """
    return StopGradient(x)


class NegativeOneDOp(UnaryElementwiseOneDOp):
    """
    1d negative.
    """
    pass


class NegativeOp(UnaryElementwiseAxesOp):
    """
    Negative of a tensor.
    """
    one_d_class = NegativeOneDOp

    def generate_adjoints(self, adjoints, delta, x):
        x.generate_add_delta(adjoints, -delta)


def negative(x):
    """
    Returns the negative of x.

    Args:
        x (TensorOp): tensor.

    Returns:
        (TensorOp): The negative of x.

    """
    return NegativeOp(x)


class AbsoluteOneDOp(UnaryElementwiseOneDOp):
    """
    1d absolute value.
    """
    pass


class AbsoluteOp(UnaryElementwiseAxesOp):
    """
    Absolute value of a tensor.
    """
    one_d_class = AbsoluteOneDOp

    def generate_adjoints(self, adjoints, delta, x):
        x.generate_add_delta(adjoints, sign(x) * delta)


def absolute(x):
    """
    Returns the absolute value of x.

    Args:
        x (TensorOp): A tensor.

    Returns:
        TensorOp: The absolute value of x.

    """
    return AbsoluteOp(x)


class SinOneDOp(UnaryElementwiseOneDOp):
    """
    1d sin.
    """
    pass


class SinOp(UnaryElementwiseAxesOp):
    """
    Sin of a tensor.
    """
    one_d_class = SinOneDOp

    def generate_adjoints(self, adjoints, delta, x):
        x.generate_add_delta(adjoints, delta * cos(x))


def sin(x):
    """
    Returns the sin of x.

    Args:
        x (TensorOp): A tensor.

    Returns:
        TensorOp: sin of x.

    """
    return SinOp(x)


class CosOneDOp(UnaryElementwiseOneDOp):
    """
    1d cos.
    """
    pass


class CosOp(UnaryElementwiseAxesOp):
    """
    Cos of a tensor.
    """
    one_d_class = CosOneDOp

    def generate_adjoints(self, adjoints, delta, x):
        x.generate_add_delta(adjoints, -delta * sin(x))


def cos(x):
    """
    Returns the cos of x.

    Args:
        x (TensorOp): A tensor.

    Returns:
        TensorOp: The cos of x.

    """
    return CosOp(x)


class TanhOneDOp(UnaryElementwiseOneDOp):
    """
    1d tanh.
    """
    pass


class TanhOp(UnaryElementwiseAxesOp):
    """
    Tanh of a tensor.
    """
    one_d_class = TanhOneDOp

    def generate_adjoints(self, adjoints, delta, x):
        x.generate_add_delta(adjoints, delta * (1.0 - self * self))


def tanh(x):
    """
    Returns the cos of x.

    Args:
        x (TensorOp): A tensor.

    Returns:
        TensorOp: The tanh of x.

    """
    return TanhOp(x)


class ExpOneDOp(UnaryElementwiseOneDOp):
    """
    1d exp.
    """
    pass


class ExpOp(UnaryElementwiseAxesOp):
    """
    Exp of a tensor.
    """
    one_d_class = ExpOneDOp

    def generate_adjoints(self, adjoints, delta, x):
        x.generate_add_delta(adjoints, delta * self)


def exp(x):
    """
    Returns the exp of x.

    Args:
        x (TensorOp): A tensor.

    Returns:
        TensorOp: The exp of x.

    """
    return ExpOp(x)


class LogOneDOp(UnaryElementwiseOneDOp):
    """
    1d log.
    """
    pass


class LogOp(UnaryElementwiseAxesOp):
    """
    Log of a tensor.
    """
    one_d_class = LogOneDOp

    def generate_adjoints(self, adjoints, delta, x):
        def do_adjoints(delta, x):
            if isinstance(x, Divide):
                a, b = x.args
                do_adjoints(delta, a)
                do_adjoints(-delta, b)
            elif isinstance(x, ExpOp):
                x.args[0].generate_add_delta(adjoints, delta)
            else:
                x.generate_add_delta(adjoints, delta / x)

        do_adjoints(delta, x)


def log(x):
    """
    Returns the log of x.

    Args:
        x (TensorOp): A tensor.

    Returns:
        TensorOp: The log of x.

    """
    return LogOp(x)


safelog_cutoff = 50.0


def safelog(x, limit=np.exp(-safelog_cutoff)):
    return log(maximum(x, limit))


class ReciprocalOneDOp(UnaryElementwiseOneDOp):
    """
    1d reciprocal.
    """
    pass


class ReciprocalOp(UnaryElementwiseAxesOp):
    """
    Reciprocal of a tensor.
    """
    one_d_class = ReciprocalOneDOp

    def generate_adjoints(self, adjoints, delta, x):
        x.generate_add_delta(adjoints, -self * self * delta)


def reciprocal(x):
    """
    Returns the reciprocal of x.

    Args:
        x (TensorOp): A tensor.

    Returns:
        TensorOp: The reciprocal of x.

    """
    return ReciprocalOp(x)


class SignOneDOp(UnaryElementwiseOneDOp):
    "1d Sign."
    pass


class SignOp(UnaryElementwiseAxesOp):
    "Sign of a tensor."
    one_d_class = SignOneDOp


def sign(x):
    """
    Returns the sign of x.

    Args:
        x (TensorOp): A tensor.

    Returns:
        TensorOp: The sign of x.

    """
    return SignOp(x)


class SquareOneDOp(UnaryElementwiseOneDOp):
    """
    1d square.
    """
    pass


class SquareOp(UnaryElementwiseAxesOp):
    """
    Square of a tensor.
    """
    one_d_class = SquareOneDOp

    def generate_adjoints(self, adjoints, delta, x):
        x.generate_add_delta(adjoints, 2.0 * delta * x)


def square(x):
    """
    Returns the square of x.

    Args:
        x (TensorOp): A tensor.

    Returns:
        TensorOp: The square of x.

    """
    return SquareOp(x)


class SqrtOneDOp(UnaryElementwiseOneDOp):
    "1d square root."
    pass


class SqrtOp(UnaryElementwiseAxesOp):
    """
    Square root of a tensor.
    """
    one_d_class = SqrtOneDOp

    def generate_adjoints(self, adjoints, delta, x):
        x.generate_add_delta(adjoints, 0.5 * delta / self)


def sqrt(x):
    """
    Returns the square root of x.

    Args:
        x (TensorOp): A tensor.

    Returns:
        TensorOp: The square root of x.

    """
    return SqrtOp(x)


class BinaryElementWiseAxesOp(ElementWise):

    def __init__(self, x, y, **kwargs):
        self.kwargs = kwargs
        x, y = as_ops((x, y))
        axes = x.axes + y.axes
        x = broadcast(x, axes)
        y = broadcast(y, axes)

        super(BinaryElementWiseAxesOp, self).__init__(
            args=(x, y),
            axes=axes,
            **kwargs
        )

    @property
    def one_dimensional(self):
        x, y = self.args
        return len(x.axes) <= 1 and len(y.axes) <= 1

    @property
    def zero_dimensional(self):
        x, y = self.args
        return len(x.axes) == 0 and len(y.axes) == 0


class BinaryElementWiseLowDOp(ElementWise):

    def __init__(self, x, y, **kwargs):
        self.kwargs = kwargs

        if x.is_scalar:
            x = x.scalar_op
        if y.is_scalar:
            y = y.scalar_op

        super(BinaryElementWiseLowDOp, self).__init__(
            args=(x, y),
            **kwargs
        )

    @property
    def one_dimensional(self):
        x, y = self.args
        return len(x.axes) <= 1 and len(y.axes) <= 1

    @property
    def zero_dimensional(self):
        x, y = self.args
        return len(x.axes) == 0 and len(y.axes) == 0


def create_binary_elementwise(name,
                              one_dim_name,
                              zero_dim_name,
                              func_name=None,
                              generate_adjoints=None,
                              one_dim_generate_adjoints=None,
                              zero_dim_generate_adjoints=None):
    d = {}
    if generate_adjoints is not None:
        d['generate_adjoints'] = generate_adjoints
    BinClass = type(name, (BinaryElementWiseAxesOp,), d)

    d = {}
    if one_dim_generate_adjoints is not None:
        d['generate_adjoints'] = one_dim_generate_adjoints
    OneDimBinClass = type(one_dim_name, (BinaryElementWiseLowDOp,), d)

    d = {}
    if zero_dim_generate_adjoints is not None:
        d['generate_adjoints'] = zero_dim_generate_adjoints
    ZeroDimBinClass = type(zero_dim_name, (BinaryElementWiseLowDOp,), d)

    def reduce_to_oned(self):
        x, y = self.args
        if x.is_scalar and y.is_scalar:
            return ZeroDimBinClass(x.scalar_op, y.scalar_op, axes=self.axes, **self.kwargs)
        else:
            x, y = flatten(x), flatten(y)
            return unflatten(OneDimBinClass(x, y, axes=FlattenedAxis(self.axes), **self.kwargs))
    BinClass.reduce_to_oned = reduce_to_oned

    if func_name is None:
        return BinClass, OneDimBinClass, ZeroDimBinClass
    else:
        def func(*args, **kwargs):
            return BinClass(*args, **kwargs)
        func.__name__ = func_name
        return BinClass, OneDimBinClass, ZeroDimBinClass, func


def add_adjoints(self, adjoints, delta, x, y):
    x.generate_add_delta(adjoints, delta)
    y.generate_add_delta(adjoints, delta)


Add, AddOneDim, AddZeroDim, add = create_binary_elementwise(
    'AddOp', 'AddOneDim', 'AddZeroDim', 'addX', add_adjoints
)


def add(x, y):
    """
    Returns a TensorOp for the sum of x and y.

    Args:
        x (TensorOp): The first input.
        y (TensorOp):  The second input.
        name (String, optional): A name for the sum.

    Returns:
        TensorOp: x + y

    """
    return Add(x, y)


def subtract_adjoints(self, adjoints, delta, x, y):
    x.generate_add_delta(adjoints, delta)
    y.generate_add_delta(adjoints, -delta)


Subtract, SubtractOneDim, SubtractZeroDim, subtract = create_binary_elementwise(
    'Subtract', 'SubtractOneDim', 'SubtractZeroDim',
    'subtract', subtract_adjoints
)


def multiply_adjoints(self, adjoints, delta, x, y):
    x.generate_add_delta(adjoints, delta * y)
    y.generate_add_delta(adjoints, x * delta)


Multiply, MultiplyOneDim, MultiplyZeroDim, multiply = create_binary_elementwise(
    'Multiply', 'MultiplyOneDim', 'MultiplyZeroDim',
    'multiply', multiply_adjoints
)


def divide_adjoints(self, adjoints, delta, x, y):
    x.generate_add_delta(adjoints, delta * self / x)
    y.generate_add_delta(adjoints, -delta * self / y)


Divide, DivideOneDim, DivideZeroDim, divide = create_binary_elementwise(
    'Divide', 'DivideOneDim', 'DivideZeroDim',
    'divide', divide_adjoints
)

Mod, ModOneDim, ModZeroDim, mod = create_binary_elementwise(
    'Mod', 'ModOneDim', 'ModZeroDim',
    'mod', None
)


def maximum_adjoints(self, adjoints, delta, x, y):
    x.generate_add_delta(adjoints, greater(x, y) * delta)
    y.generate_add_delta(adjoints, greater(y, x) * delta)


Maximum, MaximumOneDim, MaximumZeroDim, maximum = create_binary_elementwise(
    'Maximum', 'MaximumOneDim', 'MaximumZeroDim', 'maximum', maximum_adjoints
)


def minimum_adjoints(self, adjoints, delta, x, y):
    x.generate_add_delta(adjoints, less(x, y) * delta)
    y.generate_add_delta(adjoints, less(y, x) * delta)


Minimum, MinimumOneDim, MinimumZeroDim, minimum = create_binary_elementwise(
    'Minimum', 'MinimumOneDim', 'MinimumZeroDim', 'minimum', minimum_adjoints
)


def power_adjoints(self, adjoints, delta, x, y):
    x.generate_add_delta(adjoints, delta * y * self / x)
    y.generate_add_delta(adjoints, delta * self * log(x))


Power, PowerOneDim, PowerZeroDim, power = create_binary_elementwise(
    'Power', 'PowerOneDim', 'PowerZeroDim', 'power', power_adjoints
)


Equal, EqualOneDim, EqualZeroDim, equal\
    = create_binary_elementwise('Equal', 'EqualOneDim', 'EqualZeroDim', 'equal')


NotEqual, NotEqualOneDim, NotEqualZeroDim, not_equal\
    = create_binary_elementwise('NotEqual', 'NotEqualOneDim', 'NotEqualZeroDim', 'not_equal')


Greater, GreaterOneDim, GreaterZeroDim, greater\
    = create_binary_elementwise('Greater', 'GreaterOneDim', 'GreaterZeroDim', 'greater')


Less, LessOneDim, LessZeroDim, less\
    = create_binary_elementwise('Less', 'LessOneDim', 'LessZeroDim', 'less')


GreaterEqual, GreaterEqualOneDim, GreaterEqualZeroDim, greater_equal\
    = create_binary_elementwise(
        'GreaterEqual', 'GreaterEqualOneDim',
        'GreaterEqualZeroDim', 'greater_equal'
    )


LessEqual, LessEqualOneDim, LessEqualZeroDim, less_equal\
    = create_binary_elementwise('LessEqual', 'LessEqualOneDim', 'LessEqualZeroDim', 'less_equal')


class ContiguousOp(TensorOp):
    """
    Ensure that element layout is contiguous.

    Parameters:
        x (TensorOp): A possibly non-contiguous tensor.
    """

    def __init__(self, x, **kwargs):
        super(ContiguousOp, self).__init__(args=(x,), axes=x.axes, **kwargs)

    @property
    def old_axis_positions(self):
        return tuple(range(len(self.axes)))

    def generate_adjoints(self, adjoints, delta, x):
        x.generate_add_delta(adjoints, delta)


class DotOp(TensorOp):

    def __init__(self, x, y, **kwargs):
        self.x_reduction_axes = x.axes.intersect(y.axes.get_dual())
        self.y_reduction_axes = self.x_reduction_axes.get_dual(1)
        self.x_out_axes = x.axes - self.x_reduction_axes
        self.y_out_axes = y.axes - self.y_reduction_axes

        intersection_axes = self.x_out_axes.intersect(self.y_out_axes)
        if len(intersection_axes):
            raise ValueError(("Both arguments to a DotOp contained {axes}. "
                              "In order to dot two tensors with the same Axis together, one "
                              "of the Axes must be a dual. See: "
                              "https://ngraph.nervanasys.com/docs/latest/axes.html#dualaxis"
                              ).format(axes=', '.join(str(axis) for axis in intersection_axes)))

        axes = self.x_out_axes + self.y_out_axes

        super(DotOp, self).__init__(
            args=(x, y), axes=axes, **kwargs
        )

    def generate_adjoints(self, adjoints, delta, x, y):
        """
        Generates the adjoint contributions for x and y.

        On input, x axes can be grouped as IJ* and y axes as JK where
        J* is predecessor of J.

        Axes will be:
            Delta: IK.
            x adj: IJ*
            y adj: JK

        For x adj, we have IK and JK, so we dual K for delta and J for y
        to get IK* and J*K for a product of IJ*.

        For y adj, we have IJ* and IK, to get JK, so we dual I and undual
        J* in x, to get I*J and IK for a product of JK.

        Args:
            adjoints: The adjoints for the deriv being computed.
            delta (TensorOp): The backprop op.
            x (TensorOp): The x argument.
            y (TensorOp): The y argument.

        """
        x.generate_add_delta(
            adjoints,
            axes_with_order(
                dot(dualed_axes(delta, self.y_out_axes, -1, 0),
                    dualed_axes(y, self.y_reduction_axes, -1, 0)),
                x.axes)
        )
        y.generate_add_delta(
            adjoints,
            axes_with_order(
                dot(dualed_axes(x, self.x_out_axes, -1, +1), delta),
                y.axes)
        )


def dualed_axes(x, filter, in_dual_offset, out_dual_offset):
    """
    Cast axes to a dual offset of axes depending on membership in dual_axes.

    In a dot(a, b), each pair of axes (a_i, b_j) between a and b where
    a_i = b_j - 1
    will be paired for multiplication and then summing.

    Args:
        x (TensorOp): A tensor.
        filter: A collection of axes.
        in_dual_offset: Dual shift amount for axes in filter.
        out_dual_offset: Dual shift amount for axes not in filter.

    Returns:
        TesnsorOp: x with axes cast.

    """
    def dualed(axis):
        if axis in filter:
            return axis + in_dual_offset
        else:
            return axis + out_dual_offset
    return cast_axes(x, (dualed(axis) for axis in x.axes))


def dot(x, y):
    """
    The dot product of x and y.

    Reduction axes in x are those whose dual offset is one less than an axis in y.

    Args:
        x (TensorOp): First argument.
        y (TensorOp): Second argumnent.
        name (String, optional): Name for the TensorOp.

    Returns:
        TensorOp: The dot product.

    """
    return DotOp(x, y)


def squared_L2(x, out_axes=None, reduction_axes=None):
    """
    Returns the dot of x and y, with the axes of x set to their dual offset.

    Args:
        x (TensorOp): The first value, axes shifted down by 1.
        y (TensorOp): The second value.

    Returns:
        TensorOp: The result.

    """
    if reduction_axes is None:
        if out_axes is None:
            reduction_axes = x.axes.sample_axes()
        else:
            reduction_axes = x.axes - make_axes(out_axes)
    return sum(x * x, out_axes=out_axes, reduction_axes=reduction_axes)


class LowDimensionalDot(TensorOp):

    def __init__(self, x, y, axes, **kwargs):
        super(LowDimensionalDot, self).__init__(args=(x, y), axes=axes, **kwargs)


class DotOneDimensional(LowDimensionalDot):

    def __init__(self, x, y, axes, **kwargs):
        assert len(x.axes) == 1 and len(y.axes) == 1
        super(DotOneDimensional, self).__init__(
            x, y, axes, **kwargs
        )


class DotTwoDimensional(LowDimensionalDot):

    def __init__(self, x, y, axes, **kwargs):
        assert len(x.axes) == 2 and len(y.axes) == 2
        super(DotTwoDimensional, self).__init__(
            x, y, axes, **kwargs
        )


class DotTwoByOne(LowDimensionalDot):

    def __init__(self, x, y, axes, **kwargs):
        assert len(x.axes) == 2 and len(y.axes) == 1
        super(DotTwoByOne, self).__init__(
            x, y, axes, **kwargs
        )


class Softmax(object):
    """
    A schema to use to shortcut formula for the softmax derivative.
    """

    def __init__(self, x, exps, Z):
        self.x = x
        self.exps = exps
        self.Z = Z

    def generate_adjoints(self, adjoints, delta, op):
        """
        TODO.

        Arguments:
          adjoints: TODO
          delta: TODO
          op: TODO

        Returns:
          TODO
        """
        z = delta * op
        zs = sum(z)
        self.x.generate_add_delta(adjoints, (z - zs * op))


def softmax(x, normalization_axes=None, **kwargs):
    """
    The softmax activation function.

    Arguments:
      x: input
      normalization_axes: dimensions over which we normalize
      **kwargs: options

    Returns:
        y: output of softmax function
    """
    if normalization_axes is None:
        normalization_axes = x.axes.sample_axes()\
            - x.axes.recurrent_axes()
    x = x - max(x, reduction_axes=normalization_axes)
    exps = exp(x)
    Z = sum(exps, reduction_axes=normalization_axes)
    result = exps / Z
    result.add_schema(Softmax(x=x, exps=exps, Z=Z))
    return result


class ReductionOp(TensorOp):
    must_reduce = True

    def __init__(self, x, reduction_axes=None, out_axes=None, dtype=None, **kwargs):
        if reduction_axes is None and out_axes is None:
            reduction_axes = x.axes.sample_axes() - x.axes.recurrent_axes()
            out_axes = x.axes - reduction_axes
        elif reduction_axes is None:
            out_axes = make_axes(out_axes)
            reduction_axes = x.axes - out_axes
        elif out_axes is None:
            reduction_axes = make_axes(reduction_axes)
            out_axes = x.axes - reduction_axes
        else:
            out_axes = make_axes(out_axes)
            reduction_axes = make_axes(reduction_axes)
        assert reduction_axes.intersect(out_axes) == make_axes(())

        self.reduction_axes = reduction_axes
        self.kwargs = kwargs

        super(ReductionOp, self).__init__(
            args=(x,),
            axes=out_axes,
            dtype=dtype
        )
        assert self.valid

    @property
    def valid(self):
        return True


def create_twod_reduction_op(name,
                             red_cls,
                             two_dim_generate_adjoints=None):
    def valid_two(self):
        x, = self.args
        return len(x.axes) == 2\
            and self.reduction_axes == x.axes[:1]\
            and self.out_axes == x.axes[1:]
    d = {'valid': valid_two, 'must_reduce': False}

    if two_dim_generate_adjoints is not None:
        d['generate_adjoints'] = two_dim_generate_adjoints

    RedTwoDimClass = type(name, (red_cls,), d)
    return RedTwoDimClass


def create_oned_reduction_op(name,
                             red_cls,
                             one_dim_generate_adjoints=None):
    def valid_one(self):
        x, = self.args
        return len(x.axes) == 1\
            and self.reduction_axes == x.axes

    d = {'valid': valid_one, 'must_reduce': False}
    if one_dim_generate_adjoints is not None:
        d['generate_adjoints'] = one_dim_generate_adjoints

    RedOneDimClass = type(name, (red_cls,), d)
    return RedOneDimClass


def create_reduction_op(name,
                        two_dim_name,
                        one_dim_name,
                        func_name=None,
                        generate_adjoints=None,
                        two_dim_generate_adjoints=None,
                        one_dim_generate_adjoints=None):
    d = {}
    if generate_adjoints is not None:
        d['generate_adjoints'] = generate_adjoints
    RedClass = type(name, (ReductionOp,), d)

    RedTwoDimClass = create_twod_reduction_op(
        two_dim_name,
        RedClass,
        two_dim_generate_adjoints
    )

    RedOneDimClass = create_oned_reduction_op(
        one_dim_name,
        RedClass,
        one_dim_generate_adjoints
    )

    def reduce_to_twod(self):
        x, = self.args
        reduction_axes = self.reduction_axes
        out_axes = self.axes

        if len(reduction_axes) == 0:
            return broadcast(x, axes=out_axes)
        elif len(x.axes) == 0:
            return broadcast(x, axes=out_axes)

        if len(out_axes) == 0:
            x = flatten(x)
            return RedOneDimClass(
                x,
                reduction_axes=x.axes,
                out_axes=make_axes(()),
                dtype=self.dtype,
                **self.kwargs
            )
        else:
            x = broadcast(x, axes=reduction_axes + out_axes)
            x = flatten_at(x, len(reduction_axes))

            out = RedTwoDimClass(
                x,
                reduction_axes=make_axes((x.axes[0],)),
                out_axes=make_axes((x.axes[1],)),
                dtype=self.dtype,
                **self.kwargs
            )
            out = unflatten(out)
            return broadcast(out, axes=out_axes)
    RedClass.reduce_to_twod = reduce_to_twod

    if func_name is None:
        return RedClass, RedTwoDimClass, RedOneDimClass
    else:
        def func(*args, **kwargs):
            return RedClass(*args, **kwargs)
        func.__name__ = func_name
        return RedClass, RedTwoDimClass, RedOneDimClass, func


def max_adjoints(self, adjoints, delta, x):
    x.generate_add_delta(adjoints, equal(x, self) * delta)


Max, MaxTwoDim, MaxOneDim, max = create_reduction_op(
    'Max', 'MaxTwoDim', 'MaxOneDim', 'max', max_adjoints
)


def min_adjoints(self, adjoints, delta, x):
    x.generate_add_delta(adjoints, equal(x, self) * delta)


Min, MinTwoDim, MinOneDim, min = create_reduction_op(
    'Min', 'MinTwoDim', 'MinOneDim', 'min', min_adjoints
)


def sum_adjoints(self, adjoints, delta, x):
    x.generate_add_delta(
        adjoints,
        broadcast(delta, x.axes)
    )


Sum, SumTwoDim, SumOneDim, sum = create_reduction_op(
    'Sum', 'SumTwoDim', 'SumOneDim', 'sum', sum_adjoints
)


def prod_adjoints(self, adjoints, delta, x):
    # axes
    axes = x.axes
    reduction_axes = self.reduction_axes

    # x_equal_zero
    x_equal_zero = equal(x, 0)

    # count 0's occurrence by reduction axes
    x_zero_count = sum(x_equal_zero, reduction_axes=reduction_axes)

    # create mask for zero count 0 and 1
    mask_zero = broadcast(equal(x_zero_count, 0), axes=axes)
    mask_one = broadcast(equal(x_zero_count, 1), axes=axes)

    # replace all 0 to 1
    x_replaced = equal(x, 0.) * 1. + (1. - equal(x, 0.)) * x

    # do product of x_replace and gradient
    x_replaced_prod = prod(x_replaced, reduction_axes=reduction_axes)
    x_replaced_grad = x_replaced_prod / x_replaced

    # multiply mask with mask for the two cases
    x_grad = mask_zero * x_replaced_grad + mask_one * x_equal_zero * x_replaced_grad

    x.generate_add_delta(
        adjoints,
        broadcast(delta, x.axes) * x_grad
    )


Prod, ProdTwoDim, ProdOneDim, prod = create_reduction_op(
    'Prod', 'ProdTwoDim', 'ProdOneDim', 'prod', prod_adjoints
)


Argmax, ArgmaxTwoDim, ArgmaxOneDim = create_reduction_op(
    'Argmax', 'ArgmaxTwoDim', 'ArgmaxOneDim'
)


def argmax(x, dtype=None, **kwargs):
    return Argmax(x, dtype=default_int_dtype(dtype), **kwargs)


Argmin, ArgminTwoDim, ArgminOneDim = create_reduction_op(
    'Argmin', 'ArgminTwoDim', 'ArgminOneDim'
)


def argmin(x, dtype=None, **kwargs):
    return Argmin(x, dtype=default_int_dtype(dtype), **kwargs)


def variance(x, out_axes=None, reduction_axes=None):
    return mean(square(x - mean(x, out_axes=out_axes, reduction_axes=reduction_axes)),
                out_axes=out_axes, reduction_axes=reduction_axes)


class TensorSizeOp(TensorOp):
    """
    A scalar returning the total size of a tensor.
    Arguments:
        x: The tensor whose axes we are measuring.
        reduction_axes: if supplied, return the size
            of these axes instead.
        kwargs: options, including name
    """

    def __init__(self, x, reduction_axes=None, out_axes=None, **kwargs):
        if reduction_axes is None and out_axes is None:
            reduction_axes = x.axes.sample_axes()
        elif reduction_axes is None:
            reduction_axes = x.axes - out_axes
        self.reduction_axes = reduction_axes
        super(TensorSizeOp, self).__init__(axes=())


def tensor_size(x, reduction_axes=None, out_axes=None):
    """
    A scalar returning the total size of a tensor in elements.

    Arguments:
        x: The tensor whose axes we are measuring.
        reduction_axes: if supplied, return the size
            of these axes instead.
    """
    return TensorSizeOp(x, reduction_axes=reduction_axes, out_axes=out_axes)


def batch_size(x):
    """

    Args:
        x: A Tensor

    Returns:
        The size of the batch axis in x.

    """
    return tensor_size(x, reduction_axes=x.axes.batch_axes())


def pad(x, paddings, axes=None):
    """
    Pads a tensor with zeroes along each of its dimensions.

    Arguments:
      x: the tensor to be padded
      paddings: the length of the padding along each dimension.
        should be an array with the same length as x.axes.
        Each element of the array should be either an integer,
        in which case the padding will be symmetrical, or a tuple
        of the form (before, after)
      axes: the axes to be given to the padded tensor.
        If unsupplied, we create anonymous axes of the correct lengths.

    Returns:
        TensorOp: symbolic expression for the padded tensor
    """
    if len(x.axes) != len(paddings):
        raise ValueError((
            "pad's paddings has length {pad} which needs to be the same "
            "as the number of axes in x ({x})"
        ).format(
            pad=len(paddings),
            x=len(x.axes),
        ))

    def pad_to_tuple(pad):
        if isinstance(pad, int):
            pad = (pad, pad)
        return pad

    paddings = tuple(pad_to_tuple(pad) for pad in paddings)
    if axes is None:
        axes = make_axes(
            PaddedAxis(axis, pad) if pad != (0, 0) else axis
            for axis, pad in zip(x.axes, paddings)
        )

    def to_slice(pad):
        """
        TODO.

        Arguments:
          pad: TODO

        Returns:
          TODO
        """
        s = (pad[0], -pad[1])
        s = tuple(None if p == 0 else p for p in s)
        return slice(s[0], s[1], 1)
    slices = tuple(to_slice(p) for p in paddings)
    return _unslice(x, slices, axes)


class OneHotOp(TensorOp):
    """
    Converts a tensor containing class indices to a onehot representation.
    For example, if x is a one-dimesnional tensor with value [0, 1], and the
    number of classes is 2, we convert x to a onehot representation by replacing
    0 and 1 with vectors: 0 -> [1, 0] and 1 -> [0, 1].

    We add the added dimension in the leftmost place.

    Arguments:
        x: The tensor to convert to a onehot form.
        axis: The axis along which to construct the onehot form. It should not be
        in x and should have length equal to the number of classes.
    """

    def __init__(self, x, axis, **kwargs):
        self.axis = axis
        super(OneHotOp, self).__init__(
            args=(x,),
            axes=make_axes((axis,)) + x.axes,
            **kwargs
        )

    def as_two_dim(self):
        """
        Constructs a subgraph that is equivalent to this op and can be evaluated
        by a transformer that only handles two dimensions.

        Returns:
            A subgraph equivalent to this op.
        """
        x, = self.args
        if len(x.axes) > 1:
            x = flatten(x)
            out = OneHotTwoDimOp(x, self.axis)
            out = unflatten(
                out,
                [out.axes[0]] + list(out.axes[1].axes)
            )
            return out
        else:
            return OneHotTwoDimOp(x, self.axis)


def one_hot(x, axis):
    """

    Args:
        x: The one_hot tensor.
        axis: The hot axis.

    Returns:
        OneHotOp: The op.

    """
    return OneHotOp(x, axis)


class OneHotTwoDimOp(OneHotOp):
    """
    Handles conversion from one-dimensional vector of class labels
    to a two-dimensional onehot representation.

    Arguments:
        x: The tensor to convert to a onehot form.
        axis: The axis along which to construct the onehot form. It should not be
        in x and should have length equal to the number of classes.
    """

    def __init__(self, x, axis, **kwargs):
        assert len(x.axes) == 1
        super(OneHotTwoDimOp, self).__init__(x, axis, **kwargs)


class Sigmoid(object):
    """
    Marks a subgraph as a sigmoid to improve computation and autodiff.
    """

    def __init__(self, x):
        self.x = x

    def generate_adjoints(self, adjoints, delta, op):
        """
        TODO.

        Arguments:
          adjoints: TODO
          delta: TODO
          op: TODO

        Returns:
          TODO
        """
        self.x.generate_add_delta(adjoints, delta * op * (1.0 - op))


def sigmoid(x):
    """
    sigmoid(x)

    .. math::
        \\frac{1}{1+e^{-x}}

    Arguments:
        x: A tensor

    Returns:
        TensorOp: sigmoid(x).

    """
    result = reciprocal(exp(-x) + 1)
    result.add_schema(Sigmoid(x=x))
    return result


class Function(Op):
    """TODO."""

    def __init__(self, ops):
        self.ops = ops
        self.instructions = Op.ordered_ops(self.ops)
        args, defs = set(), set()
        for op in self.instructions:
            # Kernel defines the def of each operation
            defs.add(op)
            # Kernel uses the args of each operation
            # except whatever is being defined
            args |= set(op.args) - defs
        super(Function, self).__init__(args=args)
        self.__defs = defs
        self.initializers = [x for x in op.initializers
                             for op in self.instructions]

    @property
    def defs(self):
        """

        Returns:
            The cumulative invalidated storage for the op sequence.

        """
        return self.__defs

    @property
    def inputs(self):
        """TODO."""
        return self.use


def mean(x, reduction_axes=None, out_axes=None):
    """
    Computes the mean of x.

    Arguments:
        x (TensorOp): A tensor.
        reduction_axes (Axes, optional): If supplied, the mean is computed over these axes.
        out_axes (Axes, optional): If supplied, the result has these axes; the mean is computed
            over the remaining axes.

    Returns:
        TensorOp: The mean.
    """
    return sum(x, reduction_axes=reduction_axes, out_axes=out_axes) / \
        tensor_size(x, reduction_axes=reduction_axes, out_axes=out_axes)


class DerivOp(TensorOp):

    def __init__(self, dependent, independent, error):
        super(DerivOp, self).__init__()

        if not error.axes.has_same_axes(dependent.axes):
            raise ValueError("Dependent and error must have the same set of axes")
        self.dependent = as_op(dependent)
        self.independent = as_op(independent)
        self.error = as_op(error)
        self.axes = make_axes(independent.axes)


def deriv(dependent, independent, error=None):
    """
    Computes the operation for [dDependent/dIndependent](error=1).

    The derivative is a multi-linear function.

    Args:
        dependent (TensorOp): Dependent op.
        independent(TensorOp): Independent op.
        error (TensorOp, optional): The tensor holding the error where the
            derivative will be computed at. Must have the same axes as dependent.

    Returns:
        TensorOp: Derivative applied to error. Has axes of independent.

    """
    if error is None:
        error = constant(1.)
    return DerivOp(dependent, independent, error)


class CrossEntropyMultiInner(object):
    """TODO."""

    def __init__(self, x, y, s):
        self.x = x
        self.y = y
        self.s = s

    def generate_adjoints(self, adjoints, delta, op):
        """
        TODO.

        Arguments:
          adjoints: TODO
          delta: TODO
          op: TODO

        Returns:
          TODO
        """
        self.s.generate_add_delta(adjoints, delta)
        self.x.generate_add_delta(adjoints, self.y * delta)


def cross_entropy_multi(y, t, usebits=False, out_axes=None,
                        enable_softmax_opt=True,
                        enable_diff_opt=True):
    """
    Computes the cross-entropy of two distributions.

    Arguments:
        y: The output of the model; each sample is a PDF.
        t: The true values; each sample is PDF.
        usebits: Use binary log.
        out_axes: Axes in result.  Default batch and reduction axes.
        enable_softmax_opt: Use optimization when y is softmax. Default True.
        enable_diff_opt: User derivative optimization when y is softmax.  Default True.

    Returns:
        The cross-entropy.
    """
    if out_axes is None:
        out_axes = y.axes.recurrent_axes() + y.axes.batch_axes()
    smy = y.find_schema(Softmax)
    if enable_softmax_opt and smy is not None:
        # This depends on sum(t) being 1
        x = smy.x
        Z = smy.Z
        s = -sum(x * t, out_axes=out_axes)
        result = s + safelog(Z)
        if enable_diff_opt:
            result.add_schema(CrossEntropyMultiInner(x=x, y=y, s=s))
    else:
        result = -sum(safelog(y) * t, out_axes=out_axes)
    if usebits:
        result = result * np.float(1. / np.log(2.0))
    return result


class CrossEntropyBinaryInner(object):
    """TODO."""

    def __init__(self, x, y, t):
        self.x = x
        self.y = y
        self.t = t

    def generate_adjoints(self, adjoints, delta, op):
        """
        TODO.

        Arguments:
          adjoints: TODO
          delta: TODO
          op: TODO

        Returns:
      TODO
        """
        self.x.generate_add_delta(adjoints, (self.y - self.t) * delta)
        self.t.generate_add_delta(adjoints, self.x * delta)


def cross_entropy_binary_inner(y, t, enable_sig_opt=True, enable_diff_opt=True):
    """
    Computes cross-entropy of individual samples.

    Arguments:
        y: Output of model, in range [0, 1].
        t: True values, in [0, 1].
        enable_sig_opt: Enable optimization when y is sigmoid.  Default True.
        enable_diff_opt: Enable optimization of derivative when y is sigmoid.  Default True.

    Returns:
        Cross entropy of individual samples.
    """
    result = -(safelog(y) * t + safelog(1 - y) * (1 - t))
    sigy = y.find_schema(Sigmoid)
    if sigy is not None:
        x = sigy.x
        if enable_sig_opt:
            # Simpler equivalent
            result = (1 - t) * maximum(x, -safelog_cutoff) - safelog(y)
        if enable_diff_opt:
            result.add_schema(CrossEntropyBinaryInner(x=x, y=y, t=t))

    return result


def cross_entropy_binary(y, t, usebits=False, out_axes=None,
                         enable_sig_opt=True, enable_diff_opt=True):
    """
    Computes cross-entropy.

    Arguments:
        y: Output of model, in range [0, 1]
        t: True values, in [0, 1].
        use_bits: Use binary log.
        out_axes: Axes of result; default is batch and recurrent axis.
        enable_sig_opt: Enable optimization when y is sigmoid. Default True.
        enable_diff_opt: Enable optimization of derivative when y is sigmoid. Default True.

    Returns:
        Cross entropy.
    """
    result = sum(cross_entropy_binary_inner(y, t,
                                            enable_sig_opt=enable_sig_opt,
                                            enable_diff_opt=enable_diff_opt),
                 out_axes=out_axes
                 )

    if usebits:
        result = result * np.float(1. / np.log(2.0))
    return result
