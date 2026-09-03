"""Route and hardening tests."""

import os
import re
from unittest import mock

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def client(monkeypatch, tmp_path):
    from gw2 import cache

    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    import app as app_module

    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


# --- Debugger and secrets (fixes.md items 18, 19) ---------------------------

def test_debugger_is_never_enabled():
    src = open(os.path.join(ROOT, "app.py")).read()
    assert "debug=True" not in src
    assert re.search(r"debug\s*=\s*False", src)


def test_no_traceback_or_internals_leak_on_an_unhandled_error(client):
    with mock.patch("gw2.api.account", side_effect=RuntimeError("boom /secret/path")):
        r = client.get("/", headers={"Host": "127.0.0.1:5000"})
    assert r.status_code == 500
    assert b"Traceback" not in r.data
    assert b"boom" not in r.data
    assert b"/secret/path" not in r.data


def test_api_key_never_appears_in_a_response(client):
    with mock.patch("gw2.api.account", side_effect=RuntimeError("fail")):
        r = client.get("/", headers={"Host": "127.0.0.1:5000"})
    import app as app_module

    assert app_module.config["gw2"]["api_key"].encode() not in r.data


def test_typed_upstream_errors_get_useful_messages(client):
    from gw2 import api

    with mock.patch("gw2.api.account", side_effect=api.GW2AuthError("nope")):
        r = client.get("/", headers={"Host": "127.0.0.1:5000"})
    assert r.status_code == 502
    assert b"API key" in r.data


# --- Host guard (fixes.md item 22b) -----------------------------------------

def test_bad_host_header_is_rejected(client):
    assert client.get("/", headers={"Host": "evil.com:5000"}).status_code == 400


def test_allowed_host_passes_the_guard(client):
    with mock.patch("gw2.api.account", side_effect=RuntimeError("stop here")):
        r = client.get("/", headers={"Host": "127.0.0.1:5000"})
    assert r.status_code != 400


# --- Security headers (fixes.md item 22c) -----------------------------------

def test_security_headers_are_present(client):
    r = client.get("/", headers={"Host": "127.0.0.1:5000"})
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "render.guildwars2.com" in csp


# --- Routing (fixes.md item 20) ---------------------------------------------

def test_unknown_character_is_a_404_not_an_upstream_call(client):
    with mock.patch("gw2.api.characters", return_value=["Real"]), \
         mock.patch("gw2.api.character") as fetch:
        r = client.get("/inventory/Fake", headers={"Host": "127.0.0.1:5000"})
    assert r.status_code == 404
    fetch.assert_not_called()


def test_unknown_equipment_character_is_a_404_not_an_upstream_call(client):
    with mock.patch("gw2.api.characters", return_value=["Real"]), \
         mock.patch("gw2.api.character") as fetch:
        r = client.get("/equipment/Fake", headers={"Host": "127.0.0.1:5000"})
    assert r.status_code == 404
    fetch.assert_not_called()


def test_equipment_picker_lists_characters(client):
    with mock.patch("gw2.api.characters", return_value=["Main"]), \
         mock.patch(
             "gw2.api.character",
             return_value={"profession": "Engineer", "race": "Charr", "level": 80},
         ):
        r = client.get("/equipment", headers={"Host": "127.0.0.1:5000"})
    assert r.status_code == 200
    assert b"Main" in r.data and b"Engineer" in r.data


def test_equipment_route_scans_storage_and_renders_report(client):
    char = {
        "name": "Main",
        "profession": "Engineer",
        "race": "Charr",
        "level": 80,
        "equipment": [{"id": 1, "slot": "Coat"}],
        "bags": [],
    }
    with mock.patch("gw2.api.characters", return_value=["Main"]), \
         mock.patch("gw2.api.character", return_value=char), \
         mock.patch("gw2.api.bank", return_value=[]), \
         mock.patch("gw2.api.shared_inventory", return_value=[]), \
         mock.patch("gw2.api.legendary_armory", return_value=[]), \
         mock.patch("gw2.api.items_bulk", return_value=({}, {1})), \
         mock.patch("gw2.api.professions", return_value={}), \
         mock.patch("gw2.api.itemstats_bulk", return_value=({}, set())):
        r = client.get("/equipment/Main", headers={"Host": "127.0.0.1:5000"})
    assert r.status_code == 200
    assert b"Equipment" in r.data and b"Main" in r.data
    assert b"omitted rather than guessed" in r.data


# --- Config loading (fixes.md item 23) --------------------------------------

def test_config_is_resolved_relative_to_the_app_not_the_cwd():
    src = open(os.path.join(ROOT, "app.py")).read()
    assert 'open("config.toml"' not in src
    assert "GW2_CONFIG" in src and "os.path.dirname" in src


def test_missing_config_exits_with_a_clear_message(tmp_path):
    import app as app_module

    with pytest.raises(SystemExit) as e:
        app_module.load_config(str(tmp_path / "nope.toml"))
    assert "not found" in str(e.value)


def test_placeholder_api_key_is_rejected(tmp_path):
    import app as app_module

    p = tmp_path / "config.toml"
    p.write_text('[gw2]\napi_key = "YOUR-API-KEY-HERE"\n')
    with pytest.raises(SystemExit) as e:
        app_module.load_config(str(p))
    assert "placeholder" in str(e.value)


def test_malformed_toml_exits_instead_of_raising(tmp_path):
    import app as app_module

    p = tmp_path / "config.toml"
    p.write_text("[gw2\napi_key =")
    with pytest.raises(SystemExit) as e:
        app_module.load_config(str(p))
    assert "valid TOML" in str(e.value)


def test_loose_config_permissions_are_tightened(tmp_path):
    import app as app_module

    p = tmp_path / "config.toml"
    p.write_text('[gw2]\napi_key = "real-key"\n')
    os.chmod(p, 0o644)
    app_module.load_config(str(p))
    assert os.stat(p).st_mode & 0o077 == 0


# --- Dependencies (fixes.md item 21) ----------------------------------------

def test_requirements_do_not_name_the_unregistered_tomllib_package():
    reqs = open(os.path.join(ROOT, "requirements.txt")).read()
    assert not re.search(r"^\s*tomllib\b", reqs, re.M)
    assert "tomli>=" in reqs
