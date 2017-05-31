import ngraph as ng
from ngraph.transformers.passes.passes import GraphRewritePass, PeepholeGraphPass
from ngraph.util.generics import generic_method
from ngraph.transformers.cpu.relu import ReluOp, BpropReluOp
from ngraph.op_graph.op_graph import Add, Multiply, Greater, Less
from ngraph.op_graph.op_graph import Maximum, Minimum, BroadcastOp
from ngraph.op_graph.op_graph import ReciprocalOp, Subtract, SqrtOp, AssignableTensorOp, variable, TensorOp
from ngraph.op_graph.op_graph import PatternLabelOp, PatternSkipOp
from ngraph.op_graph.op_graph import Unflatten, ContiguousOp, BroadcastOp, BinaryElementWiseOp, Flatten, Divide
from ngraph.op_graph.batchnorm import BatchnormOp
from collections import deque as Queue


class CPUFusion(GraphRewritePass):

    def construct_relu_fprop_pattern(self):
        """
        Generate graph op that represents a pattern for Relu operation.
        max(val, 0) + slope * min (0, val)

        Note that there could be multiple patterns:
        Pattern 1 - max(x, 0) + slope * min (0, x)
        Pattern 2 - max(0, x) + slope * min (0, x)
        ..
        But we generate only 1 and match_pattern takes care of matching all
        permutations.

        Returns:
            Single pattern that matches Relu fprop op

        """
        zero = ng.constant(0)
        zero_w_broadcast = PatternSkipOp(zero,
                                         (lambda op:
                                          isinstance(op, BroadcastOp)))
        # We want to match x tensor and slope for Relu.
        self.relu_fwd_slope_label = "S"
        self.relu_fwd_x_label = "X"
        # We bind op to X unconditionally.
        x = PatternLabelOp(self.relu_fwd_x_label)
        max_op = Maximum(x, zero_w_broadcast)
        # We bind slope op to S only if it is scalar.
        slope_label_op = PatternLabelOp(self.relu_fwd_slope_label,
                                        (lambda op: op.is_scalar))
        slope = PatternSkipOp(slope_label_op,
                              (lambda op: isinstance(op, BroadcastOp)))
        min_op = Minimum(zero_w_broadcast, x)
        mul_op = Multiply(slope, min_op)
        add_op = Add(max_op, mul_op)
        return add_op

    def fuse_relu_fprop_callback(self, op, label_map_op_list):
        """
        Callback function that handles fusion for Relu fprop pattern
        """
        for (label_map, op) in label_map_op_list:
            # Matched Relu pattern, do the replacement here.
            x = label_map[self.relu_fwd_x_label]
            slope = label_map[self.relu_fwd_slope_label]
            relu_fwd_op = ReluOp(x, slope.tensor.const)
            # We need to store relu_fwd_op in a dictionary so that backward Relu
            # can access it.
            self.tensor_to_op_dict[x] = relu_fwd_op
        self.replace_op(op, relu_fwd_op)

    def construct_relu_bprop_pattern(self):
        """
        Generate graph op that represents a pattern for Relu backprop operation.
        delta * greater(x, 0) + delta * slope * less(x, 0)

        Returns:
            Single pattern that matches Relu bprop op

        """
        # We want to match x tensor, slope and delta for Relu.
        self.relu_bwd_slope_label = "S"
        self.relu_bwd_x_label = "X"
        self.relu_bwd_delta_label = "D"

        # construct 1st operand of Add
        zero = ng.constant(0)
        zero_w_broadcast = ng.PatternSkipOp(zero,
                                            (lambda op:
                                             isinstance(op, BroadcastOp)))
        x = ng.PatternLabelOp(self.relu_bwd_x_label,
                              (lambda op: not op.is_scalar))  # X is not scalar.
        greater_op = Greater(x, zero_w_broadcast)
        delta = PatternLabelOp(self.relu_bwd_delta_label,
                               (lambda op: not op.is_scalar))  # delta is not scalar.
        mul_greater_delta_op = Multiply(greater_op, delta)

        # Construct 2nd operand of Add
        # We bind slope op to S only if it is scalar.
        slope = PatternLabelOp(self.relu_bwd_slope_label,
                               (lambda op: op.is_scalar))
        less_op = Less(x, zero_w_broadcast)
        mul_slope_delta_op = Multiply(slope, delta)
        mul_slope_delta_less_op = Multiply(less_op, mul_slope_delta_op)

        add_op = Add(mul_greater_delta_op, mul_slope_delta_less_op)
        return add_op

    def fuse_relu_bprop_callback(self, op, label_map_op_list):
        """
        Callback function that handles fusion for Relu bprop pattern
        """
        for (label_map, op) in label_map_op_list:
            # Matched Relu pattern, do the replacement here.
            x = label_map[self.relu_bwd_x_label]
            delta = label_map[self.relu_bwd_delta_label]
            relu_fprop = self.tensor_to_op_dict[x]
            self.replace_op(op, BpropReluOp(delta, x, relu_fprop))

    def construct_batchnorm_fprop_pattern(self):
        """
        Generate graph op that represents a pattern for batchnorm fprop operation.
        self.gamma * ((in_obj - xmean) * ng.reciprocal(ng.sqrt(xvar + self.eps))) + self.beta
        Returns:
               Single pattern that matches batchnorm fprop op
        """
        self.batchnorm_fprop_input_tensor_label = "in_obj"
        self.batchnorm_fprop_gamma_label = "gamma"
        self.batchnorm_fprop_beta_label = "beta"
        self.batchnorm_fprop_variance_label = "variance"
        self.batchnorm_fprop_epsilon_label = "epsilon"
        self.batchnorm_fprop_mean_label = "mean"

        # bind the label to the op's which needed to be updated in the dict
        in_obj = PatternLabelOp(self.batchnorm_fprop_input_tensor_label,
                                (lambda op: isinstance(op, Flatten)))
        gamma = PatternLabelOp(self.batchnorm_fprop_gamma_label,
                               (lambda op: isinstance(op, BroadcastOp)))
        beta = PatternLabelOp(self.batchnorm_fprop_beta_label,
                              (lambda op: isinstance(op, BroadcastOp)))
        variance = PatternLabelOp(self.batchnorm_fprop_variance_label,
                                  (lambda op: isinstance(op, Divide)))
        epsilon = PatternLabelOp(self.batchnorm_fprop_epsilon_label,
                                 (lambda op: isinstance(op, BroadcastOp)))
        mean = PatternLabelOp(self.batchnorm_fprop_mean_label,
                              (lambda op: isinstance(op, BroadcastOp)))

        # construct the fprop batchnorm pattern matching the computation graph
        # ng.sqrt(xvar + self.eps)
        SqrtofVarianceAndEps = ng.sqrt(ng.add(variance, epsilon))
        # ng.reciprocal(ng.sqrt(xvar + self.eps))
        reciprocal_op = ng.reciprocal(SqrtofVarianceAndEps)
        reciprocal_op_w_braodcast = ng.PatternSkipOp(reciprocal_op,
                                                     lambda op: isinstance(op, BroadcastOp))
        # (in_obj - xmean) * ng.reciprocal(ng.sqrt(xvar + self.eps))
        mul_op_1 = ng.multiply(ng.subtract(in_obj, mean), reciprocal_op_w_braodcast)
        # "self.gamma * ((in_obj - xmean) * ng.reciprocal(ng.sqrt(xvar + self.eps)))
        MultiplyGamma = ng.multiply(mul_op_1, gamma)
        # self.gamma * ((in_obj - xmean) * ng.reciprocal(ng.sqrt(xvar + self.eps))) + self.beta
        AddBeta = ng.Add(MultiplyGamma, beta)
        return AddBeta

    def fuse_batchnorm_fprop_callback(self, op, label_map_op_list):
        """
        Callback function that handles fusion for batchnorm fprop pattern
        """
        for (label_map, op) in label_map_op_list:
            # Matched bprop batchnorm pattern, do the replacement here.
            inputs = label_map[self.batchnorm_fprop_input_tensor_label]
            gamma = label_map[self.batchnorm_fprop_gamma_label]
            beta = label_map[self.batchnorm_fprop_beta_label]
            variance = label_map[self.batchnorm_fprop_variance_label]
            mean = label_map[self.batchnorm_fprop_mean_label]
            epsilon = label_map[self.batchnorm_fprop_epsilon_label].args[0].tensor.const
            batchnorm_fwd_op = BatchnormOp(inputs, gamma, beta, epsilon, mean, variance)

            # book keep the fprop batchnorm op to use during back propogation
            self.tensor_to_op_dict[inputs] = batchnorm_fwd_op
            self.replace_op(op, batchnorm_fwd_op)

    def __init__(self):
        self.tensor_to_op_dict = dict()

        # Register Relu fprop pattern
        pattern_relu_fprop = self.construct_relu_fprop_pattern()
        self.register_pattern(pattern_relu_fprop, self.fuse_relu_fprop_callback)

        # Register Relu bprop pattern
        pattern_relu_bprop = self.construct_relu_bprop_pattern()
        self.register_pattern(pattern_relu_bprop, self.fuse_relu_bprop_callback)

        # Register batchnorm fprop pattern
        pattern_batchnorm_fprop = self.construct_batchnorm_fprop_pattern()
        self.register_pattern(pattern_batchnorm_fprop, self.fuse_batchnorm_fprop_callback)
