import argparse
import os
import random
import shutil
import time
import warnings
import torch
import torch.nn as nn
import torch.nn.parallel
import torch.backends.cudnn as cudnn
import torch.distributed as dist
import torch.optim
import torch.utils.data
import torch.utils.data.distributed
import vision.torchvision.transforms as transforms
from . import resnet_lst_cifar as rr
from torchvision.datasets import CIFAR100, CIFAR10

model_names = sorted(name for name in rr.__dict__
    if name.islower() and not name.startswith("__")
    and callable(rr.__dict__[name]))

parser = argparse.ArgumentParser(description='PyTorch CIFAR Training')
parser.add_argument('--arch', '-a', metavar='ARCH', default='resnet164_lst_cifar',
                    choices=model_names,
                    help='model architecture: ' +
                        ' | '.join(model_names) +
                        ' (default: resnet164_lst_cifar)')
parser.add_argument('-j', '--workers', default=8, type=int, metavar='N',
                    help='number of data loading workers (default: 8)')
parser.add_argument('--epochs', default=160, type=int, metavar='N',
                    help='number of total epochs to run (default: 160)')
parser.add_argument('--start-epoch', default=0, type=int, metavar='N',
                    help='manual epoch number (useful on restarts)')
parser.add_argument('-b', '--batch-size', default=128, type=int,
                    metavar='N', help='mini-batch size (default: 128)')
parser.add_argument('--lr', '--learning-rate', default=0.1, type=float,
                    metavar='LR', help='initial learning rate')
parser.add_argument('--momentum', default=0.9, type=float, metavar='M',
                    help='momentum')
parser.add_argument('--weight-decay', '--wd', default=5e-4, type=float,
                    metavar='W', help='weight decay (default: 5e-4)')
parser.add_argument('--print-freq', '-p', default=100, type=int,
                    metavar='N', help='print frequency (default: 100)')
parser.add_argument('--resume', default='', type=str, metavar='PATH',
                    help='path to latest checkpoint (default: none)')
parser.add_argument('-e', '--evaluate', dest='evaluate', action='store_true',
                    help='evaluate model on validation set')
parser.add_argument('--pretrained', dest='pretrained', action='store_true',
                    help='use pre-trained model')
parser.add_argument('--world-size', default=1, type=int,
                    help='number of distributed processes')
parser.add_argument('--dist-url', default='tcp://224.66.41.62:23456', type=str,
                    help='url used to set up distributed training')
parser.add_argument('--dist-backend', default='gloo', type=str,
                    help='distributed backend')
parser.add_argument('--seed', default=None, type=int,
                    help='seed for initializing training. ')
parser.add_argument('--gpu', default=None, type=int,
                    help='GPU id to use.')
parser.add_argument('--ctype', default=100, type=int, help='100(CIFAR-100, default) or 10(CIFAR-10)')
parser.add_argument('--output_dir', default='./', type=str, help='where to save the model (default: current directory)')
parser.add_argument('--lst_k',default=3,type=int, help='kernel size of a bottleneck (default: 3)')
parser.add_argument('--lst_a',default=2,type=int, help='kernel size of a bottleneck (default: 2)')
parser.add_argument('--tau', default=1e-4, type=float, help='HT/ST threshold (default:1e-4)')

best_prec1 = 0

def main():
    global args, best_prec1
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)
        torch.manual_seed(args.seed)
        cudnn.deterministic = True
        warnings.warn('You have chosen to seed training. '
                      'This will turn on the CUDNN deterministic setting, '
                      'which can slow down your training considerably! '
                      'You may see unexpected behavior when restarting '
                      'from checkpoints.')

    if args.gpu is not None:
        warnings.warn('You have chosen a specific GPU. This will completely '
                      'disable data parallelism.')

    args.distributed = args.world_size > 1

    if args.distributed:
        dist.init_process_group(backend=args.dist_backend, init_method=args.dist_url,
                                world_size=args.world_size)

    # create model
    #print(len(glob.glob(os.path.join(args.data, 'train', '*/'))))
    if args.pretrained:
        print("=> using pre-trained model '{}'".format(args.arch))
    else:
        print("=> creating model '{}'".format(args.arch))

    if not(os.path.exists(args.output_dir)):
        os.mkdir(args.output_dir)

    fid_train = open(os.path.join(args.output_dir, 'train.txt'), 'at+')
    fid_val = open(os.path.join(args.output_dir, 'val.txt'), 'at+')
    
    model = rr.__dict__[args.arch](num_classes=args.ctype, k=args.lst_k, a=args.lst_a, tau=args.tau)

    if args.gpu is not None:
        model = model.cuda(args.gpu)
    elif args.distributed:
        model.cuda()
        model = torch.nn.parallel.DistributedDataParallel(model)
    else:
        if args.arch.startswith('alexnet') or args.arch.startswith('vgg'):
            model.features = torch.nn.DataParallel(model.features)
            model.cuda()
        else:
            model = torch.nn.DataParallel(model).cuda()

    # define loss function (criterion) and optimizer
    criterion = nn.CrossEntropyLoss().cuda(args.gpu)

    optimizer = torch.optim.SGD(model.parameters(), args.lr,
                                momentum=args.momentum,
                                weight_decay=args.weight_decay)

    # optionally resume from a checkpoint
    if args.resume:
        if os.path.isfile(args.resume):
            print("=> loading checkpoint '{}'".format(args.resume))
            checkpoint = torch.load(args.resume)
            args.start_epoch = checkpoint['epoch']
            best_prec1 = checkpoint['best_prec1']
            model.load_state_dict(checkpoint['state_dict'])
            optimizer.load_state_dict(checkpoint['optimizer'])
            print("=> loaded checkpoint '{}' (epoch {})"
                  .format(args.resume, checkpoint['epoch']))
        else:
            print("=> no checkpoint found at '{}'".format(args.resume))

    cudnn.benchmark = True

    # Data loading code
    if args.ctype == 10:
        # Cifar-10 normalize
        normalize = transforms.Normalize(mean=[0.4914, 0.4822, 0.4465], std=[0.2023, 0.1994, 0.2010])
        cifar_class = CIFAR10
    elif args.ctype == 100:
        # Cifar-100 normalize
        normalize = transforms.Normalize(mean=[0.5071, 0.4867, 0.4408], std=[0.2675, 0.2565, 0.2761])
        cifar_class = CIFAR100
    else:
        print('Unknown CIFAR type!')
        return
    
    train_dataset = cifar_class(
        root=args.output_dir,
        train=True,
        download=True,
        transform=transforms.Compose([
            transforms.RandomCrop(32, padding=4),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            normalize,
        ]))

    test_dataset = cifar_class(
        root=args.output_dir,
        train=False,
        download=True,
        transform=transforms.Compose([
            transforms.ToTensor(),
            normalize,
        ]))
    if args.distributed:
        train_sampler = torch.utils.data.distributed.DistributedSampler(train_dataset)
    else:
        train_sampler = None
    
    train_loader = torch.utils.data.DataLoader(
        train_dataset, batch_size=args.batch_size, shuffle=(train_sampler is None),
        num_workers=args.workers, pin_memory=True, sampler=train_sampler)

    val_loader = torch.utils.data.DataLoader(
        test_dataset,
        batch_size=args.batch_size, shuffle=False,
        num_workers=args.workers, pin_memory=True)
    
    if args.evaluate:
        validate(val_loader, model, criterion, fid_val)
        return

    for epoch in range(args.start_epoch, args.epochs):
        if args.distributed:
            train_sampler.set_epoch(epoch)
        adjust_learning_rate(optimizer, epoch)

        # train for one epoch
        train(train_loader, model, criterion, optimizer, epoch, fid_train)

        # evaluate on validation set
        prec1 = validate(val_loader, model, criterion, fid_val)

        # remember best prec@1 and save checkpoint
        is_best = prec1 > best_prec1
        best_prec1 = max(prec1, best_prec1)
        must_save = (epoch + 1) == 80
        save_checkpoint({
            'epoch': epoch + 1,
            'arch': args.arch,
            'state_dict': model.state_dict(),
            'best_prec1': best_prec1,
            'optimizer' : optimizer.state_dict(),
        }, is_best, args.output_dir, must_save=must_save)

    fid_train.close()
    fid_val.close()


def train(train_loader, model, criterion, optimizer, epoch, fid_train):
    batch_time = AverageMeter()
    data_time = AverageMeter()

    losses = AverageMeter()
    top1 = AverageMeter()
    top5 = AverageMeter()

    # switch to train mode
    model.train()

    end = time.time()
    for i, (input, target) in enumerate(train_loader):
        # measure data loading time
        data_time.update(time.time() - end)

        if args.gpu is not None:
            input = input.cuda(args.gpu, non_blocking=True)
        target = target.cuda(args.gpu, non_blocking=True)

        # compute output
        output = model(input)

        loss = criterion(output, target)

        # measure accuracy and record loss
        prec1, prec5 = accuracy(output, target, topk=(1, 5))
        losses.update(loss.item(), input.size(0))
        top1.update(prec1[0], input.size(0))
        top5.update(prec5[0], input.size(0))

        # compute gradient and do SGD step
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        # measure elapsed time
        batch_time.update(time.time() - end)
        end = time.time()

        if i % args.print_freq == 0:
            str_arr = 'Loss {l.val:.4f} ({l.avg:.4f})\tTop1 {top1.val:.3f} ({top1.avg:.3f})\tTop5 {top5.val:.3f} ({top5.avg:.3f})'.format(l=losses, top1=top1, top5=top5) 

            msg = 'Epoch: [{0}][{1}/{2}]\tTime {batch_time.val:.3f} ({batch_time.avg:.3f})\tData {data_time.val:.3f} ({data_time.avg:.3f})\t'.format(
                  epoch, i, len(train_loader), 
                  batch_time=batch_time,
                  data_time=data_time) + str_arr

            print(msg)

            fid_train.write(msg + '\n')
            fid_train.flush()


def validate(val_loader, model, criterion, fid_val):
    batch_time = AverageMeter()

    losses = AverageMeter()
    top1 = AverageMeter()
    top5 = AverageMeter()

    # switch to evaluate mode
    model.eval()

    with torch.no_grad():
        end = time.time()
        for i, (input, target) in enumerate(val_loader):
            if args.gpu is not None:
                input = input.cuda(args.gpu, non_blocking=True)
            target = target.cuda(args.gpu, non_blocking=True)
            
            # compute output
            output = model(input)
            loss = criterion(output, target)

            # measure accuracy and record loss
            prec1, prec5 = accuracy(output, target, topk=(1, 5))
            losses.update(loss.item(), input.size(0))
            top1.update(prec1[0], input.size(0))
            top5.update(prec5[0], input.size(0))

            # measure elapsed time
            batch_time.update(time.time() - end)
            end = time.time()

            if i % args.print_freq == 0:

                str_arr = 'Loss {l.val:.4f} ({l.avg:.4f})\tTop1 {t1.val:.3f} ({t1.avg:.3f})\tTop5 {t5.val:.3f} ({t5.avg:.3f})'.format(l=losses, t1=top1, t5=top5)

                msg = 'Test: [{0}/{1}]\tTime {batch_time.val:.3f} ({batch_time.avg:.3f})\t'.format(
                      i, len(val_loader), batch_time=batch_time) + str_arr


                print(msg)

        msg = 'T1 {t1.avg:.3f} T5 {t5.avg:.3f}'.format(t1=top1,t5=top5)
        print('-'*32)
        print(msg)
        print('-'*32)
        fid_val.write(msg + '\n')
        fid_val.flush()

    return top1.avg


def save_checkpoint(state, is_best, output_dir, filename='checkpoint.pth.tar', must_save=False):
    torch.save(state, os.path.join(output_dir, filename))
    if is_best:
        shutil.copyfile(os.path.join(output_dir, filename), os.path.join(output_dir, 'model_best.pth.tar'))

    if must_save:
        while True:
            i = 0
            if not(os.path.exists(os.path.join(output_dir, 'model_must_save_%d.pth.tar'%i))):
                shutil.copyfile(os.path.join(output_dir, filename), os.path.join(output_dir, 'model_must_save_%d.pth.tar'%i))
                break
            i = i + 1


class AverageMeter(object):
    """Computes and stores the average and current value"""
    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count


def fix_learning_rate(optimizer, lr):
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr


def adjust_learning_rate(optimizer, epoch):
    """Adjust the learning rate"""
    if epoch <=81:
        lr = args.lr
    elif epoch <=122:
        lr = args.lr * 0.1
    else:
        lr = args.lr * 0.01

    for param_group in optimizer.param_groups:
        param_group['lr'] = lr

def accuracy(output, target, topk=(1,)):
    """Computes the precision@k for the specified values of k"""
    with torch.no_grad():
        maxk = max(topk)
        batch_size = target.size(0)

        _, pred = output.topk(maxk, 1, True, True)
        pred = pred.t()
        correct = pred.eq(target.view(1, -1).expand_as(pred))

        res = []
        for k in topk:
            correct_k = correct[:k].view(-1).float().sum(0, keepdim=True)
            res.append(correct_k.mul_(100.0 / batch_size))
        return res


if __name__ == '__main__':
    main()
