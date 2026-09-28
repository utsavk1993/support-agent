"""Tests for reading settings from the environment.

The point of config.py is that a missing setting stops the process with an
explanation, rather than producing an error later that says nothing about
the cause. That only holds if the failure path works.
"""

import pytest

from app import config


def test_a_missing_setting_names_itself_and_the_fix():
    with pytest.raises(RuntimeError) as failure:
        config._required("NORTHWIND_NONSENSE", "Set it in .env.")

    message = str(failure.value)
    assert "NORTHWIND_NONSENSE" in message, "the error should name the setting"
    assert "Set it in .env." in message, "and say how to fix it"


def test_an_empty_setting_counts_as_missing(monkeypatch):
    # An empty string is a configuration mistake, not a value. Accepting it
    # would send an empty API key to the provider and fail far from here.
    monkeypatch.setenv("NORTHWIND_EMPTY", "")

    with pytest.raises(RuntimeError):
        config._required("NORTHWIND_EMPTY", "hint")


def test_a_present_setting_is_returned(monkeypatch):
    monkeypatch.setenv("NORTHWIND_PRESENT", "a-value")
    assert config._required("NORTHWIND_PRESENT", "hint") == "a-value"
