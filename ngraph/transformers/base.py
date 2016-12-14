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
from __future__ import division

import collections
import weakref

import abc
from builtins import object
from future.utils import with_metaclass

from ngraph.analysis.memory import assign_buffers
from ngraph.op_graph.op_graph import Op, TensorOp, InitTensorOp, tensor_descriptions, \
    Function, doall, ResultHandle
from ngraph.transformers.passes.passes import RequiredTensorShaping, SimplePrune
from ngraph.util.generics import generic_method
from ngraph.util.names import NameableValue
from ngraph.util.ordered import OrderedSet


class Computation(NameableValue):
    """
    A handle for a computation function.

    Arguments:
        transformer (obj:`Transformer`): The associated transformer.
        returns: If an Op, return the value
            of the Op, if sequence of Ops, return the sequence of values, if
            a set return a map, if None, return None.
        *args: AllocationOps marked input will be arguments to the function.
        **kwargs: Args for related classes.
    """

    def __init__(self, transformer, returns, *args, **kwargs):
        super(Computation, self).__init__(**kwargs)
        self.transformer = transformer
        self.computation_name = None

        def wrap_op(op):
            if isinstance(op, TensorOp):
                return ResultHandle(op)
            else:
                return op

        def wrap_ops(ops):
            return [wrap_op(op) for op in ops]

        self.ops = OrderedSet()
        if isinstance(returns, collections.Set):
            returns = set(wrap_ops(returns))
            self.ops.update(returns)
        elif isinstance(returns, collections.Sequence):
            returns = wrap_ops(returns)
            self.ops.update(returns)
        elif isinstance(returns, Op):
            returns = wrap_op(returns)
            self.ops.add(returns)
        elif returns is not None:
            raise ValueError()
        self.returns = returns

        self.parameters = []
        for arg in args:
            if arg.input:
                self.parameters.append(arg)
            else:
                raise ValueError((
                    'The arguments to a computation must all have property '
                    'input=True, but the op passed had input=False.  In most '
                    'cases you want to pass placeholder ops in as arguments.  '
                    '{op} was passed in, of type {op_type}.'
                ).format(
                    op=arg,
                    op_type=arg.__class__.__name__,
                ))

            if isinstance(arg, Op):
                self.ops.add(arg)
            else:
                raise ValueError()

        control_ops = OrderedSet()
        for op in self.ops:
            control_ops.update(op.user_deps)
        processed_ops = set()
        pending_ops = OrderedSet(self.ops)
      #_c  import ipdb; ipdb.set_trace()
        while pending_ops:
            op = pending_ops.pop()
            if op in processed_ops:
                continue
            control_ops.update(op.other_deps)
            pending_ops.update(op.other_deps)
            pending_ops.update(op.args)
            processed_ops.add(op)

        self.ops.update(control_ops)
        self.transformer.all_results.update(self.ops)
        self.executor = None

    def transform(self):
        """
        Transforms the computation so that it can be run.
        """
        self.ops = {op.forwarded for op in self.ops}
        ordered_ops = self.transformer.dataflow.can_reach(self.ops, order=self.transformer.ops)
        self.computation_name = self.transformer.transform_ordered_ops(ordered_ops, name=self.name)

    def __call__(self, *args):
        """
        Executes the computation passing args in to the function.
        """
        if len(args) != len(self.parameters):
            raise ValueError((
                'Computation was expecting {expected} arguments, but was '
                'called with {called}.'
            ).format(
                expected=len(self.parameters),
                called=len(args),
            ))
 #       import ipdb;ipdb.set_trace()
        # TODO Should this be automatic?
        self.transformer.initialize()

        # Get the parameters to the device
        for param, arg in zip(self.parameters, args):
            param.value[()] = arg

        self.executor()

        # TODO Should copy this out of the device to a destination when it is not scalar
        def value(op):
            """
            Returns the computed value of op, or None if it has no value.

            :param op:
            :return: Return value for op.
            """
            if isinstance(op, TensorOp):
                if op.value is None:
                    pass
                return op.value.get(None)
            else:
                return None

        if isinstance(self.returns, Op):
            return value(self.returns)
        elif isinstance(self.returns, collections.Set):
            result = dict()
            for op in self.returns:
                dict[op] = value(op)
            return result

        elif isinstance(self.returns, collections.Sequence):
            return tuple(value(op) for op in self.returns)

        else:
            return None


class DeviceBuffer(with_metaclass(abc.ABCMeta, NameableValue)):
    """
    Something that can provide storage.

    Attributes:
        transformer: The transformer associated with this device buffer.
        views: All direct tensor views of this buffer.
    """
    def __init__(self, transformer, **kwargs):
        """

        :param transformer: The associated transformer.
        :param kwargs: Any additional arguments.
        """
        super(DeviceBuffer, self).__init__(**kwargs)
        self.transformer = transformer
        self.transformer.device_buffers.add(self)
        self.__views = weakref.WeakValueDictionary()

    @property
    def views(self):
        """

        Returns: Iterator over views of the buffer

        """
        return self.__views.values()

    def transform_allocate(self):
        """
        Generate allocation code.
        """
        self.transform_allocate_views()

    def transform_allocate_views(self):
        """Generate code for allocating views"""
        for view in self.views:
            view.transform_allocate()

    def device_tensor(self, tensor_description):
        """
        Returns a DeviceTensor for tensor_description.

        Arguments:
            tensor_description: The TensorDescription of the tensor.

        Returns: A DeviceTensor.
        """
        tensor = self.__views.get(tensor_description.parameter_key, None)
        if tensor is not None:
            return tensor
        tensor = self.create_device_tensor(tensor_description)
        self.__views[tensor_description.parameter_key] = tensor
        return tensor

    @abc.abstractmethod
    def create_device_tensor(self, tensor_description):
        """
        Creates a DeviceTensor for tensor_description.

        Arguments:
            tensor_description: The TensorDescription of the tensor.

        Returns: A DeviceTensor.
        """


class DeviceBufferStorage(with_metaclass(abc.ABCMeta, DeviceBuffer)):
    """
    A handle to allocated device storage.

    Arguments:
        transformer: The associated transformer.
        bytes: Size of storage.
        dtype: Alignment of storage.
        **kwargs: Args for related classes.

    Attributes:
        bytes: The size of the byte buffer.
        dtype: The dtype of the storage.
    """
    def __init__(self, transformer, bytes, dtype, **kwargs):
        super(DeviceBufferStorage, self).__init__(transformer, **kwargs)
        self.bytes = bytes
        self.dtype = dtype


class DeviceBufferReference(with_metaclass(abc.ABCMeta, DeviceBuffer)):
    """
    A handle to a reference to a DeviceBuffer.

    Arguments:
        transformer: The associated transformer.

    Attributes:

        transformer: The transformer associated with this device buffer reference.
        views: All direct tensor views of this buffer reference.  Does not include views of
            device buffer references set to this device buffer.
    """
    def __init__(self, transformer, **kwargs):
        super(DeviceBufferReference, self).__init__(self, **kwargs)
        self.__device_buffer = None


class DeviceTensor(with_metaclass(abc.ABCMeta, NameableValue)):
    """
    A handle to a tensor on the device.

    Arguments:
        transformer: The associated transformer.
        device_buffer: The device buffer for the elements.
        tensor_description: The tensor_description describing this device tensor.
        **kwargs: Args for related classes.
    """
    def __init__(self, transformer, device_buffer, tensor_description, **kwargs):
        super(DeviceTensor, self).__init__(**kwargs)
        self.transformer = transformer
        self.device_buffer = device_buffer
        self.tensor_description = tensor_description

    @property
    def dtype(self):
        return self.tensor_description.dtype

    @abc.abstractmethod
    def transform_allocate(self):
        """Generate code for making the device tensor usable on the device."""

    @abc.abstractmethod
    def get(self, tensor):
        """
        Copy from device to tensor.

        :param tensor: Destination of copy.  If None, tensor will be allocated.
        :return: tensor.
        """

    @abc.abstractmethod
    def __getitem__(self, key):
        """
        Provides read access to the device tensor.

        :param key: The index/slice
        :return: The item/value.
        """

    @abc.abstractmethod
    def __setitem__(self, key, value):
        """
        Provides write access to the device tensor.

        :param key: The index/slice
        :param value: Tensor with same size as index/slice
        """


class Transformer_ABC_Meta(abc.ABCMeta):
    """
    metaclass for the backend objects
    takes care of registering all the backend subclasses
    """
    def __init__(self, name, bases, dict_):
        if not hasattr(self, 'transformers'):
            self.transformers = {}
        else:
            name = getattr(self, 'transformer_name', None)
            if name and name not in ['Transformer']:
                self.transformers[name] = self
        super(Transformer_ABC_Meta, self).__init__(name, bases, dict_)


class Transformer(with_metaclass(Transformer_ABC_Meta, object)):
    """
    Produce an executable version of op-graphs.

    Computations are subsets of Ops to compute.  The transformer determines storage
    allocation and transforms the computations and allocations into functions.

    Arguments:
        fusion (bool): Whether to combine sequences of operations into one operation.
        **kwargs: Args for related classes.

    Attributes:
        computations (:obj:`set` of :class:`Computation`): The set of requested computations.
        all_results (:obj:`set` of :class:`ngraph.op_graph.op_graph.Op`):  A root set of Ops that
            need to be computed.
        finalized (bool): True when transformation has been performed.
        initialized (bool): True when variables have been initialized/restored.
        fusion (bool): True when fusion was enabled.
        device_buffers (set): Set of handles for storage allocations.
        cpu_initializations (list): Initializations to be performed from the CPU after
            allocation.
        init_computation (Computation): The computation that performs initialization
            after allocation.  This happens once per training session, not once per-minibatch.
    """
    def __init__(self, fusion=None, **kwargs):
        super(Transformer, self).__init__(**kwargs)
        self.computations = OrderedSet()
        self.all_results = OrderedSet()
        self.finalized = False
        self.allocated = False
        self.initialized = False
        self.fusion = fusion
        self.device_buffers = OrderedSet()
        self.cpu_initializations = []
        self.init_computation = None
        self.graph_passes = [SimplePrune(), RequiredTensorShaping()]

    def register_graph_pass(self, graph_pass):
        self.graph_passes.append(graph_pass)

    def run_registered_graph_passes(self, ops):
        for graph_pass in self.graph_passes:
            print ("run graph pass ", graph_pass)
            graph_pass.do_pass(ops)

    def _transform_computations(self):
        """
        Transform computation graphs to a form that can be run.
        """

        # with Op.saved_user_deps():
        # Run passes on the computation graphs
        self.run_registered_graph_passes(self.all_results)

        # Collect up all ops from the graph and obtain the init graph
        all_ops = OrderedSet(Op.ordered_ops(self.all_results))
        init_op = doall(self.ordered_initializers(all_ops))

        # Run passes on the initialization graphs
        self.run_registered_graph_passes([init_op])

        # Union the init and computation graphs
        self.inits = Op.ordered_ops([init_op])
        all_ops.update(self.inits)

        # create computation which initializes values (called once per session)
        init_op.update_forwards()
        self.init_computation = self.computation(init_op, name="init")

        self.dataflow, self.memory = assign_buffers(self, all_ops, self.fusion)

        # Initialize tensor descriptions
        for op in all_ops:
            self.initialize_tensor_descriptions(op)

        self.ops = self.dataflow.instructions

        self.start_transform_allocate()
        for device_buffer in self.device_buffers:
            device_buffer.transform_allocate()
        self.finish_transform_allocate()

        # Compile the computations now that we know their storage
        for computation in self.computations:
            computation.transform()
        self.finish_transform()
        self.finalized = True

    @generic_method(Op)
    def initialize_tensor_descriptions(self, op):
        """
        Ensures that tensor descriptions associated with op are initialized.

        Arguments:
            op (class:`ngraph.op_graph.op_graph.Op`): Initialize the tensor description for op.
        """
        # op
        tensor_description = op.tensor_description()
        if tensor_description is not None and tensor_description.transformer is None:
            tensor_description.initialize(self)

        # Call info for op
        for tensor_description in op.call_info():
            if tensor_description.transformer is None:
                tensor_description.initialize(self)

    @initialize_tensor_descriptions.on_type(Function)
    def initialize_tensor_descriptions(self, op):
        """
        For Function, recurse into instructions

        Arguments:
            op: The function.
        :return:
        """
        for inst in op.instructions:
            self.initialize_tensor_descriptions(inst)

    @abc.abstractmethod
    def start_transform_allocate(self):
        """
        Called just before allocation code is transformed.
        """

    @abc.abstractmethod
    def finish_transform_allocate(self):
        """
        Called after last allocation is transformed.
        """

    @abc.abstractmethod
    def transform_ordered_ops(self, ordered_ops):
        """
        Generate code to compute ordered_ops.

        Arguments:
        ordered_ops: Ops to compute

        Returns: Handle for generated code
        """

    @abc.abstractmethod
    def finish_transform(self):
        """
        Finish generating the model.
        """

    @abc.abstractmethod
    def allocate_storage(self):
        """
        Allocate storage on the device.
        """

    @generic_method(Op)
    def initialize_constant(self, op):
        pass

    @initialize_constant.on_type(InitTensorOp)
    def initialize_constant(self, op):
        tensor_description, = tensor_descriptions(op.args)
        value = op.valfun(tensor_description)
        tensor_description.value[()] = value

    def ordered_initializers(self, ordered_ops):
        """
        TODO.

        Arguments:
          ordered_ops: TODO

        Returns:

        """
        initializers = OrderedSet()
        todo = OrderedSet(ordered_ops)
        while todo:
            these_ops = todo
            todo = OrderedSet()
            for op in these_ops:
                op = op.forwarded
                op.update_forwards()
                initializers.update(op.initializers)
                todo.update(op.initializers)

        ordered_initializer_ops = []
        visited = set()
        inits = OrderedSet()

        def visit(node):
            node = node.forwarded
            node.update_forwards()
            if node not in visited:
                if node.initializers:
                    if node in inits:
                        if node not in visited:
                            ordered_initializer_ops.append(node)
                            visited.add(node)
                    else:
                        inits.add(node)
                        for n in node.initializers:
                            visit(n)
                else:
                    for n in node.args:
                        visit(n)
                if node not in visited:
                    ordered_initializer_ops.append(node)
                    visited.add(node)

        for node in initializers:
            visit(node)

        return ordered_initializer_ops

    @abc.abstractmethod
    def device_buffer_storage(self, bytes, dtype, name):
        """
        Make a DeviceBuffer.

        Arguments:
            bytes: Size of buffer.
            dtype: dtype of buffer.
            name: Name of the storage variable

        returns: A DeviceBuffer.
        """

    @abc.abstractmethod
    def device_buffer_reference(self):
        """
        Make a DeviceBufferReference.

        Returns: A DeviceBufferReference.
        """

    # User API follows
    def computation(self, results, *parameters, **kwargs):
        """
        Adds a computation to the transformer.

        Arguments:
            results: Values to be computed
            *parameters: Values to be set as arguments to evaluate
            name: Name for function.  Defaults to None.

        Returns:
            Dictionary from results to their values
        """
        if self.finalized:
            raise ValueError(
                'Cannot create computations from a finalized transformer'
            )

        result = Computation(self, results, *parameters, **kwargs)
        import ipdb
 #       ipdb.set_trace()
        self.computations.add(result)
        return result

    def allocate(self):
        """
        Allocate storage and then initializes constants.

        Will finalize if not already done.
        """
        if self.allocated:
            return

        with Op.saved_user_deps():
            # Disable user_deps during transformations

            if not self.finalized:
                self._transform_computations()

            self.allocate_storage()

            for op in OrderedSet(self.inits + self.ops):
                self.initialize_constant(op)

        self.allocated = True

    def initialize(self):
        """
        Initialize storage.  Will allocate if not already performed.
        """
        if self.initialized:
            return
        self.allocate()

        # Need to set initialized before we are done because the init computation will
        # try to initialize.
        self.initialized = True
        self.init_computation()


__transformer_factory = None


def make_transformer():
    """
    Generates a Transformer using the factory in this module which defaults
    to NumPy

    Returns: Transformer
    """
    return __transformer_factory()


def set_transformer_factory(factory):
    """
    Sets the Transformer factory used by make_transformer

    Arguments:
        factory (object): Callable object which generates a Transformer
    """
    global __transformer_factory
    __transformer_factory = factory


def transformer_choices():
    """Return the list of available transformers."""
    names = sorted(Transformer.transformers.keys())
    return names


def allocate_transformer(name, **kargs):
    """Allocate a named backend."""
    try:
        return Transformer.transformers[name](**kargs)
    except KeyError:
        names = ', '.join(["'%s'" % (_,) for _ in Transformer.transformer_choices()])
        raise ValueError("transformer must be one of (%s)" % (names,))


def make_transformer_factory(name, **kargs):
    def factory():
        return allocate_transformer(name, **kargs)

    return factory
