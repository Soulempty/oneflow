import oneflow as flow


class MaskHead(object):
    def __init__(self, cfg):
        self.cfg = cfg

    # In mask_head, we reuse pos_proposals and pos_gt_indices from box_head,
    # instead of using matcher to calculate them. We can do this because
    # mask_head and box_head use the same matcher in R_50_FPN_1x training.
    # TODO: add matcher to mask_head for the case that mask_head and box_head
    # use different matchers.

    # pos_proposals: list of [num_pos_rois, 4] wrt. images
    # pos_gt_indices: list of [num_pos_rois,] wrt. images
    # gt_segms: list of (G, 7, 7) wrt. images
    # gt_labels: list of (G,) wrt. images
    # features: list of [N, C_i, H_i, W_i] wrt. fpn layers
    def build_train(
        self, pos_proposals, pos_gt_indices, gt_segms, gt_labels, features
    ):
        with flow.deprecated.variable_scope("mask"):
            img_ids = flow.concat(
                flow.detection.extract_piece_slice_id(pos_proposals), axis=0
            )
            proposals = flow.concat(pos_proposals, axis=0)

            # mask head feature extractor
            x = self.mask_feature_extractor(proposals, img_ids, features)

            # mask head predictor
            mask_fcn_logits = self.mask_predictor(x)

            gt_segm_list = []
            gt_label_list = []
            for img_idx in range(self.cfg.TRAINING_CONF.IMG_PER_GPU):
                # if it is mask target projected, not need to do piece_slice
                if isinstance(gt_segms, (list, tuple)):
                    gt_segm_list.append(
                        flow.local_gather(
                            gt_segms[img_idx], pos_gt_indices[img_idx]
                        )
                    )
                gt_label_list.append(
                    flow.local_gather(
                        gt_labels[img_idx], pos_gt_indices[img_idx]
                    )
                )

            gt_labels = flow.concat(
                gt_label_list, axis=0, name="concat_gt_labels"
            )

            mask_pred = flow.squeeze(
                flow.gather(
                    params=mask_fcn_logits,
                    indices=flow.expand_dims(gt_labels, 1),
                    batch_dims=1,
                    name="gather_mask_fcn_logits",
                ),
                axis=[1],
                name="squeeze_mask_pred",
            )

            if isinstance(gt_segms, (list, tuple)):
                gt_segms = flow.concat(
                    gt_segm_list, axis=0, name="concat_gt_segms"
                )
                gt_segms = flow.detection.masks_crop_and_resize(
                    flow.expand_dims(gt_segms, 1),
                    proposals,
                    mask_pred.shape[1],
                    mask_pred.shape[2],
                )
                gt_segms = flow.squeeze(gt_segms, axis=[1])

            mask_loss = flow.math.reduce_sum(
                flow.nn.sigmoid_cross_entropy_with_logits(gt_segms, mask_pred)
            )

            elem_cnt = flow.elem_cnt(gt_labels, dtype=mask_loss.dtype) * (
                gt_segms.shape[1] * gt_segms.shape[2]
            )

            mask_loss = mask_loss / elem_cnt
            return mask_loss

    def build_eval(self, proposals, features):
        with flow.deprecated.variable_scope("mask"):
            image_ids = flow.concat(
                flow.detection.extract_piece_slice_id(proposals), axis=0
            )
            proposals = flow.concat(proposals, axis=0)
            x = self.mask_feature_extractor(proposals, image_ids, features)
            mask_logits = self.mask_predictor(x)

        return mask_logits

    def mask_feature_extractor(self, proposals, img_ids, features):
        proposals_with_img_ids = flow.concat(
            [flow.expand_dims(flow.cast(img_ids, flow.float), 1), proposals],
            axis=1,
        )
        levels = flow.detection.level_map(proposals)
        level_idx_dict = {}
        for (level, scalar) in zip(range(2, 6), range(0, 4)):
            level_idx_dict[level] = flow.squeeze(
                flow.local_nonzero(
                    levels == flow.constant_scalar(int(scalar), flow.int32)
                ),
                axis=[1],
                name="squeeze_level_idx_" + str(scalar),
            )

        roi_features_list = []
        for (level, i) in zip(range(2, 6), range(0, 4)):
            roi_feature_i = flow.detection.roi_align(
                features[i],
                rois=flow.local_gather(
                    proposals_with_img_ids, level_idx_dict[level]
                ),
                pooled_h=self.cfg.MASK_HEAD.POOLED_H,
                pooled_w=self.cfg.MASK_HEAD.POOLED_W,
                spatial_scale=self.cfg.MASK_HEAD.SPATIAL_SCALE / pow(2, i),
                sampling_ratio=self.cfg.MASK_HEAD.SAMPLING_RATIO,
                name="mask_roi_align_" + str(i),
            )
            roi_features_list.append(roi_feature_i)

        roi_features = flow.stack(
            roi_features_list, axis=0, name="stack_roi_features"
        )
        origin_indices = flow.stack(
            list(level_idx_dict.values()), axis=0, name="stack_origin_indices"
        )
        x = flow.local_scatter_nd_update(
            flow.constant_like(roi_features, float(0)),
            flow.expand_dims(origin_indices, axis=1),
            roi_features,
        )
        for i in range(1, 5):
            x = flow.layers.conv2d(
                inputs=x,
                filters=256,
                kernel_size=[3, 3],
                strides=[1, 1],
                padding="SAME",
                data_format="NCHW",
                dilation_rate=[1, 1],
                activation=flow.keras.activations.relu,
                use_bias=True,
                kernel_initializer=flow.kaiming_initializer(
                    shape=(256, x.static_shape[1]) + (3, 3),
                    distribution="random_normal",
                    mode="fan_out",
                    nonlinearity="relu",
                ),
                bias_initializer=flow.constant_initializer(0),
                name="fcn{}".format(i),
            )

        return x

    def mask_predictor(self, x):
        filter = flow.get_variable(
            "conv5-weight",
            shape=(x.static_shape[1], 256, 2, 2),
            dtype=x.dtype,
            initializer=flow.kaiming_initializer(
                shape=(x.static_shape[1], 256, 2, 2),
                distribution="random_normal",
                mode="fan_out",
                nonlinearity="relu",
            ),
        )
        bias = flow.get_variable(
            name="conv5-bias",
            shape=(256,),
            dtype=x.dtype,
            initializer=flow.constant_initializer(0),
            model_name="bias",
        )
        x = flow.nn.conv2d_transpose(
            x,
            filter=filter,
            data_format="NCHW",
            padding="same",
            strides=[2, 2],
            name="conv5",
        )
        x = flow.nn.bias_add(x, bias, "NCHW", name="conv5_bias_add")
        x = flow.keras.activations.relu(x, name="conv5_relu")
        x = flow.layers.conv2d(
            x,
            filters=81,
            kernel_size=[1, 1],
            data_format="NCHW",
            padding="SAME",
            strides=[1, 1],
            dilation_rate=[1, 1],
            kernel_initializer=flow.kaiming_initializer(
                shape=(81, x.static_shape[1]) + (1, 1),
                distribution="random_normal",
                mode="fan_out",
                nonlinearity="relu",
            ),
            bias_initializer=flow.constant_initializer(0),
            name="fcn_logits",
        )

        return x