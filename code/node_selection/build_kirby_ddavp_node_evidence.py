#!/usr/bin/env python3
"""Materialize Kirby dDAVP phosphoprotein and inferred-kinase node factors."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUI_ROOT = PROJECT_ROOT / "gui"
if str(GUI_ROOT) not in sys.path:
    sys.path.insert(0, str(GUI_ROOT))

from kinase_site_concordance import kirby_node_factor_catalog  # noqa: E402


def main() -> None:
    output = PROJECT_ROOT / "data/node_selection/kirby_ddavp/processed"
    output.mkdir(parents=True, exist_ok=True)
    factors, audit = kirby_node_factor_catalog(PROJECT_ROOT)
    factor_path = output / "kirby_ddavp_node_factors.tsv.gz"
    audit_path = output / "kirby_ddavp_kinase_activity_audit.tsv.gz"
    factors.to_csv(factor_path, sep="\t", index=False, compression="gzip")
    audit.to_csv(audit_path, sep="\t", index=False, compression="gzip")
    summary = {
        "schema_version": 1,
        "phosphoprotein_nodes_observed": int(factors["kirby_ddavp_observed"].sum()),
        "kinases_with_inferred_activity": int(
            factors["kirby_kinase_activity_observed"].sum()
        ),
        "factor_file": str(factor_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "kinase_activity_audit": str(audit_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
    }
    (output / "kirby_ddavp_node_evidence_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
