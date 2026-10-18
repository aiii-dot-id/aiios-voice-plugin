def predictor_fp32(model, torch):
    predictor = model.model.talker.code_predictor
    predictor.to(dtype=torch.float32)
    for embedding in predictor.get_input_embeddings():
        embedding.to(dtype=torch.float16)

    def promote(_module, args, kwargs):
        kwargs = dict(kwargs)
        if kwargs.get("inputs_embeds") is not None:
            kwargs["inputs_embeds"] = kwargs["inputs_embeds"].to(dtype=torch.float32)
        elif args:
            raise ValueError(
                "Predictor positional input violates the pinned keyword-call seam"
            )
        return args, kwargs

    handle = predictor.model.register_forward_pre_hook(promote, with_kwargs=True)
    return handle, {
        "fp32_modules": ["talker.code_predictor"],
        "fp16_embedding_boundary": True,
        "observed_failure": "talker.code_predictor.model.layers.2.mlp.down_proj overflow",
        "clips_or_nan_replacements": False,
    }
