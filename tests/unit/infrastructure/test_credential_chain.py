import pytest

from devtunnel.domain.errors import CredentialResolutionError, NonInteractiveInputRequiredError
from devtunnel.infrastructure.credentials.chain import CredentialChain
from tests.fakes.fake_ports import FakePrompter


def test_env_var_wins_over_flag_and_prompt(monkeypatch):
    monkeypatch.setenv("NGROK_AUTHTOKEN", "from-env")
    chain = CredentialChain(FakePrompter(secret_answers=["from-prompt"]))

    value = chain.resolve(
        what="token", env_var="NGROK_AUTHTOKEN", flag_value="from-flag", non_interactive=False
    )

    assert value == "from-env"


def test_flag_wins_over_prompt_when_env_var_is_unset(monkeypatch):
    monkeypatch.delenv("NGROK_AUTHTOKEN", raising=False)
    chain = CredentialChain(FakePrompter(secret_answers=["from-prompt"]))

    value = chain.resolve(
        what="token", env_var="NGROK_AUTHTOKEN", flag_value="from-flag", non_interactive=False
    )

    assert value == "from-flag"


def test_falls_through_to_the_prompt_when_nothing_else_resolves(monkeypatch):
    monkeypatch.delenv("NGROK_AUTHTOKEN", raising=False)
    chain = CredentialChain(FakePrompter(secret_answers=["from-prompt"]))

    value = chain.resolve(
        what="token", env_var="NGROK_AUTHTOKEN", flag_value=None, non_interactive=False
    )

    assert value == "from-prompt"


def test_non_interactive_raises_instead_of_prompting(monkeypatch):
    monkeypatch.delenv("NGROK_AUTHTOKEN", raising=False)
    chain = CredentialChain(FakePrompter(secret_answers=["from-prompt"]))

    with pytest.raises(NonInteractiveInputRequiredError):
        chain.resolve(
            what="token", env_var="NGROK_AUTHTOKEN", flag_value=None, non_interactive=True
        )


def test_empty_prompt_response_raises(monkeypatch):
    monkeypatch.delenv("NGROK_AUTHTOKEN", raising=False)
    chain = CredentialChain(FakePrompter(secret_answers=[""]))

    with pytest.raises(CredentialResolutionError):
        chain.resolve(
            what="token", env_var="NGROK_AUTHTOKEN", flag_value=None, non_interactive=False
        )
