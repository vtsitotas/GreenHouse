#!/usr/bin/env python3
"""Shared FCM push-notification helper for greenhouse Pi services.

Reads currently-registered device tokens from retained MQTT messages
(published by the app to greenhouse/app/fcm_token/<device-uuid>, one retained
topic per device) and sends a push via Firebase Cloud Messaging to each one.
Never raises — a missing Firebase setup or a bad/expired token for one
device must not stop alerts from reaching the rest, or block whatever
rule-evaluation loop called send_push().
"""
import subprocess

try:
    import firebase_admin
    from firebase_admin import credentials, messaging
    _FIREBASE_AVAILABLE = True
except ImportError:
    _FIREBASE_AVAILABLE = False

MQTT_HOST = '127.0.0.1'
MQTT_PORT = '1883'
FCM_TOKEN_TOPIC_FILTER = 'greenhouse/app/fcm_token/+'
FIREBASE_CREDENTIALS = '/etc/greenhouse/firebase-service-account.json'

_firebase_app = None


def parse_fcm_tokens(sub_output: str) -> dict[str, str]:
    """Parse `mosquitto_sub -v` output for the fcm_token wildcard into
    {device_uuid: token}."""
    tokens: dict[str, str] = {}
    for line in sub_output.splitlines():
        line = line.strip()
        if not line:
            continue
        topic, _, payload = line.partition(' ')
        if not payload:
            continue
        device_id = topic.rsplit('/', 1)[-1]
        tokens[device_id] = payload
    return tokens


def get_registered_tokens() -> dict[str, str]:
    """Query the broker for every currently-retained fcm_token/<device> value."""
    try:
        result = subprocess.run(
            ['mosquitto_sub', '-h', MQTT_HOST, '-p', MQTT_PORT,
             '-t', FCM_TOKEN_TOPIC_FILTER, '-v', '-W', '3'],
            capture_output=True, text=True, timeout=6,
        )
        return parse_fcm_tokens(result.stdout)
    except Exception as e:
        print(f'[push] WARN: could not read registered tokens: {e}', flush=True)
        return {}


def _ensure_firebase_app():
    global _firebase_app
    if _firebase_app is None:
        cred = credentials.Certificate(FIREBASE_CREDENTIALS)
        _firebase_app = firebase_admin.initialize_app(cred)
    return _firebase_app


def send_push(title: str, body: str) -> None:
    if not _FIREBASE_AVAILABLE:
        print('[push] WARN: firebase_admin not installed, skipping push', flush=True)
        return
    tokens = get_registered_tokens()
    if not tokens:
        return
    try:
        _ensure_firebase_app()
    except Exception as e:
        print(f'[push] WARN: Firebase init failed, skipping push: {e}', flush=True)
        return
    for device_id, token in tokens.items():
        try:
            messaging.send(messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                token=token,
            ))
        except Exception as e:
            print(f'[push] WARN: send failed for device {device_id}: {e}', flush=True)
