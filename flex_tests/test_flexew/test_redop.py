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
import pytest
import ngraph as ng
from ngraph.testing.flexutil import template_one_placeholder, template_two_placeholders, MINIMUM_FLEX_VALUE, \
    MAXIMUM_FLEX_VALUE, id_func


bug_1424 = pytest.mark.xfail(strict=True, reason="GitHub issue #1424, for ng.argmax and ng.argmin, "
                                                 "the values outside of the flex range are computed")
bug_1461 = pytest.mark.xfail(strict=True, reason="GitHub issue #1461, ng.maximum and ng.minimum results are different "
                                                 "than for numpy, instead of vector matrix is returned")

test_data_single_operand = (
    # template: (ng_operation, [(operand, expected_result, *case_description)], test_description),
    # *case_description is optional

    # test_sum
    (ng.sum, [(np.array([MINIMUM_FLEX_VALUE, MINIMUM_FLEX_VALUE / 2]), MINIMUM_FLEX_VALUE)],
     "Redop sum function - values from flex range, result expected to underflow"),
    (ng.sum, [(np.array([MAXIMUM_FLEX_VALUE, 2.0]), MAXIMUM_FLEX_VALUE)],
     "Redop sum function - values from flex range, result expected to overflow"),
    (ng.sum, [(np.array([MAXIMUM_FLEX_VALUE, -1.0, -2.0]), MAXIMUM_FLEX_VALUE - 1.0 - 2.0)],
     "Redop sum function - values from flex range, near positive boundary"),
    (ng.sum, [(np.array([1, 2, 14, 4, 5, 6, 7, 8, 9, -1]), 55.0)], "Redop sum function - values from flex range"),
    (ng.sum, [(np.array([1.0, 2.0, 3.0, 4.0],), 10),
              (np.array([0.4, 0.03, 0.44, 1.47]), 2.3398),
              (np.array([100, 2000, 3500.4, 10000]), 31.9990, "Array's elements and result overflow to 31.9990")],
     "Iterations sum of x"),

    # test_prod
    (ng.prod, [(np.array([MINIMUM_FLEX_VALUE, 2.0]), MINIMUM_FLEX_VALUE)],
     "Redop product function - values from flex range, result expected to underflow"),
    (ng.prod, [(np.array([MAXIMUM_FLEX_VALUE, 2.0]), MAXIMUM_FLEX_VALUE)],
     "Redop product function - values from flex range, result expected to overflow"),
    (ng.prod, [(np.array([MINIMUM_FLEX_VALUE / 10.0, 1.0, 2.0]), -6553.5)],
     "Redop product function - values from flex range"),
    (ng.prod, [(np.array([MAXIMUM_FLEX_VALUE / 10.0, 1.0, 1.0]), 3276.625)],
     "Redop product function - value from flex range, multiplied by 1.0"),
    (ng.prod, [(np.array([MAXIMUM_FLEX_VALUE, 0.0, 5.0]), 0)],
     "Redop product function - values from flex range, multiplied by zero"),
    (ng.prod, [(np.array([1.0, 2.0, 3.0, 4.0]), 24.0)], "Redop product function - values from flex range"),
    (ng.prod, [(np.array([1.0, 2.0, 3.0, 4.0]), 24.0),
               (np.array([100, 200, 3, 4]), 31.9990, "Array's first two elements overflow to 7.9997 "
                                                     "and result overflow to 31.9990"),
               (np.array([0.4, 0.7, 100, 10000]), 127.9960, "Array's last two elements overflow to 31.9990 "
                                                             "and result overflow to 127.9960")],
     "Iterations prod of x"),

    # test_max
    (ng.max, [(np.array([MAXIMUM_FLEX_VALUE - 2.0, MAXIMUM_FLEX_VALUE, MAXIMUM_FLEX_VALUE + 2.0]), MAXIMUM_FLEX_VALUE)],
     "Redop max function - result expected to overflow"),
    (ng.max, [(np.array([MAXIMUM_FLEX_VALUE + 2.0, MAXIMUM_FLEX_VALUE + 2.0, MAXIMUM_FLEX_VALUE + 2.0]),
               MAXIMUM_FLEX_VALUE)], "Redop max function - result expected to overflow"),
    (ng.max, [(np.array([MAXIMUM_FLEX_VALUE - 2.0, MAXIMUM_FLEX_VALUE]), MAXIMUM_FLEX_VALUE)],
     "Redop max function - values from flex range"),
    (ng.max, [(np.array([0.4, 0.1, 0.2]), 0.3999),
              (np.array([0, 100, 10]), 0.4999, "Array's last two elements overflow to 0.4999"),
              (np.array([7, 1, 0]), 1.9999, "Array's first element overflow to 1.9999")],
     "Iterations max of x"),

    # test_min
    (ng.min, [(np.array([MINIMUM_FLEX_VALUE + 2.0, MINIMUM_FLEX_VALUE, MINIMUM_FLEX_VALUE - 2]), MINIMUM_FLEX_VALUE)],
     "Redop min function - result expected to underflow"),
    (ng.min, [(np.array([MINIMUM_FLEX_VALUE + 2.0, MINIMUM_FLEX_VALUE]), MINIMUM_FLEX_VALUE)],
     "Redop min function - values from flex range"),
    (ng.min, [(np.array([MINIMUM_FLEX_VALUE, MINIMUM_FLEX_VALUE + 1, MINIMUM_FLEX_VALUE + 2]), MINIMUM_FLEX_VALUE),
              (np.array([0.4, 0.39, 0.38]), 0, "All array's elements overflow to 0"),
              (np.array([MAXIMUM_FLEX_VALUE, MAXIMUM_FLEX_VALUE - 1, MAXIMUM_FLEX_VALUE - 2]), MAXIMUM_FLEX_VALUE - 2)],
     "Iterations min of x"),

    # test_argmax
    bug_1424((ng.argmax, [(np.array([MAXIMUM_FLEX_VALUE - 2.0, MAXIMUM_FLEX_VALUE, MAXIMUM_FLEX_VALUE + 2.0]), 1)],
              "Redop argmax function - result expected to overflow")),
    (ng.argmax, [(np.array([MAXIMUM_FLEX_VALUE - 2.0, MAXIMUM_FLEX_VALUE]), 1)],
     "Redop argmax function - values from flex range"),
    (ng.argmax, [(np.array([1, 0, 1]), 0)], "Redop argmax function - values from flex range"),

    # test_argmin
    bug_1424((ng.argmin, [(np.array([MINIMUM_FLEX_VALUE + 2.0, MINIMUM_FLEX_VALUE, MINIMUM_FLEX_VALUE - 2]), 1)],
              "Redop argmin function - result expected to underflow")),
    (ng.argmin, [(np.array([MINIMUM_FLEX_VALUE + 2.0, MINIMUM_FLEX_VALUE]), 1)],
     "Redop argmin function - values from flex range")
 )


test_data_double_operand = (
    # template:(ng_operation, [(operand_1, operand_2, expected_result, *case_description)], test_description),
    # *case_description is optional

    # test_maximum
    bug_1461((ng.maximum, [(np.array([1, 1.1, 1.2, 1.3]),
                            (np.array([1.2, 1, 1.3, 1.4])),
                            (np.array([1.1999, 1.0999, 1.2999, 1.3999])))],
              "Maxiumum function - values withing flex range")),
    bug_1461((ng.maximum, [(np.array([MAXIMUM_FLEX_VALUE + 1, MAXIMUM_FLEX_VALUE - 1, MAXIMUM_FLEX_VALUE - 2]),
                            (np.array([MAXIMUM_FLEX_VALUE - 1, MAXIMUM_FLEX_VALUE + 1, MAXIMUM_FLEX_VALUE - 1])),
                            (np.array([MAXIMUM_FLEX_VALUE, MAXIMUM_FLEX_VALUE, MAXIMUM_FLEX_VALUE - 1])))],
              "Maximum function - result expected to overflow")),
    (ng.maximum, [(MAXIMUM_FLEX_VALUE - 2.0, MAXIMUM_FLEX_VALUE, MAXIMUM_FLEX_VALUE)],
     "Maximum function - values from flex range - scalars as input"),

    # test_minimum
    (ng.minimum, [(MINIMUM_FLEX_VALUE, MINIMUM_FLEX_VALUE - 2, MINIMUM_FLEX_VALUE)],
     "Minimum function - result expected to underflow - scalars as input"),
    (ng.minimum, [(MINIMUM_FLEX_VALUE + 2.0, MINIMUM_FLEX_VALUE, MINIMUM_FLEX_VALUE)],
     "Minimum function - values from flex range - scalars as input"),
    bug_1461((ng.minimum, [(np.array([0.4, MAXIMUM_FLEX_VALUE, 0.3]),
                            np.array([0.3, 0.4, 0.2]),
                            np.array([0, 0.3999, 0]), "For 1st array: 1st and 2nd elements overflow to 0")],
              "Minimum function - values from flex range"))

)


@pytest.mark.parametrize("operation, operands, test_name", test_data_single_operand, ids=id_func)
def test_single_operand(transformer_factory, operation, operands, test_name):
    template_one_placeholder(operands, operation)


@pytest.mark.parametrize("operation, operands, test_name", test_data_double_operand, ids=id_func)
def test_double_operand(transformer_factory, operation, operands, test_name):
    template_two_placeholders(operands, operation)
