import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import Input, Dense, Conv2D, LSTM, MaxPooling2D,AveragePooling2D,GlobalMaxPooling2D, GlobalAveragePooling2D, Flatten, Dropout, Reshape, BatchNormalization, ReLU
from tensorflow.keras.regularizers import l2
from tensorflow.keras.optimizers import Adam, SGD
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint
import tensorboard
import kerastuner as kt #(kt.tuners.RandomSearch, kt.tuners.Hyperband)
from kerastuner_tensorboard_logger import (
    TensorBoardLogger,
    setup_tb  # Optional
)
from sklearn import metrics
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import OneHotEncoder, LabelEncoder, RobustScaler

from functools import partial
from matplotlib import rc, style
import matplotlib.pyplot as plt
import seaborn as sns
from pylab import rcParams
import pandas as pd #pd.plotting.register_matplotlib_converters
import numpy as np
from scipy import stats

import sys, os, math, time, datetime

print("kt: ", kt.__version__)
print("tf: ", tf.__version__)
print(os.getcwd())

RANDOM_SEED = 42

np.random.seed(RANDOM_SEED)
tf.random.set_seed(RANDOM_SEED)
tf.get_logger().setLevel('ERROR')
tf.autograph.set_verbosity(1)

snapshot = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
path = '../Version9.128timesteps'
fileslist = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
FILESNUMBER = 1
LSTMNUMBER  = 1
def seperateValues(data):
    x_data, y_data = None, None
    for i in range(data.shape[0]):
        x_data_i = data[i].reshape(-1, noInput+noOutput).astype('float32')
        x_data_i, y_data_i = x_data_i[:, 0:noInput], x_data_i[-1, noInput:]
        x_data = x_data_i[np.newaxis,:,:] if x_data is None else np.append(x_data, x_data_i[np.newaxis,:,:], axis=0)
        y_data = y_data_i.reshape(1, -1) if y_data is None else np.append(y_data, y_data_i.reshape(1, -1), axis=0)
    return x_data, y_data
   
val_performance, train_time, training_history = {}, {}, {}
for i in range(FILESNUMBER):
    # Getting data from csv file
    filepath = os.path.join(path,fileslist[i])
    with open(filepath, "r") as fp:
        [noInput, noOutput] = [int(x) for x in fp.readline().split(',')]
    
    print('+++', fileslist[i])
    rdf = np.array(pd.read_csv(filepath, skiprows=1))

    print('Step 1: Dividing the training and testing set with ratio 1:1 (50%).')
    df_val, df_train = train_test_split(rdf,test_size=0.5)
    print(df_train.shape, df_val.shape)

    print('Step 2: Separating values and labels.')
    x_train, y_train = seperateValues(df_train)
    x_val, y_val = seperateValues(df_val)
    print("+ Training set:   ", x_train.shape, y_train.shape, x_train.dtype)
    print("+ Validating set: ", x_val.shape, y_val.shape, x_val.dtype)
    
    print('Step 3: Defining a LSTM model for tuning.')
    log_dir_lstm_v1 = "../logs//hparams//" + snapshot
    lstm_af = ['relu', 'sigmoid', 'tanh', 'softmax', 'softsign', 'selu', 'elu']
    lstm_raf = ['sigmoid']
    dense_af = ['sigmoid']
    loss_methods = ['mse']
    learning_rates = [1e-2, 1e-3, 1e-4]
    thresholds = [0.5, 0.51, 0.6, 0.7]
    def tunner_lstm_model_v1(hp):
        """Builds a recurrent model."""
        model = tf.keras.Sequential()
        model.add(tf.keras.layers.Input(shape=(x_train.shape[1], x_train.shape[2])))
        model.add(tf.keras.layers.LSTM(units=8, 
                       activation=hp.Choice('af_LSTM', lstm_af),
                       recurrent_activation=hp.Choice('raf_LSTM', lstm_raf),
                       unroll =False,
                       use_bias=True,
                       recurrent_dropout=0,
                       return_sequences=False))

        model.add(tf.keras.layers.Dense(y_train.shape[1], hp.Choice('af_dense', dense_af)))
        if (hp.Choice('optimizer', ['adam', 'sgd']) == 'adam'):
            optimizer = tf.keras.optimizers.Adam(hp.Choice('learning_rate', values=learning_rates))
        else:
            optimizer = tf.keras.optimizers.SGD(hp.Choice('learning_rate', values=learning_rates))
        model.compile(optimizer=optimizer,
                      loss=loss_methods,
                      metrics=[tf.keras.metrics.BinaryAccuracy(threshold=hp.Choice('thresholds_BA', thresholds)),
                               tf.keras.metrics.Precision(name='precision'),
                               tf.keras.metrics.Recall(name='recall')
                              ])
        return model

    lstm_tuner_v1=kt.tuners.Hyperband(
        tunner_lstm_model_v1,
        objective=kt.Objective('val_binary_accuracy', direction='max'),
        max_epochs=8,
        seed=42,
        factor=3,
        hyperband_iterations=1,
        directory="../logs/tuner/",
        project_name="MasterProject",
        overwrite=True,
        logger=TensorBoardLogger(
            metrics=['loss', 'binary_accuracy', 'val_loss', 'val_binary_accuracy', 'precision', 'recall'], logdir=log_dir_lstm_v1,
        ) # add only this argument
    )
    
    print('Step 4: Tuning ...')
    setup_tb(lstm_tuner_v1)  # (Optional) For more accurate visualization.
    lstm_tuner_v1.search(x_train, y_train,
                 epochs=8,
                 batch_size=1,
                 validation_data=(x_val, y_val),
                 shuffle=True,
                 use_multiprocessing=True,
                 callbacks=[tf.keras.callbacks.EarlyStopping("val_binary_accuracy")]
                )
    tensorboard_callback = tf.keras.callbacks.TensorBoard(log_dir=log_dir_lstm_v1, profile_batch = '500,520')
    bestparams_v1 = lstm_tuner_v1.get_best_hyperparameters(1)[0]
    hyper_model_v1 = lstm_tuner_v1.hypermodel.build(bestparams_v1)

    strt_time = datetime.datetime.now() 
    training_history_v1 = hyper_model_v1.fit(
        x_train, # input
        y_train, # output
        batch_size=1,
        verbose=0, # Suppress chatty output; use Tensorboard instead
        epochs=8,
        validation_data=(x_val, y_val),
        callbacks=[tensorboard_callback])
    curr_time = datetime.datetime.now()
    timedelta = curr_time - strt_time
    val_performance = hyper_model_v1.evaluate(x_val, y_val)
    tuning_result = {**{'project': lstm_tuner_v1.project_name},
                     **{'log_dir': lstm_tuner_v1.project_dir},
                     **{'dataset_no': fileslist[i].split('.')[6]},
                     **{'objectives': '{}, {}'.format(lstm_tuner_v1.oracle.objective.name, lstm_tuner_v1.oracle.objective.direction)},
                     **(lstm_tuner_v1.oracle.get_best_trials(1)[0].hyperparameters.values), 
                     **{'tuned_score': round(lstm_tuner_v1.oracle.get_best_trials(1)[0].score, 5)},
                     **{'loss': round(val_performance[0],5)},
                     **{'binary_accuracy': round(val_performance[1],5)},
                     **{'precision': round(val_performance[2],5)},
                     **{'recall': round(val_performance[3],5)},
                     **{'training_time (ms)': round(timedelta.total_seconds(),5)},
                    }
    [tuning_result.pop(key, None) for key in ['tuner/initial_epoch', 'tuner/bracket', 'tuner/round']]
    tuning_results.append(tuning_result)

df = (pd.DataFrame(tuning_results))
df.to_csv('../results/tuning/TuningResult_MSE_{}.csv'.format(snapshot), index=False)