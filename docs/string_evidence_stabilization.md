# STRING evidence stabilization

## Problem

The retained STRING v12 relationships in the seed edge catalog have combined
scores from 0.150 to 0.999. Converting each score `s` to odds relative to
STRING's 0.041 reference probability gives

```text
BF_raw = odds(s) / odds(0.041)
```

Because the downloaded network starts above the reference probability, every
retained relationship has `BF_raw > 1`. In the 65,764-row seed catalog, the raw
BF minimum, median, 99th percentile, and maximum are 4.13, 6.83, 644.90, and
23,366.85. At weight 1 these values can overwhelm narrower evidence streams.

## Stabilized default

STRING now has a default evidence weight of 0.25 in both Version 1 and Version
2. The raw score and raw BF are not changed. Bayesian integration already uses

```text
posterior_odds = prior_odds * product(BF_stream ** weight_stream)
```

so STRING's effective multiplier is

```text
BF_effective = BF_raw ** 0.25
```

This is a fractional or power-likelihood update. It preserves every STRING
ranking, keeps the original values auditable, and reduces only the amount of
log-odds information assigned to the broad, internally combined stream.

## Choice of 0.25

The weight was selected from the observed factor distribution rather than by
changing STRING's documented reference probability. Requiring the 99th
percentile raw factor (644.90) to contribute approximately BF 5 gives

```text
w = log(5) / log(644.90) = 0.249
```

which was rounded to the transparent default `w = 0.25`.

| Retained-factor position | Raw BF | Effective BF at weight 0.25 | Posterior from STRING alone, prior 0.5 |
|---|---:|---:|---:|
| Minimum | 4.13 | 1.43 | 0.588 |
| Median | 6.83 | 1.62 | 0.618 |
| 90th percentile | 28.24 | 2.30 | 0.697 |
| 95th percentile | 61.36 | 2.80 | 0.737 |
| 99th percentile | 644.90 | 5.04 | 0.834 |
| Maximum | 23,366.85 | 12.36 | 0.925 |

The default does not assert that STRING is uninformative. Very high-confidence
relationships can still provide strong evidence, but ordinary STRING records
no longer drive an edge close to probability 1 by themselves.

## Full default-graph check

The 1,506-node default graph was rebuilt with every other setting held fixed.
Reducing only STRING's weight from 1.0 to 0.25 produced:

| Measure across 1,133,265 unique pairs | Historical weight 1.0 | Stabilized weight 0.25 |
|---|---:|---:|
| Mean edge posterior | 0.5782 | 0.5441 |
| 90th percentile | 0.8563 | 0.6429 |
| 99th percentile | 0.9902 | 0.9265 |
| Edges above 0.90 | 78,071 | 14,361 |
| Edges above 0.99 | 11,503 | 1,446 |
| Edges above the exclusive 0.50 output cutoff | 419,680 | 385,064 |

Thus the change removes most STRING-driven near-certain edges while retaining
the large majority of above-baseline candidates for downstream ranking.

## Interpretation and controls

- The evidence inspector continues to display the raw STRING BF, the weight,
  and `weight * log2(BF)`. The latter is the actual posterior contribution.
- Users can restore the historical behavior by setting the STRING weight to 1.
- Positive-control calibration may fit a different weight. Its regularization
  target now begins at the stabilized 0.25 default.
- The `Ref ×` control remains separate. It changes the 0.041 reference score;
  the weight controls confidence in transferring STRING evidence into this
  selected signaling universe.
- Unreported STRING pairs remain neutral unless the user explicitly enables
  scope-aware negative evidence.
