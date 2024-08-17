import json
import os

from collections import namedtuple

from metaflow.exception import MetaflowException
from metaflow.util import is_stringish

ConfigValue = namedtuple("ConfigValue", "value serializer is_default")

NON_CHANGED_VALUES = 1
NULL_VALUES = 2
ALL_VALUES = 3


def init_config():
    # Read configuration from $METAFLOW_HOME/config_<profile>.json.
    home = os.environ.get("METAFLOW_HOME", "~/.metaflowconfig")
    profile = os.environ.get("METAFLOW_PROFILE")
    path_to_config = os.path.join(home, "config.json")
    if profile:
        path_to_config = os.path.join(home, "config_%s.json" % profile)
    path_to_config = os.path.expanduser(path_to_config)
    config = {}
    if os.path.exists(path_to_config):
        with open(path_to_config, encoding="utf-8") as f:
            return json.load(f)
    elif profile:
        raise MetaflowException(
            "Unable to locate METAFLOW_PROFILE '%s' in '%s')" % (profile, home)
        )
    return config


def init_local_config():
    # This function is heavily inspired from LocalStorage.get_datastore_root_from_config
    # but simplifies certain things and also does not depend on DATASTORE_SYSROOT_LOCAL.
    #
    # In other words, since this config is meant to be local to a directory, it does not
    # check in DATASTORE_SYSROOT_LOCAL but only up the current getcwd() path. This also
    # prevents nasty circular dependencies :)

    from metaflow.metaflow_config import DATASTORE_LOCAL_DIR, LOCAL_CONFIG_FILE

    current_path = os.getcwd()
    check_dir = os.path.join(current_path, DATASTORE_LOCAL_DIR)
    check_dir = os.path.realpath(check_dir)
    while not os.path.isdir(check_dir):
        new_path = os.path.dirname(current_path)
        if new_path == current_path:  # No longer making upward progress
            return {}
        current_path = new_path
        check_dir = os.path.join(current_path, DATASTORE_LOCAL_DIR)
    path_to_config = os.path.join(check_dir, LOCAL_CONFIG_FILE)
    # We found a directory to look for the config file in
    if os.path.exists(path_to_config):
        with open(path_to_config, encoding="utf-8") as f:
            return json.load(f)
    return {}


# Initialize defaults required to setup environment variables.
# (initialized lazily in from_conf since init_local_config requires
# some configuration values

METAFLOW_CONFIG = None

METAFLOW_LOCAL_CONFIG = None

_all_configs = {}


def config_values(include=0):
    # By default, we just return non-null values and that
    # are not default. This is the common use case because in all other cases, the code
    # is sufficient to recreate the value (ie: there is no external source for the value)
    for name, config_value in _all_configs.items():
        if (config_value.value is not None or include & NULL_VALUES) and (
            not config_value.is_default or include & NON_CHANGED_VALUES
        ):
            yield name, config_value.serializer(config_value.value)


def from_conf(name, default=None, validate_fn=None):
    """
    Pull value from the environment or configuration.
    Order is:
    1. Environment (use any environment variable explicitly set by user)
    2. Local config (use any value set in the local config file -- so stuff in
       .metaflow/project.json for example)
    3. Global config (use any value set in the global config file)
    4. Default

    Prior to a value being returned, we will validate using validate_fn (if provided).
    Only non-None values are validated.

    validate_fn should accept (name, value).
    If the value validates, return None, else raise an MetaflowException.
    """
    global METAFLOW_CONFIG, METAFLOW_LOCAL_CONFIG

    if METAFLOW_CONFIG is None:
        METAFLOW_CONFIG = init_config()
        METAFLOW_LOCAL_CONFIG = init_local_config()

    is_default = True
    env_name = "METAFLOW_%s" % name
    value = os.environ.get(
        env_name,
        METAFLOW_LOCAL_CONFIG.get(env_name, METAFLOW_CONFIG.get(env_name, default)),
    )
    if validate_fn and value is not None:
        validate_fn(env_name, value)
    if default is not None:
        # In this case, value is definitely not None because default is the ultimate
        # fallback and all other cases will return a string (even if an empty string)
        if isinstance(default, (list, dict)):
            # If we used the default, value is already a list or dict, else it is a
            # string so we can just compare types to determine is_default
            if isinstance(value, (list, dict)):
                is_default = True
            else:
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    raise ValueError(
                        "Expected a valid JSON for %s, got: %s" % (env_name, value)
                    )
                if type(value) != type(default):
                    raise ValueError(
                        "Expected value of type '%s' for %s, got: %s"
                        % (type(default), env_name, value)
                    )
                is_default = value == default
            _all_configs[env_name] = ConfigValue(
                value=value,
                serializer=json.dumps,
                is_default=is_default,
            )
            return value
        elif isinstance(default, (bool, int, float)) or is_stringish(default):
            try:
                if type(value) != type(default):
                    if isinstance(default, bool):
                        # Env vars are strings so try to evaluate logically
                        value = value.lower() not in ("0", "false", "")
                    else:
                        value = type(default)(value)
                is_default = value == default
            except ValueError:
                raise ValueError(
                    "Expected a %s for %s, got: %s" % (type(default), env_name, value)
                )
        else:
            raise RuntimeError(
                "Default of type %s for %s is not supported" % (type(default), env_name)
            )
    else:
        is_default = value is None
    _all_configs[env_name] = ConfigValue(
        value=value,
        serializer=str,
        is_default=is_default,
    )
    return value


def get_validate_choice_fn(choices):
    """Returns a validate_fn for use with from_conf().
    The validate_fn will check a value against a list of allowed choices.
    """

    def _validate_choice(name, value):
        if value not in choices:
            raise MetaflowException(
                "%s must be set to one of %s. Got '%s'." % (name, choices, value)
            )

    return _validate_choice
