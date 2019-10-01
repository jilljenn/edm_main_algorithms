# mainly inspired by Théophile Gervet 
# https://github.com/theophilee/kt-algos/blob/master/train_ffw.py

import os
import argparse
import numpy as np
from scipy.sparse import load_npz, csr_matrix
import torch
import torch.nn as nn
from torch.optim import Adam

from utils.logger import Logger
from utils.metrics import Metrics
from FFN import FeedForwardNetwork
from utils.misc import *


def get_tensors(sparse):
    dense = torch.tensor(sparse.toarray())
    inputs = dense[:, 4:].float()
    item_ids = dense[:, 1].long()
    labels = dense[:, 3].float()
    return inputs, item_ids, labels


def train(X_train, X_val, model, optimizer, logger, num_epochs, batch_size):
    """Train FFW model.
    Arguments:
        X (sparse matrix): output by encode_ffw.py
        model (torch Module)
        optimizer (torch optimizer)
        logger: wrapper for TensorboardX logger
        num_epochs (int): number of epochs to train for
        batch_size (int)
    """
    criterion = nn.BCELoss()
    metrics = Metrics()
    train_idxs = np.arange(X_train.shape[0])
    val_idxs = np.arange(X_val.shape[0])
    step = 0

    for epoch in range(num_epochs):
        shuffle(train_idxs)
        shuffle(val_idxs)

        # Training
        for k in range(0, len(train_idxs), batch_size):
            inputs, item_ids, labels = get_tensors(X_train[train_idxs[k:k + batch_size]])
            inputs = inputs.cuda()
            preds = model(inputs)
            relevant_preds = preds[torch.arange(preds.shape[0]), item_ids.cuda()]
            loss = criterion(relevant_preds, labels.cuda())
            
            train_auc = compute_auc(preds.detach().cpu(), item_ids, labels)

            model.zero_grad()
            loss.backward()
            optimizer.step()
            step += 1
            metrics.store({'loss/train': loss.item()})
            metrics.store({'auc/train': train_auc})

            # Logging
            if step % 20 == 0:
                logger.log_scalars(metrics.average(), step * batch_size)

        # Validation
        model.eval()
        for k in range(0, len(val_idxs), batch_size):
            inputs, item_ids, labels = get_tensors(X_val[val_idxs[k:k + batch_size]])
            inputs = inputs.cuda()
            with torch.no_grad():
                preds = model(inputs)
            val_auc = compute_auc(preds.cpu(), item_ids, labels)
            metrics.store({'auc/val': val_auc})
        model.train()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Train feedforward neural network on dense feature matrix.')
    parser.add_argument('X_file', type=str)
    parser.add_argument('--dataset', type=str)
    parser.add_argument('--logdir', type=str, default='runs/ffw')
    parser.add_argument('--hid_size', type=int, default=200)
    parser.add_argument('--drop_prob', type=float, default=0.2)
    parser.add_argument('--batch_size', type=int, default=500)
    parser.add_argument('--lr', type=float, default=1e-3)
    parser.add_argument('--num_epochs', type=int, default=25)
    args = parser.parse_args()

    # First four columns are original dataset
    # then previous interaction encodings and wins/attempts statistics
    X = csr_matrix(load_npz(args.X_file))

    # Student-level train-val split
    user_ids = X[:, 0].toarray().flatten()
    users = np.unique(user_ids)
    np.random.shuffle(users)
    split = int(0.8 * len(users))
    users_train, users_val = users[:split], users[split:]

    df = args.dataset
    X_train = X[np.where(np.isin(user_ids, users_train))]
    X_val = X[np.where(np.isin(user_ids, users_val))]

    n_items = len(np.unique(X[:, 1].toarray()))
    n_skills = len(np.unique(X[:, 2].toarray()))

    model = FeedForwardNetwork(n_skills=n_skills, n_items=n_items, n_counters=2, hidden_dim=args.hid_size, drop_prob=args.drop_prob).cuda()
    optimizer = Adam(model.parameters(), lr=args.lr)

    param_str = f'{args.dataset}'
    logger = Logger(os.path.join(args.logdir, param_str))

    train(X_train, X_val, model, optimizer, logger, args.num_epochs, args.batch_size)

    logger.close()