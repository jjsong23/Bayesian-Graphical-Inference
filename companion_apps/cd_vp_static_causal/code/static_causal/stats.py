from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Iterable

import numpy as np


def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1e300 if abs(d) < 1e-300 else 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d; d = 1e-300 if abs(d) < 1e-300 else d
        c = 1.0 + aa / c; c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d; h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d; d = 1e-300 if abs(d) < 1e-300 else d
        c = 1.0 + aa / c; c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        delta = d * c; h *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    if x <= 0: return 0.0
    if x >= 1: return 1.0
    bt = math.exp(math.lgamma(a+b)-math.lgamma(a)-math.lgamma(b)+a*math.log(x)+b*math.log1p(-x))
    return bt * _betacf(a,b,x) / a if x < (a+1)/(a+b+2) else 1-bt*_betacf(b,a,1-x)/b


def t_two_sided_p(t: float, df: int) -> float:
    if df <= 0 or not math.isfinite(t): return math.nan
    return min(1.0, max(0.0, _betai(df / 2.0, 0.5, df / (df + t*t))))


@dataclass
class PartialCorrelationResult:
    x: str
    y: str
    conditioned: str
    n_complete: int
    effective_n: int
    rank: int
    df: int
    partial_r: float
    t_statistic: float
    p_value: float
    ci_low: float
    ci_high: float
    status: str
    detail: str
    q_value: float = math.nan

    def record(self) -> dict[str, object]: return asdict(self)


def partial_correlation(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray | None,
    names: tuple[str,str,str],
    clusters: np.ndarray|None=None,
) -> PartialCorrelationResult:
    x, y = np.asarray(x,float), np.asarray(y,float)
    z = np.empty((len(x),0)) if z is None else np.asarray(z,float).reshape(len(x),-1)
    mask = np.isfinite(x) & np.isfinite(y) & np.all(np.isfinite(z),axis=1)
    cluster_values=np.asarray(clusters,object)[mask] if clusters is not None else None
    x, y, z = x[mask], y[mask], z[mask]
    n, k = len(x), z.shape[1]
    effective_n=len(set(cluster_values.tolist())) if cluster_values is not None else n
    base = dict(x=names[0],y=names[1],conditioned=names[2],n_complete=n,effective_n=effective_n,rank=0,df=effective_n-k-2,
                partial_r=math.nan,t_statistic=math.nan,p_value=math.nan,ci_low=math.nan,ci_high=math.nan)
    if effective_n <= k + 3:
        return PartialCorrelationResult(**base,status="insufficient_observations",detail=f"need more than |S|+3 independent units; got {effective_n}")
    design = np.column_stack([np.ones(n), z])
    rank = int(np.linalg.matrix_rank(design)); base["rank"] = rank
    if rank < design.shape[1]:
        return PartialCorrelationResult(**base,status="rank_deficient",detail="conditioning design is not full rank")
    bx = np.linalg.lstsq(design,x,rcond=None)[0]; by=np.linalg.lstsq(design,y,rcond=None)[0]
    rx, ry = x-design@bx, y-design@by
    if np.std(rx)<=1e-14 or np.std(ry)<=1e-14:
        return PartialCorrelationResult(**base,status="unavailable",detail="zero residual variance")
    r=float(np.clip(np.corrcoef(rx,ry)[0,1],-0.999999999,0.999999999))
    df=effective_n-k-2; t=r*math.sqrt(df/(1-r*r)); p=t_two_sided_p(t,df)
    se=1/math.sqrt(max(effective_n-k-3,1)); zr=math.atanh(r); lo=math.tanh(zr-1.96*se); hi=math.tanh(zr+1.96*se)
    status = "supported_nonzero" if p < 0.05 and not (lo <= 0 <= hi) else "inconclusive_low_power"
    return PartialCorrelationResult(**{**base,"partial_r":r,"t_statistic":t,"p_value":p,"ci_low":lo,"ci_high":hi},status=status,
                                    detail=("two-sided partial-correlation test; inference uses matched-block effective n" if clusters is not None else
                                      "two-sided partial-correlation test; nonsignificance is not proof of independence"))


def bh_adjust(results: list[PartialCorrelationResult]) -> None:
    valid=[(i,r.p_value) for i,r in enumerate(results) if math.isfinite(r.p_value)]
    ordered=sorted(valid,key=lambda x:x[1]); m=len(ordered); running=1.0
    for rev in range(m-1,-1,-1):
        idx,p=ordered[rev]; running=min(running,p*m/(rev+1)); results[idx].q_value=running
    for r in results:
        if math.isfinite(r.q_value):
            if r.q_value < .05 and r.status == "supported_nonzero": pass
            elif r.status not in {"supported_zero","insufficient_observations","rank_deficient","unavailable"}: r.status="inconclusive_low_power"


def log_bic_evidence(r: float, n: int, expect_zero: bool) -> float:
    """Composite local score, explicitly not an independent likelihood or posterior."""
    if n < 4 or not math.isfinite(r): return 0.0
    dependence = -.5*n*math.log(max(1-r*r,1e-12)) - .5*math.log(n)
    return -dependence if expect_zero else dependence

