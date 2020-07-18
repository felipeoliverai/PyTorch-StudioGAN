# PyTorch StudioGAN: https://github.com/POSTECH-CVLab/PyTorch-StudioGAN
# The MIT License (MIT)
# See license file or visit https://github.com/POSTECH-CVLab/PyTorch-StudioGAN for details

# models/biggan.py


from models.model_ops import *

import torch
import torch.nn as nn
import torch.nn.functional as F



class GenBlock(nn.Module):
    def __init__(self, in_channels, out_channels, g_spectral_norm, activation_fn, conditional_bn, z_dims_after_concat, synchronized_bn):
        super(GenBlock, self).__init__()
        self.conditional_bn = conditional_bn

        if self.conditional_bn:
            self.bn1 = ConditionalBatchNorm2d_for_skip_and_shared(num_features=in_channels, z_dims_after_concat=z_dims_after_concat,
                                                                  spectral_norm=g_spectral_norm, synchronized_bn=synchronized_bn)
            self.bn2 = ConditionalBatchNorm2d_for_skip_and_shared(num_features=out_channels, z_dims_after_concat=z_dims_after_concat,
                                                                  spectral_norm=g_spectral_norm, synchronized_bn=synchronized_bn)
        else:
            if synchronized_bn:
                self.bn1 = sync_batchnorm_2d(in_features=in_channels)
                self.bn2 = sync_batchnorm_2d(in_features=out_channels)
            else:
                self.bn1 = batchnorm_2d(in_features=in_channels)
                self.bn2 = batchnorm_2d(in_features=out_channels)

        if activation_fn == "ReLU":
            self.activation = nn.ReLU(inplace=True)
        elif activation_fn == "Leaky_ReLU":
            self.activation = nn.LeakyReLU(negative_slope=0.1, inplace=True)
        else:
            raise NotImplementedError

        if g_spectral_norm:
            self.conv2d0 = snconv2d(in_channels=in_channels, out_channels=out_channels, kernel_size=1, stride=1, padding=0)
            self.conv2d1 = snconv2d(in_channels=in_channels, out_channels=out_channels, kernel_size=3, stride=1, padding=1)
            self.conv2d2 = snconv2d(in_channels=out_channels, out_channels=out_channels, kernel_size=3, stride=1, padding=1)
        else:
            self.conv2d0 = conv2d(in_channels=in_channels, out_channels=out_channels, kernel_size=1, stride=1, padding=0)
            self.conv2d1 = conv2d(in_channels=in_channels, out_channels=out_channels, kernel_size=3, stride=1, padding=1)
            self.conv2d2 = conv2d(in_channels=out_channels, out_channels=out_channels, kernel_size=3, stride=1, padding=1)


    def forward(self, x, label):
        x0 = x
        if self.conditional_bn:
            x = self.bn1(x, label)
        else:
            x = self.bn1(x)
        x = self.activation(x)
        x = F.interpolate(x, scale_factor=2, mode='nearest') # upsample
        x = self.conv2d1(x)
        if self.conditional_bn:
            x = self.bn2(x, label)
        else:
            x = self.bn2(x)
        x = self.activation(x)
        x = self.conv2d2(x)

        x0 = F.interpolate(x0, scale_factor=2, mode='nearest') # upsample
        x0 = self.conv2d0(x0)

        out = x + x0
        return out


class Generator(nn.Module):
    """Generator."""
    def __init__(self, z_dim, shared_dim, img_size, g_conv_dim, g_spectral_norm, attention, attention_after_nth_gen_block, activation_fn,
                 conditional_strategy, num_classes, synchronized_bn, initialize):
        super(Generator, self).__init__()
        g_in_dims_collection = {"32": [g_conv_dim*4, g_conv_dim*4, g_conv_dim*4], 
                                "64": [g_conv_dim*16, g_conv_dim*8, g_conv_dim*4, g_conv_dim*2],
                                "96": [g_conv_dim*16, g_conv_dim*16, g_conv_dim*8, g_conv_dim*4, g_conv_dim*2],
                                "128": [g_conv_dim*16, g_conv_dim*16, g_conv_dim*8, g_conv_dim*4, g_conv_dim*2]}

        g_out_dims_collection = {"32": [g_conv_dim*4, g_conv_dim*4, g_conv_dim*4],
                                 "64": [g_conv_dim*8, g_conv_dim*4, g_conv_dim*2, g_conv_dim],
                                 "96": [g_conv_dim*16, g_conv_dim*8, g_conv_dim*4, g_conv_dim*2, g_conv_dim],
                                 "128": [g_conv_dim*16, g_conv_dim*8, g_conv_dim*4, g_conv_dim*2, g_conv_dim]}
        
        bottom_collection = {"32": 4, "64": 4, "96": 3, "128":4}

        self.z_dim = z_dim
        self.shared_dim = shared_dim
        self.num_classes = num_classes
        conditional_bn = True if conditional_strategy == "ACGAN" or conditional_strategy =="cGAN" or conditional_strategy == "ContraGAN" else False
        
        self.in_dims =  g_in_dims_collection[str(img_size)]
        self.out_dims = g_out_dims_collection[str(img_size)]
        self.bottom = bottom_collection[str(img_size)]
        self.n_blocks = len(self.in_dims)
        self.chunk_size = z_dim//(self.n_blocks+1)
        self.z_dims_after_concat = self.chunk_size + self.shared_dim
        assert self.z_dim % (self.n_blocks+1) == 0, "z_dim should be divided by the number of blocks "

        if g_spectral_norm:
            self.linear0 = snlinear(in_features=self.chunk_size, out_features=self.in_dims[0]*self.bottom*self.bottom)
        else:
            self.linear0 = linear(in_features=self.chunk_size, out_features=self.in_dims[0]*self.bottom*self.bottom)

        self.shared = embedding(self.num_classes, self.shared_dim)

        self.blocks = []
        for index in range(self.n_blocks):
            self.blocks += [[GenBlock(in_channels=self.in_dims[index],
                                      out_channels=self.out_dims[index],
                                      g_spectral_norm=g_spectral_norm,
                                      activation_fn=activation_fn,
                                      conditional_bn=conditional_bn,
                                      z_dims_after_concat=self.z_dims_after_concat,
                                      synchronized_bn=synchronized_bn)]]
                                          
            if index+1 == attention_after_nth_gen_block and attention is True:
                self.blocks += [[Self_Attn(self.out_dims[index], g_spectral_norm)]]

        self.blocks = nn.ModuleList([nn.ModuleList(block) for block in self.blocks])

        if synchronized_bn:
            self.bn4 = sync_batchnorm_2d(in_features=self.out_dims[-1])
        else:
            self.bn4 = batchnorm_2d(in_features=self.out_dims[-1])

        if activation_fn == "ReLU":
            self.activation = nn.ReLU(inplace=True)
        elif activation_fn == "Leaky_ReLU":
            self.activation = nn.LeakyReLU(negative_slope=0.1, inplace=True)
        else:
            raise NotImplementedError

        if g_spectral_norm:
            self.conv2d5 = snconv2d(in_channels=self.out_dims[-1], out_channels=3, kernel_size=3, stride=1, padding=1)
        else:
            self.conv2d5 = conv2d(in_channels=self.out_dims[-1], out_channels=3, kernel_size=3, stride=1, padding=1)

        self.tanh = nn.Tanh()

        # Weight init
        if initialize is not False:
            init_weights(self.modules, initialize)


    def forward(self, z, label):
        zs = torch.split(z, self.chunk_size, 1)
        z = zs[0]
        label = self.shared(label)
        labels = [torch.cat([label, item], 1) for item in zs[1:]]

        act = self.linear0(z)
        act = act.view(-1, self.in_dims[0], self.bottom, self.bottom)
        counter = 0
        for index, blocklist in enumerate(self.blocks):
            for block in blocklist:
                if isinstance(block, Self_Attn):
                    act = block(act)
                else:
                    act = block(act, labels[counter])
                    counter +=1

        act = self.bn4(act)
        act = self.activation(act)
        act = self.conv2d5(act)
        out = self.tanh(act)
        return out


class DiscOptBlock(nn.Module):
    def __init__(self, in_channels, out_channels, d_spectral_norm, activation_fn, synchronized_bn):
        super(DiscOptBlock, self).__init__()
        self.d_spectral_norm = d_spectral_norm

        if d_spectral_norm:
            self.conv2d0 = snconv2d(in_channels=in_channels, out_channels=out_channels, kernel_size=1, stride=1, padding=0)
            self.conv2d1 = snconv2d(in_channels=in_channels, out_channels=out_channels, kernel_size=3, stride=1, padding=1)
            self.conv2d2 = snconv2d(in_channels=out_channels, out_channels=out_channels, kernel_size=3, stride=1, padding=1)
        else:
            self.conv2d0 = conv2d(in_channels=in_channels, out_channels=out_channels, kernel_size=1, stride=1, padding=0)
            self.conv2d1 = conv2d(in_channels=in_channels, out_channels=out_channels, kernel_size=3, stride=1, padding=1)
            self.conv2d2 = conv2d(in_channels=out_channels, out_channels=out_channels, kernel_size=3, stride=1, padding=1)

            if synchronized_bn:
                self.bn0 = sync_batchnorm_2d(in_features=in_channels)
                self.bn1 = sync_batchnorm_2d(in_features=out_channels)
            else:
                self.bn0 = batchnorm_2d(in_features=in_channels)
                self.bn1 = batchnorm_2d(in_features=out_channels)

        if activation_fn == "ReLU":
            self.activation = nn.ReLU(inplace=True)
        elif activation_fn == "Leaky_ReLU":
            self.activation = nn.LeakyReLU(negative_slope=0.1, inplace=True)
        else:
            raise NotImplementedError

        self.average_pooling = nn.AvgPool2d(2)


    def forward(self, x):
        x0 = x
        x = self.conv2d1(x)
        if self.d_spectral_norm is False:
            x = self.bn1(x)
        x = self.activation(x)
        x = self.conv2d2(x)
        x = self.average_pooling(x)

        x0 = self.average_pooling(x0)
        if self.d_spectral_norm is False:
            x0 = self.bn0(x0)
        x0 = self.conv2d0(x0)

        out = x + x0
        return out


class DiscBlock(nn.Module):
    def __init__(self, in_channels, out_channels, d_spectral_norm, activation_fn, synchronized_bn, downsample=True):
        super(DiscBlock, self).__init__()
        self.d_spectral_norm = d_spectral_norm
        self.downsample = downsample

        if activation_fn == "ReLU":
            self.activation = nn.ReLU(inplace=True)
        elif activation_fn == "Leaky_ReLU":
            self.activation = nn.LeakyReLU(negative_slope=0.1, inplace=True)
        else:
            raise NotImplementedError
        
        self.ch_mismatch = False
        if in_channels != out_channels:
            self.ch_mismatch = True

        if d_spectral_norm:
            if self.ch_mismatch or downsample:
                self.conv2d0 = snconv2d(in_channels=in_channels, out_channels=out_channels, kernel_size=1, stride=1, padding=0)
            self.conv2d1 = snconv2d(in_channels=in_channels, out_channels=out_channels, kernel_size=3, stride=1, padding=1)
            self.conv2d2 = snconv2d(in_channels=out_channels, out_channels=out_channels, kernel_size=3, stride=1, padding=1)
        else:
            if self.ch_mismatch or downsample:
                self.conv2d0 = conv2d(in_channels=in_channels, out_channels=out_channels, kernel_size=1, stride=1, padding=0)
            self.conv2d1 = conv2d(in_channels=in_channels, out_channels=out_channels, kernel_size=3, stride=1, padding=1)
            self.conv2d2 = conv2d(in_channels=out_channels, out_channels=out_channels, kernel_size=3, stride=1, padding=1)

            if synchronized_bn:
                if self.ch_mismatch or downsample:
                    self.bn0 = sync_batchnorm_2d(in_features=in_channels)
                self.bn1 = sync_batchnorm_2d(in_features=in_channels)
                self.bn2 = sync_batchnorm_2d(in_features=out_channels)
            else:
                if self.ch_mismatch or downsample:
                    self.bn0 = batchnorm_2d(in_features=in_channels)
                self.bn1 = batchnorm_2d(in_features=in_channels)
                self.bn2 = batchnorm_2d(in_features=out_channels)

        self.average_pooling = nn.AvgPool2d(2)

            
    def forward(self, x):
        x0 = x

        if self.d_spectral_norm is False:
            x = self.bn1(x)
        x = self.activation(x)
        x = self.conv2d1(x)
        if self.d_spectral_norm is False:
            x = self.bn2(x)
        x = self.activation(x)
        x = self.conv2d2(x)
        if self.downsample:
            x = self.average_pooling(x)

        if self.downsample or self.ch_mismatch:
            if self.d_spectral_norm is False:
                x0 = self.bn0(x0)
            x0 = self.conv2d0(x0)
            if self.downsample:
                x0 = self.average_pooling(x0)

        out = x + x0
        return out


class Discriminator(nn.Module):
    """Discriminator."""
    def __init__(self, img_size, d_conv_dim, d_spectral_norm, attention, attention_after_nth_dis_block, activation_fn, conditional_strategy, 
                 hypersphere_dim, num_classes, nonlinear_embed, normalize_embed, synchronized_bn, initialize):
        super(Discriminator, self).__init__()
        d_in_dims_collection = {"32": [3] + [d_conv_dim*2, d_conv_dim*2, d_conv_dim*2], 
                                "64": [3] +[d_conv_dim, d_conv_dim*2, d_conv_dim*4, d_conv_dim*8],
                                "96": [3] +[d_conv_dim, d_conv_dim*2, d_conv_dim*4, d_conv_dim*8, d_conv_dim*16],
                                "128": [3] +[d_conv_dim, d_conv_dim*2, d_conv_dim*4, d_conv_dim*8, d_conv_dim*16]}

        d_out_dims_collection = {"32": [d_conv_dim*2, d_conv_dim*2, d_conv_dim*2, d_conv_dim*2],
                                 "64": [d_conv_dim, d_conv_dim*2, d_conv_dim*4, d_conv_dim*8, d_conv_dim*16],
                                 "96": [d_conv_dim, d_conv_dim*2, d_conv_dim*4, d_conv_dim*8, d_conv_dim*16, d_conv_dim*16],
                                 "128": [d_conv_dim, d_conv_dim*2, d_conv_dim*4, d_conv_dim*8, d_conv_dim*16, d_conv_dim*16]}

        d_down = {"32": [True, True, False, False],
                  "64": [True, True, True, True, False],
                  "96": [True, True, True, True, True, False],
                  "128": [True, True, True, True, True, False]}

        self.nonlinear_embed = nonlinear_embed
        self.normalize_embed = normalize_embed
        self.conditional_strategy = conditional_strategy

        self.in_dims  = d_in_dims_collection[str(img_size)]
        self.out_dims = d_out_dims_collection[str(img_size)]
        down = d_down[str(img_size)]

        self.blocks = []
        for index in range(len(self.in_dims)):
            if index == 0:
                self.blocks += [[DiscOptBlock(in_channels=self.in_dims[index],
                                              out_channels=self.out_dims[index],
                                              d_spectral_norm=d_spectral_norm,
                                              activation_fn=activation_fn,
                                              synchronized_bn=synchronized_bn)]]
            else:
                self.blocks += [[DiscBlock(in_channels=self.in_dims[index],
                                           out_channels=self.out_dims[index],
                                           d_spectral_norm=d_spectral_norm,
                                           activation_fn=activation_fn,
                                           synchronized_bn=synchronized_bn,
                                           downsample=down[index])]]

            if index+1 == attention_after_nth_dis_block and attention is True:
                self.blocks += [[Self_Attn(self.out_dims[index], d_spectral_norm)]]

        self.blocks = nn.ModuleList([nn.ModuleList(block) for block in self.blocks])

        if activation_fn == "ReLU":
            self.activation = nn.ReLU(inplace=True)
        elif activation_fn == "Leaky_ReLU":
            self.activation = nn.LeakyReLU(negative_slope=0.1, inplace=True)
        else:
            raise NotImplementedError

        if d_spectral_norm:
            self.linear1 = snlinear(in_features=self.out_dims[-1], out_features=1)
            if self.conditional_strategy == 'ContraGAN':
                self.linear2 = snlinear(in_features=self.out_dims[-1], out_features=hypersphere_dim)
                if self.nonlinear_embed:
                    self.linear3 = snlinear(in_features=hypersphere_dim, out_features=hypersphere_dim)
                self.embedding = sn_embedding(num_classes, hypersphere_dim)
            elif self.conditional_strategy == 'cGAN':
                self.embedding = sn_embedding(num_classes, self.out_dims[-1])
            elif self.conditional_strategy == 'ACGAN':
                self.linear4 = snlinear(in_features=self.out_dims[-1], out_features=num_classes)
            else:
                raise NotImplementedError
        else:
            self.linear1 = linear(in_features=self.out_dims[-1], out_features=1)
            if self.conditional_strategy == 'ContraGAN':
                self.linear2 = linear(in_features=self.out_dims[-1], out_features=hypersphere_dim)
                if self.nonlinear_embed:
                    self.linear3 = linear(in_features=hypersphere_dim, out_features=hypersphere_dim)
                self.embedding = embedding(num_classes, hypersphere_dim)
            elif self.conditional_strategy == 'cGAN':
                self.embedding = embedding(num_classes, self.out_dims[-1])
            elif self.conditional_strategy == 'ACGAN':
                self.linear4 = linear(in_features=self.out_dims[-1], out_features=num_classes)
            else:
                raise NotImplementedError

        # Weight init
        if initialize is not False:
            init_weights(self.modules, initialize)


    def forward(self, x, label):
        h = x
        for index, blocklist in enumerate(self.blocks):
            for block in blocklist:
                h = block(h)
        h = self.activation(h)
        h = torch.sum(h, dim=[2,3])
        
        if self.conditional_strategy == 'no':
            authen_output = torch.squeeze(self.linear1(h))
            return authen_output
        elif self.conditional_strategy == 'ContraGAN':
            authen_output = torch.squeeze(self.linear1(h))
            cls_proxy = self.embedding(label)
            cls_embed = self.linear2(h)
            if self.nonlinear_embed:
                cls_embed = self.linear3(self.activation(cls_embed))
            if self.normalize_embed:
                cls_proxy = F.normalize(cls_proxy, dim=1)
                cls_embed = F.normalize(cls_embed, dim=1)
            return cls_proxy, cls_embed, authen_output

        elif self.conditional_strategy == 'cGAN':
            authen_output = torch.squeeze(self.linear1(h))
            proj = torch.sum(torch.mul(self.embedding(label), h), 1)
            return authen_output + proj
        
        elif self.conditional_strategy == 'ACGAN':
            authen_output = torch.squeeze(self.linear1(h))
            cls_output = self.linear4(h)
            return cls_output, authen_output

        else:
            raise NotImplementedError