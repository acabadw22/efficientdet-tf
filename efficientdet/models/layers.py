from typing import Tuple

import tensorflow as tf

from efficientdet.config import AnchorsConfig
from efficientdet.utils import bndbox, anchors


class Resize(tf.keras.Model):

    def __init__(self, features: int):
        super(Resize, self).__init__()
        self.antialiasing_conv = ConvBlock(features,
                                           separable=True,
                                           kernel_size=3, 
                                           padding='same')

    def call(self, 
             images: tf.Tensor, 
             target_dim: Tuple[int, int, int, int] = None, 
             training: bool = True) -> tf.Tensor:
        dims = target_dim[1:3]
        x = tf.image.resize(images, dims, method='nearest')
        x = self.antialiasing_conv(x, training=training)
        return x 


class ConvBlock(tf.keras.Model):

    def __init__(self, 
                 features: int = None, 
                 separable: bool = False, 
                 activation: str = None,
                 **kwargs):
        super(ConvBlock, self).__init__()

        if separable:
            self.conv = tf.keras.layers.SeparableConv2D(filters=features, 
                                                        **kwargs)
        else:
            self.conv = tf.keras.layers.Conv2D(features, **kwargs)
        self.bn = tf.keras.layers.BatchNormalization()
        
        if activation == 'swish':
            self.activation = tf.keras.layers.Activation(tf.nn.swish)
        elif activation is not None:
            self.activation = tf.keras.layers.Activation(activation)
        else:
            self.activation = tf.keras.layers.Activation('linear')

    def call(self, x: tf.Tensor, training: bool = True) -> tf.Tensor:
        x = self.bn(self.conv(x), training=training)
        return self.activation(x)


class FilterDetections(object):

    def __init__(self, 
                 anchors_config: AnchorsConfig,
                 score_threshold: float):

        self.score_threshold = score_threshold
        self.anchors_gen = [anchors.AnchorGenerator(
            size=anchors_config.sizes[i - 3],
            aspect_ratios=anchors_config.ratios,
            stride=anchors_config.strides[i - 3]
        ) for i in range(3, 8)] # 3 to 7 pyramid levels

        # Accelerate calls
        self.regress_boxes = tf.function(
            bndbox.regress_bndboxes, input_signature=[
                tf.TensorSpec(shape=[None, None, 4], dtype=tf.float32),
                tf.TensorSpec(shape=[None, None, 4], dtype=tf.float32)])

        self.clip_boxes = tf.function(
            bndbox.clip_boxes, input_signature=[
                tf.TensorSpec(shape=[None, None, 4], dtype=tf.float32),
                tf.TensorSpec(shape=None)])
    
    def __call__(self, 
                 images: tf.Tensor, 
                 regressors: tf.Tensor, 
                 class_scores: tf.Tensor):
        im_shape = tf.shape(images)
        batch_size, h, w = im_shape[0], im_shape[1], im_shape[2]
        num_classes = tf.shape(class_scores)[-1]

        # Create the anchors
        shapes = [w // (2 ** x) for x in range(3, 8)]
        anchors = [g((size, size, 3))
                   for g, size in zip(self.anchors_gen, shapes)]
        anchors = tf.concat(anchors, axis=0)
        
        # Tile anchors over batches, so they can be regressed
        anchors = tf.tile(tf.expand_dims(anchors, 0), [batch_size, 1, 1])

        # Regress anchors and clip in case they go outside of the image
        boxes = self.regress_boxes(anchors, regressors)
        boxes = self.clip_boxes(boxes, [h, w])

        # Supress overlapping detections
        boxes, labels, scores = bndbox.nms(
            boxes, class_scores, score_threshold=self.score_threshold)

        # TODO: Pad output
        return boxes, labels, scores