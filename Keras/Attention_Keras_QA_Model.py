import logging
import os
import random
from random import randint

from keras.layers.core import *
from keras.layers.embeddings import Embedding
from keras.layers.recurrent import LSTM
from keras.models import Graph
from keras.optimizers import RMSprop
from keras.regularizers import l2

logging.basicConfig(format='[%(asctime)s] : [%(levelname)s] : [%(message)s]',
                    level=logging.INFO)


class Attentive_Reader_LSTM(object):

    def __init__(self):
        self.vocab_size = 20000
        self.context_maxlen = 450
        self.question_maxlen = 50
        self.concat_maxlen = self.context_maxlen + self.question_maxlen
        self.embedding_size = 200

        self.layer1_dim = 256
        self.atten_dim = 100
        self.entity_size = 550
        self.dropout_val = 0.1
        self.l2_regularizer = 0.01

    def create_graph(self):

        q_and_a_model = Graph()

        # Story/Context
        q_and_a_model.add_input(name='input_context',
                                input_shape=(self.context_maxlen,),
                                dtype=int)
        q_and_a_model.add_node(Embedding(self.embedding_size,
                                         128,
                                         input_length=self.context_maxlen),
                               name='embedding_context',
                               input='input_context')
        q_and_a_model.add_node(LSTM(self.layer1_dim,
                                    return_sequences=True),
                               name='forward_context',
                               input='embedding_context')
        q_and_a_model.add_node(LSTM(self.layer1_dim,
                                    go_backwards=True,
                                    return_sequences=True),
                               name='backward_context',
                               input='embedding_context')
        q_and_a_model.add_node(Dropout(self.dropout_val),
                               name='merge_context',
                               inputs=['forward_context',
                                       'backward_context'],
                               merge_mode='sum')

        # Query/Question
        q_and_a_model.add_input(name='input_query',
                                input_shape=(self.context_maxlen,),
                                dtype=int)
        q_and_a_model.add_node(Embedding(self.embedding_size,
                                         128,
                                         input_length=self.context_maxlen),
                               name='embedding_query',
                               input='input_query')
        q_and_a_model.add_node(LSTM(self.layer1_dim,
                                    return_sequences=True),
                               name='forward_query',
                               input='embedding_query')
        q_and_a_model.add_node(LSTM(self.layer1_dim,
                                    go_backwards=True,
                                    return_sequences=True),
                               name='backward_query',
                               input='embedding_query')
        q_and_a_model.add_node(Dropout(self.dropout_val),
                               name='merge_query',
                               inputs=['forward_query',
                                       'backward_query'],
                               merge_mode='sum')

        # Attention Module                      )
        q_and_a_model.add_node(Activation('tanh'),
                               name='attention_tanh',
                               inputs=['merge_context',
                                       'merge_query'],
                               merge_mode='sum')
        q_and_a_model.add_node(TimeDistributedDense(self.atten_dim),
                               name='attention_time',
                               input='attention_tanh')
        q_and_a_model.add_node(Activation('softmax'),
                               name='attention_softmax',
                               input='attention_time')
        q_and_a_model.add_node(TimeDistributedDense(self.layer1_dim),
                               name='attention_time2',
                               input='attention_softmax')

        # Attended Layer
        q_and_a_model.add_node(Dropout(self.dropout_val),
                               name='attended',
                               inputs=['merge_context',
                                       'attention_time2'],
                               merge_mode='mul')

        # Output Layer
        q_and_a_model.add_node(Dropout(self.dropout_val),
                               name='attention_a',
                               inputs=['attended',
                                       'merge_query'],
                               merge_mode='concat',
                               concat_axis=-1)

        q_and_a_model.add_node(TimeDistributedMerge(mode='sum'),
                               name='output_dense',
                               input='attention_a')

        q_and_a_model.add_node(Dense(self.entity_size,
                                     activation='softmax',
                                     W_regularizer=l2(self.l2_regularizer)),
                               name='output_dense2',
                               input='output_dense')
        q_and_a_model.add_output(name='output',
                                 input='output_dense2')
        # Print Model
        q_and_a_model.summary()

        # Leaving the Compile Step out
        return q_and_a_model


class QADataset(object):

    def __init__(self, data_path, vocab_file,
                 n_entities, need_sep_token, **kwargs):
        self.provides_sources = ('context', 'question', 'answer', 'candidates')
        self.path = data_path
        self.vocab = ['@entity%d' % i for i in range(n_entities)] + \
                     [w.rstrip('\n') for w in open(vocab_file)] + \
                     ['<UNK>', '@placeholder'] + \
            (['<SEP>'] if need_sep_token else [])
        self.n_entities = n_entities
        self.vocab_size = len(self.vocab)
        self.reverse_vocab = {w: i for i, w in enumerate(self.vocab)}
        super(QADataset, self).__init__(**kwargs)

    def to_word_id(self, w, cand_mapping):
        if w in cand_mapping:
            return cand_mapping[w]
        elif w[:7] == '@entity':
            raise ValueError("Unmapped entity token: %s" % w)
        elif w in self.reverse_vocab:
            return self.reverse_vocab[w]
        else:
            return self.reverse_vocab['<UNK>']

    def to_word_ids(self, s, cand_mapping):
        return np.array([self.to_word_id(x, cand_mapping)
                         for x in s.split(' ')], dtype=np.int32)

    def get_data(self, state=None, request=None):
        if request is None or state is not None:
            raise ValueError(
                "Expected a request (name of a question file) and no state.")

        lines = [l.rstrip('\n')
                 for l in open(os.path.join(self.path, request))]

        ctx = lines[2]
        q = lines[4]
        a = lines[6]
        cand = [s.split(':')[0] for s in lines[8:]]

        entities = range(self.n_entities)
        while len(cand) > len(entities):
            logging.warning("Too many entities (%d) for question: %s, using duplicate entity identifiers"
                            % (len(cand), request))
            entities = entities + entities
        random.shuffle(entities)
        cand_mapping = {t: k for t, k in zip(cand, entities)}

        ctx = self.to_word_ids(ctx, cand_mapping)
        q = self.to_word_ids(q, cand_mapping)
        cand = np.array([self.to_word_id(x, cand_mapping)
                         for x in cand], dtype=np.int32)
        a = np.int32(self.to_word_id(a, cand_mapping))

        if not a < self.n_entities:
            raise ValueError("Invalid answer token %d" % a)
        if not np.all(cand < self.n_entities):
            raise ValueError("Invalid candidate in list %s" % repr(cand))
        if not np.all(ctx < self.vocab_size):
            raise ValueError(
                "Context word id out of bounds: %d" % int(
                    ctx.max()))
        if not np.all(ctx >= 0):
            raise ValueError("Context word id negative: %d" % int(ctx.min()))
        if not np.all(q < self.vocab_size):
            raise ValueError(
                "Question word id out of bounds: %d" % int(
                    q.max()))
        if not np.all(q >= 0):
            raise ValueError("Question word id negative: %d" % int(q.min()))

        return (ctx, q, a, cand)


class QAIterator(object):

    def __init__(self, path, QA_dataset, batch_n, **kwargs):
        self.path = path
        self.files = [f for f in os.listdir(self.path)
                      if os.path.isfile(os.path.join(self.path, f))]
        self.QA_dataset = QA_dataset
        self.batch_n = batch_n

    def select(self, data):
        if data != []:
            index = randint(0, len(data) - 1)
            elem = data[index]
            data[index] = data[-1]
            del data[-1]
            return elem
        else:
            return data[0]

    def selection(self, data, size):
        if data.shape[0] < size:
            new_data = np.zeros((size,))
            new_data[:data.shape[0]] = data
        else:
            new_data = data[:size]
        return new_data

    def get_request_iterator(self):

        batch_ctx, batch_q, batch_a = np.zeros((self.batch_n, 450)), \
            np.zeros((self.batch_n, 50)), \
            np.zeros((self.batch_n, 550))

        for row_val in np.arange(self.batch_n):

            if len(self.files) == 0:
                self.files = [f for f in os.listdir(self.path)
                              if os.path.isfile(os.path.join(self.path, f))]

            file_n = self.select(self.files)

            (ctx, q, a, cand) = self.QA_dataset.get_data(request=file_n)

            batch_ctx[row_val] = self.selection(ctx, 450)
            batch_q[row_val] = self.selection(ctx, 50)
            batch_a[row_val][a.item()] = 10

        # Ensure the Correct Type
        batch_ctx = batch_ctx.astype(int)
        batch_q = batch_q.astype(int)
        batch_a = batch_a.astype(int)

        # Return Type
        return (batch_ctx, batch_q, batch_a)


# Parameters:
dataset = '/home/dan1/Desktop/Subversion/trunk/NewsAnalytics/Q&A/deepmind-qa'
dataset_name = 'cnn'
batch_size = 8
n_entities = 550
epoch_count = 1
n_recursions = np.arange(2)
vocab_file = '/home/dan1/Desktop/Subversion/trunk/NewsAnalytics' \
             '/Q&A/deepmind-qa/cnn/stats/training/vocab.txt'
# Where Mini_test is approximately 1100 files
data_path = os.path.join(dataset,
                         dataset_name,
                         "questions",
                         "mini_test")


# Add Iterators and Models
model = Attentive_Reader_LSTM().create_graph()
QA_dataset = QADataset(data_path=data_path,
                       vocab_file=vocab_file,
                       n_entities=n_entities,
                       need_sep_token=False)
QAIterator_ = QAIterator(path=data_path,
                         QA_dataset=QA_dataset,
                         batch_n=batch_size)
n_files = len(QAIterator_.files)


# Compile Model
model.compile(optimizer=RMSprop(lr=5e-5),
              loss={'output': 'categorical_crossentropy'})

for recursion in n_recursions:
    # Print Progress
    if recursion % (n_files / batch_size) == 0 and recursion > 0:
        logging.info('Epochs Complete: {}'.format(epoch_count))
        epoch_count += 1
    # Get a Batch of Data
    (batch_ctx, batch_q, batch_a) = QAIterator_.get_request_iterator()
    model.train_on_batch(data={'input_context': batch_ctx,
                               'input_query': batch_q,
                               'output': batch_a}, accuracy=False)