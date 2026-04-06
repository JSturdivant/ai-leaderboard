// ============================================================
// LLM Cost vs. Performance Tracker — Data Layer
// Last updated: April 2026
// ============================================================

const DATA_LAST_UPDATED = "April 6, 2026";

// Provider color palette
const PROVIDER_COLORS = {
    "OpenAI":    { bg: "rgba(16,163,127,0.75)", border: "#10a37f" },
    "Anthropic": { bg: "rgba(212,162,127,0.75)", border: "#d4a27f" },
    "Google":    { bg: "rgba(66,133,244,0.75)",  border: "#4285f4" },
    "Meta":      { bg: "rgba(6,104,225,0.75)",   border: "#0668e1" },
    "xAI":       { bg: "rgba(29,161,242,0.75)",  border: "#1da1f2" },
    "Mistral":   { bg: "rgba(249,115,22,0.75)",  border: "#f97316" },
    "DeepSeek":  { bg: "rgba(139,92,246,0.75)",  border: "#8b5cf6" },
};

// ============================================================
// Benchmark definitions
// ============================================================
const BENCHMARKS = {
    mmlu: {
        name: "MMLU",
        fullName: "Massive Multitask Language Understanding",
        scale: "0–100%",
        description: "Tests knowledge across 57 academic subjects including STEM, humanities, and social sciences. Each question is multiple-choice. Higher scores indicate broader factual knowledge and reasoning ability across disciplines.",
        measures: "Breadth of academic and professional knowledge",
        source: "Hendrycks et al., 2021 — scores from official model technical reports & OpenAI simple-evals"
    },
    gpqa: {
        name: "GPQA Diamond",
        fullName: "Graduate-Level Google-Proof Q&A (Diamond subset)",
        scale: "0–100%",
        description: "Expert-level science questions (biology, physics, chemistry) designed to be difficult even for domain PhDs. 'Google-proof' means the answers cannot be easily looked up. The Diamond subset contains the hardest questions where experts agree on the answer.",
        measures: "Deep scientific reasoning and expert-level knowledge",
        source: "Rein et al., 2023 — scores from model technical reports & Artificial Analysis"
    },
    humaneval: {
        name: "HumanEval",
        fullName: "HumanEval Code Generation",
        scale: "0–100% (pass@1)",
        description: "164 Python programming challenges testing function-level code generation. The model must produce correct, runnable code from a docstring specification. pass@1 means the first attempt must be correct.",
        measures: "Code generation ability (Python)",
        source: "Chen et al., 2021 — scores from model technical reports & LLM-Stats"
    },
    math: {
        name: "MATH",
        fullName: "MATH Benchmark (Competition Mathematics)",
        scale: "0–100%",
        description: "12,500 problems from high-school math competitions (AMC, AIME, etc.) spanning algebra, geometry, number theory, combinatorics, and calculus. Problems require multi-step reasoning and formal mathematical manipulation.",
        measures: "Mathematical problem-solving and formal reasoning",
        source: "Hendrycks et al., 2021 — scores from model technical reports"
    },
    mmlu_pro: {
        name: "MMLU-Pro",
        fullName: "MMLU-Pro (Enhanced MMLU)",
        scale: "0–100%",
        description: "A harder version of MMLU with 10 answer choices instead of 4, more reasoning-intensive questions, and reduced noise. Designed to better differentiate between top-performing models where standard MMLU is saturating.",
        measures: "Advanced reasoning across academic domains",
        source: "Wang et al., 2024 — scores from model reports & Vellum LLM Leaderboard"
    },
    arena_elo: {
        name: "Arena ELO",
        fullName: "Chatbot Arena ELO Rating (LMArena / LMSYS)",
        scale: "ELO rating (~800–1600)",
        description: "Crowdsourced blind head-to-head comparisons where human users chat with two anonymous models and pick the better response. ELO ratings are computed from hundreds of thousands of votes. Measures overall conversational quality as judged by real users.",
        measures: "Overall human-preference quality in open-ended conversation",
        source: "LMSYS / LMArena Chatbot Arena, lmarena.ai — rankings as of March 2026"
    },
    ifeval: {
        name: "IFEval",
        fullName: "Instruction-Following Evaluation",
        scale: "0–100%",
        description: "Tests how precisely a model follows specific formatting and content constraints (e.g., 'write exactly 3 paragraphs', 'include the word X exactly twice'). Measures instruction compliance rather than creative quality.",
        measures: "Precise instruction-following ability",
        source: "Zhou et al., 2023 — scores from model technical reports"
    },
    swe_bench: {
        name: "SWE-bench Verified",
        fullName: "Software Engineering Benchmark (Verified subset)",
        scale: "0–100%",
        description: "Real-world GitHub issues from popular Python repositories. The model must generate a correct patch that resolves the issue and passes existing test suites. The Verified subset contains human-validated instances.",
        measures: "Real-world software engineering and bug-fixing ability",
        source: "Jimenez et al., 2024 — scores from model technical reports & SWE-bench leaderboard"
    }
};

// ============================================================
// LLM Model Data
// Pricing: USD per 1 million tokens
// Benchmark scores: percentage (0-100) unless otherwise noted
// null = score not available / not reported
// ============================================================
const LLM_DATA = [
    // ── OpenAI ──────────────────────────────────────────────
    {
        name: "GPT-4o",
        provider: "OpenAI",
        input_cost: 2.50,
        output_cost: 10.00,
        benchmarks: {
            mmlu: 85.7,
            gpqa: 53.6,
            humaneval: 90.2,
            math: 76.6,
            mmlu_pro: 72.6,
            arena_elo: 1290,
            ifeval: 84.0,
            swe_bench: 33.2
        }
    },
    {
        name: "GPT-4o mini",
        provider: "OpenAI",
        input_cost: 0.15,
        output_cost: 0.60,
        benchmarks: {
            mmlu: 82.0,
            gpqa: 40.2,
            humaneval: 87.2,
            math: 70.2,
            mmlu_pro: 63.2,
            arena_elo: 1230,
            ifeval: 80.5,
            swe_bench: null
        }
    },
    {
        name: "GPT-4.1",
        provider: "OpenAI",
        input_cost: 2.00,
        output_cost: 8.00,
        benchmarks: {
            mmlu: 90.2,
            gpqa: 66.3,
            humaneval: 93.4,
            math: 82.0,
            mmlu_pro: 77.3,
            arena_elo: 1355,
            ifeval: 88.3,
            swe_bench: 54.6
        }
    },
    {
        name: "GPT-4.1 mini",
        provider: "OpenAI",
        input_cost: 0.40,
        output_cost: 1.60,
        benchmarks: {
            mmlu: 87.5,
            gpqa: 56.2,
            humaneval: 90.8,
            math: 78.1,
            mmlu_pro: 70.5,
            arena_elo: 1305,
            ifeval: 85.0,
            swe_bench: 37.3
        }
    },
    {
        name: "GPT-4.1 nano",
        provider: "OpenAI",
        input_cost: 0.10,
        output_cost: 0.40,
        benchmarks: {
            mmlu: 80.1,
            gpqa: 50.3,
            humaneval: 83.6,
            math: 65.2,
            mmlu_pro: 58.7,
            arena_elo: 1190,
            ifeval: 78.2,
            swe_bench: null
        }
    },
    {
        name: "o1",
        provider: "OpenAI",
        input_cost: 15.00,
        output_cost: 60.00,
        benchmarks: {
            mmlu: 91.8,
            gpqa: 78.0,
            humaneval: 92.4,
            math: 94.8,
            mmlu_pro: 81.4,
            arena_elo: 1350,
            ifeval: 86.5,
            swe_bench: 48.9
        }
    },
    {
        name: "o1-mini",
        provider: "OpenAI",
        input_cost: 1.10,
        output_cost: 4.40,
        benchmarks: {
            mmlu: 85.2,
            gpqa: 60.0,
            humaneval: 92.4,
            math: 90.0,
            mmlu_pro: 74.3,
            arena_elo: 1300,
            ifeval: 82.0,
            swe_bench: null
        }
    },
    {
        name: "o3",
        provider: "OpenAI",
        input_cost: 2.00,
        output_cost: 8.00,
        benchmarks: {
            mmlu: 92.9,
            gpqa: 82.8,
            humaneval: 87.4,
            math: 96.7,
            mmlu_pro: 84.5,
            arena_elo: 1415,
            ifeval: 91.2,
            swe_bench: 69.1
        }
    },
    {
        name: "o3-mini",
        provider: "OpenAI",
        input_cost: 1.10,
        output_cost: 4.40,
        benchmarks: {
            mmlu: 86.9,
            gpqa: 70.2,
            humaneval: 92.6,
            math: 94.4,
            mmlu_pro: 78.0,
            arena_elo: 1340,
            ifeval: 87.0,
            swe_bench: 49.3
        }
    },
    {
        name: "o4-mini",
        provider: "OpenAI",
        input_cost: 1.10,
        output_cost: 4.40,
        benchmarks: {
            mmlu: 90.0,
            gpqa: 81.3,
            humaneval: 98.2,
            math: 96.3,
            mmlu_pro: 82.8,
            arena_elo: 1395,
            ifeval: 90.5,
            swe_bench: 68.1
        }
    },

    // ── Anthropic ───────────────────────────────────────────
    {
        name: "Claude 3 Opus",
        provider: "Anthropic",
        input_cost: 15.00,
        output_cost: 75.00,
        benchmarks: {
            mmlu: 86.8,
            gpqa: 50.4,
            humaneval: 84.9,
            math: 60.1,
            mmlu_pro: 68.5,
            arena_elo: 1250,
            ifeval: 82.2,
            swe_bench: 22.0
        }
    },
    {
        name: "Claude 3.5 Sonnet",
        provider: "Anthropic",
        input_cost: 3.00,
        output_cost: 15.00,
        benchmarks: {
            mmlu: 88.7,
            gpqa: 65.0,
            humaneval: 93.7,
            math: 78.3,
            mmlu_pro: 78.0,
            arena_elo: 1320,
            ifeval: 88.0,
            swe_bench: 49.0
        }
    },
    {
        name: "Claude 3.5 Haiku",
        provider: "Anthropic",
        input_cost: 0.80,
        output_cost: 4.00,
        benchmarks: {
            mmlu: 84.0,
            gpqa: 51.1,
            humaneval: 88.1,
            math: 69.2,
            mmlu_pro: 65.0,
            arena_elo: 1230,
            ifeval: 82.5,
            swe_bench: null
        }
    },
    {
        name: "Claude Sonnet 4",
        provider: "Anthropic",
        input_cost: 3.00,
        output_cost: 15.00,
        benchmarks: {
            mmlu: 89.3,
            gpqa: 72.4,
            humaneval: 94.2,
            math: 81.5,
            mmlu_pro: 79.8,
            arena_elo: 1360,
            ifeval: 89.5,
            swe_bench: 53.6
        }
    },
    {
        name: "Claude Opus 4",
        provider: "Anthropic",
        input_cost: 15.00,
        output_cost: 75.00,
        benchmarks: {
            mmlu: 87.4,
            gpqa: 78.5,
            humaneval: 92.0,
            math: 83.2,
            mmlu_pro: 78.2,
            arena_elo: 1370,
            ifeval: 88.0,
            swe_bench: 72.0
        }
    },
    {
        name: "Claude Haiku 4.5",
        provider: "Anthropic",
        input_cost: 1.00,
        output_cost: 5.00,
        benchmarks: {
            mmlu: 90.8,
            gpqa: 73.0,
            humaneval: 85.2,
            math: 78.4,
            mmlu_pro: 75.2,
            arena_elo: 1315,
            ifeval: 87.5,
            swe_bench: null
        }
    },
    {
        name: "Claude Sonnet 4.5",
        provider: "Anthropic",
        input_cost: 3.00,
        output_cost: 15.00,
        benchmarks: {
            mmlu: 89.5,
            gpqa: 83.4,
            humaneval: 97.6,
            math: 85.0,
            mmlu_pro: 80.5,
            arena_elo: 1410,
            ifeval: 91.0,
            swe_bench: 59.7
        }
    },
    {
        name: "Claude Opus 4.5",
        provider: "Anthropic",
        input_cost: 5.00,
        output_cost: 25.00,
        benchmarks: {
            mmlu: 90.2,
            gpqa: 87.0,
            humaneval: 96.8,
            math: 86.5,
            mmlu_pro: 82.0,
            arena_elo: 1430,
            ifeval: 92.0,
            swe_bench: 64.0
        }
    },
    {
        name: "Claude Sonnet 4.6",
        provider: "Anthropic",
        input_cost: 3.00,
        output_cost: 15.00,
        benchmarks: {
            mmlu: 89.3,
            gpqa: 84.0,
            humaneval: 97.8,
            math: 86.0,
            mmlu_pro: 81.2,
            arena_elo: 1435,
            ifeval: 92.5,
            swe_bench: 62.5
        }
    },
    {
        name: "Claude Opus 4.6",
        provider: "Anthropic",
        input_cost: 5.00,
        output_cost: 25.00,
        benchmarks: {
            mmlu: 91.0,
            gpqa: 88.2,
            humaneval: 97.5,
            math: 88.0,
            mmlu_pro: 83.5,
            arena_elo: 1505,
            ifeval: 93.0,
            swe_bench: 72.5
        }
    },

    // ── Google ──────────────────────────────────────────────
    {
        name: "Gemini 1.5 Pro",
        provider: "Google",
        input_cost: 1.25,
        output_cost: 5.00,
        benchmarks: {
            mmlu: 85.9,
            gpqa: 46.2,
            humaneval: 84.1,
            math: 67.7,
            mmlu_pro: 69.4,
            arena_elo: 1260,
            ifeval: 83.0,
            swe_bench: null
        }
    },
    {
        name: "Gemini 2.0 Flash",
        provider: "Google",
        input_cost: 0.10,
        output_cost: 0.40,
        benchmarks: {
            mmlu: 84.5,
            gpqa: 55.8,
            humaneval: 89.0,
            math: 73.5,
            mmlu_pro: 66.2,
            arena_elo: 1300,
            ifeval: 84.5,
            swe_bench: null
        }
    },
    {
        name: "Gemini 2.5 Pro",
        provider: "Google",
        input_cost: 1.25,
        output_cost: 10.00,
        benchmarks: {
            mmlu: 89.8,
            gpqa: 84.0,
            humaneval: 95.2,
            math: 91.8,
            mmlu_pro: 81.8,
            arena_elo: 1445,
            ifeval: 91.5,
            swe_bench: 63.8
        }
    },
    {
        name: "Gemini 2.5 Flash",
        provider: "Google",
        input_cost: 0.30,
        output_cost: 2.50,
        benchmarks: {
            mmlu: 88.4,
            gpqa: 82.8,
            humaneval: 92.5,
            math: 89.2,
            mmlu_pro: 78.5,
            arena_elo: 1380,
            ifeval: 89.0,
            swe_bench: 46.2
        }
    },

    // ── Meta (Llama — pricing via API providers) ────────────
    {
        name: "Llama 3.1 70B",
        provider: "Meta",
        input_cost: 0.18,
        output_cost: 0.54,
        benchmarks: {
            mmlu: 82.0,
            gpqa: 42.7,
            humaneval: 80.5,
            math: 68.0,
            mmlu_pro: 56.3,
            arena_elo: 1200,
            ifeval: 77.0,
            swe_bench: null
        }
    },
    {
        name: "Llama 3.1 405B",
        provider: "Meta",
        input_cost: 0.80,
        output_cost: 2.40,
        benchmarks: {
            mmlu: 87.3,
            gpqa: 51.1,
            humaneval: 89.0,
            math: 73.8,
            mmlu_pro: 73.3,
            arena_elo: 1260,
            ifeval: 84.0,
            swe_bench: null
        }
    },
    {
        name: "Llama 3.3 70B",
        provider: "Meta",
        input_cost: 0.20,
        output_cost: 0.60,
        benchmarks: {
            mmlu: 86.0,
            gpqa: 49.8,
            humaneval: 88.4,
            math: 77.0,
            mmlu_pro: 62.4,
            arena_elo: 1250,
            ifeval: 82.0,
            swe_bench: null
        }
    },
    {
        name: "Llama 4 Scout",
        provider: "Meta",
        input_cost: 0.08,
        output_cost: 0.30,
        benchmarks: {
            mmlu: 79.6,
            gpqa: 57.2,
            humaneval: 74.1,
            math: 58.3,
            mmlu_pro: 74.3,
            arena_elo: 1280,
            ifeval: 81.0,
            swe_bench: null
        }
    },
    {
        name: "Llama 4 Maverick",
        provider: "Meta",
        input_cost: 0.27,
        output_cost: 0.85,
        benchmarks: {
            mmlu: 85.5,
            gpqa: 69.8,
            humaneval: 86.4,
            math: 61.2,
            mmlu_pro: 80.5,
            arena_elo: 1340,
            ifeval: 85.5,
            swe_bench: null
        }
    },

    // ── xAI ─────────────────────────────────────────────────
    {
        name: "Grok-2",
        provider: "xAI",
        input_cost: 2.00,
        output_cost: 10.00,
        benchmarks: {
            mmlu: 87.5,
            gpqa: 56.4,
            humaneval: 88.4,
            math: 76.1,
            mmlu_pro: 72.0,
            arena_elo: 1285,
            ifeval: 83.0,
            swe_bench: null
        }
    },
    {
        name: "Grok-3",
        provider: "xAI",
        input_cost: 3.00,
        output_cost: 15.00,
        benchmarks: {
            mmlu: 92.7,
            gpqa: 84.6,
            humaneval: 90.5,
            math: 93.3,
            mmlu_pro: 79.9,
            arena_elo: 1402,
            ifeval: 89.0,
            swe_bench: null
        }
    },

    // ── Mistral ─────────────────────────────────────────────
    {
        name: "Mistral Large",
        provider: "Mistral",
        input_cost: 2.00,
        output_cost: 6.00,
        benchmarks: {
            mmlu: 84.0,
            gpqa: 53.1,
            humaneval: 92.0,
            math: 72.6,
            mmlu_pro: 69.4,
            arena_elo: 1265,
            ifeval: 83.5,
            swe_bench: null
        }
    },
    {
        name: "Mistral Small",
        provider: "Mistral",
        input_cost: 0.20,
        output_cost: 0.60,
        benchmarks: {
            mmlu: 80.5,
            gpqa: 43.8,
            humaneval: 84.0,
            math: 61.5,
            mmlu_pro: 56.8,
            arena_elo: 1195,
            ifeval: 78.0,
            swe_bench: null
        }
    },

    // ── DeepSeek ────────────────────────────────────────────
    {
        name: "DeepSeek-V3",
        provider: "DeepSeek",
        input_cost: 0.27,
        output_cost: 1.10,
        benchmarks: {
            mmlu: 87.1,
            gpqa: 68.4,
            humaneval: 82.6,
            math: 61.6,
            mmlu_pro: 75.9,
            arena_elo: 1310,
            ifeval: 86.0,
            swe_bench: 42.0
        }
    },
    {
        name: "DeepSeek-R1",
        provider: "DeepSeek",
        input_cost: 0.55,
        output_cost: 2.19,
        benchmarks: {
            mmlu: 90.8,
            gpqa: 81.0,
            humaneval: 90.2,
            math: 94.3,
            mmlu_pro: 84.0,
            arena_elo: 1370,
            ifeval: 88.2,
            swe_bench: 49.2
        }
    },
];

// ============================================================
// Data Sources — used for the Sources section
// ============================================================
const PRICING_SOURCES = [
    { name: "OpenAI API Pricing", url: "https://openai.com/api/pricing/" },
    { name: "Anthropic Claude Pricing", url: "https://www.anthropic.com/pricing" },
    { name: "Google Gemini API Pricing", url: "https://ai.google.dev/gemini-api/docs/pricing" },
    { name: "Mistral AI Pricing", url: "https://mistral.ai/pricing" },
    { name: "DeepSeek API Pricing", url: "https://api-docs.deepseek.com/quick_start/pricing" },
    { name: "xAI Grok Models & Pricing", url: "https://docs.x.ai/developers/models" },
    { name: "Together AI Pricing (Llama)", url: "https://www.together.ai/pricing" },
];

const BENCHMARK_SOURCES = [
    { name: "Vellum LLM Leaderboard", url: "https://www.vellum.ai/llm-leaderboard" },
    { name: "LLM-Stats Benchmarks", url: "https://llm-stats.com/benchmarks" },
    { name: "Artificial Analysis Leaderboard", url: "https://artificialanalysis.ai/leaderboards/models" },
    { name: "LMArena Chatbot Arena", url: "https://lmarena.ai/" },
    { name: "OpenAI Simple-Evals (GitHub)", url: "https://github.com/openai/simple-evals" },
    { name: "SWE-bench Leaderboard", url: "https://www.swebench.com/" },
    { name: "MMLU Paper (Hendrycks et al.)", url: "https://arxiv.org/abs/2009.03300" },
    { name: "GPQA Paper (Rein et al.)", url: "https://arxiv.org/abs/2311.12022" },
    { name: "Llama 4 Model Card", url: "https://www.llama.com/models/llama-4/" },
    { name: "Grok-3 Announcement", url: "https://x.ai/news/grok-3" },
];
