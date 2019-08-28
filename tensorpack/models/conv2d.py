# Copyright 2019 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# -*- coding: utf-8 -*-
# File: conv2d.py


import tensorflow as tf

from ..tfutils.common import get_tf_version_tuple
from ..utils.argtools import get_data_format, shape2d, shape4d
from .common import VariableHolder, layer_register
from .tflayer import convert_to_tflayer_args, rename_get_variable

__all__ = ['Conv2D', 'Deconv2D', 'Conv2DTranspose']

def float32_variable_storage_getter(getter, name, shape=None, dtype=None,
                                    initializer=None, regularizer=None,
                                    trainable=True,
                                    *args, **kwargs):
    """Custom variable getter that forces trainable variables to be stored in
       float32 precision and then casts them to the training precision.
    """
    storage_dtype = tf.float32 if trainable else dtype
    variable = getter(name, shape, dtype=storage_dtype,
                      initializer=initializer, regularizer=regularizer,
                      trainable=trainable,
                      *args, **kwargs)
    if trainable and dtype != tf.float32:
        variable = tf.cast(variable, dtype)
    return variable

@layer_register(log_shape=True)
@convert_to_tflayer_args(
    args_names=['filters', 'kernel_size'],
    name_mapping={
        'out_channel': 'filters',
        'kernel_shape': 'kernel_size',
        'stride': 'strides',
    })
def Conv2D(
        inputs,
        filters,
        kernel_size,
        strides=(1, 1),
        padding='same',
        data_format='channels_last',
        dilation_rate=(1, 1),
        activation=None,
        use_bias=True,
        kernel_initializer=None,
        bias_initializer=tf.zeros_initializer(),
        kernel_regularizer=None,
        bias_regularizer=None,
        activity_regularizer=None,
        split=1,
        seed=None):
    """
    A wrapper around `tf.layers.Conv2D`.
    Some differences to maintain backward-compatibility:

    1. Default kernel initializer is variance_scaling_initializer(2.0).
    2. Default padding is 'same'.
    3. Support 'split' argument to do group conv. Note that this is not efficient.

    Variable Names:

    * ``W``: weights
    * ``b``: bias
    """
    if kernel_initializer is None:
        if get_tf_version_tuple() <= (1, 12):
            kernel_initializer = tf.contrib.layers.variance_scaling_initializer(2.0, seed=seed)
        else:
            kernel_initializer = tf.keras.initializers.VarianceScaling(2.0, distribution='untruncated_normal', seed=seed)
    dilation_rate = shape2d(dilation_rate)

    if split == 1 and dilation_rate == [1, 1]:
        # tf.layers.Conv2D has bugs with dilations (https://github.com/tensorflow/tensorflow/issues/26797)
        with rename_get_variable({'kernel': 'W', 'bias': 'b'}):
            layer = tf.layers.Conv2D(
                filters,
                kernel_size,
                strides=strides,
                padding=padding,
                data_format=data_format,
                dilation_rate=dilation_rate,
                activation=activation,
                use_bias=use_bias,
                kernel_initializer=kernel_initializer,
                bias_initializer=bias_initializer,
                kernel_regularizer=kernel_regularizer,
                bias_regularizer=bias_regularizer,
                activity_regularizer=activity_regularizer,
                _reuse=tf.get_variable_scope().reuse)
            ret = layer.apply(inputs, scope=tf.get_variable_scope())
            ret = tf.identity(ret, name='output')

        ret.variables = VariableHolder(W=layer.kernel)
        if use_bias:
            ret.variables.b = layer.bias

    else:
        # group conv implementation
        data_format = get_data_format(data_format, tfmode=False)
        in_shape = inputs.get_shape().as_list()
        channel_axis = 3 if data_format == 'NHWC' else 1
        in_channel = in_shape[channel_axis]
        assert in_channel is not None, "[Conv2D] Input cannot have unknown channel!"
        assert in_channel % split == 0

        assert kernel_regularizer is None and bias_regularizer is None and activity_regularizer is None, \
            "Not supported by group conv or dilated conv!"

        out_channel = filters
        assert out_channel % split == 0
        assert dilation_rate == [1, 1] or get_tf_version_tuple() >= (1, 5), 'TF>=1.5 required for dilated conv.'

        kernel_shape = shape2d(kernel_size)
        filter_shape = kernel_shape + [in_channel / split, out_channel]
        stride = shape4d(strides, data_format=data_format)

        kwargs = dict(data_format=data_format)
        if get_tf_version_tuple() >= (1, 5):
            kwargs['dilations'] = shape4d(dilation_rate, data_format=data_format)

        W = tf.get_variable(
            'W', filter_shape, initializer=kernel_initializer)

        if use_bias:
            b = tf.get_variable('b', [out_channel], initializer=bias_initializer)

        if split == 1:
            conv = tf.nn.conv2d(inputs, W, stride, padding.upper(), **kwargs)
        else:
            conv = None
            if get_tf_version_tuple() >= (1, 13):
                try:
                    conv = tf.nn.conv2d(inputs, W, stride, padding.upper(), **kwargs)
                except ValueError:
                    log_once("CUDNN group convolution support is only available with "
                             "https://github.com/tensorflow/tensorflow/pull/25818 . "
                             "Will fall back to a loop-based slow implementation instead!", 'warn')
            if conv is None:
                inputs = tf.split(inputs, split, channel_axis)
                kernels = tf.split(W, split, 3)
                outputs = [tf.nn.conv2d(i, k, stride, padding.upper(), **kwargs)
                           for i, k in zip(inputs, kernels)]
                conv = tf.concat(outputs, channel_axis)

        ret = tf.nn.bias_add(conv, b, data_format=data_format) if use_bias else conv
        if activation is not None:
            ret = activation(ret)
        ret = tf.identity(ret, name='output')

        ret.variables = VariableHolder(W=W)
        if use_bias:
            ret.variables.b = b
    return ret


@layer_register(log_shape=True)
@convert_to_tflayer_args(
    args_names=['filters', 'kernel_size', 'strides'],
    name_mapping={
        'out_channel': 'filters',
        'kernel_shape': 'kernel_size',
        'stride': 'strides',
    })
def Conv2DTranspose(
        inputs,
        filters,
        kernel_size,
        strides=(1, 1),
        padding='same',
        data_format='channels_last',
        activation=None,
        use_bias=True,
        kernel_initializer=None,
        bias_initializer=tf.zeros_initializer(),
        kernel_regularizer=None,
        bias_regularizer=None,
        activity_regularizer=None,
        seed=None):
    """
    A wrapper around `tf.layers.Conv2DTranspose`.
    Some differences to maintain backward-compatibility:

    1. Default kernel initializer is variance_scaling_initializer(2.0).
    2. Default padding is 'same'

    Variable Names:

    * ``W``: weights
    * ``b``: bias
    """
    if kernel_initializer is None:
        if get_tf_version_tuple() <= (1, 12):
            kernel_initializer = tf.contrib.layers.variance_scaling_initializer(2.0, seed=seed)
        else:
            kernel_initializer = tf.keras.initializers.VarianceScaling(2.0, distribution='untruncated_normal', seed=seed)

    if get_tf_version_tuple() <= (1, 12):
        with rename_get_variable({'kernel': 'W', 'bias': 'b'}):
            layer = tf.layers.Conv2DTranspose(
                filters,
                kernel_size,
                strides=strides,
                padding=padding,
                data_format=data_format,
                activation=activation,
                use_bias=use_bias,
                kernel_initializer=kernel_initializer,
                bias_initializer=bias_initializer,
                kernel_regularizer=kernel_regularizer,
                bias_regularizer=bias_regularizer,
                activity_regularizer=activity_regularizer,
                _reuse=tf.get_variable_scope().reuse)
            ret = layer.apply(inputs, scope=tf.get_variable_scope())
            ret = tf.identity(ret, name='output')
        ret.variables = VariableHolder(W=layer.kernel)
        if use_bias:
            ret.variables.b = layer.bias
    else:
        # Our own implementation, to avoid Keras bugs. https://github.com/tensorflow/tensorflow/issues/25946
        assert kernel_regularizer is None and bias_regularizer is None and activity_regularizer is None, \
            "Unsupported arguments due to Keras bug in TensorFlow 1.13"
        data_format = get_data_format(data_format, tfmode=False)
        shape_dyn = tf.shape(inputs)
        strides2d = shape2d(strides)
        channels_in = inputs.shape[1 if data_format == 'NCHW' else 3]
        if data_format == 'NCHW':
            channels_in = inputs.shape[1]
            out_shape_dyn = tf.stack(
                [shape_dyn[0], filters,
                 shape_dyn[2] * strides2d[0],
                 shape_dyn[3] * strides2d[1]])
            out_shape3_sta = [filters,
                              None if inputs.shape[2] is None else inputs.shape[2] * strides2d[0],
                              None if inputs.shape[3] is None else inputs.shape[3] * strides2d[1]]
        else:
            channels_in = inputs.shape[-1]
            out_shape_dyn = tf.stack(
                [shape_dyn[0],
                 shape_dyn[1] * strides2d[0],
                 shape_dyn[2] * strides2d[1],
                 filters])
            out_shape3_sta = [None if inputs.shape[1] is None else inputs.shape[1] * strides2d[0],
                              None if inputs.shape[2] is None else inputs.shape[2] * strides2d[1],
                              filters]

        kernel_shape = shape2d(kernel_size)
        with tf.variable_scope(tf.get_variable_scope(),, custom_getter=float32_variable_storage_getter(dtype=tf.float16)):
            W = tf.get_variable('W', kernel_shape + [filters, channels_in], initializer=kernel_initializer)
        if use_bias:
            with tf.variable_scope(tf.get_variable_scope(),, custom_getter=float32_variable_storage_getter(dtype=tf.float16)):
                b = tf.get_variable('b', [filters], initializer=bias_initializer)
        conv = tf.nn.conv2d_transpose(
            inputs, W, out_shape_dyn,
            shape4d(strides, data_format=data_format),
            padding=padding.upper(),
            data_format=data_format)
        conv.set_shape(tf.TensorShape([None] + out_shape3_sta))

        ret = tf.nn.bias_add(conv, b, data_format=data_format) if use_bias else conv
        if activation is not None:
            ret = activation(ret)
        ret = tf.identity(ret, name='output')

        ret.variables = VariableHolder(W=W)
        if use_bias:
            ret.variables.b = b

    return ret


Deconv2D = Conv2DTranspose
