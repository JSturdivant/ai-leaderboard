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


# Plain-language glossary text for the UI: what the benchmark measures, and its main caveat.
EXPLAIN = {
    "aa_intelligence": (
        "Artificial Analysis's headline score: one number combining many evaluations of knowledge, reasoning, maths, coding and agentic tasks. A good general-ability anchor.",
        "The mix of evaluations behind it changes between index versions, and OpenRouter republishes it for only some models."),
    "aa_coding": (
        "Artificial Analysis's coding composite, built from several programming and scientific-coding evaluations.",
        "Covers fewer models than the intelligence index, and the evaluations included change between versions."),
    "aa_agentic": (
        "Artificial Analysis's composite for agentic work: using tools and completing multi-step tasks without help.",
        "The newest of the three AA indices, so many cheaper models don't have it yet."),
    "design_arena_code": (
        "Elo rating from people voting between two AI-generated UI or code outputs on Design Arena (its code categories).",
        "Reflects how good the output looks and works to a human voter, not whether the logic is correct. Low default weight."),
    "epoch_eci": (
        "Epoch AI's Capabilities Index: a statistical model that fits one ability number per model across many benchmarks, so models tested on different sets stay comparable.",
        "A fitted estimate, so it can shift slightly when Epoch adds benchmarks."),
    "swe_bench_verified": (
        "Fix real GitHub issues from popular Python projects. A fix counts only if the project's own tests pass. 'Verified' is the human-checked 500-task subset.",
        "Close to saturated for the best models, depends on the agent scaffold used, and Epoch has run only some models."),
    "gpqa_diamond": (
        "Hard multiple-choice science questions (biology, physics, chemistry) written and checked by experts to be unsearchable. Random guessing scores 25%.",
        "Frontier models are near the ceiling, so it separates mid-tier models better than top ones."),
    "hle": (
        "Humanity's Last Exam: expert-written questions across many fields, built to stay hard after other benchmarks saturated.",
        "Few models are scored, mostly frontier ones, so cheaper models often show no value."),
    "arc_agi_2": (
        "Novel grid puzzles that test learning a new rule from a few examples, designed to resist memorisation.",
        "Results depend on reasoning effort (and cost per task); each model's best result is kept."),
    "frontiermath": (
        "Research-level maths problems with exact answers, kept private by Epoch (tiers 1-3, version 2).",
        "Only strong models are evaluated."),
    "aider_polyglot": (
        "Edit existing code to solve 225 hard Exercism exercises across C++, Go, Java, JavaScript, Python and Rust. The score is the share solved.",
        "Now mostly covers older models, so newer ones often lack it."),
    "scicode": (
        "Write code for problems drawn from real scientific research workflows (physics, maths, chemistry, biology, materials).",
        "Scores are low even for strong models, so absolute differences are small."),
    "terminal_bench": (
        "An AI agent works in a Linux terminal to complete real tasks such as building, debugging and system setup. The score is the share of tasks solved.",
        "Results depend heavily on the agent harness, so the median across harnesses is used rather than the best."),
    "apex_agents": (
        "Long-horizon tasks from professional work (such as finance, consulting and law) that an agent must carry out with tools. Score is pass@1: solved on the first attempt.",
        "A newer benchmark with a smaller set of models."),
    "arena_text": (
        "Arena (LMArena): people compare two anonymous answers to their own prompts and vote. Votes become an Elo-style rating. This is the style-controlled overall board, which discounts answer length and formatting.",
        "Measures what people prefer, which is not always what is most accurate."),
    "arena_coding": (
        "The same Arena text votes, restricted to prompts about coding.",
        "Chat-style coding questions, not whole-repository engineering."),
    "arena_hard_prompts": (
        "Arena text votes restricted to the hardest prompts: complex, specific, multi-part.",
        "A smaller sample than the overall board."),
    "arena_instruction_following": (
        "Arena text votes on prompts with explicit constraints such as format, length or content rules.",
        "A smaller sample than the overall board."),
    "arena_multi_turn": (
        "Arena text votes on conversations with several back-and-forth turns, which is how agent chat really goes.",
        "A smaller sample than the overall board."),
    "arena_webdev": (
        "Arena WebDev: people vote on complete web pages and apps that models build from a prompt.",
        "Judges the visible result, so it favours polished interfaces."),
    "arena_agent": (
        "Arena's agent leaderboard, drawn from real agentic coding sessions rather than a fixed test set.",
        "Few models are listed, and the score is a relative scale, not a percentage."),
}
for _key, (_what, _caveat) in EXPLAIN.items():
    METRICS[_key]["explain"] = _what
    METRICS[_key]["caveat"] = _caveat
