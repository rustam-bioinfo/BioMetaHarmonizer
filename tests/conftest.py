import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "network: mark test as requiring network access")
