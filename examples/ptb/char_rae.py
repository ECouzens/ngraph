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
"""
Character-level recurrent autoencoder. This model shows how to build an Encoder-Decoder style RNN.

The model uses a sequence from the PTB dataset as input, and learns to output
the same sequence in reverse order.
"""

import ngraph as ng
from ngraph.frontends.neon import Preprocess, Recurrent, Affine, Softmax, Tanh
from ngraph.frontends.neon import UniformInit, RMSProp, ax, make_bound_computation
from ngraph.frontends.neon import NgraphArgparser
from ngraph.frontends.neon import SequentialArrayIterator
import ngraph.transformers as ngt

from ptb import PTB


# parse the command line arguments
parser = NgraphArgparser(__doc__)
parser.set_defaults(gen_be=False)
args = parser.parse_args()

# these hyperparameters are from the paper
args.batch_size = 50
time_steps = 5
hidden_size = 10
gradient_clip_value = None

# download penn treebank
# set shift_target to be False, since it is going to predict the same sequence
tree_bank_data = PTB(path=args.data_dir, shift_target=False)
ptb_data = tree_bank_data.load_data()
train_set = SequentialArrayIterator(ptb_data['train'],
                                    batch_size=args.batch_size,
                                    time_steps=time_steps,
                                    total_iterations=args.num_iterations,
                                    reverse_target=True,
                                    get_prev_target=True)

# weight initialization
init = UniformInit(low=-0.08, high=0.08)

# model initialization
one_hot_enc = Preprocess(functor=lambda x: ng.one_hot(x, axis=ax.Y))
enc = Recurrent(hidden_size, init, activation=Tanh(), reset_cells=True, return_sequence=False)
one_hot_dec = Preprocess(functor=lambda x: ng.one_hot(x, axis=ax.Y))
dec = Recurrent(hidden_size, init, activation=Tanh(), reset_cells=True, return_sequence=True)
linear = Affine(init, activation=Softmax(), bias_init=init, axes=(ax.Y, ax.REC))

# Bind axes lengths:
ax.Y.length = len(tree_bank_data.vocab)
ax.REC.length = time_steps
ax.N.length = args.batch_size

# placeholders with descriptive names
inputs = dict(inp_enc=ng.placeholder([ax.REC, ax.N]),
              inp_dec=ng.placeholder([ax.REC, ax.N]),
              tgt=ng.placeholder([ax.REC, ax.N]))

optimizer = RMSProp(decay_rate=0.95, learning_rate=2e-3, epsilon=1e-6,
                    gradient_clip_value=gradient_clip_value)

# build network graph
one_hot_enc_out = one_hot_enc.train_outputs(inputs['inp_enc'])
one_hot_dec_out = one_hot_dec.train_outputs(inputs['inp_dec'])
enc_out = enc.train_outputs(one_hot_enc_out)
dec_out = dec.train_outputs(one_hot_dec_out, init_state=enc_out)
output_prob = linear.train_outputs(dec_out)

loss = ng.cross_entropy_multi(output_prob,
                              ng.one_hot(inputs['tgt'], axis=ax.Y),
                              usebits=True)
mean_cost = ng.mean(loss, out_axes=[])
updates = optimizer(loss)

train_outputs = dict(batch_cost=mean_cost, updates=updates)
loss_outputs = dict(cross_ent_loss=loss)

# Now bind the computations we are interested in
transformer = ngt.make_transformer()
train_computation = make_bound_computation(transformer, train_outputs, inputs)

######################
# Train Loop

for mb_idx, data in enumerate(train_set):
    batch_output = train_computation(dict(inp_enc=data['inp_txt'],
                                          inp_dec=data['prev_tgt'],
                                          tgt=data['tgt_txt']))
    if mb_idx % args.iter_interval == 0:
        print mb_idx, float(batch_output['batch_cost'])
