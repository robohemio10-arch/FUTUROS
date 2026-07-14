"""Research-only economic analysis for paper trades and market candles."""

from .paper_analysis import (
    ProfitResearchPaths,
    ProfitResearchResult,
    build_profit_research,
    resolve_profit_research_paths,
)

__all__ = [
    "ProfitResearchPaths",
    "ProfitResearchResult",
    "build_profit_research",
    "resolve_profit_research_paths",
]
