import tensorflow as tf
from .neighbors import euclidean_squared,indexing_tensor, indexing_tensor_2, sort_last_dim_tensor, get_sorted_vertices_ids
from ops.nn import *
import numpy as np
from .initializers import NoisyEyeInitializer
from .activations import gauss_of_lin, gauss_times_linear, sinc, open_tanh
import math

###helper functions
_sparse_conv_naming_index=0

def construct_sparse_io_dict(all_features, spatial_features_global, spatial_features_local, num_entries):
    """
    Constructs dictionary for readers of sparse convolution layers

    :param all_features: All features tensor.  Should be of shape [batch_size, num_entries, num_features]
    :param spatial_features_global: Space like features tensor. Should be of shape [batch_size, num_entries, num_features]
    :param spatial_features_local: Space like features tensor (sensor sizes etc). Should be of shape [batch_size, num_entries, num_features]
    :param num_entries: Number of entries tensor for each batch entry.
    :return: dictionary in the format of the sparse conv layer
    """
    return {
        'all_features': all_features,
        'spatial_features_global': spatial_features_global,
        'spatial_features_local': spatial_features_local,
        'num_entries' : num_entries
    }
    
def sparse_conv_collapse(sparse_dict):
    all_features, spatial_features_global, spatial_features_local, num_entries = sparse_dict['all_features'], \
                                                                                 sparse_dict['spatial_features_global'], \
                                                                                 sparse_dict['spatial_features_local'], \
                                                                                 sparse_dict['num_entries']
    return tf.concat([spatial_features_global,all_features , spatial_features_local],axis=-1)                                                                             
    


def sprint(tensor,pstr):
    return tensor
    return tf.Print(tensor,[tensor],pstr,summarize=300)

def make_sequence(nfilters):
    isseq=(not hasattr(nfilters, "strip") and
            hasattr(nfilters, "__getitem__") or
            hasattr(nfilters, "__iter__"))
    
    if not isseq:
        nfilters=[nfilters]
    return nfilters

def sparse_conv_normalise(sparse_dict, log_energy=False):
    colours_in, space_global, space_local, num_entries = sparse_dict['all_features'], \
                                                         sparse_dict['spatial_features_global'], \
                                                         sparse_dict['spatial_features_local'], \
                                                         sparse_dict['num_entries']
    
    scaled_colours_in = colours_in*1e-4
    if log_energy:
        scaled_colours_in = tf.log(colours_in+1)/10.
    
    scaled_space_global=tf.concat([tf.expand_dims(space_global[:,:,0]/150.,axis=2),
                                   tf.expand_dims(space_global[:,:,1]/150.,axis=2),
                                   tf.expand_dims(space_global[:,:,2]/1600.,axis=2)],
                                  axis=-1)
    
    scaled_space_local = space_local/150.
    
    return construct_sparse_io_dict(scaled_colours_in, 
                                    scaled_space_global, 
                                    scaled_space_local, 
                                    num_entries)

#just for compat
def make_batch_selection(ids):
    n_batch=ids.shape[0]
    n_vertices=ids.shape[1]
    ids = tf.cast(ids, dtype=tf.int64)
    batch=tf.range(n_batch, dtype=tf.int64)
    batch = tf.tile(batch[..., tf.newaxis, tf.newaxis], [1,n_vertices,1])
    select = tf.concat((batch, ids[..., tf.newaxis]), axis=-1)
    return select
    
    
    

def apply_distance_weight(x, zero_is_one=False):
    #return x
    if zero_is_one:
        return gauss_of_lin(x)
    else:
        return gauss_times_linear(x)
    
def add_rot_symmetric_distance(raw_difference):
    rot_raw_difference = tf.reduce_sum(raw_difference*raw_difference,axis=-1)
    rot_raw_difference = tf.sqrt(rot_raw_difference+1e-6)
    rot_raw_difference = tf.expand_dims(rot_raw_difference,axis=3)
    
    edges = tf.concat([rot_raw_difference,raw_difference],axis=-1)
    return edges
    

def create_edges(vertices_a, vertices_b, zero_is_one_weight=False, n_properties=-1):
    #BxVxF
    expanded_vertices_a = tf.expand_dims(vertices_a, axis=1)
    expanded_vertices_b = tf.expand_dims(vertices_b, axis=2)
    raw_difference = expanded_vertices_a - expanded_vertices_b
    #calculate explicitly rotational symmetric on
    edges = add_rot_symmetric_distance(raw_difference)
    
    if n_properties>0:
        edges = edges[:,:,:,0:n_properties]
    return apply_distance_weight(edges,zero_is_one_weight)
    
    
def apply_edges(vertices, edges, reduce_sum=True, flatten=True): 
    '''
    edges are naturally BxVxV'xF
    vertices are BxVxF'  or BxV'xF'
    This function returns BxVxF'' if flattened and summed
    '''
    edges = tf.expand_dims(edges,axis=3)
    vertices = tf.expand_dims(vertices,axis=1)
    vertices = tf.expand_dims(vertices,axis=4)
    
    out = edges*vertices
    if reduce_sum:
        out = tf.reduce_sum(out,axis=2)/float(int(out.shape[2]))
    if flatten:
        out = tf.reshape(out,shape=[out.shape[0],out.shape[1],-1])
    
    return out

 
def apply_space_transform(vertices, units_transform, output_dimensions): 
    
    trans_space = tf.layers.dense(vertices/10.,units_transform,activation=open_tanh,
                                   kernel_initializer=NoisyEyeInitializer)
    trans_space = tf.layers.dense(trans_space*10.,output_dimensions,activation=None,
                                  kernel_initializer=NoisyEyeInitializer, use_bias=False)
    return trans_space
########

def sparse_conv_add_simple_seed_labels(net,seed_indices):
    colours_in, space_global, space_local, num_entries = net['all_features'], \
                                                                    net['spatial_features_global'], \
                                                                    net['spatial_features_local'], \
                                                                    net['num_entries']
    seedselector = make_batch_selection(seed_indices)
    seed_space = tf.gather_nd(space_global,seedselector)
    label = tf.argmin(euclidean_squared(space_global, seed_space), axis=-1)
    label = tf.cast(label,dtype=tf.float32)
    colours_in = tf.concat([colours_in,tf.expand_dims(label, axis=2)], axis=-1)
    return construct_sparse_io_dict(colours_in , space_global, space_local, num_entries)
    

def get_distance_weight_to_seeds(vertices_in, seed_idx, dimensions=4, add_zeros=0):
    
    seedselector = make_batch_selection(seed_idx)
    vertices_in = tf.layers.dense (vertices_in, dimensions, kernel_initializer=NoisyEyeInitializer)
    seed_vertices = tf.gather_nd(vertices_in,seedselector)
    edges = create_edges(vertices_in,seed_vertices, zero_is_one_weight=True)
    distance = edges[:,:,:,0]
    distance = tf.transpose(distance, perm=[0,2,1])
    if add_zeros>0:
        zeros = tf.zeros_like(distance[:,:,0], dtype=tf.float32)
        zeros = tf.expand_dims(zeros, axis=2)
        for i in range(add_zeros):
            distance = tf.concat([distance,zeros], axis=-1)
    return distance
    
    

def sparse_conv_seeded3(vertices_in, 
                       seed_indices, 
                       nfilters, 
                       nspacefilters=32, 
                       nspacedim=3, 
                       seed_talk=True,
                       compress_before_propagate=True,
                       use_edge_properties=-1):
    global _sparse_conv_naming_index
    '''
    '''
    #for later
    _sparse_conv_naming_index+=1
    
    seedselector = make_batch_selection(seed_indices)
    
    trans_space = apply_space_transform(vertices_in, nspacefilters,nspacedim)
    
    seed_trans_space = tf.gather_nd(trans_space,seedselector)
    
    edges = create_edges(trans_space,seed_trans_space,n_properties=use_edge_properties)
    
    trans_vertices = tf.layers.dense(vertices_in,nfilters,activation=tf.nn.relu)
    
    expanded_collapsed = apply_edges(trans_vertices, edges, reduce_sum=True, flatten=True)
   
    #add back seed features
    seed_all_features = tf.gather_nd(trans_vertices,seedselector)
    
    #simple dense check this part
    #maybe add additional dense
    if seed_talk:
        #seed space transform?
        seed_edges = create_edges(seed_trans_space,seed_trans_space,n_properties=use_edge_properties)
        trans_seeds = apply_edges(seed_all_features, seed_edges, reduce_sum=True, flatten=True)
        seed_merged_features = tf.concat([seed_all_features,trans_seeds],axis=-1)
        seed_all_features = tf.layers.dense(seed_merged_features,seed_all_features.shape[2],
                                            activation=tf.nn.tanh,
                                            kernel_initializer=NoisyEyeInitializer)
    
    #compress
    
    expanded_collapsed = tf.concat([expanded_collapsed,seed_all_features],axis=-1)
    if compress_before_propagate:
        expanded_collapsed = tf.layers.dense(expanded_collapsed,nfilters, activation=tf.nn.tanh)
        
    print('expanded_collapsed',expanded_collapsed.shape)
    
    #propagate back, transposing the edges does the trick, now they point from Nseeds to Nvertices
    edges = tf.transpose(edges, perm=[0,2, 1,3])
    expanded_collapsed = apply_edges(expanded_collapsed, edges, reduce_sum=False, flatten=True)
    if compress_before_propagate:
        expanded_collapsed = tf.layers.dense(expanded_collapsed,nfilters, activation=tf.nn.tanh,
                                             kernel_initializer=NoisyEyeInitializer)
    

    #combien old features with new ones
    feature_layerout = tf.concat([vertices_in,trans_space,expanded_collapsed],axis=-1)
    feature_layerout = tf.layers.dense(feature_layerout,nfilters,activation=tf.nn.tanh,
                                       kernel_initializer=NoisyEyeInitializer)
    return feature_layerout




def sparse_conv_make_neighbors2(vertices_in, num_neighbors=10, 
                               output_all=15, space_transformations=10,
                               merge_neighbours=1,
                               edge_activation=gauss_of_lin):
    
    assert merge_neighbours <= num_neighbors
    global _sparse_conv_naming_index
    
    #for later
    _sparse_conv_naming_index+=1
    
    space_transformations = make_sequence(space_transformations)
    output_all = make_sequence(output_all)
    
    
    trans_space = vertices_in
    for i in range(len(space_transformations)):
        if i< len(space_transformations)-1:
            trans_space = tf.layers.dense(trans_space/10.,space_transformations[i],activation=open_tanh,
                                       kernel_initializer=NoisyEyeInitializer)
            trans_space*=10.
        else:
            trans_space = tf.layers.dense(trans_space,space_transformations[i],activation=None,
                                       kernel_initializer=NoisyEyeInitializer)

    indexing, _ = indexing_tensor_2(trans_space, num_neighbors)
    
    neighbour_space = tf.gather_nd(trans_space, indexing)
    
    #build edges manually
    expanded_trans_space = tf.expand_dims(trans_space, axis=2)
    diff = expanded_trans_space - neighbour_space
    edges = add_rot_symmetric_distance(diff)
    #edges = apply_distance_weight(edges)
    edges = tf.expand_dims(edges,axis=3)
    
        
    updated_vertices = vertices_in
    orig_edges = edges
    for f in output_all:
        #interpret distances in a different way -> dense on edges (with funny activations TBI)
        edges = tf.layers.dense(tf.concat([orig_edges,edges], axis=-1), 
                                edges.shape[-1],activation=edge_activation,
                                kernel_initializer = NoisyEyeInitializer)
        
        vertex_with_neighbours = tf.gather_nd(updated_vertices, indexing)
        vertex_with_neighbours = tf.expand_dims(vertex_with_neighbours,axis=4)
        flattened_gathered = vertex_with_neighbours * edges
        flattened_gathered = tf.reduce_mean(flattened_gathered, axis=2)
        flattened_gathered = tf.reshape(flattened_gathered, shape=[flattened_gathered.shape[0],
                                                                   flattened_gathered.shape[1],-1])
        updated_vertices = tf.layers.dense(tf.concat([vertices_in,flattened_gathered],axis=-1), 
                                           f, activation=tf.nn.relu) 


    if merge_neighbours>1:
        rest = int(vertices_in.shape[1])%merge_neighbours
        if rest == 0:
            rest=merge_neighbours
        cut_at = int((int(vertices_in.shape[1])+merge_neighbours-rest)/merge_neighbours)
        gathered_feat = tf.gather_nd(updated_vertices, indexing)
        gathered_feat = gathered_feat[:,0:cut_at,0:merge_neighbours,:]
        gathered_feat = tf.reshape(gathered_feat, [gathered_feat.shape[0],gathered_feat.shape[1],-1])
        #do dimension averaging
        gathered_space = tf.gather_nd(trans_space, indexing)
        gathered_space = gathered_space[:,0:cut_at,0:merge_neighbours,:]
        gathered_space = tf.reduce_mean(gathered_space, axis=2)
        tf.concat([gathered_space,gathered_feat],axis=-1) 
        
    else:
        tf.concat([trans_space,updated_vertices],axis=-1)
        
    return updated_vertices


    
    