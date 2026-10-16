"""Explicit, length-aware export boundaries for the pinned Parakeet checkpoint.

The encoder keeps the actual feature length as DATA. Padded context is never
speech. The predictor has caller-owned recurrent state, and the joint reads
one encoder frame rather than materializing a time-by-token logits cube.
These are candidate export boundaries, not a qualified mobile backend.
"""
import torch


class LengthAwareEncoder(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.encoder = model.encoder
        self.projector = model.encoder_projector

    def forward(self, features, valid_frames):
        mask = torch.arange(features.shape[1], device=features.device)[None, :] < valid_frames[:, None]
        output = self.encoder(input_features=features, attention_mask=mask, output_attention_mask=False)
        projected = self.projector(output.last_hidden_state)
        length = self.encoder._get_subsampling_output_length(valid_frames)
        valid = torch.arange(projected.shape[1], device=features.device)[None, :] < length[:, None]
        # Fully masked attention rows have no speech meaning and lower
        # differently across runtimes. Define their exported value explicitly,
        # AFTER projection (which may have bias). Valid speech is untouched.
        return torch.where(valid[:, :, None], projected, 0.0), length


class PredictorStep(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.embedding = model.decoder.embedding
        self.lstm = model.decoder.lstm
        self.projector = model.decoder.decoder_projector

    def forward(self, token, hidden, cell):
        output, (new_hidden, new_cell) = self.lstm(self.embedding(token), (hidden, cell))
        return self.projector(output), new_hidden, new_cell


class JointStep(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.joint = model.joint

    def forward(self, encoder_frame, predictor_output):
        return self.joint(predictor_output, encoder_frame)


def greedy_decode(encoder_output, valid_frames, predictor, joint, *, max_steps=2048):
    """Single-stream TDT reference with explicit state and bounded progress.

    Do not use token zero or an empty transcript to hide invalid numerics.
    Blank emissions do not advance predictor state; only duration advances the
    encoder clock, with duration-one forced for a zero-duration blank.
    """
    if encoder_output.ndim != 3 or encoder_output.shape[0] != 1:
        raise ValueError('One encoder stream required')
    if not torch.isfinite(encoder_output).all():
        raise ValueError('Non-finite encoder output')
    limit = int(valid_frames[0])
    if not 0 < limit <= encoder_output.shape[1]:
        raise ValueError('Invalid encoder length')
    hidden = torch.zeros((2, 1, 640), dtype=encoder_output.dtype, device=encoder_output.device)
    cell = torch.zeros_like(hidden)
    token = torch.tensor([[8192]], dtype=torch.int64, device=encoder_output.device)
    predicted, hidden, cell = predictor(token, hidden, cell)
    frame = 0
    tokens = []
    steps = []
    while frame < limit:
        if len(steps) >= max_steps:
            raise RuntimeError('Decoder step budget exhausted; no complete transcript')
        logits = joint(encoder_output[:, frame:frame+1], predicted).reshape(-1)
        if logits.shape != (8198,) or not torch.isfinite(logits).all():
            raise ValueError('Invalid joint logits')
        emitted = int(logits[:8193].argmax())
        duration = int(logits[8193:].argmax())
        steps.append([frame, emitted, duration])
        if emitted != 8192:
            tokens.append(emitted)
            token.fill_(emitted)
            predicted, hidden, cell = predictor(token, hidden, cell)
        frame += 1 if emitted == 8192 and duration == 0 else duration
    return {'tokens': tokens, 'steps': steps, 'advanced_frames': frame, 'valid_encoder_frames': limit}
