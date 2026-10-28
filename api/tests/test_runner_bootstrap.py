"""bootstrap 子命令：幂等探测（全命中零安装）/ 新机安装清单序列 / .claude.json 深合并。"""

from __future__ import annotations

import json

import pytest

from vega import runner as runner_mod
from vega.runner import bootstrap


class _R:  # subprocess.run 的假返回值
    def __init__(self, rc: int = 0, out: str = "") -> None:
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


def test_bootstrap_all_present_zero_installs(monkeypatch, tmp_path):
    monkeypatch.setenv("VEGA_CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(runner_mod.shutil, "which", lambda name, path=None: f"/fake/bin/{name}")
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        joined = " ".join(cmd)
        if joined == "crg-daemon status":
            return _R(out="Daemon:  running (PID 1)")
        if joined == "claude plugin marketplace list":
            return _R(out="claude-plugins-official")
        if joined == "claude plugin list":
            return _R(
                out="typescript-lsp@claude-plugins-official\n"
                "pyright-lsp@claude-plugins-official\njdtls-lsp@claude-plugins-official"
            )
        return _R()

    monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)
    report = bootstrap()

    assert report["failed"] == []
    assert report["installed"] == []
    assert set(report["skipped"]) == {
        "node", "jq", "ast-grep", "uv", "code-review-graph", "serena", "jdtls",
        "typescript-language-server", "pyright", "claude-cli", "crg-daemon",
        "plugin-marketplace", "typescript-lsp", "pyright-lsp", "jdtls-lsp",
    }
    # 全命中时不发任何安装/拉起命令
    assert not any("install" in " ".join(c) or "marketplace add" in " ".join(c) for c in calls)
    assert not any(c[:2] == ["crg-daemon", "start"] for c in calls)


def test_bootstrap_fresh_machine_install_sequence(monkeypatch, tmp_path):
    monkeypatch.setenv("VEGA_CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(runner_mod.shutil, "which", lambda name, path=None: f"/fake/bin/{name}" if name == "brew" else None)
    calls: list[list[str]] = []
    monkeypatch.setattr(runner_mod.subprocess, "run", lambda cmd, **kw: (calls.append(list(cmd)), _R())[1])
    report = bootstrap()

    flat = [" ".join(c) for c in calls]
    expected = [
        "brew install node",
        "brew install jq",
        "brew install ast-grep",
        "brew install uv",
        "uv tool install code-review-graph",
        "uv tool install serena-agent",
        "brew install jdtls",
        "npm install -g typescript typescript-language-server",
        "npm install -g pyright",
        "npm install -g @anthropic-ai/claude-code",
        "crg-daemon start",
        "claude plugin marketplace add anthropics/claude-plugins-official",
        "claude plugin install typescript-lsp@claude-plugins-official",
        "claude plugin install pyright-lsp@claude-plugins-official",
        "claude plugin install jdtls-lsp@claude-plugins-official",
    ]
    pos = [flat.index(c) for c in expected]
    assert pos == sorted(pos)  # 按清单顺序全部发出
    assert report["skipped"] == []
    assert report["installed"] == [
        "node", "jq", "ast-grep", "uv", "code-review-graph", "serena", "jdtls",
        "typescript-language-server", "pyright", "claude-cli", "crg-daemon",
        "plugin-marketplace", "typescript-lsp", "pyright-lsp", "jdtls-lsp",
    ]
    # MCP 条目落在 .claude.json（settings.json 的 mcpServers 运行时不生效）
    data = json.loads((tmp_path / "cfg" / ".claude.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["code-review-graph"]["command"] == "code-review-graph"
    assert data["mcpServers"]["code-review-graph"]["args"] == ["mcp"]
    assert "CRG_OPENAI_BASE_URL" in data["mcpServers"]["code-review-graph"]["env"]
    assert data["mcpServers"]["serena"]["command"] == "serena"


def test_bootstrap_settings_deep_merge(monkeypatch, tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    original = {
        "extraKnownMarketplaces": {"claude-plugins-official": {"source": {"source": "github", "repo": "x"}}},
        "other": 1,
        "mcpServers": {"keep": {"command": "x"}},
    }
    (cfg / ".claude.json").write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("VEGA_CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(runner_mod.shutil, "which", lambda name, path=None: f"/fake/bin/{name}")
    monkeypatch.setattr(runner_mod.subprocess, "run", lambda cmd, **kw: _R())
    bootstrap()

    data = json.loads((cfg / ".claude.json").read_text(encoding="utf-8"))
    assert data["other"] == 1  # 无关键保留
    assert data["extraKnownMarketplaces"] == original["extraKnownMarketplaces"]
    assert data["mcpServers"]["keep"] == {"command": "x"}  # 已有 MCP server 保留
    assert data["mcpServers"]["code-review-graph"]["args"] == ["mcp"]


def test_bootstrap_requires_brew(monkeypatch):
    monkeypatch.setattr(runner_mod.shutil, "which", lambda name, path=None: None)
    with pytest.raises(SystemExit):
        bootstrap()
