from app.services.terminal.protocol import WSMessage, WSMessageType, decode_bytes, encode_bytes


def test_ws_message_serialization_roundtrip() -> None:
    message = WSMessage(type=WSMessageType.OUTPUT, payload="abc")
    dumped = message.model_dump()
    loaded = WSMessage.model_validate(dumped)
    assert loaded.type == WSMessageType.OUTPUT
    assert loaded.payload == "abc"


def test_base64_encode_decode_bytes() -> None:
    source = b"hello\nworld\x00"
    encoded = encode_bytes(source)
    assert isinstance(encoded, str)
    decoded = decode_bytes(encoded)
    assert decoded == source
