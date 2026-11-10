"""Shared pytest fixtures for the backend test suite."""

import pytest


@pytest.fixture
def data_dir(tmp_path):
    """A fresh, isolated data directory for storage tests."""
    d = tmp_path / "data"
    d.mkdir()
    return d
