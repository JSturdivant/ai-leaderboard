"""Catalogue of every benchmark/metric the leaderboard can ingest.

Which metrics feed which category, and with what weight, lives in config.json.
"""

AA = "https://artificialanalysis.ai/"
EPOCH = "https://epoch.ai/benchmarks"
ARENA = "https://lmarena.ai/leaderboard"

METRICS = {
    # --- Artificial Analysis indices, republished by OpenRouter -------------------------
    "aa_intelligence": dict(label="AA Intelligence Index", short="AA Intel", source="openrouter", unit="index 0-100", url=AA,
        desc="Artificial Analysis composite across knowledge, reasoning, math, coding and agentic evals."),
    "aa_coding": dict(label="AA Coding Index", short="AA Code", source="openrouter", unit="index 0-100", url=AA,
        desc="Artificial Analysis coding composite (e.g. LiveCodeBench, SciCode, Terminal-Bench)."),
    "aa_agentic": dict(label="AA Agentic Index", short="AA Agent", source="openrouter", unit="index 0-100", url=AA,
        desc="Artificial Analysis agentic composite (tool use, multi-step tasks)."),
    "design_arena_code": dict(label="Design Arena (code)", short="Design Arena", source="openrouter", unit="Elo", url="https://www.designarena.ai/",
        desc="Human-preference Elo for generated code/UI (Design Arena, 'codecategories'), via OpenRouter."),
    # --- Epoch AI -------------------------------------------------------------------------
    "epoch_eci": dict(label="Epoch Capabilities Index", short="Epoch ECI", source="epoch", unit="ECI", url=EPOCH,
        desc="Epoch's single capability score fitted across many benchmarks."),
    "swe_bench_verified": dict(label="SWE-bench Verified", short="SWE-bench", source="epoch", unit="% resolved", url=EPOCH,
        desc="Real GitHub issues fixed by the model; run by Epoch with a common scaffold."),
    "gpqa_diamond": dict(label="GPQA Diamond", short="GPQA", source="epoch", unit="% correct", url=EPOCH,
        desc="Graduate-level, 'Google-proof' science questions."),
    "hle": dict(label="Humanity's Last Exam", short="HLE", source="epoch", unit="% correct", url=EPOCH,
        desc="Expert-written frontier questions across disciplines."),
    "arc_agi_2": dict(label="ARC-AGI-2", short="ARC-AGI-2", source="epoch", unit="% solved", url=EPOCH,
        desc="Novel abstract-reasoning puzzles that resist memorisation."),
    "frontiermath": dict(label="FrontierMath T1-3", short="FrontierMath", source="epoch", unit="% solved", url=EPOCH,
        desc="Research-level mathematics problems (tiers 1-3, v2)."),
    "aider_polyglot": dict(label="Aider polyglot", short="Aider", source="epoch", unit="% correct", url=EPOCH,
        desc="Multi-language code editing exercises (Aider leaderboard, via Epoch)."),
    "scicode": dict(label="SciCode", short="SciCode", source="epoch", unit="score", url=EPOCH,
        desc="Scientific-computing coding problems."),
    "terminal_bench": dict(label="Terminal-Bench 2.0", short="Terminal-Bench", source="epoch", unit="% solved", url=EPOCH,
        desc="Command-line agent tasks. Median across agent harnesses, so a single tuned harness cannot inflate a model."),
    "apex_agents": dict(label="APEX-Agents", short="APEX-Agents", source="epoch", unit="% pass@1", url=EPOCH,
        desc="Long-horizon professional-services agent tasks."),
    # --- Arena (LMArena) ------------------------------------------------------------------
    "arena_text": dict(label="Arena text (overall)", short="Arena chat", source="arena", unit="Elo", url=ARENA,
        desc="Blind human preference in open-ended chat (style-controlled)."),
    "arena_coding": dict(label="Arena text: coding", short="Arena code", source="arena", unit="Elo", url=ARENA,
        desc="Arena text votes on coding prompts."),
    "arena_hard_prompts": dict(label="Arena text: hard prompts", short="Arena hard", source="arena", unit="Elo", url=ARENA,
        desc="Arena text votes on the hardest prompts."),
    "arena_instruction_following": dict(label="Arena text: instruction following", short="Arena instr.", source="arena", unit="Elo", url=ARENA,
        desc="Arena text votes on prompts with explicit constraints."),
    "arena_multi_turn": dict(label="Arena text: multi-turn", short="Arena multi-turn", source="arena", unit="Elo", url=ARENA,
        desc="Arena text votes on multi-turn conversations."),
    "arena_webdev": dict(label="Arena WebDev", short="Arena WebDev", source="arena", unit="Elo", url="https://lmarena.ai/leaderboard/webdev",
        desc="Human votes on web apps built by the model."),
    "arena_agent": dict(label="Arena agent", short="Arena agent", source="arena", unit="score", url=ARENA,
        desc="Agent leaderboard from real agentic coding sessions."),
}

SOURCE_LABELS = {
    "openrouter": "OpenRouter (incl. Artificial Analysis indices)",
    "epoch": "Epoch AI Benchmarking Hub",
    "arena": "Arena (LMArena)",
}
