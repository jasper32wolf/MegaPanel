from app.services.indexnow import new_indexnow_key


def test_indexnow_key_length():
    key = new_indexnow_key()
    assert len(key) == 32
    assert key.isalnum()
