import __init__
from ogb.nodeproppred import Evaluator
import torch
import torch.nn.functional as F
from torch_geometric.utils import to_undirected, add_self_loops
from args import ArgsInit
from ogb.nodeproppred import PygNodePropPredDataset
from model import DeeperGCN, count_parameters
from utils.ckpt_util import save_ckpt
import logging
import time,os

import cloudpickle as pickle

def save_obj(obj, name ):
    with open(name + '.pkl', 'wb') as f:
        pickle.dump(obj, f)
        
def load_obj(name):
    with open(name, 'rb') as f:
        return pickle.load(f)

@torch.no_grad()
def test(model, x, edge_index, y_true, split_idx, evaluator):
    model.eval()
    out = model(x, edge_index)

    y_pred = out.argmax(dim=-1, keepdim=True)

    train_acc = evaluator.eval({
        'y_true': y_true[split_idx['train']],
        'y_pred': y_pred[split_idx['train']],
    })['acc']
    valid_acc = evaluator.eval({
        'y_true': y_true[split_idx['valid']],
        'y_pred': y_pred[split_idx['valid']],
    })['acc']
    test_acc = evaluator.eval({
        'y_true': y_true[split_idx['test']],
        'y_pred': y_pred[split_idx['test']],
    })['acc']

    return train_acc, valid_acc, test_acc


def train(model, x, edge_index, y_true, train_idx, optimizer):
    model.train()

    optimizer.zero_grad()

    pred = model(x, edge_index)[train_idx]

    loss = F.nll_loss(pred, y_true.squeeze(1)[train_idx])
    loss.backward()
    optimizer.step()

    return loss.item()


def main():

    args = ArgsInit().save_exp()
    logging.getLogger().setLevel(logging.INFO)

    if args.use_gpu:
        device = torch.device("cuda:" + str(args.device)) if torch.cuda.is_available() else torch.device("cpu")
    else:
        device = torch.device('cpu')

    # fix the random seed
    torch.manual_seed(args.random_seed)

    dataset = PygNodePropPredDataset(name=args.dataset, root=args.datadir)
    data = dataset[0]
    print('data', data)
    print('data edges', data.edge_index)
    print('data num nodes', data.num_nodes)
    #data.num_nodes = torch.tensor(data.num_nodes).to(device)

    split_idx = dataset.get_idx_split()

    evaluator = Evaluator(args.dataset)

    x = data.x.to(device)
    y_true = data.y.to(device)
    train_idx = split_idx['train'].to(device)

    edge_index = data.edge_index.to(device)
    edge_index = to_undirected(edge_index, num_nodes=data.num_nodes)

    if args.self_loop:
        edge_index = add_self_loops(edge_index, num_nodes=data.num_nodes)[0]

    sub_dir = 'SL_{}'.format(args.self_loop)

    args.in_channels = data.x.size(-1)
    args.num_tasks = dataset.num_classes

    logging.info('%s' % args)

    model = DeeperGCN(args).to(device)

    # print out model complexity
    print("-----")
    print("number of learnable parameters in model: ", count_parameters(model))
    print("-----")

    logging.info(model)

    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)

    results = {'highest_valid': 0,
               'final_train': 0,
               'final_test': 0,
               'highest_train': 0}
    
    # training history
    history = {
        "train_acc": [],
        "valid_acc": [],
        "test_acc": [],
        "losses": []
    }

    start_time = time.time()

    for epoch in range(1, args.epochs + 1):

        epoch_loss = train(model, x, edge_index, y_true, train_idx, optimizer)
        logging.info('Epoch {}, training loss {:.4f}'.format(epoch, epoch_loss))
        model.print_params(epoch=epoch)

        result = test(model, x, edge_index, y_true, split_idx, evaluator)
        logging.info("%s" % results)
        train_accuracy, valid_accuracy, test_accuracy = result

        if train_accuracy > results['highest_train']:
            results['highest_train'] = train_accuracy

        if valid_accuracy > results['highest_valid']:
            results['highest_valid'] = valid_accuracy
            results['final_train'] = train_accuracy
            results['final_test'] = test_accuracy

            save_ckpt(model, optimizer,
                      round(epoch_loss, 4), epoch,
                      args.model_save_path,
                      sub_dir, name_post='valid_best')
            
        history['train_acc'].append(train_accuracy)
        history['valid_acc'].append(valid_accuracy)
        history['test_acc'].append(test_accuracy)

        # save the training history as we go
        save_obj(history, name=args.save + "/history")

    logging.info("%s" % results)

    end_time = time.time()
    total_time = end_time - start_time
    logging.info('Total time: {}'.format(time.strftime('%H:%M:%S', time.gmtime(total_time))))


if __name__ == "__main__":
    main()