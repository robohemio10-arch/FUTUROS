"""Research-only accumulator for paper auto-training quarantine microbatch evidence.

This module is read-only by default. It does not import or execute Freqtrade,
ccxt, Docker tooling, RiskManager, or Qlib/IA Shadow runtime, and it never
trains or promotes a model. Optional writes are restricted to JSON/Markdown
reports under data/reports and an accumulated research dataset under
data/research/paper_autotrain_evidence_accumulation_window.
"""

from .accumulation import build_paper_autotrain_evidence_accumulation_window_v1

__all__ = ["build_paper_autotrain_evidence_accumulation_window_v1"]
