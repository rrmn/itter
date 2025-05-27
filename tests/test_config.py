import pytest
from unittest.mock import patch
from pydantic import ValidationError

from itter.config import Config, validate_config


@pytest.fixture
def valid_env_vars():
    """Fixture providing valid environment variables."""
    return {
        "ITTER_DEBUG_MODE": "true",
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_KEY": "test_key_123",
        "SUPABASE_WSURL": "wss://test.supabase.co",
        "IP_HASH_SALT": "test_salt_123",
    }


@pytest.fixture
def clean_env(monkeypatch):
    """Fixture to clean environment variables before each test."""
    env_vars_to_clean = [
        "BANNER_FILE",
        "EET_MAX_LENGTH",
        "SSH_HOST_KEY_PATH",
        "MIN_TIMELINE_PAGE_SIZE",
        "MAX_TIMELINE_PAGE_SIZE",
        "DEFAULT_TIMELINE_PAGE_SIZE",
        "WATCH_REFRESH_INTERVAL_SECONDS",
        "ITTER_DEBUG_MODE",
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "SUPABASE_WSURL",
        "SSH_HOST",
        "SSH_PORT",
        "IP_HASH_SALT",
    ]

    for var in env_vars_to_clean:
        monkeypatch.delenv(var, raising=False)


class TestConfig:
    """Test cases for the Config class."""

    def test_config_with_all_required_vars(
        self, monkeypatch, valid_env_vars, clean_env
    ):
        """Test Config creation with all required environment variables."""
        for key, value in valid_env_vars.items():
            monkeypatch.setenv(key, value)

        config = Config()

        # Test required fields
        assert config.itter_debug_mode is True
        assert config.supabase_url == "https://test.supabase.co"
        assert config.supabase_key == "test_key_123"
        assert config.supabase_wsurl == "wss://test.supabase.co"
        assert config.ip_hash_salt == "test_salt_123"

        # Test default values
        assert config.banner_file == "itter_banner.txt"
        assert config.eet_max_length == 180
        assert config.ssh_host_key_path == "./ssh_host_key"
        assert config.min_timeline_page_size == 1
        assert config.max_timeline_page_size == 30
        assert config.default_timeline_page_size == 10
        assert config.watch_refresh_interval_seconds == 15
        assert config.ssh_host == "0.0.0.0"
        assert config.ssh_port == "8022"

    def test_config_with_overridden_defaults(
        self, monkeypatch, valid_env_vars, clean_env
    ):
        """Test Config with environment variables overriding default values."""
        # Set required vars
        for key, value in valid_env_vars.items():
            monkeypatch.setenv(key, value)

        # Override some default values
        monkeypatch.setenv("BANNER_FILE", "custom_banner.txt")
        monkeypatch.setenv("EET_MAX_LENGTH", "250")
        monkeypatch.setenv("SSH_HOST", "127.0.0.1")
        monkeypatch.setenv("SSH_PORT", "9022")
        monkeypatch.setenv("MIN_TIMELINE_PAGE_SIZE", "5")
        monkeypatch.setenv("MAX_TIMELINE_PAGE_SIZE", "50")
        monkeypatch.setenv("DEFAULT_TIMELINE_PAGE_SIZE", "20")
        monkeypatch.setenv("WATCH_REFRESH_INTERVAL_SECONDS", "30")

        config = Config()

        # Test overridden values
        assert config.banner_file == "custom_banner.txt"
        assert config.eet_max_length == 250
        assert config.ssh_host == "127.0.0.1"
        assert config.ssh_port == "9022"
        assert config.min_timeline_page_size == 5
        assert config.max_timeline_page_size == 50
        assert config.default_timeline_page_size == 20
        assert config.watch_refresh_interval_seconds == 30

    def test_config_missing_required_itter_debug_mode(self, monkeypatch, clean_env):
        """Test Config fails when ITTER_DEBUG_MODE is missing."""
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("SUPABASE_WSURL", "wss://test.supabase.co")
        monkeypatch.setenv("IP_HASH_SALT", "test_salt")

        with pytest.raises(ValidationError) as exc_info:
            Config()

        assert "itter_debug_mode" in str(exc_info.value)

    def test_config_missing_required_supabase_url(self, monkeypatch, clean_env):
        """Test Config fails when SUPABASE_URL is missing."""
        monkeypatch.setenv("ITTER_DEBUG_MODE", "true")
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("SUPABASE_WSURL", "wss://test.supabase.co")
        monkeypatch.setenv("IP_HASH_SALT", "test_salt")

        with pytest.raises(ValidationError) as exc_info:
            Config()

        assert "supabase_url" in str(exc_info.value)

    def test_config_missing_required_supabase_key(self, monkeypatch, clean_env):
        """Test Config fails when SUPABASE_KEY is missing."""
        monkeypatch.setenv("ITTER_DEBUG_MODE", "true")
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_WSURL", "wss://test.supabase.co")
        monkeypatch.setenv("IP_HASH_SALT", "test_salt")

        with pytest.raises(ValidationError) as exc_info:
            Config()

        assert "supabase_key" in str(exc_info.value)

    def test_config_missing_required_supabase_wsurl(self, monkeypatch, clean_env):
        """Test Config fails when SUPABASE_WSURL is missing."""
        monkeypatch.setenv("ITTER_DEBUG_MODE", "true")
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("IP_HASH_SALT", "test_salt")

        with pytest.raises(ValidationError) as exc_info:
            Config()

        assert "supabase_wsurl" in str(exc_info.value)

    def test_config_missing_required_ip_hash_salt(self, monkeypatch, clean_env):
        """Test Config fails when IP_HASH_SALT is missing."""
        monkeypatch.setenv("ITTER_DEBUG_MODE", "true")
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("SUPABASE_WSURL", "wss://test.supabase.co")

        with pytest.raises(ValidationError) as exc_info:
            Config()

        assert "ip_hash_salt" in str(exc_info.value)

    def test_config_boolean_parsing(self, monkeypatch, valid_env_vars, clean_env):
        """Test that boolean environment variables are parsed correctly."""
        # Test with different boolean representations
        test_cases = [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("1", True),
            ("false", False),
            ("False", False),
            ("FALSE", False),
            ("0", False),
        ]

        for bool_str, expected in test_cases:
            # Clean and set up environment
            for key, value in valid_env_vars.items():
                if key != "ITTER_DEBUG_MODE":
                    monkeypatch.setenv(key, value)

            monkeypatch.setenv("ITTER_DEBUG_MODE", bool_str)

            config = Config()
            assert config.itter_debug_mode == expected

    def test_config_integer_parsing(self, monkeypatch, valid_env_vars, clean_env):
        """Test that integer environment variables are parsed correctly."""
        for key, value in valid_env_vars.items():
            monkeypatch.setenv(key, value)

        monkeypatch.setenv("EET_MAX_LENGTH", "500")
        monkeypatch.setenv("MIN_TIMELINE_PAGE_SIZE", "2")

        config = Config()
        assert config.eet_max_length == 500
        assert config.min_timeline_page_size == 2

    def test_config_invalid_integer_parsing(
        self, monkeypatch, valid_env_vars, clean_env
    ):
        """Test that invalid integer values raise ValidationError."""
        for key, value in valid_env_vars.items():
            monkeypatch.setenv(key, value)

        monkeypatch.setenv("EET_MAX_LENGTH", "not_a_number")

        with pytest.raises(ValidationError):
            Config()


class TestValidateConfig:
    """Test cases for the validate_config function."""

    @patch("itter.config.logger")
    def test_validate_config_success(
        self, mock_logger, monkeypatch, valid_env_vars, clean_env
    ):
        """Test validate_config succeeds with valid environment variables."""
        for key, value in valid_env_vars.items():
            monkeypatch.setenv(key, value)

        # Should not raise any exception
        validate_config()

        # Logger should not be called
        mock_logger.exception.assert_not_called()

    @patch("itter.config.logger")
    def test_validate_config_failure(self, mock_logger, monkeypatch, clean_env):
        """Test validate_config fails and logs error when required vars are missing."""
        # Don't set required environment variables

        with pytest.raises(ValidationError):
            validate_config()

        # Should log the fatal error
        mock_logger.exception.assert_called_once_with(
            "[FATAL ERROR] Missing environment variables"
        )

    @patch("itter.config.logger")
    def test_validate_config_partial_missing_vars(
        self, mock_logger, monkeypatch, clean_env
    ):
        """Test validate_config with some missing required variables."""
        # Set only some required variables
        monkeypatch.setenv("ITTER_DEBUG_MODE", "true")
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        # Missing: SUPABASE_KEY, SUPABASE_WSURL, IP_HASH_SALT

        with pytest.raises(ValidationError):
            validate_config()

        mock_logger.exception.assert_called_once_with(
            "[FATAL ERROR] Missing environment variables"
        )


class TestEnvironmentLoading:
    """Test cases for environment loading behavior."""

    def test_config_field_types(self, monkeypatch, valid_env_vars, clean_env):
        """Test that Config fields have correct types."""
        for key, value in valid_env_vars.items():
            monkeypatch.setenv(key, value)

        config = Config()

        # Test string fields
        assert isinstance(config.banner_file, str)
        assert isinstance(config.ssh_host_key_path, str)
        assert isinstance(config.supabase_url, str)
        assert isinstance(config.supabase_key, str)
        assert isinstance(config.supabase_wsurl, str)
        assert isinstance(config.ssh_host, str)
        assert isinstance(config.ssh_port, str)
        assert isinstance(config.ip_hash_salt, str)

        # Test integer fields
        assert isinstance(config.eet_max_length, int)
        assert isinstance(config.min_timeline_page_size, int)
        assert isinstance(config.max_timeline_page_size, int)
        assert isinstance(config.default_timeline_page_size, int)
        assert isinstance(config.watch_refresh_interval_seconds, int)

        # Test boolean field
        assert isinstance(config.itter_debug_mode, bool)
