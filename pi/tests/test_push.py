import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import push


def test_parse_fcm_tokens_single_device():
    output = 'greenhouse/app/fcm_token/device-a token-abc123\n'
    assert push.parse_fcm_tokens(output) == {'device-a': 'token-abc123'}


def test_parse_fcm_tokens_multiple_devices():
    output = (
        'greenhouse/app/fcm_token/device-a token-abc\n'
        'greenhouse/app/fcm_token/device-b token-xyz\n'
    )
    assert push.parse_fcm_tokens(output) == {
        'device-a': 'token-abc',
        'device-b': 'token-xyz',
    }


def test_parse_fcm_tokens_ignores_blank_lines():
    output = '\ngreenhouse/app/fcm_token/device-a token-abc\n\n'
    assert push.parse_fcm_tokens(output) == {'device-a': 'token-abc'}


def test_parse_fcm_tokens_empty_output_returns_empty_dict():
    assert push.parse_fcm_tokens('') == {}


def test_get_registered_tokens_parses_mosquitto_sub_output(monkeypatch):
    fake_result = MagicMock()
    fake_result.stdout = 'greenhouse/app/fcm_token/device-a token-abc\n'
    monkeypatch.setattr(
        push.subprocess, 'run', lambda *a, **k: fake_result)

    assert push.get_registered_tokens() == {'device-a': 'token-abc'}


def test_get_registered_tokens_returns_empty_dict_on_subprocess_error(monkeypatch):
    def _raise(*a, **k):
        raise OSError('mosquitto_sub not found')
    monkeypatch.setattr(push.subprocess, 'run', _raise)

    assert push.get_registered_tokens() == {}


def test_send_push_calls_messaging_send_once_per_token(monkeypatch):
    monkeypatch.setattr(push, '_FIREBASE_AVAILABLE', True)
    monkeypatch.setattr(push, '_ensure_firebase_app', lambda: None)
    monkeypatch.setattr(push, 'get_registered_tokens',
                         lambda: {'device-a': 'token-a', 'device-b': 'token-b'})
    fake_messaging = MagicMock()
    monkeypatch.setattr(push, 'messaging', fake_messaging)

    push.send_push('Frost warning', 'Frost expected tonight')

    assert fake_messaging.send.call_count == 2


def test_send_push_continues_after_one_token_fails(monkeypatch):
    monkeypatch.setattr(push, '_FIREBASE_AVAILABLE', True)
    monkeypatch.setattr(push, '_ensure_firebase_app', lambda: None)
    monkeypatch.setattr(push, 'get_registered_tokens',
                         lambda: {'device-a': 'bad-token', 'device-b': 'good-token'})

    fake_messaging = MagicMock()
    fake_messaging.Message.side_effect = (
        lambda notification=None, token=None: MagicMock(token=token))

    def _send(message):
        if message.token == 'bad-token':
            raise Exception('registration-token-not-registered')
        return 'projects/x/messages/ok'

    fake_messaging.send.side_effect = _send
    monkeypatch.setattr(push, 'messaging', fake_messaging)

    push.send_push('Test', 'Body')  # must not raise

    assert fake_messaging.send.call_count == 2


def test_send_push_noop_when_no_tokens_registered(monkeypatch):
    monkeypatch.setattr(push, '_FIREBASE_AVAILABLE', True)
    monkeypatch.setattr(push, 'get_registered_tokens', lambda: {})
    called = MagicMock()
    monkeypatch.setattr(push, '_ensure_firebase_app', called)

    push.send_push('Test', 'Body')

    called.assert_not_called()


def test_send_push_noop_when_firebase_not_available(monkeypatch, capsys):
    monkeypatch.setattr(push, '_FIREBASE_AVAILABLE', False)

    push.send_push('Test', 'Body')

    captured = capsys.readouterr()
    assert 'firebase_admin not installed' in captured.out
