import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.regularizers import l2
from tensorflow.keras.optimizers import Adam, SGD
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint
from tensorflow.python.util.tf_export import keras_export
from tensorflow.keras.backend import eval

from sklearn import metrics
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import OneHotEncoder, LabelEncoder, RobustScaler, StandardScaler

# import tensorboard
import keras
from keras.utils import tf_utils
import pandas as pd #pd.plotting.register_matplotlib_converters
import numpy as np
import sys, os, math, time, datetime, re

print("tf: ", tf.__version__)
# print("tb: ", tensorboard.__version__)
print(os.getcwd())

RANDOM_SEED = 42
ISMOORE_DATASETS = True
timestep = 40
tf.random.set_seed(np.random.seed(RANDOM_SEED))
tf.get_logger().setLevel('ERROR')
tf.autograph.set_verbosity(1)
tf.config.set_visible_devices([], 'GPU')
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '1'

# snapshot = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
# path = '../../../../Datasets/6_har/0_WISDM/WISDM_ar_v1.1/WISDM_ar_v1.1_processed/WISDM_ar_v1.1_wt_overlap'
# Debugging with Tensorboard
# logdir="logs/fit/rnn_v1_1/" + snapshot
# tensorboard_callback = tf.keras.callbacks.TensorBoard(log_dir=logdir)
# tf.debugging.experimental.enable_dump_debug_info(logdir, tensor_debug_mode="FULL_HEALTH", circular_buffer_size=-1)

with open("../../params/params_har.txt") as f:
    hyperparams = dict([re.sub('['+' ,\n'+']','',x.replace(' .', '')).split('=') for x in f][1:-1])
hyperparams = dict([k, float(v)] for k, v in hyperparams.items())
hyperparams['testSize'] = 0.500
hyperparams['noUnits'] = 81
hyperparams['timestep'] = 40
print(hyperparams)

def seperateValues(data, noInput, noOutput, isMoore=True):
    x_data, y_data = None, None
    for i in range(data.shape[0]):
        if isMoore:
            x_data_i = data[i].reshape(-1, noInput+noOutput)
            x_data_i, y_data_i = x_data_i[:, 0:noInput], x_data_i[-1, noInput:]
        else:
            x_data_i = data[i][:-1].reshape(-1, noInput)
            y_data_i = data[i][-1].reshape(-1, noOutput)
        x_data = x_data_i[np.newaxis,:,:] if x_data is None else np.append(x_data, x_data_i[np.newaxis,:,:], axis=0)
        y_data = y_data_i.reshape(1, -1) if y_data is None else np.append(y_data, y_data_i.reshape(1, -1), axis=0)
    return x_data, y_data

def fromBit_v0( b ) :
    return -0.9 if b == 0.0 else 0.9

def fromBit_v1( b ) :
    return 0 if b == 0.0 else 1

def isCorrect( target, actual ) :
    y1 = False if target < 0.0 else True
    y2 = False if actual < 0.0 else True
    return y1 == y2 

class CustomMetricError(tf.keras.metrics.MeanMetricWrapper):
    def __init__(self, name='custom_metric_error', dtype=None, threshold=0.5):
        super(CustomMetricError, self).__init__(
            customMetricfn_tensor, name, dtype=dtype, threshold=threshold)

    def customMetricfn_tensor(true, pred, threshold=0.5):
        true = tf.convert_to_tensor(true)
        pred = tf.convert_to_tensor(pred)
        threshold = tf.cast(threshold, pred.dtype)
        pred = tf.cast(pred >= threshold, pred.dtype)
        true = tf.cast(true >= threshold, true.dtype)
        return keras.backend.mean(tf.equal(true, pred), axis=-1)

def customMetricfn(y_true, y_pred):
    numCorrect = 0
    for i in range( y_true.shape[0] ) :
        for j in range( y_pred.shape[ 1 ] ) :
            if isCorrect( y_true[ i, j ], y_pred[ i, j ] ) :
                numCorrect += 1
    return (numCorrect/(y_pred.shape[1]*y_true.shape[0]))

def indexOfMax( xs ) :
    m, k = xs[ 0 ], 0
    for i in range( 0, xs.size ) :
        if xs[ i ] > m :
            m = xs[ i ]
            k = i
    return k

def customMetricfn_full(y_true, y_pred):
    numCorrect = 0
    for i in range(y_pred.shape[0]) :
        if indexOfMax(y_pred[i]) == indexOfMax(y_val[i]) :
            numCorrect += 1
    return numCorrect/y_pred.shape[0]
            
def srelu(x):
    return tf.keras.backend.clip(x, -1, 1)

def _generate_zero_filled_state_for_cell(cell, inputs, batch_size, dtype):
    if inputs is not None:
        batch_size = tf.shape(inputs)[0]
        dtype = inputs.dtype
    return _generate_zero_filled_state(batch_size, cell.state_size, dtype)

def _generate_zero_filled_state(batch_size_tensor, state_size, dtype):
    def create_zeros(unnested_state_size):
        flat_dims = tf.TensorShape(unnested_state_size).as_list()
        init_state_size = [batch_size_tensor] + flat_dims
        return tf.zeros(init_state_size, dtype=dtype)
    
    if batch_size_tensor is None or dtype is None:
        raise ValueError(
            'batch_size and dtype cannot be None while constructing initial state. '
            f'Received: batch_size={batch_size_tensor}, dtype={dtype}')

    return tf.nest.map_structure(create_zeros, state_size)  if tf.nest.is_nested(state_size) else create_zeros(state_size)

class RNN_plus_v1_cell(tf.keras.layers.LSTMCell):
    def __init__(self, units, kernel_initializer='glorot_uniform', recurrent_initializer='orthogonal', bias_initializer='zeros', dropout=0., recurrent_dropout=0., use_bias=True, **kwargs):
        if units < 0:
            raise ValueError(f'Received an invalid value for argument `units`, '
                                f'expected a positive integer, got {units}.')
        # By default use cached variable under v2 mode, see b/143699808.
        if tf.compat.v1.executing_eagerly_outside_functions():
            self._enable_caching_device = kwargs.pop('enable_caching_device', True)
        else:
            self._enable_caching_device = kwargs.pop('enable_caching_device', False)
        super(RNN_plus_v1_cell, self).__init__(units, **kwargs)
        self.units = units
        self.state_size = self.units
        self.output_size = self.units
        
        self.kernel_initializer = tf.keras.initializers.get(kernel_initializer)
        self.recurrent_initializer = tf.keras.initializers.get(recurrent_initializer)
        self.aux_initializer = tf.keras.initializers.get('zeros')
        self.bias_initializer = tf.keras.initializers.get(bias_initializer)
        
        self.dropout = min(1., max(0., dropout))
        self.recurrent_dropout = min(1., max(0., recurrent_dropout))
        self.state_size = [self.units, self.units, self.units]
        self.output_size = self.units
        self.use_bias = True
    
    def build(self, input_shape):
        input_dim = input_shape[-1]
        self.kernel = self.add_weight(shape=(input_dim, self.units), name='w_input', initializer=self.kernel_initializer, regularizer=None, constraint=None)
        self.recurrent_kernel = self.add_weight(shape=(self.units, self.units*2), name='w_otherpeeps', initializer=self.recurrent_initializer, regularizer=None, constraint=None)
        self.aux_kernel  = self.add_weight(shape=(1, self.units), name='w_aux', initializer=self.recurrent_initializer, regularizer=None, constraint=None)
        self.bias = self.add_weight( shape=(self.units,), name='b', initializer=self.bias_initializer, regularizer=None, constraint=None) if self.use_bias else None
        self.built = True
        
    def call(self, inputs, states, training=None):
        state0, state1, prev_output = states[0], states[1], states[2]
        
        w_in_0 = self.kernel

        w_op0, w_op1 = tf.split(self.recurrent_kernel, num_or_size_splits=2, axis=1)
        w_op0 = tf.linalg.set_diag(w_op0, np.zeros((self.units,), dtype=int))
        w_op1 = tf.linalg.set_diag(w_op1, np.zeros((self.units,), dtype=int))
        
        w_aux = self.aux_kernel
        
        inputs_0 = tf.keras.backend.dot(inputs, w_in_0)
        if self.bias is not None:
            inputs_0 = tf.keras.backend.bias_add(inputs_0, self.bias)
            
        op0 = tf.keras.backend.dot(state0, w_op0)
        op1 = tf.keras.backend.dot(state0, w_op1)
        
        z  = tf.nn.relu(w_aux[0]*op0 + inputs_0) - state1
        output = srelu(state0*state0 + prev_output)

        return output, [z, op1, output]
    
    def get_initial_state(self, inputs=None, batch_size=None, dtype=None):
        return list(_generate_zero_filled_state_for_cell(self, inputs, batch_size, dtype))

class LearningRateLoggingCallback(tf.keras.callbacks.Callback):
    def on_epoch_begin(self, epoch, logs=None):
        print("Learning Rate: ", self.model.optimizer.learning_rate.lr)
    
    def on_epoch_end(self, epoch, logs=None):
        tf.summary.scalar('learning rate', self.model.optimizer.learning_rate.lr, step=epoch)

class customLRSchedule(tf.keras.optimizers.schedules.LearningRateSchedule):
    def __init__(self, batchSize, initialLearningRate, learningRateDecay, decayDurationFactor, numTrainingSteps, glorotScaleFactor=0.1, orthogonalScaleFactor=0.1, name=None):
        self.batchSize = batchSize
        self.initialLearningRate = initialLearningRate
        self.learningRateDecay = learningRateDecay
        self.decayDurationFactor = decayDurationFactor
        self.glorotScaleFactor = glorotScaleFactor
        self.orthogonalScaleFactor = orthogonalScaleFactor
        self.numTrainingSteps = numTrainingSteps
        self.name = name
        self.T = tf.constant(self.decayDurationFactor * (self.numTrainingSteps/self.batchSize), dtype=tf.float32, name="T")
        self.lr = self.initialLearningRate
    
    def __call__(self, step):
        self.t = tf.cast(step, tf.float32)
        self.lr = tf.cond(self.t > self.T, 
                           lambda: tf.constant(self.learningRateDecay * self.initialLearningRate, dtype=tf.float32),
                           lambda: self.initialLearningRate - (1.0 - self.learningRateDecay) * self.initialLearningRate * self.t / self.T
                          )
        return self.lr
    
    def get_config(self):
        return {
            "initial_learning_rate": self.initialLearningRate,
            "decay_rate": self.learningRateDecay,
            "name": self.name
        }
    
def rnn_plus_model(noInput, noOutput, timestep):
    """Builds a recurrent model."""
    model = tf.keras.Sequential()
    model.add(tf.keras.layers.RNN(cell=RNN_plus_v1_cell(units=hyperparams['noUnits']), input_shape=[timestep, noInput], unroll=False, name='RNNp_layer'))
    model.add(tf.keras.layers.Dense(noInput+noOutput, activation='tanh', name='MLP_layer'))
    model.add(tf.keras.layers.Dense(noOutput))
    optimizer = tf.keras.optimizers.Adam(learning_rate=customLRSchedule(hyperparams['batchSize'], hyperparams['initialLearningRate'], hyperparams['learningRateDecay'], hyperparams['decayDurationFactor'], hyperparams['numTrainingSteps']), \
                                        beta_1=hyperparams['beta1'], beta_2=hyperparams['beta2'], epsilon=hyperparams['epsilon'], amsgrad=False, name="tunedAdam")
    model.compile(optimizer=optimizer, loss = 'mse', run_eagerly=False)
    return model