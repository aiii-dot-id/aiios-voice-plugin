import numpy as np
import pytest

from scripts.uid_domain_adapter import apply, disjoint_partitions, fit, vectors


def test_identity_data_retains_exact_identity():
    data = np.random.default_rng(1).normal(size=(30, 8))
    matrix = fit(data, data, data)
    np.testing.assert_array_equal(matrix, np.eye(8))
    np.testing.assert_allclose(apply(data, matrix), vectors(data))


def test_paired_distortion_improves_on_unseen_vectors():
    rng = np.random.default_rng(4)
    clean = vectors(rng.normal(size=(100, 8)))
    test = vectors(rng.normal(size=(30, 8)))
    distortion = np.diag(np.linspace(.4, 1.6, 8))
    degraded = vectors(clean @ distortion)
    matrix = fit(degraded, clean, clean, ridge=.1, preservation=.1)
    before = np.mean(np.sum(vectors(test @ distortion)*test, axis=1))
    after = np.mean(np.sum(apply(test @ distortion, matrix)*test, axis=1))
    assert after > before + .02
    # This synthetic optimization proof does not establish acoustic accuracy.


def test_clean_preservation_penalty_reduces_clean_drift():
    rng = np.random.default_rng(6)
    clean = vectors(rng.normal(size=(100, 8)))
    degraded = vectors(clean @ np.diag(np.linspace(.2, 2., 8)))
    weak = fit(degraded, clean, clean, ridge=.1, preservation=0)
    strong = fit(degraded, clean, clean, ridge=.1, preservation=10)
    assert np.linalg.norm(apply(clean, strong)-clean) < np.linalg.norm(apply(clean, weak)-clean)


def test_rank_deficient_data_and_scaling():
    x = np.tile([1., 0., 0.], (10, 1))
    y = np.tile([1., .1, 0.], (10, 1))
    a = fit(x, y, x)
    assert np.isfinite(a).all()
    np.testing.assert_allclose(a, fit(3*x, 7*y, 2*x))


def test_solution_satisfies_declared_objective_without_mutating_inputs():
    rng = np.random.default_rng(9)
    inputs = [rng.normal(size=(n, 8)) for n in (40, 40, 20)]
    copies = [x.copy() for x in inputs]
    matrix = fit(*inputs, ridge=.7, preservation=2.)
    x, y, c = map(vectors, inputs)
    residual = matrix - np.eye(8)
    gradient = (x.T @ x / len(x) + 2 * c.T @ c / len(c) + .7 * np.eye(8) / 8) @ residual - x.T @ (y-x) / len(x)
    np.testing.assert_allclose(gradient, 0, atol=1e-14)
    apply(inputs[0], matrix)
    for value, original in zip(inputs, copies):
        np.testing.assert_array_equal(value, original)


@pytest.mark.parametrize('value', [[], [[0., 0.]], [[float('nan'), 1.]], [[float('inf'), 1.]], [1., 2.]])
def test_invalid_vectors_refused(value):
    with pytest.raises(ValueError): vectors(value)


@pytest.mark.parametrize('kwargs', [{'ridge': 0}, {'ridge': -1}, {'ridge': float('nan')}, {'preservation': -1}])
def test_invalid_regularization_refused(kwargs):
    with pytest.raises(ValueError): fit(np.eye(3), np.eye(3), np.eye(3), **kwargs)


def test_extent_and_matrix_refused():
    for x, y, c in [(np.eye(2), np.eye(3), np.eye(2)), (np.eye(2), np.eye(2), np.eye(3))]:
        with pytest.raises(ValueError): fit(x, y, c)
    for matrix in [np.eye(3), np.full((2, 2), np.nan), np.zeros((2, 2))]:
        with pytest.raises(ValueError): apply(np.eye(2), matrix)


def test_speaker_overlap_and_empty_partition_refused():
    disjoint_partitions(['a'], ['b'], ['c'])
    for groups in [(['a'], ['a'], ['c']), (['a'], ['b'], ['a']), ([], ['b'], ['c']), ([1], ['1'], ['c'])]:
        with pytest.raises(ValueError): disjoint_partitions(*groups)
