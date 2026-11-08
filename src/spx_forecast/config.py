from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    start: str = "2015-01-01"
    end: str = "2026-11-06"
    reference_date: str = "2026-11-06"
    raw: Path = Path("data/raw")
    normalized: Path = Path("data/normalized")
    imports: Path = Path("data/imports")
    output: Path = Path("artifacts")
    seed: int = 42
    refresh: bool = False
    run_expensive_backtest: bool = True
    timeout_seconds: int = 15
    retries: int = 2

    def prepare(self):
        for path in (self.raw, self.normalized, self.imports, self.output):
            path.mkdir(parents=True, exist_ok=True)
