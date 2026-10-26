"""Sparse continuous-time linear baseline for compact signaling hypotheses."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .candidates import CandidateGraph


@dataclass
class DynamicFit:
    candidate: CandidateGraph
    parameters: np.ndarray
    parameter_names: tuple[str, ...]
    transition_matrix: np.ndarray
    input_vector: np.ndarray
    training_sigma: np.ndarray
    objective: float
    converged: bool
    nfev: int
    seed: int


@dataclass
class _LeastSquaresResult:
    x: np.ndarray
    fun: np.ndarray
    success: bool
    nfev: int


def _matrix_exponential(matrix: np.ndarray) -> np.ndarray:
    """Scaling-and-squaring Taylor exponential for the pilot's small matrices."""

    matrix = np.asarray(matrix, dtype=float)
    size = matrix.shape[0]
    norm = float(np.linalg.norm(matrix, ord=np.inf))
    scale_power = max(0, int(math.ceil(math.log2(norm))) if norm > 1.0 else 0)
    scaled = matrix / (2**scale_power)
    result = np.eye(size)
    term = np.eye(size)
    for order in range(1, 80):
        term = term @ scaled / order
        result = result + term
        if float(np.linalg.norm(term, ord=np.inf)) < 1e-14:
            break
    for _ in range(scale_power):
        result = result @ result
    return result


def _bounded_least_squares(
    residual_function,
    initial: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    *,
    max_nfev: int,
) -> _LeastSquaresResult:
    """Small deterministic Levenberg-Marquardt solver with box bounds."""

    x = np.clip(np.asarray(initial, dtype=float), lower, upper)
    residual = residual_function(x)
    evaluations = 1
    damping = 1e-2
    success = False
    while evaluations < max_nfev:
        parameter_count = len(x)
        jacobian = np.empty((len(residual), parameter_count), dtype=float)
        for column in range(parameter_count):
            step = 1e-5 * max(1.0, abs(float(x[column])))
            trial = x.copy()
            trial[column] = min(upper[column], x[column] + step)
            actual_step = trial[column] - x[column]
            if actual_step <= 0:
                trial[column] = max(lower[column], x[column] - step)
                actual_step = trial[column] - x[column]
            trial_residual = residual_function(trial)
            evaluations += 1
            jacobian[:, column] = (trial_residual - residual) / actual_step
        normal = jacobian.T @ jacobian + damping * np.eye(parameter_count)
        gradient = jacobian.T @ residual
        try:
            step_vector = np.linalg.solve(normal, -gradient)
        except np.linalg.LinAlgError:
            step_vector = np.linalg.lstsq(normal, -gradient, rcond=None)[0]
        if float(np.linalg.norm(step_vector)) < 1e-7 * (1.0 + float(np.linalg.norm(x))):
            success = True
            break
        proposal = np.clip(x + step_vector, lower, upper)
        proposal_residual = residual_function(proposal)
        evaluations += 1
        if float(np.dot(proposal_residual, proposal_residual)) < float(np.dot(residual, residual)):
            x = proposal
            residual = proposal_residual
            damping = max(damping / 2.5, 1e-8)
            if float(np.linalg.norm(step_vector)) < 1e-5:
                success = True
                break
        else:
            damping = min(damping * 5.0, 1e8)
        if damping >= 1e8:
            break
    return _LeastSquaresResult(x=x, fun=residual, success=success, nfev=evaluations)


def parameter_names(candidate: CandidateGraph) -> tuple[str, ...]:
    names = [f"log_decay[{state}]" for state in candidate.states]
    names.extend(f"input[{candidate.source_symbol}->{target}]" for target in candidate.input_targets)
    names.extend(f"edge[{source}->{target}]" for source, target in candidate.edges)
    return tuple(names)


def unpack_parameters(candidate: CandidateGraph, parameters: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    states = list(candidate.states)
    index = {state: position for position, state in enumerate(states)}
    count = len(states)
    matrix = np.zeros((count, count), dtype=float)
    vector = np.zeros(count, dtype=float)
    cursor = 0
    decays = np.exp(parameters[cursor : cursor + count])
    matrix[np.arange(count), np.arange(count)] = -decays
    cursor += count
    for target in candidate.input_targets:
        vector[index[target]] = parameters[cursor]
        cursor += 1
    for source, target in candidate.edges:
        matrix[index[target], index[source]] = parameters[cursor]
        cursor += 1
    return matrix, vector


def predict_from_matrices(matrix: np.ndarray, vector: np.ndarray, times: np.ndarray) -> np.ndarray:
    """Exact response to a unit step input with x(0)=0."""

    count = matrix.shape[0]
    augmented = np.zeros((count + 1, count + 1), dtype=float)
    augmented[:count, :count] = matrix
    augmented[:count, count] = vector
    initial = np.zeros(count + 1, dtype=float)
    initial[count] = 1.0
    predictions = []
    for time in np.asarray(times, dtype=float):
        predictions.append((_matrix_exponential(augmented * float(time)) @ initial)[:count])
    return np.asarray(predictions)


def predict_dynamic_model(fit: DynamicFit, times: np.ndarray) -> np.ndarray:
    return predict_from_matrices(fit.transition_matrix, fit.input_vector, times)


def _initial_parameters(candidate: CandidateGraph, rng: np.random.Generator) -> np.ndarray:
    count = len(candidate.states)
    values = np.zeros(len(parameter_names(candidate)), dtype=float)
    values[:count] = math.log(0.35) + rng.normal(0, 0.15, size=count)
    values[count:] = rng.normal(0.15, 0.15, size=len(values) - count)
    return values


def _derivative_initial_parameters(
    candidate: CandidateGraph,
    times: np.ndarray,
    values: np.ndarray,
    training_indices: np.ndarray,
    ridge: float,
) -> np.ndarray:
    """Initialize the ODE from ridge fits to irregular finite differences."""

    state_count = len(candidate.states)
    state_index = {state: index for index, state in enumerate(candidate.states)}
    means = np.nanmean(values, axis=1)
    ordered = sorted(int(index) for index in training_indices)
    interval_states: list[np.ndarray] = []
    derivatives: list[np.ndarray] = []
    previous_time = 0.0
    previous_state = np.zeros(state_count, dtype=float)
    for index in ordered:
        current_state = means[index]
        current_time = float(times[index])
        if not np.all(np.isfinite(current_state)) or current_time <= previous_time:
            previous_time = current_time
            previous_state = current_state
            continue
        interval_states.append((previous_state + current_state) / 2.0)
        derivatives.append((current_state - previous_state) / (current_time - previous_time))
        previous_time = current_time
        previous_state = current_state
    parameters = np.zeros(len(parameter_names(candidate)), dtype=float)
    parameters[:state_count] = math.log(0.35)
    input_parameter_index = {
        target: state_count + offset for offset, target in enumerate(candidate.input_targets)
    }
    edge_start = state_count + len(candidate.input_targets)
    edge_parameter_index = {
        edge: edge_start + offset for offset, edge in enumerate(candidate.edges)
    }
    if not interval_states:
        return parameters
    states_matrix = np.asarray(interval_states, dtype=float)
    derivative_matrix = np.asarray(derivatives, dtype=float)
    for target in candidate.states:
        target_position = state_index[target]
        columns = [-states_matrix[:, target_position]]
        keys: list[tuple[str, object]] = [("decay", target)]
        if target in input_parameter_index:
            columns.append(np.ones(len(states_matrix)))
            keys.append(("input", target))
        for edge in candidate.edges:
            if edge[1] == target:
                columns.append(states_matrix[:, state_index[edge[0]]])
                keys.append(("edge", edge))
        design = np.column_stack(columns)
        outcome = derivative_matrix[:, target_position]
        usable = np.isfinite(outcome) & np.all(np.isfinite(design), axis=1)
        if not usable.any():
            continue
        regularization = max(float(ridge), 1e-6) * np.eye(design.shape[1])
        coefficients = np.linalg.solve(
            design[usable].T @ design[usable] + regularization,
            design[usable].T @ outcome[usable],
        )
        decay = float(np.clip(coefficients[0], math.exp(-4.0), math.exp(2.0)))
        parameters[target_position] = math.log(decay)
        for key, coefficient in zip(keys[1:], coefficients[1:]):
            if key[0] == "input":
                parameters[input_parameter_index[str(key[1])]] = float(np.clip(coefficient, -4.0, 4.0))
            else:
                parameters[edge_parameter_index[key[1]]] = float(np.clip(coefficient, -4.0, 4.0))
    return parameters


def _scale_from_training(values: np.ndarray, training_indices: np.ndarray) -> np.ndarray:
    subset = values[training_indices, :, :]
    scales = np.nanstd(subset.reshape(-1, subset.shape[-1]), axis=0, ddof=1)
    finite = scales[np.isfinite(scales) & (scales > 1e-8)]
    fallback = float(np.median(finite)) if len(finite) else 0.25
    return np.where(np.isfinite(scales) & (scales >= 0.08), scales, max(0.08, fallback))


def fit_dynamic_model(
    candidate: CandidateGraph,
    times: np.ndarray,
    observations: np.ndarray,
    *,
    training_time_indices: np.ndarray | None = None,
    ridge: float = 0.1,
    starts: int = 3,
    seed: int = 20261015,
    max_nfev: int = 700,
) -> DynamicFit:
    """Fit a stable sparse linear ODE to independent destructive samples."""

    times = np.asarray(times, dtype=float)
    values = np.asarray(observations, dtype=float)
    if values.shape != (len(times), 3, len(candidate.states)):
        raise ValueError(
            f"observations have shape {values.shape}; expected {(len(times), 3, len(candidate.states))}"
        )
    training = (
        np.asarray(training_time_indices, dtype=int)
        if training_time_indices is not None
        else np.arange(len(times), dtype=int)
    )
    scales = _scale_from_training(values, training)
    count = len(candidate.states)
    names = parameter_names(candidate)
    lower = np.concatenate(
        [np.full(count, -4.0), np.full(len(names) - count, -4.0)]
    )
    upper = np.concatenate(
        [np.full(count, 2.0), np.full(len(names) - count, 4.0)]
    )

    def residuals(parameters: np.ndarray) -> np.ndarray:
        matrix, vector = unpack_parameters(candidate, parameters)
        prediction = predict_from_matrices(matrix, vector, times)
        residual_parts: list[np.ndarray] = []
        for time_index in training:
            difference = (prediction[time_index][None, :] - values[time_index]) / scales[None, :]
            residual_parts.append(difference[np.isfinite(difference)])
        # Decays are regularized toward a one-to-several-minute time scale;
        # couplings are shrunk toward zero. These are penalties, not priors.
        penalty = np.concatenate(
            [0.2 * (parameters[:count] - math.log(0.35)), parameters[count:]]
        )
        residual_parts.append(math.sqrt(max(ridge, 0.0)) * penalty)
        return np.concatenate(residual_parts)

    best = None
    rng = np.random.default_rng(seed)
    initial_values = [_derivative_initial_parameters(candidate, times, values, training, ridge)]
    initial_values.extend(_initial_parameters(candidate, rng) for _ in range(max(0, starts - 1)))
    for initial in initial_values:
        result = _bounded_least_squares(
            residuals,
            initial,
            lower,
            upper,
            max_nfev=max_nfev,
        )
        if best is None or float(np.sum(np.square(result.fun))) < float(np.sum(np.square(best.fun))):
            best = result
    assert best is not None
    matrix, vector = unpack_parameters(candidate, best.x)
    prediction = predict_from_matrices(matrix, vector, times)
    residual_matrix = values[training] - prediction[training, None, :]
    residual_sigma = np.sqrt(np.nanmean(np.square(residual_matrix), axis=(0, 1)))
    residual_sigma = np.where(np.isfinite(residual_sigma) & (residual_sigma >= 0.08), residual_sigma, scales)
    return DynamicFit(
        candidate=candidate,
        parameters=np.asarray(best.x, dtype=float),
        parameter_names=names,
        transition_matrix=matrix,
        input_vector=vector,
        training_sigma=residual_sigma,
        objective=float(np.sum(np.square(best.fun))),
        converged=bool(best.success),
        nfev=int(best.nfev),
        seed=seed,
    )


def held_out_log_predictive_density(
    fit: DynamicFit,
    times: np.ndarray,
    observations: np.ndarray,
    held_out_indices: np.ndarray,
) -> tuple[float, float, int]:
    prediction = predict_dynamic_model(fit, times)
    sigma = np.maximum(fit.training_sigma, 0.08)
    log_density = 0.0
    squared_error = 0.0
    count = 0
    for time_index in np.asarray(held_out_indices, dtype=int):
        for replicate in range(observations.shape[1]):
            observed = observations[time_index, replicate, :]
            usable = np.isfinite(observed)
            if not usable.any():
                continue
            residual = observed[usable] - prediction[time_index, usable]
            selected_sigma = sigma[usable]
            log_density += float(
                np.sum(-0.5 * np.square(residual / selected_sigma) - np.log(selected_sigma) - 0.5 * np.log(2 * np.pi))
            )
            squared_error += float(np.sum(np.square(residual)))
            count += int(usable.sum())
    rmse = math.sqrt(squared_error / count) if count else float("nan")
    return log_density, rmse, count


def clamp_intervention(fit: DynamicFit, node: str, times: np.ndarray) -> np.ndarray:
    """Idealized acute clamp-to-baseline simulation for design screening."""

    symbol = node.upper()
    if symbol not in fit.candidate.states:
        raise KeyError(node)
    position = fit.candidate.states.index(symbol)
    matrix = fit.transition_matrix.copy()
    vector = fit.input_vector.copy()
    matrix[position, :] = 0.0
    vector[position] = 0.0
    return predict_from_matrices(matrix, vector, times)


def local_identifiability(fit: DynamicFit, times: np.ndarray) -> dict[str, object]:
    """Numerical local sensitivity/Fisher audit (not a structural certificate)."""

    base = predict_dynamic_model(fit, times).reshape(-1)
    columns: list[np.ndarray] = []
    for index, value in enumerate(fit.parameters):
        step = 1e-5 * max(1.0, abs(float(value)))
        plus = fit.parameters.copy()
        minus = fit.parameters.copy()
        plus[index] += step
        minus[index] -= step
        plus_matrix, plus_vector = unpack_parameters(fit.candidate, plus)
        minus_matrix, minus_vector = unpack_parameters(fit.candidate, minus)
        derivative = (
            predict_from_matrices(plus_matrix, plus_vector, times).reshape(-1)
            - predict_from_matrices(minus_matrix, minus_vector, times).reshape(-1)
        ) / (2 * step)
        columns.append(derivative)
    sensitivity = np.column_stack(columns) if columns else np.empty((len(base), 0))
    singular_values = np.linalg.svd(sensitivity, compute_uv=False)
    tolerance = (
        max(sensitivity.shape) * np.finfo(float).eps * singular_values[0]
        if len(singular_values) and singular_values[0] > 0
        else 0.0
    )
    rank = int(np.sum(singular_values > tolerance))
    condition = (
        float(singular_values[0] / singular_values[-1])
        if len(singular_values) and singular_values[-1] > tolerance
        else float("inf")
    )
    return {
        "graph_id": fit.candidate.graph_id,
        "audit_type": "numerical_local_sensitivity",
        "parameter_count": len(fit.parameters),
        "local_sensitivity_rank": rank,
        "full_local_rank": rank == len(fit.parameters),
        "condition_number": condition,
        "smallest_singular_value": float(singular_values[-1]) if len(singular_values) else float("nan"),
        "largest_singular_value": float(singular_values[0]) if len(singular_values) else float("nan"),
        "interpretation": "local practical diagnostic conditional on the linear ODE and observation mapping; not global structural identification",
    }
