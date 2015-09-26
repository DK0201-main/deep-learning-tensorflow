from __future__ import print_function

from sklearn.linear_model import LogisticRegression
from copy import copy
import numpy as np
import json

from rbm import RBM
import utils

__author__ = 'blackecho'


class DBN(object):
    """Deep Belief Network implementation.
    """

    def __init__(self, num_layers, *args, **kwargs):
        """Initialization of the Deep Belief Network.
        For supervised learning for classification there are two choices:

        1 - greedy unsupervised learning and then Logistic Regression on top of the last layer
            The methods for supervised learning of LR are fit_cls for training and predict_cls for testing.

        2 - greedy unsupervised learning and then add another RBM that models the joint distribution between data
            and labels, using the wake-sleep algorithm. The methods for supervised training are wake_sleep for
            training and predict_ws for testing.

        :param num_layers: array whose elements are the number of
               units for each layer.
        """
        self.layers = [RBM(num_layers[i], num_layers[i+1]) for i in xrange(len(num_layers)-1)]

        # Logistic Regression classifier on top of the penultime rbm
        self.cls = LogisticRegression(*args, **kwargs)
        # Output units on top of the penultime rbm to train with backpropagation
        self.softmax_output = None
        self.pen_softmax_w = None  # weights from the penultime layer to the softmax output
        # Last layer rbm for supervised training (initialized in wake-sleep algorithm)
        self.last_rbm = None
        # top-down generative weights. They are initialized to be the same as the bottom-up recognition weights
        # in the wake sleep algorithm
        self.top_down_w = None

        # Training performance metrics
        self.errors = []

    def unsupervised_pretrain(self,
                              data,
                              validation=None,
                              epochs=100,
                              batch_size=10,
                              alpha=[0.01],
                              momentum=[0.5],
                              gibbs_k=1,
                              alpha_update_rule='constant',
                              momentum_update_rule='constant',
                              verbose=False,
                              display=None):
        """Unsupervised greedy layer-wise pre-training of the Deep Belief Net.
        :param data: the training set
        :param validation: the validation set
        :param epochs: number of training steps
        :param batch_size: size of each batch
        :param alpha: learning rate
        :param momentum: momentum parameter
        :param gibbs_k: number of gibbs sampling steps
        :param alpha_update_rule: type of update rule for the learning rate. Can be constant,
               linear or exponential
        :param momentum_update_rule: type of update rule for the momentum parameter. Can be constant,
           linear or exponential
        :param verbose: if true display a progress bar through the loop
        :param display: function used to display reconstructed samples
                        after gibbs sampling for each epoch.
                        If batch_size is greater than one, one
                        random sample will be displayed.
        """
        middle_repr = None
        middle_val_repr = None
        for l, rbm in enumerate(self.layers):
            print('########## Training {}* RBM - ( {}, {} ) ##########'.format(l+1, rbm.num_visible, rbm.num_hidden))
            if l == 0:
                rbm.train(data,
                          validation=validation,
                          epochs=epochs,
                          batch_size=batch_size,
                          alpha=alpha,
                          momentum=momentum,
                          gibbs_k=gibbs_k,
                          alpha_update_rule=alpha_update_rule,
                          momentum_update_rule=momentum_update_rule,
                          verbose=verbose,
                          display=display)
                # dataset's representation of the first rbm
                _, middle_repr = rbm.sample_hidden_from_visible(data)
                # validation set representation of the first rbm
                _, middle_val_repr = rbm.sample_hidden_from_visible(validation)
            else:
                # train the next rbm using the representation of the previous rbm as visible layer
                rbm.train(middle_repr,
                          validation=middle_val_repr,
                          epochs=epochs,
                          batch_size=batch_size,
                          alpha=alpha,
                          momentum=momentum,
                          gibbs_k=gibbs_k,
                          alpha_update_rule=alpha_update_rule,
                          momentum_update_rule=momentum_update_rule)
                # features representation of the current rbm
                _, middle_repr = rbm.sample_hidden_from_visible(middle_repr)
                # validation set representation of the first rbm
                _, middle_val_repr = rbm.sample_hidden_from_visible(middle_val_repr)

    def backprop(self,
                 num_softmax,
                 data,
                 y,
                 batch_size=1,
                 epochs=100,
                 alpha=[0.01],
                 momentum=[0.5],
                 alpha_update_rule='constant',
                 momentum_update_rule='constant'):
        """Fine-tuning of the deep belief net using backpropagation algorithm. To be used after
        unsupervised_pretrain.
        :param num_softmax: number of softmax units in the output layer
        :param data: input dataset
        :param y: dataset labels
        :param batch_size: size of each bach
        :param epochs: number of training epochs
        :param alpha: learning rate parameter
        :param momentum: momentum parameter
        :param alpha_update_rule: update rule for the learning rate
        :param momentum_update_rule: update rule for the momentum
        """
        assert data.shape[0] == y.shape[0]

        # add a column of ones to data for the bias
        for i, sample in enumerate(data):
            data[i] = np.insert(sample, 0, 1)

        # Initialize the softmax output layer
        self.softmax_output = np.zeros(num_softmax)
        self.pen_softmax_w = 0.01 * np.random.random(self.layers[-1].num_hidden + 1, num_softmax)

        # convert integer labels to binary vectors
        bin_y = utils.int2binary_vect(y)

        # divide data into batches
        batches = utils.generate_batches(data, batch_size)
        # divide labels into batches
        y_batches = utils.generate_batches(bin_y, batch_size)

        alpha_rule = utils.prepare_parameter_update(alpha_update_rule, alpha, epochs)

        mean_square_error = 0.

        for epoch in xrange(epochs):
            alpha = alpha_rule.update()  # learning rate update
            for i, batch in enumerate(batches):
                targets = y_batches[i]

                # Do a forward pass to compute the output of penultime layer
                middle_out, pen_out = self.forward(data)
                # compute the output of the softmax layer
                net_out_probs = np.dot(pen_out, self.pen_softmax_w)
                net_out = utils.softmax(net_out_probs)

                # Compute mean square error for the batch
                mean_square_error += utils.compute_mean_square_error(net_out, targets)

                # error
                e = targets - net_out

                # softmax layer update
                softmax_delta = e * utils.logistic_dot(net_out_probs)
                # penultime layer weights update
                for j in range(len(self.layers[-1].num_hidden)):
                    for k in range(len(softmax_delta)):
                        self.pen_softmax_w[j][k] += alpha*softmax_delta[k]*pen_out[k]

                # middle layers update
                next_layer_delta = softmax_delta  # initialization
                for l, rbm in reversed(list(enumerate(self.layers))):
                    e = []
                    for v in range(rbm.num_visible):
                        e.append(np.dot(rbm.W[v], next_layer_delta))
                    this_layer_delta = []
                    for v in range(rbm.num_visible):
                        this_layer_delta.append(e[v] * utils.logistic_dot(middle_out[l-1]))
                    for v in range(rbm.num_visible):
                        rbm.W[v] += alpha*this_layer_delta[v]*middle_out[l-1]

            print("Epoch {:d} : cross entropy error is {:f}".format(epoch,
                  mean_square_error))
            self.errors.append(mean_square_error)
            mean_square_error = 0.

    def wake_sleep(self,
                   num_last_layer,
                   data,
                   y,
                   batch_size=1,
                   epochs=100,
                   alpha=[0.01],
                   momentum=[0.5],
                   top_gibbs_k=1,
                   alpha_update_rule='constant',
                   momentum_update_rule='constant'):
        """Fine-tuning of the deep belief net using the wake-sleep algorithm proposed by Hinton et al. 2006.
        :param num_last_layer: number of hidden units for the last RBM
        :param data: input dataset
        :param y: dataset labels
        :param batch_size: size of each bach
        :param epochs: number of training epochs
        :param alpha: learning rate parameter
        :param momentum: momentum parameter
        :param top_gibbs_k: number of gibbs sampling steps using the top level undirected associative
                            memory
        :param alpha_update_rule: update rule for the learning rate
        :param momentum_update_rule: update rule for the momentum
        """
        assert data.shape[0] == y.shape[0]

        # Initialize the top down generative weights to be the same as the recognition weights of the rbm
        # and they are untied, se that their values can become different
        self.top_down_w = copy([rbm.W.T for rbm in self.layers])

        # convert integer labels to binary vectors
        bin_y = utils.int2binary_vect(y)

        # divide data into batches
        batches = utils.generate_batches(data, batch_size)
        # divide labels into batches
        y_batches = utils.generate_batches(bin_y, batch_size)

        num_pen_units = self.layers[-1].num_visible
        # initialize the last layer rbm
        self.last_rbm = RBM(num_pen_units + bin_y[0].shape[0], num_last_layer)

        alpha_rule = utils.prepare_parameter_update(alpha_update_rule, alpha, epochs)

        cross_entropy_error = 0.

        for epoch in xrange(epochs):
            alpha = alpha_rule.update()  # learning rate update
            for i, batch in enumerate(batches):
                targets = y_batches[i]
                # ========== WAKE/POSITIVE PHASE ==========
                # TODO: for now only works with fixed architecture: lab <--> top <--> pen -> hid -> vis
                # TODO: this is the architecture used by Hinton et al. 2006 for MNIST.

                # ===== Bottom-up Pass =====
                wake_hid_probs = utils.logistic(np.dot(batch, self.layers[0].W) + self.layers[0].h_bias)
                wake_hid_states = utils.probs_to_binary(wake_hid_probs)

                wake_pen_probs = utils.logistic(np.dot(wake_hid_states, self.layers[1].W) + self.layers[1].h_bias)
                wake_pen_states = utils.probs_to_binary(wake_pen_probs)

                joint_data = utils.merge_data_labels(wake_pen_states, targets)
                wake_top_probs = utils.logistic(np.dot(joint_data, self.last_rbm.W) + self.last_rbm.h_bias)
                wake_top_states = utils.probs_to_binary(wake_top_probs)

                # ===== Positive phase statistics for contrastive divergence =====
                poslabtopstatistics = np.dot(targets.T, wake_top_states)
                pospentopstatistics = np.dot(wake_pen_states.T, wake_top_states)

                # divide last rbm weights and biases in pen and lab
                pen_w = self.last_rbm.W[:num_pen_units]
                lab_w = self.last_rbm.W[num_pen_units:]
                pen_gen_b = self.last_rbm.v_bias[:num_pen_units]
                lab_gen_b = self.last_rbm.v_bias[num_pen_units:]

                # Perform gibbs sampling using the top level undirected associative memory
                sofmax_values = None  # softmax values used to compute cross entropy error
                neg_top_states = wake_top_states  # initialization
                for j in range(top_gibbs_k):
                    neg_pen_probs = utils.logistic(np.dot(neg_top_states, pen_w.T) + pen_gen_b)
                    neg_pen_states = utils.probs_to_binary(neg_pen_probs)

                    sofmax_values, neg_lab_probs = utils.softmax(np.dot(neg_top_states, lab_w.T) + lab_gen_b)
                    neg_top_probs = utils.logistic(np.dot(neg_pen_states, pen_w) + np.dot(neg_lab_probs, lab_w) +
                                                   self.last_rbm.h_bias)
                    neg_top_states = utils.probs_to_binary(neg_top_probs)

                # Compute cross entropy error for the batch
                cross_entropy_error += utils.compute_cross_entropy_error(sofmax_values, targets)

                # ===== Negative phase statistics for contrastive divergence =====
                negpentopstatistics = np.dot(neg_pen_states.T, neg_top_states)
                neglabtopstatistics = np.dot(neg_lab_probs.T, neg_top_states)

                # Starting from the end of the gibbs sampling run, perform a top-down
                # generative pass to get sleep/negative phase probabilities and sample states
                sleep_pen_states = neg_pen_states
                sleep_hid_probs = utils.logistic(np.dot(sleep_pen_states, self.top_down_w[1]) + self.layers[1].v_bias)
                sleep_hid_states = utils.probs_to_binary(sleep_hid_probs)
                sleep_vis_probs = utils.logistic(np.dot(sleep_hid_states, self.top_down_w[0]) + self.layers[0].v_bias)

                # Predictions
                p_sleep_pen_states = utils.logistic(np.dot(sleep_hid_states, self.layers[1].W) + self.layers[1].h_bias)
                p_sleep_hid_states = utils.logistic(np.dot(sleep_vis_probs, self.layers[0].W) + self.layers[0].h_bias)
                p_vis_probs = utils.logistic(np.dot(wake_hid_states, self.top_down_w[0]) + self.layers[0].v_bias)
                p_hid_probs = utils.logistic(np.dot(wake_pen_states, self.top_down_w[1]) + self.layers[1].h_bias)

                # ===== Updates to Generative Parameters =====
                self.top_down_w[0] += alpha*(np.dot(wake_hid_states.T, batch-p_vis_probs))
                self.layers[0].v_bias += alpha*(batch - p_vis_probs).mean(axis=0)
                self.top_down_w[1] += alpha*(np.dot(wake_pen_states.T, wake_hid_states - p_hid_probs))
                self.layers[1].v_bias += alpha*(wake_hid_states - p_hid_probs).mean(axis=0)

                # ===== Updates to Top level associative memory parameters =====
                self.last_rbm.W[num_pen_units:] += alpha*(poslabtopstatistics - neglabtopstatistics)
                self.last_rbm.v_bias[num_pen_units:] += alpha*(targets - neg_lab_probs).mean(axis=0)
                self.last_rbm.W[:num_pen_units] += alpha*(pospentopstatistics - negpentopstatistics)
                self.last_rbm.v_bias[:num_pen_units] += alpha*(wake_pen_states - neg_pen_states).mean(axis=0)
                self.last_rbm.h_bias += alpha*(wake_top_states - neg_top_states).mean(axis=0)

                # ===== Updates to Recognition/Inference approximation parameters =====
                self.layers[1].W += alpha*(np.dot(sleep_hid_states.T, sleep_pen_states - p_sleep_pen_states))
                self.layers[1].h_bias += alpha*(sleep_pen_states - p_sleep_pen_states).mean(axis=0)
                self.layers[0].W += alpha*(np.dot(sleep_vis_probs.T, sleep_hid_states - p_sleep_hid_states))
                self.layers[0].h_bias += alpha*(sleep_hid_states - p_sleep_hid_states).mean(axis=0)

            print("Epoch {:d} : cross entropy error is {:f}".format(epoch,
                  cross_entropy_error))
            self.errors.append(cross_entropy_error)
            cross_entropy_error = 0.

    def predict_ws(self, data, top_gibbs_k=1):
        """Perform a bottom-up recognition pass and then get a sample of the labels for the test data
        after alternating gibbs sampling on the undirected associative memory of the deep net.
        :param data: test dataset
        :param top_gibbs_k: number of gibbs sampling steps using the top level undirected associative
                            memory
        :return: predicted labels
        """

        num_pen_units = self.layers[-1].num_visible

        # the label units are set to be on with probability 0.1, then, after gibbs sampling, they
        # will converge to the corret label unit
        num_labels = len(self.last_rbm.W[num_pen_units:])
        targets = [(0.1 > np.random.random(num_labels)).astype(np.int) for _ in range(len(data))]
        # ========== WAKE/POSITIVE PHASE ==========
        # TODO: for now only works with fixed architecture: lab <--> top <--> pen -> hid -> vis
        # TODO: this is the architecture used by Hinton et al. 2006 for MNIST.

        # ===== Bottom-up Pass =====
        wake_hid_probs = utils.logistic(np.dot(data, self.layers[0].W) + self.layers[0].h_bias)
        wake_hid_states = utils.probs_to_binary(wake_hid_probs)

        # ############# USING PROBS ##################
        wake_pen_probs = utils.logistic(np.dot(wake_hid_probs, self.layers[1].W) + self.layers[1].h_bias)
        wake_pen_states = utils.probs_to_binary(wake_pen_probs)

        # ############# USING PROBS ##################
        joint_data = utils.merge_data_labels(wake_pen_probs, targets)
        wake_top_probs = utils.logistic(np.dot(joint_data, self.last_rbm.W) + self.last_rbm.h_bias)
        wake_top_states = utils.probs_to_binary(wake_top_probs)

        # divide last rbm weights and biases in pen and lab
        pen_w = self.last_rbm.W[:num_pen_units]
        lab_w = self.last_rbm.W[num_pen_units:]
        # pen_gen_b = self.last_rbm.v_bias[:num_pen_units]
        lab_gen_b = self.last_rbm.v_bias[num_pen_units:]

        # Perform gibbs sampling using the top level undirected associative memory, clamped on
        # pen_state representation
        # ####################### USING PROBS ###########
        neg_top_states = wake_top_probs  # initialization
        for j in range(top_gibbs_k):
            _, neg_lab_probs = utils.softmax(np.dot(neg_top_states, lab_w.T) + lab_gen_b)
            # #################### USING wake pen probs
            neg_top_probs = utils.logistic(np.dot(wake_pen_probs, pen_w) + np.dot(neg_lab_probs, lab_w) +
                                           self.last_rbm.h_bias)
            neg_top_states = neg_top_probs  # utils.probs_to_binary(neg_top_probs)

        return utils.binary2int_vect(neg_lab_probs)

    def predict_bp(self, data):
        """Predicts labels for data using softmax layer trained after backpropagation.
        :param data: test dataset
        :return: predicted labels
        """
        pass

    def fantasy(self, k=1):
        """Generate a sample from the DBN after n steps of gibbs sampling, starting with a
        random sample.
        :param k: number of gibbs sampling steps
        :return: what's in the mind of the DBN
        """
        pass

    def forward(self, data):
        """Do a forward pass through the deep belief net and return the middle layer representations
        as well as the last layer representation.
        :param data: input data to the visible units of the first rbm
        :return middle_reprs, last_repr: middle representations, last representation layer
        """
        middle_reprs = []
        last_repr = None
        aux = None
        for l, rbm in enumerate(self.layers):
            aux, _ = rbm.sample_hidden_from_visible(data) if l == 0 else rbm.sample_hidden_from_visible(aux)
            middle_reprs.append(aux[0])
            if l == len(self.layers) - 1:
                last_repr = aux
        return middle_reprs, last_repr

    def backward(self, data):
        """Do a backward pass through the deep belief net and generate a sample
        according to the model.
        :param data: input data to the hidden units of the last rbm
        :return middle_repr: first representation layer
        """
        middle_repr = None
        for l, rbm in reversed(list(enumerate(self.layers))):
            middle_repr, _ = rbm.sample_visible_from_hidden(data) if l == len(self.layers)-1\
                else rbm.sample_visible_from_hidden(middle_repr)
        return middle_repr

    def fit_cls(self, data, y):
        """Fit classifier for the given supervised dataset.
        :param data: supervised training set for the classification layer.
        """
        out_layer = self.forward(data)
        self.cls.fit(out_layer, y)

    def predict_cls(self, data):
        """Predict the labels for data using the classification layer top of the
        deep belief net.
        :param data: input data to the visible units of the first rbm
        """
        out_layer = self.forward(data)
        return self.cls.predict(out_layer)

    def load_rbms(self, infiles):
        """Load json configuration of trained rbms to initialize the deep net.
        :param infiles: list of input files, one for each rbm
        """
        self.layers = []  # delete previously rbms
        for rbm_file in infiles:
            r = RBM(1, 1)
            r.load_configuration(rbm_file)
            self.layers.append(r)

    def save_performance_metrics(self, outfile):
        """Save a json configuration of the deep net to out file.
        :param: output file
        """
        with open(outfile, 'w') as f:
            f.write(json.dumps({'errors': self.errors}))
