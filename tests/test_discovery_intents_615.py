"""Task 615: an intent query for create finds only create; models stays findable by its own intents."""
from __future__ import annotations

import pytest

from graftling.discovery import DESCRIPTIONS, handle_request


def discovered(query: str) -> list[str]:
    """Return the features one discover query lists."""
    result = handle_request({'action': 'discover', 'query': query}, lambda request: None)
    assert not result.failed, result.document
    return [item['feature'] for item in result.document['features']]


@pytest.mark.parametrize('query', ['start an agent', 'launch a new agent', 'spawn agent', 'Start a new Agent'])
def test_create_intents_find_only_create(query: str) -> None:
    """models' description no longer carries 'start' or 'agent', so these find create alone."""
    assert discovered(query) == ['create']


@pytest.mark.parametrize('query', ['models', 'providers', 'list models', 'choose a model'])
def test_model_listing_intents_find_models(query: str) -> None:
    assert 'models' in discovered(query)


@pytest.mark.parametrize('query', ['bake a cake', 'start a cake', 'deploy kubernetes'])
def test_unrelated_queries_find_nothing(query: str) -> None:
    assert discovered(query) == []


def test_empty_and_action_name_queries_unchanged() -> None:
    assert discovered('') == list(DESCRIPTIONS)
    assert discovered('retire') == ['retire']
