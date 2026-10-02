"""Offline identity-regularized embedding adaptation; not a runtime policy.

Fit only on a speaker-disjoint training partition. Never pass evaluation
vectors or labels to fit(). Query-only use leaves the enrollment gallery
unchanged. A fitted matrix is an experiment, not a qualified model: measured
held-out identification, unknown rejection and selected-PCM gates still apply.
"""
import numpy as np


def vectors(value):
    x = np.asarray(value, dtype=np.float64)
    if x.ndim != 2 or not x.shape[0] or not 2 <= x.shape[1] <= 512:
        raise ValueError('expected nonempty bounded embedding matrix')
    lengths = np.linalg.norm(x, axis=1, keepdims=True)
    if not np.isfinite(x).all() or not np.isfinite(lengths).all() or (lengths < 1e-10).any():
        raise ValueError('invalid embedding')
    return x / lengths


def disjoint_partitions(training, validation, evaluation):
    groups = [set(map(str, g)) for g in (training, validation, evaluation)]
    if any(not g for g in groups) or any(groups[i] & groups[j] for i in range(3) for j in range(i)):
        raise ValueError('speaker partitions must be nonempty and disjoint')


def fit(separated, clean, clean_controls, *, ridge=1.0, preservation=1.0):
    """Solve paired squared error + clean preservation + identity shrinkage.

    For row-vector embeddings and W=I+D, minimize
      mean(||X D - (Y-X)||^2) + preservation*mean(||C D||^2)
      + ridge*||D||_F^2 / dimensions.
    No bias or speaker-specific parameters; all arrays are normalized first.
    Positive ridge ensures a unique bounded solve even for deficient data.
    """
    x, y, c = (vectors(v) for v in (separated, clean, clean_controls))
    if x.shape != y.shape or c.shape[1] != x.shape[1]:
        raise ValueError('embedding dimensions or pair counts differ')
    if not np.isfinite(ridge) or ridge <= 0 or not np.isfinite(preservation) or preservation < 0:
        raise ValueError('invalid regularization')
    identity = np.eye(x.shape[1])
    left = x.T @ x / len(x) + preservation * (c.T @ c / len(c)) + ridge * identity / x.shape[1]
    right = x.T @ (y-x) / len(x)
    matrix = identity + np.linalg.solve(left, right)
    if not np.isfinite(matrix).all():
        raise ValueError('nonfinite fit')
    return matrix


def apply(value, matrix):
    x = vectors(value)
    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.shape != (x.shape[1], x.shape[1]) or not np.isfinite(matrix).all():
        raise ValueError('invalid adapter matrix')
    return vectors(x @ matrix)
