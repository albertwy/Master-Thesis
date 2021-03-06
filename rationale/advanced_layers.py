#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
Theano code ported from Tao Lei's rationalizing neural predictions

Classes:
    RCNNCell --> implementation of the RCNN network.
    Z-Layer --> Implementation of the Z-Layer
    MultiRNNCell --> adapted multirnncell from tensorflow to allow for the state
    of rcnn to be passed.
    
"""

import tensorflow as tf
from tensorflow.python.ops.rnn_cell import RNNCell 
from tensorflow.python.ops.math_ops import sigmoid
from tensorflow.python.ops.math_ops import tanh
from tensorflow.python.ops import variable_scope as vs
from tensorflow.python.ops import array_ops
from tensorflow.python.util import nest
from basic_layers import BasicRNNCell, _linear

tf.set_random_seed(2345)


###############################
#### Layer Normalisation ######
###############################

def ln(tensor, scope = None, epsilon = 1e-5):
    """ Layer normalizes a 2D tensor along its second axis """
    assert(len(tensor.get_shape()) == 2)
    m, v = tf.nn.moments(tensor, [1], keep_dims=True)
    if not isinstance(scope, str):
        scope = ''
    with tf.variable_scope(scope + 'layer_norm'):
        scale = tf.get_variable('scale',
                                shape=[tensor.get_shape()[1]],
                                initializer=tf.constant_initializer(1))
        shift = tf.get_variable('shift',
                                shape=[tensor.get_shape()[1]],
                                initializer=tf.constant_initializer(0))
    LN_initial = (tensor - m) / tf.sqrt(v + epsilon)

    return LN_initial * scale + shift

###############################
#######    RCNNCell   #########
###############################
class RCNNCell(RNNCell):
    '''
    RCNN Cell Tensorflow implementation from the paper: Semi-supervised Question
    Retrieval with gated Convolutions. 
    
    '''
    def Layer(self, n_in, n_out, order, inputs,
              hasbias = False, scope = None,
              reuse = None, 
              initializer = tf.random_uniform_initializer(-0.05,
                                                          0.05, 
                                                          seed = 2345) ): 
        
        # this determines what the variable scope will be. dynamic_rnn messes 
        # with the scope and adds rnn/ in front of it. This corrects it.
        if scope:
            scope = scope + '_' + str(order) + '_ZLayer'
        else:
            name = 'RCNN_Feed_Forward_Layer_' + str(order) + '_%s'% self._idx
            

        with vs.variable_scope(scope or name, reuse = reuse) as scop:
            
            W = tf.get_variable('weights' + str(order) ,
                                    [n_in, n_out],
                                    initializer = initializer
                                    )
            
            out = tf.matmul(inputs, W)
            
            # add bias if asked so
            if hasbias:
                B = tf.get_variable( 'biases_'+ str(order),
                                    [1],
                                    initializer = tf.constant_initializer(0.0), 
                                    dtype = tf.float32)
                out = out + B
        
            return out
    
    def __init__(self, 
                 num_units,
                 order = 2, 
                 forget_bias = 1.0, 
                 has_bias = False,
                 input_size = None,
                 state_is_tuple = True, 
                 activation = tanh, 
                 mode = 1,
                 has_outgate= False,
                 state_size = 100,      
                 idx = 1,              
                 use_ln = False, 
                 initializer = tf.random_uniform_initializer(-0.05, 0.05, 
                                                             seed = 2345)       
                 ):
        
        '''
        RCNN
        Inputs
        ------
            order           : CNN feature width
            has_outgate     : whether to add a output gate as in LSTM; this can be
                              useful for language modeling
            mode            : 0 if non-linear filter; 1 if linear filter (default)
            
        Tensorflow Edition
        '''
        
        if not state_is_tuple:
            print 'Please use the tuple function. Not implemented for usage with tensors.'
            raise NotImplemented
        
        self._num_units = num_units
        self._forget_bias = forget_bias
        self._state_is_tuple = state_is_tuple
        self._activation = activation
        self._state_size = num_units 
        self._idx = idx
        self._has_bias = has_bias
        
        # specific to the RCNN layer 
        self._order = order
        self._mode = mode 
        self._has_outgate = has_outgate
        
        self.reuse = None
        self.useln = use_ln
        
        self.initializer = initializer 

    @property
    def state_size(self):
        return self._num_units
    
    @property
    def output_size(self):
        return self._num_units
    
    
    def zero_state(self, batch_size, dtype = tf.float32):

        zeros = tf.zeros([batch_size,
                           self._state_size*(self._order + 1)], 
                           name = 'initstateLT1', 
                           dtype = tf.float32)
        

        return zeros
        
    # Note swap state and inputs if NOT using scan        
    def __call__(self, 
                 state,                 # state tenso
                 inputs,                # INPUTS TENSOR
                 mode = 1,              # 0 if non-linear filter; 1 if linear filter (default),
                 scope = None,          # custom scope if you'd like.
                 scope2 = None          # custom scope for the feedforward layer
                 ):
        
        '''
        Recurrent Convolutional Neural Network
        
        '''
        
        input_size = inputs.get_shape()[1] # 256
        
        # ensure correct scope is used when using dynamic rnn
        self.name = "RCNN_cell" + '_%s'% (str(self._idx))
        
        with vs.variable_scope(scope or self.name, reuse = self.reuse) as var_scope: 
            
            # depending on the state, take the appropriate slice
            if len(state.get_shape())>1: 
                ht_m1 = state[:,self._num_units*self._order:]
            else:
                ht_m1 = state[self._num_units*self._order:]
            
            bias = tf.get_variable( 'bias_out' + '_%s'%self._idx,
                            [self._num_units,],
                            initializer = tf.constant_initializer(0.0),
                            dtype = tf.float32)
            
            # forget cell
            forget_cell = BasicRNNCell(self._num_units, activation = tf.nn.sigmoid)
            forget_t = forget_cell(inputs, ht_m1)[0]
            
        lst = [ ]   
        for i in range(self._order):
            
            # check size of the state tensor
            if len(state.get_shape()) > 1:
                c_i_tm1 = state[:,
                                self._num_units*i:self._num_units*i+self._num_units]
                                
            else:
                c_i_tm1 = state[self._num_units*i:self._num_units*i+self._num_units]
            
            # Create feed forward layer
            in_i_t = self.Layer(input_size,
                                self._num_units,
                                i,
                                inputs,
                                hasbias = self._has_bias,
                                scope = scope2,
                                reuse = self.reuse,
                                initializer = self.initializer)
            
            
            # formulae for the sum of the n_grams
            if i == 0:
                c_i_t = forget_t * c_i_tm1 + (1.0-forget_t) * in_i_t
            
            elif self._mode == 0:
                c_i_t = forget_t * c_i_tm1 + (1.0-forget_t) * (in_i_t * c_im1_t)
            
            else:
                c_i_t = forget_t * c_i_tm1 + (1.0-forget_t) * (in_i_t + c_im1_tm1)
            
            # whether to use layer normalization
            if self.useln:
                lst.append(ln(c_i_t, scope = 'cit' + str(self._idx) + str(i)+ '/'))
            else:
                lst.append(c_i_t)
            
            c_im1_tm1 = c_i_tm1
            c_im1_t = c_i_t
        
        
        # Use an outgate or not!
        if not self._has_outgate:
            if self.useln:
                h_t = ln(self._activation(c_i_t + bias, name = 'no_outgate'), 
                         scope = 'ht_' + str(self._idx))
            else:
                h_t = self._activation(c_i_t + bias, name = 'no_outgate')
            
        else:
            with vs.variable_scope(scope or self.name, reuse = self.reuse ) as var_scope:
                out_t = _linear([inputs, ht_m1],
                                self._num_units,
                                True, 1.0,
                                scope = scope or "RCNN_cell" + '_%s'% \
                                (str(self._idx) + 'out_t'))
                
                
            
            h_t = out_t * self._activation(c_i_t + bias, name = 'with_outgate')
            
        # add the outgate to the next state
        lst.append(h_t)
        
        # return state
        if len(state.get_shape()) > 1:
            return tf.concat(1,lst)
        else:
            return tf.concatenate(lst)
        

###############################
#######  MultiRNNCell #########
###############################
class MultiRNNCell(RNNCell):
    """RNN cell composed sequentially of multiple simple cells."""

    def __init__(self, cells, state_is_tuple=True):
        """Create a RNN cell composed sequentially of a number of RNNCells.
        Args:
          cells: list of RNNCells that will be composed in this order.
          state_is_tuple: If True, accepted and returned states are n-tuples, where
            `n = len(cells)`.  If False, the states are all
            concatenated along the column axis.  This latter behavior will soon be
            deprecated.
        Raises:
          ValueError: if cells is empty (not allowed), or at least one of the cells
            returns a state tuple but the flag `state_is_tuple` is `False`.
        """
        if not cells:
            raise ValueError("Must specify at least one cell for MultiRNNCell.")
        if not nest.is_sequence(cells):
            raise TypeError(
                "cells must be a list or tuple, but saw: %s." % cells)

        self._cells = cells
        self._state_is_tuple = state_is_tuple
        if not state_is_tuple:
            if any(nest.is_sequence(c.state_size) for c in self._cells):
                raise ValueError("Some cells return tuples of states, but the flag "
                             "state_is_tuple is not set.  State sizes are: %s"
                             % str([c.state_size for c in self._cells]))

    @property
    def state_size(self):
        if self._state_is_tuple:
            return tuple(cell.state_size for cell in self._cells)
        else:
            return sum([cell.state_size for cell in self._cells])

    @property
    def output_size(self):
        return self._cells[-1].output_size
    
    def zero_state(self, batch_size, dtype = tf.float32):
        return tuple([c.zero_state(batch_size,dtype) for c in self._cells])

    def __call__(self, inputs, state, scope=None):
        """Run this multi-layer cell on inputs, starting from state."""
        
        
        with vs.variable_scope(scope or "multi_rnn_cell"):
            
            cur_state_pos = 0 
            cur_inp = inputs
            new_states = []
            for i, cell in enumerate(self._cells):
                with vs.variable_scope("cell_%d" % i):
                    if self._state_is_tuple:
                        if not nest.is_sequence(state):
                            raise ValueError(
                                  "Expected state to be a tuple of length %d, but received: %s"
                                  % (len(self.state_size), state))
                        cur_state = state[i]
                    else:
                        cur_state = array_ops.slice(
                                    state, [0, cur_state_pos], [-1, cell.state_size])
                        cur_state_pos += cell.state_size
                    cur_inp, new_state = cell(cur_inp, cur_state)
                    new_states.append(new_state)
                    
                    
        new_states = (tuple(new_states) if self._state_is_tuple else
                      array_ops.concat(new_states, 1))
        return cur_inp, new_states
    
        
###############################################
####### Numerically stable sigmoid    #########
###############################################
def stable_sigmoid(x):
    '''
    Numerically stable sigmoid function
    '''
    x = tf.squeeze(x, squeeze_dims= [2])
    def softmax(x):
        return tf.ones_like(x, tf.float32) /(tf.ones_like(x, tf.float32)+ tf.exp(-x))
        
    def softplus(x):
        return tf.exp(x)/(tf.ones_like(x, tf.float32) + tf.exp(x))
    
    sftmx = softmax(x)
    sftpls = softplus(x)
    
    condition = tf.greater_equal(x, tf.zeros_like(x, tf.float32))
    val = tf.select(condition, sftmx, sftpls)
    return tf.expand_dims(val, 2)

###############################
#######    Z-Layer    #########
###############################
class Z_Layer(object):
    
    def __init__(self,
                 n_in,                   # input size
                 n_hidden = 30,               # Number of units in the layer
                 state_is_tuple = True,  
                 activation = tanh,
                 initializer = tf.random_uniform_initializer(-0.05, 0.05,
                                                             seed = 2345)
                ):
        
        '''
        Zlayer
        Inputs
        ------
             inputs constructor:
             num_units = number of output units
             activation = activation
            
        Tensorflow Edition
        '''
        self._n_hidden = n_hidden
        self._n_in = n_in
        self._activation = activation
        self._idx = 'ZLayer'
        
        with vs.variable_scope('ZLayerWeights') as var_scope: 
            w1 = tf.get_variable('W1', [n_in,1], dtype = tf.float32, 
                                 initializer = initializer)
            w2 = tf.get_variable('W2', [n_hidden,1], dtype = tf.float32, 
                                 initializer = initializer)
            bias = tf.get_variable('Bias', 
                                   [1], 
                                   initializer=tf.constant_initializer(0.0), 
                                   dtype = tf.float32)
            
        self.rlayer = RCNNCell(self._n_hidden, idx = self._idx, 
                               initializer = initializer )
        
        if not state_is_tuple:
            print 'Please use the tuple function. Not implemented for usage with tensors.'
            raise NotImplemented
        
    
    
    def forward(self, x_t, z_t, z_tm1, h_tm1):
        
        
        with vs.variable_scope('ZLayerWeights', reuse=True) as var_scope:
            w1 = tf.get_variable('W1', dtype = tf.float32)
            w2 = tf.get_variable('W2', dtype = tf.float32)
            bias = tf.get_variable('Bias', dtype = tf.float32)

            
        # make a prediction
        pz_t = sigmoid(
                    tf.matmul(x_t, w1) +
                    tf.matmul(h_tm1[:,-self.n_hidden:], w2) +
                    bias, name = 'pzt_sigmoid'
                )
        logits = tf.matmul(x_t, w1) + \
                    tf.matmul(h_tm1[:,-self.n_hidden:], w2) +\
                    bias
        
        xz_t =  tf.concat(1, [x_t, tf.reshape(z_t, [-1,1])], name = 'xzt_concat')
        
        rnn_outputs, final_state = self.rlayer( xz_t,  h_tm1)
        
        return final_state, pz_t, logits
    
    
    def forward_all(self, x, z):
        
        assert len(x.get_shape()) == 3
        assert len(z.get_shape()) == 2
        
        # get the variables
        with vs.variable_scope('ZLayerWeights', reuse=True) as var_scope:
            w1 = tf.get_variable('W1', dtype = tf.float32)
            w2 = tf.get_variable('W2', dtype = tf.float32)
            bias = tf.get_variable('Bias', dtype = tf.float32)

        
        # z would be ( len, batch_size)
        # x would be (len, batch_size, n_d)
        xz = tf.concat(2, [x, tf.expand_dims(z, 2)])
        
        # initial state
        h0 = tf.zeros([1, x.get_shape()[1], self._n_hidden], 
                       name = 'H_0_matrix_zlayer', 
                       dtype = tf.float32)
        
        # get the zero state for the rlayer
        h_temp = self.rlayer.zero_state(x.get_shape()[1]) 
        
        # ensure that the variables are reused in RCNN
        self.rlayer.reuse = True
        
        
        with tf.variable_scope('RNN'):
            
            # here too changed the dynamic rnn to scan
            htp = tf.scan(self.rlayer, xz, initializer= h_temp)
            if len(htp.get_shape())>1:
            
                h = htp[:,:, self.rlayer._order * self.rlayer._num_units:]
            else:
                h = htp[:, self.rlayer._order * self.rlayer._num_units:]
        

        h_prev = tf.concat(0,[h0, h[:-1]])
        
        # check shapes
        assert len(h_prev.get_shape()) == 3
        assert len(h.get_shape())      == 3

        # get the shapes
        xshape = x.get_shape().as_list()
        hshape = h_prev.get_shape().as_list()
        
        # reshape x such that matmul is possible
        tp1 = tf.reshape(x, [-1, xshape[2]])
        a_tp = tf.matmul(tp1, w1)
        a = tf.reshape(a_tp, [-1, xshape[1], 1])
        
        # reshape h_prev such that matmul is possible
        tp2 = tf.reshape(h_prev, [-1, hshape[2]])
        b_tp = tf.matmul(tp2, w2)
        b = tf.reshape(b_tp, [-1, hshape[1], 1])
        
        # sigmoid it
        logits = a+b+bias
        pz = sigmoid(logits)
        
        # reshape, remove last dim
        pz_reshaped = tf.squeeze(pz, squeeze_dims= [2])
        logits_reshaped = tf.squeeze(logits, squeeze_dims= [2])
        
        # return the matrix of predictions
        assert len(pz_reshaped.get_shape()) == 2
        assert len(logits_reshaped.get_shape()) == 2
        
        return pz_reshaped, logits_reshaped
    
        
    # adapted version of sample
    def sample(self, prev, x_t, seed = 2345):
        
        # get the appropriate values
        h_tm1 = prev[0]
        z_tm1 =prev[1]
    
        # get the variables
        with vs.variable_scope('ZLayerWeights', reuse=True) as var_scope:
            w1 = tf.get_variable('W1', dtype = tf.float32)
            w2 = tf.get_variable('W2', dtype = tf.float32)
            bias = tf.get_variable('Bias', dtype = tf.float32)
        
        
        pz_t = sigmoid(
                    tf.matmul(x_t, w1) +
                    tf.matmul(h_tm1[:,-self._n_hidden:], w2) +
                    bias
                )
        
        
        pz_t = tf.squeeze(pz_t)
        
        # predict z
        z_t = tf.cast(tf.less_equal(tf.random_uniform(pz_t.get_shape(),
                                                      dtype=tf.float32, seed=seed),
                                                        pz_t),
                                                      tf.float32)
        
        xz_t = tf.concat(1,[x_t, tf.reshape(z_t,  [-1,1])])
        
        # set reuse in rlayer to none
        self.rlayer.reuse = None
        
       
        
        # do a forward pass
        h_t = self.rlayer(h_tm1, xz_t, scope = 'RNN/RCNN_cell_ZLayer',
                          scope2 = 'RNN/RCNN_Feed_Forward_Layer')
        
        return [h_t , tf.expand_dims(z_t, 1)] 
    
    def sample_all(self, x):
        
        # get the variables
        with vs.variable_scope('ZLayerWeights', reuse=True) as var_scope:
            w1 = tf.get_variable('W1', dtype = tf.float32)
            w2 = tf.get_variable('W2', dtype = tf.float32)
            bias = tf.get_variable('Bias', dtype = tf.float32)
        
        h0 = tf.zeros((x.get_shape()[1], self._n_hidden*(self.rlayer._order+1)), dtype = tf.float32)
        z0 = tf.zeros((x.get_shape()[1],), dtype=tf.float32)

        
        h, z = tf.scan(
                    self.sample,
                    x, 
                    initializer = [ h0, tf.expand_dims(z0, 1 )]
                    )
        
        z = tf.squeeze(z, squeeze_dims = [2])
        assert len(z.get_shape()) == 2
        
        return z

###############################
####### Extended RCNN #########
###############################

class ExtRCNNCell(RCNNCell):
    
    # changes made here
    def __call__(self, hc_tm1, x ):
        x_t, mask_t = x[0], x[1]
        hc_t  = super(ExtRCNNCell, self).__call__(hc_tm1, x_t)
        a= mask_t * hc_t 
        b = (1 - mask_t) * hc_tm1    
        hc_t = a + b
        return hc_t


    def copy_params(self, from_obj):
        self.internal_layers = from_obj.internal_layers
        self.bias = from_obj.bias