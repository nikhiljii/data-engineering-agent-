import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import ecom_helpers as H  # noqa: E402


@pytest.fixture(scope="session")
def baseline(tmp_path_factory):
    """Run the real pipeline once (--backfill) on a tmp copy of the raw CSVs into a tmp DuckDB file."""
    base = tmp_path_factory.mktemp("ecom_baseline")
    raw = H.copy_raw(base / "raw")
    db = base / "warehouse.duckdb"
    proc = H.run_pipeline(raw, db, "--backfill")
    if proc.returncode != 0 or not db.exists():
        pytest.fail("pipeline did not complete a clean --backfill run on the unmodified raw files "
                    "(expected before the builder finishes):\n" + H.describe(proc), pytrace=False)
    return {"raw": raw, "db": db, "proc": proc}


@pytest.fixture()
def con(baseline):
    c = H.connect_ro(baseline["db"])
    H.load_oracle(c, baseline["raw"])
    yield c
    c.close()


@pytest.fixture()
def workdir(tmp_path):
    """Fresh raw copy + db path for seeded-defect runs."""
    raw = H.copy_raw(tmp_path / "raw")
    return {"raw": raw, "db": tmp_path / "w.duckdb", "tmp": tmp_path}
