def test_config_settings_cached():
    from app.config import get_settings

    assert get_settings() is get_settings()
