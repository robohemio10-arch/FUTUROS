"""Exact, versioned provenance classification for historical and OCR rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .contracts import KNOWN_PROVENANCE_FIELD_VALUES, PROVENANCE_CONTRACTS


@dataclass(frozen=True)
class ProvenanceResult:
    contract_id: str
    status: str
    block_reasons: tuple[str, ...]
    matched_contracts: tuple[str, ...]
    observed_source_file: str
    observed_ocr_source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "provenance_contract": self.contract_id,
            "provenance_status": self.status,
            "provenance_block_reasons": list(self.block_reasons),
            "matched_provenance_contracts": list(self.matched_contracts),
            "source_file": self.observed_source_file,
            "ocr_source": self.observed_ocr_source,
        }


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def normalized(value: Any) -> str:
    return clean_text(value).casefold()


def classify_provenance(row: pd.Series | dict[str, Any]) -> ProvenanceResult:
    source_file = clean_text(row.get("source_file"))
    ocr_source = clean_text(row.get("ocr_source"))
    segment = normalized(row.get("segment"))

    observed = {
        "source_file": normalized(source_file),
        "ocr_source": normalized(ocr_source),
    }

    exact_matches: list[str] = []
    partial_matches: list[str] = []
    for contract in PROVENANCE_CONTRACTS:
        required = dict(contract.required_fields)
        matched_fields = [
            field_name
            for field_name, expected in required.items()
            if observed.get(field_name, "") == expected.casefold()
        ]
        if len(matched_fields) == len(required):
            exact_matches.append(contract.contract_id)
        elif matched_fields:
            partial_matches.append(contract.contract_id)

    if len(exact_matches) > 1:
        return ProvenanceResult(
            contract_id="AMBIGUOUS",
            status="blocked",
            block_reasons=("BLOCKED_AMBIGUOUS_PROVENANCE",),
            matched_contracts=tuple(sorted(exact_matches)),
            observed_source_file=source_file,
            observed_ocr_source=ocr_source,
        )

    if len(exact_matches) == 1:
        return ProvenanceResult(
            contract_id=exact_matches[0],
            status="ok",
            block_reasons=(),
            matched_contracts=(exact_matches[0],),
            observed_source_file=source_file,
            observed_ocr_source=ocr_source,
        )

    known_individual_marker_present = any(
        observed.get(field_name, "") == expected
        for field_name, expected in KNOWN_PROVENANCE_FIELD_VALUES
    )
    if partial_matches or known_individual_marker_present:
        return ProvenanceResult(
            contract_id="PARTIAL",
            status="blocked",
            block_reasons=("BLOCKED_PARTIAL_PROVENANCE",),
            matched_contracts=tuple(sorted(set(partial_matches))),
            observed_source_file=source_file,
            observed_ocr_source=ocr_source,
        )

    # Historical rows are not inferred from broad string matching. They are accepted
    # only when the row is not explicitly marked as an OCR segment.
    if segment not in {"bitradex_ocr", "ocr", "bitradex"}:
        return ProvenanceResult(
            contract_id="historical",
            status="ok",
            block_reasons=(),
            matched_contracts=(),
            observed_source_file=source_file,
            observed_ocr_source=ocr_source,
        )

    return ProvenanceResult(
        contract_id="UNKNOWN",
        status="blocked",
        block_reasons=("BLOCKED_UNRECOGNIZED_PROVENANCE",),
        matched_contracts=(),
        observed_source_file=source_file,
        observed_ocr_source=ocr_source,
    )


def classify_provenance_frame(frame: pd.DataFrame) -> pd.DataFrame:
    records = [classify_provenance(row).as_dict() for _, row in frame.iterrows()]
    return pd.DataFrame(records, index=frame.index)


def provenance_summary(classified: pd.DataFrame) -> dict[str, Any]:
    contracts = classified["provenance_contract"].astype(str)
    status = classified["provenance_status"].astype(str)
    return {
        "contract_counts": contracts.value_counts(dropna=False).sort_index().to_dict(),
        "recognized_rows": int(status.eq("ok").sum()),
        "blocked_rows": int(status.ne("ok").sum()),
        "v5_recognized_rows": int(contracts.eq("ocr_v5_20260714").sum()),
        "unknown_rows": int(contracts.eq("UNKNOWN").sum()),
        "partial_rows": int(contracts.eq("PARTIAL").sum()),
        "ambiguous_rows": int(contracts.eq("AMBIGUOUS").sum()),
    }
