from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine


class StateRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.database_path}")

    def write_frame(self, name: str, frame: pd.DataFrame) -> None:
        frame.to_sql(name, self.engine, if_exists="replace", index=False)

    def read_frame(self, name: str) -> pd.DataFrame:
        return pd.read_sql_table(name, self.engine)
