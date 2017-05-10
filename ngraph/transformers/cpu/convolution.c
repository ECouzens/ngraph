/*******************************************************************************
* Copyright 2016 Nervana Systems Inc.
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
*      http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
*******************************************************************************/

#include "mkldnn_engine.h"
#include "mkldnn_util.h"

void create_mkldnn_conv_fprop_kernel(
    mkldnn_engine_t engine, 
    int src_dims, int weights_dims, int dst_dims, 
    int stride_dims, int pad_dims, 
    int* conv_src_sizes, int* conv_weights_sizes, int* conv_dst_sizes,
    int* conv_strides, int* conv_padding,
    mkldnn_primitive_desc_t input_src_pd, mkldnn_primitive_desc_t input_weights_pd,
    mkldnn_opkernel_t opkernel) {
  
    // Only 2-D convolution is supported for now 
    int mkl_src_dims = 4;
    int mkl_weights_dims = 4;
    int mkl_dst_dims = 4;
    int mkl_src_sizes[4];
    int mkl_weights_sizes[4];
    int mkl_dst_sizes[4];
    int mkl_strides[2];
    int mkl_padding[2];

    /* Flatten out the depth (D, M) dimension and reorder logical dimensions to
     * match MKLDNN */
    set_mkl_dimensions("convolution", conv_src_sizes, conv_dst_sizes,
                       conv_weights_sizes, conv_strides, conv_padding,
                       mkl_src_sizes, mkl_dst_sizes, mkl_weights_sizes,
                       mkl_strides, mkl_padding);
    
    // Create an optimized convolution kernel
    // Let MKL pick the best format (mkldnn_any)
    mkldnn_memory_desc_t mkldnn_memory_desc_src_md, mkldnn_memory_desc_weights_md,
          mkldnn_memory_desc_dst_md;
    MKL_CHECK(mkldnn_memory_desc_init(&mkldnn_memory_desc_src_md, mkl_src_dims,
                                      mkl_src_sizes, mkldnn_f32, mkldnn_any));
    MKL_CHECK(mkldnn_memory_desc_init(&mkldnn_memory_desc_weights_md,
                                      mkl_weights_dims, mkl_weights_sizes,
                                      mkldnn_f32, mkldnn_any));
    MKL_CHECK(mkldnn_memory_desc_init(&mkldnn_memory_desc_dst_md, mkl_dst_dims,
                                      mkl_dst_sizes, mkldnn_f32, mkldnn_any));
    mkldnn_convolution_desc_t conv_desc;
    MKL_CHECK(mkldnn_convolution_forward_desc_init(
                &conv_desc, mkldnn_forward, mkldnn_convolution_direct,
                &mkldnn_memory_desc_src_md, &mkldnn_memory_desc_weights_md, NULL,
                &mkldnn_memory_desc_dst_md, mkl_strides, mkl_padding, mkl_padding,
                mkldnn_padding_zero));
    MKL_CHECK(mkldnn_primitive_desc_create(&opkernel->op_desc, &conv_desc, engine, NULL));
    
    const_mkldnn_primitive_desc_t kernel_src_pd =
          mkldnn_primitive_desc_query_pd(opkernel->op_desc, mkldnn_query_src_pd, 0);
    const_mkldnn_primitive_desc_t kernel_weights_pd =
          mkldnn_primitive_desc_query_pd(opkernel->op_desc, mkldnn_query_weights_pd, 0);
    const_mkldnn_primitive_desc_t kernel_dst_pd =
          mkldnn_primitive_desc_query_pd(opkernel->op_desc, mkldnn_query_dst_pd, 0);

    if (input_src_pd) {
        mkldnn_memory_desc_t md = *(mkldnn_primitive_desc_query_memory_d((const_mkldnn_primitive_desc_t)input_src_pd));
        create_mkldnn_tensor_from_pd(mkl_src_dims, mkl_src_sizes, &md,
                            engine, &(opkernel->inputs[0]));
    } else {
        create_mkldnn_tensor(mkl_src_dims, mkl_src_sizes, mkldnn_f32, mkldnn_chwn,
                            engine, &(opkernel->inputs[0]));
    }
    if (input_weights_pd) {
        mkldnn_memory_desc_t md = *(mkldnn_primitive_desc_query_memory_d((const_mkldnn_primitive_desc_t)input_weights_pd));
        create_mkldnn_tensor_from_pd(mkl_weights_dims, mkl_weights_sizes, &md,
                            engine, &(opkernel->inputs[1]));
    } else {
        create_mkldnn_tensor(mkl_weights_dims, mkl_weights_sizes, mkldnn_f32, mkldnn_ihwo,
                            engine, &(opkernel->inputs[1]));
    }
    mkldnn_memory_desc_t dst_md = *mkldnn_primitive_desc_query_memory_d(kernel_dst_pd);
    create_mkldnn_tensor_from_pd(mkl_dst_dims, mkl_dst_sizes, &dst_md,
                        engine, &(opkernel->outputs[0]));
    //create_mkldnn_tensor(mkl_dst_dims, mkl_dst_sizes, mkldnn_f32, mkldnn_chwn,
    //                     engine, &(opkernel->outputs[0]));
    opkernel->num_inputs = 2;
    opkernel->num_outputs = 1;
    
    // Reorder inputs 
    if (!mkldnn_memory_primitive_desc_equal(opkernel->inputs[0].desc, kernel_src_pd)) {
        mkldnn_memory_desc_t md = *mkldnn_primitive_desc_query_memory_d(kernel_src_pd);
        create_mkldnn_tensor_from_pd(mkl_src_dims, mkl_src_sizes, &md, engine, &(opkernel->internal_inputs[0]));
        mkldnn_primitive_desc_t reorder_pd;
        MKL_CHECK(mkldnn_reorder_primitive_desc_create(&reorder_pd,
                    opkernel->inputs[0].desc, kernel_src_pd));
        mkldnn_primitive_at_t inputs[] = {opkernel->inputs[0].prim};
        const_mkldnn_primitive_t outputs[] = {opkernel->internal_inputs[0].prim};
        MKL_CHECK(mkldnn_primitive_create(&(opkernel->reorder_i[0]), reorder_pd, inputs, outputs));
    } else {
        opkernel->reorder_i[0] = NULL;
    }

    if (!mkldnn_memory_primitive_desc_equal(opkernel->inputs[1].desc, kernel_weights_pd)) {
        mkldnn_memory_desc_t md = *mkldnn_primitive_desc_query_memory_d(kernel_weights_pd);
        create_mkldnn_tensor_from_pd(mkl_weights_dims, mkl_weights_sizes, &md, engine, &(opkernel->internal_inputs[1]));
        mkldnn_primitive_desc_t reorder_pd;
        MKL_CHECK(mkldnn_reorder_primitive_desc_create(&reorder_pd,
                    opkernel->inputs[1].desc, kernel_weights_pd));
        mkldnn_primitive_at_t inputs[] = {opkernel->inputs[1].prim};
        const_mkldnn_primitive_t outputs[] = {opkernel->internal_inputs[1].prim};
        MKL_CHECK(mkldnn_primitive_create(&(opkernel->reorder_i[1]), reorder_pd, inputs, outputs));
    } else {
        opkernel->reorder_i[1] = NULL;
    }

    if (!mkldnn_memory_primitive_desc_equal(opkernel->outputs[0].desc, kernel_dst_pd)) {
        mkldnn_memory_desc_t md = *mkldnn_primitive_desc_query_memory_d(kernel_dst_pd);
        create_mkldnn_tensor_from_pd(mkl_src_dims, mkl_src_sizes, &md, engine, &(opkernel->internal_outputs[0]));
        mkldnn_primitive_desc_t reorder_pd;
        MKL_CHECK(mkldnn_reorder_primitive_desc_create(&reorder_pd,
                    opkernel->outputs[0].desc, kernel_dst_pd));
        mkldnn_primitive_at_t outputs[] = {opkernel->outputs[0].prim};
        const_mkldnn_primitive_t inputs[] = {opkernel->internal_outputs[0].prim};
        MKL_CHECK(mkldnn_primitive_create(&(opkernel->reorder_o[0]), reorder_pd, inputs, outputs));
    } else {
        opkernel->reorder_o[0] = NULL;
    }

    /* Allocate memory for internal format conversions */
    if (opkernel->reorder_i[0]) {
        float* tmp_buf =
            (float*)calloc(product(conv_src_sizes, src_dims), sizeof(float));
        opkernel->internal_inputs[0].buffer = tmp_buf;
        MKL_CHECK(mkldnn_memory_set_data_handle(opkernel->internal_inputs[0].prim,
                                                tmp_buf));
    }
    if (opkernel->reorder_i[1]) {
        float* tmp_buf =
            (float*)calloc(product(conv_weights_sizes, weights_dims), sizeof(float));
        opkernel->internal_inputs[1].buffer = tmp_buf;
        MKL_CHECK(mkldnn_memory_set_data_handle(opkernel->internal_inputs[1].prim,
                                                tmp_buf));
    }
    if (opkernel->reorder_o[0]) {
        float* tmp_buf =
            (float*)calloc(product(conv_dst_sizes, dst_dims), sizeof(float));
        opkernel->internal_outputs[0].buffer = tmp_buf;
        MKL_CHECK(mkldnn_memory_set_data_handle(opkernel->internal_outputs[0].prim,
                                                tmp_buf));
    }

    /* select input and output primitives for convolution */
    mkldnn_primitive_t mkldnn_memory_prim_src =
        opkernel->reorder_i[0] ? opkernel->internal_inputs[0].prim
                                : opkernel->inputs[0].prim;
    mkldnn_primitive_t mkldnn_memory_prim_weights =
        opkernel->reorder_i[1] ? opkernel->internal_inputs[1].prim
                                : opkernel->inputs[1].prim;
    mkldnn_primitive_t mkldnn_memory_prim_dst =
        opkernel->reorder_o[0] ? opkernel->internal_outputs[0].prim
                                : opkernel->outputs[0].prim;

    const_mkldnn_primitive_t conv_dsts[] = {mkldnn_memory_prim_dst};

  /* create a convolution primitive */
    mkldnn_primitive_at_t conv_srcs[] = {
        mkldnn_primitive_at(mkldnn_memory_prim_src, 0),
        mkldnn_primitive_at(mkldnn_memory_prim_weights, 0)};

    MKL_CHECK(mkldnn_primitive_create(&opkernel->op_prim, opkernel->op_desc, conv_srcs, conv_dsts));

    if (opkernel->reorder_i[0])
        opkernel->net[opkernel->net_size++] = opkernel->reorder_i[0];
    if (opkernel->reorder_i[1])
        opkernel->net[opkernel->net_size++] = opkernel->reorder_i[1];
    opkernel->net[opkernel->net_size++] = opkernel->op_prim;
}

void run_mkldnn_conv_fprop_kernel(
        void* conv_src, void* conv_weights, void* conv_dst,
        mkldnn_opkernel_t opkernel) {
    MKL_CHECK(mkldnn_memory_set_data_handle(opkernel->inputs[0].prim, conv_src));
    MKL_CHECK(mkldnn_memory_set_data_handle(opkernel->inputs[1].prim, conv_weights));
    MKL_CHECK(mkldnn_memory_set_data_handle(opkernel->outputs[0].prim, conv_dst));
    run_mkldnn_opkernel(opkernel);
}

void create_mkldnn_conv_bprop_data_kernel(
    mkldnn_engine_t engine, int src_dims, int weights_dims,
    int dst_dims, int stride_dims, int pad_dims, 
    int* conv_src_sizes, int* conv_weights_sizes, int* conv_dst_sizes,
    int* conv_strides, int* conv_padding,
    mkldnn_primitive_desc_t input_src_pd, mkldnn_primitive_desc_t input_weights_pd,
    mkldnn_opkernel_t opkernel) {
  
    int mkl_src_dims = 4;
    int mkl_weights_dims = 4;
    int mkl_dst_dims = 4;
    int mkl_src_sizes[4];
    int mkl_weights_sizes[4];
    int mkl_dst_sizes[4];
    int mkl_strides[2];
    int mkl_padding[2];

    /* Flatten out the depth (D, M) dimension and reorder logical dimensions to
     * match MKLDNN */
    set_mkl_dimensions("convolution", conv_src_sizes, conv_dst_sizes,
                       conv_weights_sizes, conv_strides, conv_padding,
                       mkl_src_sizes, mkl_dst_sizes, mkl_weights_sizes,
                       mkl_strides, mkl_padding);

    mkldnn_memory_desc_t mkldnn_memory_desc_src_md, mkldnn_memory_desc_weights_md,
       mkldnn_memory_desc_dst_md;
    MKL_CHECK(mkldnn_memory_desc_init(&mkldnn_memory_desc_src_md, mkl_src_dims,
                                      mkl_src_sizes, mkldnn_f32, mkldnn_any));
    MKL_CHECK(mkldnn_memory_desc_init(&mkldnn_memory_desc_weights_md,
                                      mkl_weights_dims, mkl_weights_sizes,
                                      mkldnn_f32, mkldnn_any));
    MKL_CHECK(mkldnn_memory_desc_init(&mkldnn_memory_desc_dst_md, mkl_dst_dims,
                                      mkl_dst_sizes, mkldnn_f32, mkldnn_any));
    mkldnn_convolution_desc_t conv_desc_data;
    MKL_CHECK(mkldnn_convolution_backward_data_desc_init(
        &conv_desc_data, mkldnn_convolution_direct, &mkldnn_memory_desc_dst_md,
        &mkldnn_memory_desc_weights_md, &mkldnn_memory_desc_src_md, mkl_strides,
        mkl_padding, mkl_padding, mkldnn_padding_zero));
    MKL_CHECK(mkldnn_primitive_desc_create(&opkernel->op_desc, &conv_desc_data, engine, NULL));


    const_mkldnn_primitive_desc_t kernel_src_pd =
        mkldnn_primitive_desc_query_pd(opkernel->op_desc, mkldnn_query_diff_dst_pd, 0);
    const_mkldnn_primitive_desc_t kernel_weights_pd =
        mkldnn_primitive_desc_query_pd(opkernel->op_desc, mkldnn_query_weights_pd, 0);
    const_mkldnn_primitive_desc_t kernel_dst_pd =
        mkldnn_primitive_desc_query_pd(opkernel->op_desc, mkldnn_query_diff_src_pd, 0);

    if (input_src_pd) {
        mkldnn_memory_desc_t md = *(mkldnn_primitive_desc_query_memory_d((const_mkldnn_primitive_desc_t)input_src_pd));
        create_mkldnn_tensor_from_pd(mkl_src_dims, mkl_src_sizes, &md,
                            engine, &(opkernel->inputs[0]));
    } else {
        create_mkldnn_tensor(mkl_src_dims, mkl_src_sizes, mkldnn_f32, mkldnn_chwn,
                            engine, &(opkernel->inputs[0]));
    }
    if (input_weights_pd) {
        mkldnn_memory_desc_t md = *(mkldnn_primitive_desc_query_memory_d((const_mkldnn_primitive_desc_t)input_weights_pd));
        create_mkldnn_tensor_from_pd(mkl_weights_dims, mkl_weights_sizes, &md,
                            engine, &(opkernel->inputs[1]));
    } else {
        create_mkldnn_tensor(mkl_weights_dims, mkl_weights_sizes, mkldnn_f32, mkldnn_ihwo,
                            engine, &(opkernel->inputs[1]));
    }
    mkldnn_memory_desc_t dst_md = *mkldnn_primitive_desc_query_memory_d(kernel_dst_pd);
    create_mkldnn_tensor_from_pd(mkl_dst_dims, mkl_dst_sizes, &dst_md,
                        engine, &(opkernel->outputs[0]));
    opkernel->num_inputs = 2;
    opkernel->num_outputs = 1;

    // Reorder inputs 
    if (!mkldnn_memory_primitive_desc_equal(opkernel->inputs[0].desc, kernel_src_pd)) {
        mkldnn_memory_desc_t md = *mkldnn_primitive_desc_query_memory_d(kernel_src_pd);
        create_mkldnn_tensor_from_pd(mkl_src_dims, mkl_src_sizes, &md, engine, &(opkernel->internal_inputs[0]));
        mkldnn_primitive_desc_t reorder_pd;
        MKL_CHECK(mkldnn_reorder_primitive_desc_create(&reorder_pd,
                    opkernel->inputs[0].desc, kernel_src_pd));
        mkldnn_primitive_at_t inputs[] = {opkernel->inputs[0].prim};
        const_mkldnn_primitive_t outputs[] = {opkernel->internal_inputs[0].prim};
        MKL_CHECK(mkldnn_primitive_create(&(opkernel->reorder_i[0]), reorder_pd, inputs, outputs));
    } else {
        opkernel->reorder_i[0] = NULL;
    }


    if (!mkldnn_memory_primitive_desc_equal(opkernel->inputs[1].desc, kernel_weights_pd)) {
        mkldnn_memory_desc_t md = *mkldnn_primitive_desc_query_memory_d(kernel_weights_pd);
        create_mkldnn_tensor_from_pd(mkl_weights_dims, mkl_weights_sizes, &md, engine, &(opkernel->internal_inputs[1]));
        mkldnn_primitive_desc_t reorder_pd;
        MKL_CHECK(mkldnn_reorder_primitive_desc_create(&reorder_pd,
                    opkernel->inputs[1].desc, kernel_weights_pd));
        mkldnn_primitive_at_t inputs[] = {opkernel->inputs[1].prim};
        const_mkldnn_primitive_t outputs[] = {opkernel->internal_inputs[1].prim};
        MKL_CHECK(mkldnn_primitive_create(&(opkernel->reorder_i[1]), reorder_pd, inputs, outputs));
    } else {
        opkernel->reorder_i[1] = NULL;
    }

    // No reorder on the output side
    opkernel->reorder_o[0] = NULL; 

    /* Allocate memory for internal format conversions */
    if (opkernel->reorder_i[0]) {
        float* tmp_buf =
            (float*)calloc(product(conv_src_sizes, src_dims), sizeof(float));
        opkernel->internal_inputs[0].buffer = tmp_buf;
        MKL_CHECK(mkldnn_memory_set_data_handle(opkernel->internal_inputs[0].prim,
                                                tmp_buf));
    }
    if (opkernel->reorder_i[1]) {
        float* tmp_buf =
            (float*)calloc(product(conv_weights_sizes, weights_dims), sizeof(float));
        opkernel->internal_inputs[1].buffer = tmp_buf;
        MKL_CHECK(mkldnn_memory_set_data_handle(opkernel->internal_inputs[1].prim,
                                                tmp_buf));
    }

    /* select input and output primitives for convolution */
    mkldnn_primitive_t mkldnn_memory_prim_src =
        opkernel->reorder_i[0] ? opkernel->internal_inputs[0].prim
                                : opkernel->inputs[0].prim;
    mkldnn_primitive_t mkldnn_memory_prim_weights =
        opkernel->reorder_i[1] ? opkernel->internal_inputs[1].prim
                                : opkernel->inputs[1].prim;
    mkldnn_primitive_t mkldnn_memory_prim_dst = opkernel->outputs[0].prim;

    const_mkldnn_primitive_t conv_dsts[] = {mkldnn_memory_prim_dst};

  /* create a convolution primitive */
    mkldnn_primitive_at_t conv_srcs[] = {
        mkldnn_primitive_at(mkldnn_memory_prim_src, 0),
        mkldnn_primitive_at(mkldnn_memory_prim_weights, 0)};

    MKL_CHECK(mkldnn_primitive_create(&opkernel->op_prim, opkernel->op_desc, conv_srcs, conv_dsts));

    if (opkernel->reorder_i[0])
        opkernel->net[opkernel->net_size++] = opkernel->reorder_i[0];
    if (opkernel->reorder_i[1])
        opkernel->net[opkernel->net_size++] = opkernel->reorder_i[1];
    opkernel->net[opkernel->net_size++] = opkernel->op_prim;
}

void run_mkldnn_conv_bprop_data_kernel(
        void* conv_src, void* conv_weights, void* conv_dst,
        mkldnn_opkernel_t opkernel) {
    MKL_CHECK(mkldnn_memory_set_data_handle(opkernel->inputs[0].prim, conv_src));
    MKL_CHECK(mkldnn_memory_set_data_handle(opkernel->inputs[1].prim, conv_weights));
    MKL_CHECK(mkldnn_memory_set_data_handle(opkernel->outputs[0].prim, conv_dst));
    run_mkldnn_opkernel(opkernel);
}

// src - diff_dst
// weights - diff_weights
// dst - fprop_src
void create_mkldnn_conv_bprop_weights_kernel(
    mkldnn_engine_t engine, int src_dims, int weights_dims,
    int dst_dims, int stride_dims, int pad_dims, 
    int* conv_src_sizes, int* conv_weights_sizes, int* conv_dst_sizes,
    int* conv_strides, int* conv_padding,
    mkldnn_primitive_desc_t input_src_pd, mkldnn_primitive_desc_t input_weights_pd, mkldnn_primitive_desc_t input_dst_pd,
    mkldnn_opkernel_t opkernel) {
  
    int mkl_src_dims = 4;
    int mkl_weights_dims = 4;
    int mkl_dst_dims = 4;
    int mkl_src_sizes[4];
    int mkl_weights_sizes[4];
    int mkl_dst_sizes[4];
    int mkl_strides[2];
    int mkl_padding[2];

    /* Flatten out the depth (D, M) dimension and reorder logical dimensions to
     * match MKLDNN */
    set_mkl_dimensions("convolution", conv_src_sizes, conv_dst_sizes,
                       conv_weights_sizes, conv_strides, conv_padding,
                       mkl_src_sizes, mkl_dst_sizes, mkl_weights_sizes,
                       mkl_strides, mkl_padding);

    mkldnn_memory_desc_t mkldnn_memory_desc_src_md, mkldnn_memory_desc_dst_md, mkldnn_memory_desc_weights_md;
    MKL_CHECK(mkldnn_memory_desc_init(&mkldnn_memory_desc_src_md, mkl_src_dims,
                                      mkl_src_sizes, mkldnn_f32, mkldnn_any));
    MKL_CHECK(mkldnn_memory_desc_init(&mkldnn_memory_desc_weights_md,
                                      mkl_weights_dims, mkl_weights_sizes,
                                      mkldnn_f32, mkldnn_any));
    MKL_CHECK(mkldnn_memory_desc_init(&mkldnn_memory_desc_dst_md, mkl_dst_dims,
                                      mkl_dst_sizes, mkldnn_f32, mkldnn_any));
    mkldnn_convolution_desc_t conv_desc_weights;
    MKL_CHECK(mkldnn_convolution_backward_weights_desc_init(
        &conv_desc_weights, mkldnn_convolution_direct, &mkldnn_memory_desc_dst_md,
        &mkldnn_memory_desc_weights_md, NULL, &mkldnn_memory_desc_src_md, mkl_strides,
        mkl_padding, mkl_padding, mkldnn_padding_zero));
    MKL_CHECK(mkldnn_primitive_desc_create(&opkernel->op_desc, &conv_desc_weights, engine, NULL));


    const_mkldnn_primitive_desc_t kernel_src_pd =
        mkldnn_primitive_desc_query_pd(opkernel->op_desc, mkldnn_query_diff_dst_pd, 0);
    const_mkldnn_primitive_desc_t kernel_weights_pd =
        mkldnn_primitive_desc_query_pd(opkernel->op_desc, mkldnn_query_diff_weights_pd, 0);
    const_mkldnn_primitive_desc_t kernel_dst_pd =
        mkldnn_primitive_desc_query_pd(opkernel->op_desc, mkldnn_query_src_pd, 0);

    if (input_src_pd) {
        mkldnn_memory_desc_t md = *(mkldnn_primitive_desc_query_memory_d((const_mkldnn_primitive_desc_t)input_src_pd));
        create_mkldnn_tensor_from_pd(mkl_src_dims, mkl_src_sizes, &md,
                            engine, &(opkernel->inputs[0]));
    } else {
        create_mkldnn_tensor(mkl_src_dims, mkl_src_sizes, mkldnn_f32, mkldnn_chwn,
                            engine, &(opkernel->inputs[0]));
    }
    if (input_dst_pd) {
        mkldnn_memory_desc_t md = *(mkldnn_primitive_desc_query_memory_d((const_mkldnn_primitive_desc_t)input_dst_pd));
        create_mkldnn_tensor_from_pd(mkl_dst_dims, mkl_dst_sizes, &md,
                            engine, &(opkernel->inputs[1]));
    } else {
        create_mkldnn_tensor(mkl_dst_dims, mkl_dst_sizes, mkldnn_f32, mkldnn_chwn,
                            engine, &(opkernel->inputs[1]));
    }
    if (input_weights_pd) {
        mkldnn_memory_desc_t md = *(mkldnn_primitive_desc_query_memory_d((const_mkldnn_primitive_desc_t)input_weights_pd));
        create_mkldnn_tensor_from_pd(mkl_weights_dims, mkl_weights_sizes, &md,
                            engine, &(opkernel->outputs[0]));
    } else {
        create_mkldnn_tensor(mkl_weights_dims, mkl_weights_sizes, mkldnn_f32, mkldnn_ihwo,
                            engine, &(opkernel->outputs[0]));
    }
    opkernel->num_inputs = 2;
    opkernel->num_outputs = 1;

    // Reorder inputs 
    if (!mkldnn_memory_primitive_desc_equal(opkernel->inputs[0].desc, kernel_src_pd)) {
        mkldnn_memory_desc_t md = *mkldnn_primitive_desc_query_memory_d(kernel_src_pd);
        create_mkldnn_tensor_from_pd(mkl_src_dims, mkl_src_sizes, &md, engine, &(opkernel->internal_inputs[0]));
        mkldnn_primitive_desc_t reorder_pd;
        MKL_CHECK(mkldnn_reorder_primitive_desc_create(&reorder_pd,
                    opkernel->inputs[0].desc, kernel_src_pd));
        mkldnn_primitive_at_t inputs[] = {opkernel->inputs[0].prim};
        const_mkldnn_primitive_t outputs[] = {opkernel->internal_inputs[0].prim};
        MKL_CHECK(mkldnn_primitive_create(&(opkernel->reorder_i[0]), reorder_pd, inputs, outputs));
    } else {
        opkernel->reorder_i[0] = NULL;
    }

    if (!mkldnn_memory_primitive_desc_equal(opkernel->inputs[1].desc, kernel_dst_pd)) {
        mkldnn_memory_desc_t md = *mkldnn_primitive_desc_query_memory_d(kernel_dst_pd);
        create_mkldnn_tensor_from_pd(mkl_dst_dims, mkl_dst_sizes, &md, engine, &(opkernel->internal_inputs[1]));
        mkldnn_primitive_desc_t reorder_pd;
        MKL_CHECK(mkldnn_reorder_primitive_desc_create(&reorder_pd,
                    opkernel->inputs[1].desc, kernel_dst_pd));
        mkldnn_primitive_at_t inputs[] = {opkernel->inputs[1].prim};
        const_mkldnn_primitive_t outputs[] = {opkernel->internal_inputs[1].prim};
        MKL_CHECK(mkldnn_primitive_create(&(opkernel->reorder_i[1]), reorder_pd, inputs, outputs));
    } else {
        opkernel->reorder_i[1] = NULL;
    }

    if (!mkldnn_memory_primitive_desc_equal(opkernel->outputs[0].desc, kernel_weights_pd)) {
        mkldnn_memory_desc_t md = *mkldnn_primitive_desc_query_memory_d(kernel_weights_pd);
        create_mkldnn_tensor_from_pd(mkl_weights_dims, mkl_weights_sizes, &md, engine, &(opkernel->internal_outputs[0]));
        mkldnn_primitive_desc_t reorder_pd;
        MKL_CHECK(mkldnn_reorder_primitive_desc_create(&reorder_pd,
                    opkernel->outputs[0].desc, kernel_weights_pd));
        mkldnn_primitive_at_t outputs[] = {opkernel->outputs[0].prim};
        const_mkldnn_primitive_t inputs[] = {opkernel->internal_outputs[0].prim};
        MKL_CHECK(mkldnn_primitive_create(&(opkernel->reorder_o[0]), reorder_pd, inputs, outputs));
    } else {
        opkernel->reorder_o[0] = NULL;
    }

    /* Allocate memory for internal format conversions */
    if (opkernel->reorder_i[0]) {
        float* tmp_buf =
            (float*)calloc(product(conv_src_sizes, src_dims), sizeof(float));
        opkernel->internal_inputs[0].buffer = tmp_buf;
        MKL_CHECK(mkldnn_memory_set_data_handle(opkernel->internal_inputs[0].prim,
                                                tmp_buf));
    }
    if (opkernel->reorder_i[1]) {
        float* tmp_buf =
            (float*)calloc(product(conv_dst_sizes, dst_dims), sizeof(float));
        opkernel->internal_inputs[1].buffer = tmp_buf;
        MKL_CHECK(mkldnn_memory_set_data_handle(opkernel->internal_inputs[1].prim,
                                                tmp_buf));
    }
    if (opkernel->reorder_o[0]) {
        float* tmp_buf =
            (float*)calloc(product(conv_weights_sizes, weights_dims), sizeof(float));
        opkernel->internal_outputs[0].buffer = tmp_buf;
        MKL_CHECK(mkldnn_memory_set_data_handle(opkernel->internal_outputs[0].prim,
                                                tmp_buf));
    }

    /* select input and output primitives for convolution */
    mkldnn_primitive_t mkldnn_memory_prim_src =
        opkernel->reorder_i[0] ? opkernel->internal_inputs[0].prim
                                : opkernel->inputs[0].prim;
    mkldnn_primitive_t mkldnn_memory_prim_dst =
        opkernel->reorder_i[1] ? opkernel->internal_inputs[1].prim
                                : opkernel->inputs[1].prim;
    mkldnn_primitive_t mkldnn_memory_prim_weights =
        opkernel->reorder_o[0] ? opkernel->internal_outputs[0].prim
                                : opkernel->outputs[0].prim;

    const_mkldnn_primitive_t conv_dsts[] = {mkldnn_memory_prim_weights};

  /* create a convolution primitive */
    mkldnn_primitive_at_t conv_srcs[] = {
        mkldnn_primitive_at(mkldnn_memory_prim_dst, 0),
        mkldnn_primitive_at(mkldnn_memory_prim_src, 0)};

    MKL_CHECK(mkldnn_primitive_create(&opkernel->op_prim, opkernel->op_desc, conv_srcs, conv_dsts));

    if (opkernel->reorder_i[0])
        opkernel->net[opkernel->net_size++] = opkernel->reorder_i[0];
    if (opkernel->reorder_i[1])
        opkernel->net[opkernel->net_size++] = opkernel->reorder_i[1];
    opkernel->net[opkernel->net_size++] = opkernel->op_prim;
    if (opkernel->reorder_o[0])
        opkernel->net[opkernel->net_size++] = opkernel->reorder_o[0];
}

void run_mkldnn_conv_bprop_weights_kernel(
        void* conv_src, void* conv_weights, void* conv_dst,
        mkldnn_opkernel_t opkernel) {
    MKL_CHECK(mkldnn_memory_set_data_handle(opkernel->inputs[0].prim, conv_src));
    MKL_CHECK(mkldnn_memory_set_data_handle(opkernel->inputs[1].prim, conv_dst));
    MKL_CHECK(mkldnn_memory_set_data_handle(opkernel->outputs[0].prim, conv_weights));
    run_mkldnn_opkernel(opkernel);
}

