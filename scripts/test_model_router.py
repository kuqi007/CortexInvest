import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).with_name("model_router.py")
spec = importlib.util.spec_from_file_location("model_router", SCRIPT)
model_router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model_router)


def test_parse_glm_quota_extracts_5h_and_weekly_limits():
    data = {
        "success": True,
        "data": {
            "level": "pro",
            "limits": [
                {
                    "type": "TOKENS_LIMIT",
                    "unit": 6,
                    "number": 1,
                    "percentage": 12,
                    "nextResetTime": 1777891613000,
                },
                {
                    "type": "TOKENS_LIMIT",
                    "unit": 3,
                    "number": 5,
                    "percentage": 7,
                    "nextResetTime": 1777285758000,
                },
                {
                    "type": "TIME_LIMIT",
                    "usage": 1000,
                    "currentValue": 124,
                    "remaining": 876,
                },
            ],
        },
    }

    rows = model_router.parse_glm_quota(data)

    assert rows[0]["label"] == "GLM 5小时额度"
    assert rows[0]["used"] == 7
    assert rows[0]["remaining"] == 93
    assert rows[1]["label"] == "GLM 每周额度"
    assert rows[1]["used"] == 12
    assert rows[1]["remaining"] == 88
    assert rows[2]["label"] == "GLM MCP/TIME_LIMIT"
    assert rows[2]["remaining"] == 876
    assert rows[2]["total"] == 1000


def test_parse_minimax_quota_treats_usage_count_as_remaining():
    data = {
        "base_resp": {"status_code": "0", "status_msg": "success"},
        "model_remains": [
            {
                "model_name": "MiniMax-M*",
                "current_interval_total_count": 1500,
                "current_interval_usage_count": 399,
                "current_weekly_total_count": 0,
                "current_weekly_usage_count": 0,
                "remains_time": 7477635,
            },
            {
                "model_name": "coding-plan-vlm",
                "current_interval_total_count": 150,
                "current_interval_usage_count": 150,
            },
        ],
    }

    rows = model_router.parse_minimax_quota(data)

    assert rows[0]["label"] == "MiniMax-M* 5小时额度"
    assert rows[0]["remaining"] == 399
    assert rows[0]["total"] == 1500
    assert rows[0]["used"] == 1101
    assert all("每周额度" not in row["label"] for row in rows)


def test_minimax_coding_filter_accepts_real_m2_model_names():
    assert model_router.is_minimax_coding_quota_row("MiniMax-M2.7-highspeed 5小时额度")
    assert model_router.is_minimax_coding_quota_row("MiniMax-M* 5小时额度")
    assert model_router.is_minimax_coding_quota_row("coding-plan-search 5小时额度")
    assert not model_router.is_minimax_coding_quota_row("music-2.6 5小时额度")


def test_cmd_auto_uses_glm_dual_endpoint_probe(monkeypatch):
    calls = []
    chosen = []

    monkeypatch.setattr(
        model_router,
        "load_api_keys",
        lambda: {"minimax": "minimax-key", "glm": "glm-key", "kimi": ""},
    )

    def fake_probe_provider(name, _config, _api_key):
        calls.append(name)
        return "ok", "available"

    monkeypatch.setattr(model_router, "probe_provider", fake_probe_provider)
    monkeypatch.setattr(model_router, "probe_glm", lambda _key: ("warning", "generic endpoint usable"))
    monkeypatch.setattr(model_router, "switch_preset", lambda preset: chosen.append(preset))

    model_router.cmd_auto()

    assert "glm" not in calls
    assert chosen == ["economy"]


def test_cmd_auto_switches_preset_from_quota_before_probe(monkeypatch):
    chosen = []

    monkeypatch.setattr(
        model_router,
        "load_api_keys",
        lambda: {"minimax": "minimax-key", "glm": "glm-key", "kimi": "kimi-key"},
    )
    monkeypatch.setattr(
        model_router,
        "fetch_all_quota",
        lambda _keys: {
            "glm": (
                200,
                [
                    {"label": "GLM 5小时额度", "remaining": 99, "unit": "%"},
                    {"label": "GLM 每周额度", "remaining": 99, "unit": "%"},
                ],
                {},
            ),
            "minimax": (200, [{"label": "MiniMax-M* 5小时额度", "remaining": 600, "total": 1500}], {}),
            "kimi": (200, [{"label": "Kimi 每周额度", "remaining": 28, "unit": "%"}], {}),
        },
    )
    monkeypatch.setattr(model_router, "probe_provider", lambda *_args: (_ for _ in ()).throw(AssertionError("probe should not run")))
    monkeypatch.setattr(model_router, "switch_preset", lambda preset: chosen.append(preset))

    model_router.cmd_auto()

    assert chosen == ["balanced"]


def test_resolve_preset_name_maps_provider_aliases_to_new_presets():
    assert model_router.resolve_preset_name("minimax") == "economy"
    assert model_router.resolve_preset_name("glm") == "balanced"
    assert model_router.resolve_preset_name("kimi") == "smart"
    assert model_router.resolve_preset_name("balanced") == "balanced"


def test_strip_top_level_council_removes_unsupported_agent_config():
    text = '''{
  "preset": "balanced",
  "presets": {
    "balanced": {
      "orchestrator": {"model": "glm/glm-5"}
    }
  },
  "council": {
    "presets": {
      "balanced": {
        "a": {"model": "glm/glm-5"}
      }
    },
    "timeout": 240000
  }
}'''

    cleaned = model_router.strip_top_level_key(text, "council")

    assert '"council"' not in cleaned
    assert '"presets"' in cleaned
    assert '"balanced"' in cleaned


def test_switch_preset_sanitizes_when_preset_is_unchanged(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "oh-my-opencode-slim.jsonc"
    config_path.write_text('''{
  "preset": "balanced",
  "presets": {
    "balanced": {
      "orchestrator": {"model": "glm/glm-5"}
    }
  },
  "council": {
    "presets": {
      "balanced": {
        "a": {"model": "glm/glm-5"}
      }
    }
  }
}''')
    monkeypatch.setattr(model_router, "CONFIG_PATH", config_path)

    model_router.switch_preset("balanced")

    output = capsys.readouterr().out
    assert "配置已清理" in output
    assert "切换到 'balanced'" not in output
    assert '"council"' not in config_path.read_text()


def test_recommend_preset_from_quota_prefers_balanced_when_glm_has_quota():
    rows_by_provider = {
        "glm": [
            {"label": "GLM 5小时额度", "remaining": 99, "unit": "%"},
            {"label": "GLM 每周额度", "remaining": 80, "unit": "%"},
        ],
        "minimax": [
            {"label": "MiniMax-M* 5小时额度", "remaining": 600, "total": 1500},
        ],
        "kimi": [
            {"label": "Kimi 每周额度", "remaining": 28, "unit": "%"},
        ],
    }

    assert model_router.recommend_preset_from_quota(rows_by_provider) == "balanced"


def test_recommend_preset_from_quota_falls_back_to_economy_when_glm_low():
    rows_by_provider = {
        "glm": [
            {"label": "GLM 5小时额度", "remaining": 3, "unit": "%"},
            {"label": "GLM 每周额度", "remaining": 4, "unit": "%"},
        ],
        "minimax": [
            {"label": "MiniMax-M* 5小时额度", "remaining": 600, "total": 1500},
        ],
        "kimi": [
            {"label": "Kimi 每周额度", "remaining": 90, "unit": "%"},
        ],
    }

    assert model_router.recommend_preset_from_quota(rows_by_provider) == "economy"


def test_recommend_preset_requires_both_glm_windows_at_least_50():
    rows_by_provider = {
        "glm": [
            {"label": "GLM 5小时额度", "remaining": 80, "unit": "%"},
            {"label": "GLM 每周额度", "remaining": 49, "unit": "%"},
        ],
        "minimax": [
            {"label": "MiniMax-M* 5小时额度", "remaining": 600, "total": 1500},
        ],
        "kimi": [],
    }

    assert model_router.recommend_preset_from_quota(rows_by_provider) == "economy"


def test_parse_kimi_quota_extracts_weekly_and_5h_windows():
    data = {
        "usage": {
            "limit": "100",
            "used": "71",
            "remaining": "29",
            "resetTime": "2026-05-01T04:50:22.242278Z",
        },
        "limits": [
            {
                "window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
                "detail": {
                    "limit": "100",
                    "remaining": "100",
                    "resetTime": "2026-04-28T08:50:22.242278Z",
                },
            }
        ],
    }

    rows = model_router.parse_kimi_quota(data)

    assert rows[0]["label"] == "Kimi 每周额度"
    assert rows[0]["used"] == 71
    assert rows[0]["remaining"] == 29
    assert rows[0]["unit"] == "%"
    assert rows[1]["label"] == "Kimi 5小时额度"
    assert rows[1]["remaining"] == 100
    assert rows[1]["total"] == 100
    assert model_router.format_quota_row(rows[0]) == "Kimi 每周额度: 剩余 29%，已用 71%，重置 2026-05-01 12:50:22 CST"
