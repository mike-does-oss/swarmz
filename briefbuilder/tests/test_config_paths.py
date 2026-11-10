import os

from app.config import Config


def test_data_paths_are_defined_and_absolute():
    assert os.path.isabs(Config.DATA_DIR)
    assert Config.SHOALS_DB_PATH == os.path.join(Config.DATA_DIR, "shoals.db")
    assert Config.BRIEFS_V2_DIR == os.path.join(Config.DATA_DIR, "briefs_v2")
    assert Config.DATA_DIR.endswith("data")
