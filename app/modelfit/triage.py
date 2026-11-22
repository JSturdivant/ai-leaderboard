"""Request triage: classify a prompt / conversation / task description into what it needs from a model.

Standard library only. Three layers, combined in log space:

1. a multinomial naive Bayes model over unigrams + bigrams (stop words removed, light stemming), trained at import
   time on the seed corpus in triage_corpus.py;
2. structural signals that bag-of-words misses: code fences and syntax, stack traces, SQL, LaTeX and equations,
   tool definitions and tool-call messages, "translate into <language>", JSON/schema requests, images;
3. weighting by source: an explicit task description counts double, the latest user turn more than older turns.

The task-type posterior is mapped onto the four benchmark dimensions (coding, agentic, reasoning, chat) that the
ranker scores with, plus complexity, needs (tools, vision, long context, structured output), language and a
token estimate.
"""

import math
import re
from collections import Counter

from .triage_corpus import EXAMPLES, TASKS

DIMENSIONS = ("coding", "agentic", "reasoning", "chat")
STOP = set("""a an the and or but if then else of to in on at for from by with without about into over under is are was
were be been being am do does did done have has had having i me my we our you your he she it its they them their this
that these those there here what which who whom whose when where why how can could should would will shall may might
must please pls thanks thank just also very so as not no yes up out than too more most some any all each other such
only own same few both again further once let lets get got make made like want need help me us im ive id youre""".split())
WORD = re.compile(r"[a-z][a-z0-9+#.'-]*[a-z0-9+#]|[a-z]")

LANGS = {
    "es": set("el la los las de que y en un una por con para es no se lo como más pero sus le ya o este sí porque esta entre cuando muy sin sobre también me hasta hay donde quien desde todo nos durante todos uno les ni contra otros ese eso ante ellos e esto mí antes algunos qué unos yo otro otras otra él tanto esa estos mucho quienes nada muchos cual poco ella estar estas algunas algo nosotros".split()),
    "fr": set("le la les de des du un une et en est que qui dans pour pas sur au avec ce cet il elle ne se plus par son sont mais ou comme nous vous leur tout cette ont été aux ces je tu trois deux points".split()),
    "de": set("der die das und ist nicht ein eine zu den von mit sich des auf für im dem auch es an werden aus er hat dass sie nach wird bei einer um am sind noch wie einem über einen so zum war haben nur oder aber vor zur bis mehr durch man ich du".split()),
    "pt": set("o a os as de que e do da em um uma para com não por mais se na no ao dos das como mas foi ele ela sua seu ou ser quando muito há nos já está eu também só pelo pela até isso".split()),
    "it": set("il lo la i gli le di che e un una per con non sono del della è nel alla da come più ma anche se questo questa ci si ho io tu".split()),
}
SCRIPTS = [("zh", re.compile(r"[\u4e00-\u9fff]")), ("ja", re.compile(r"[\u3040-\u30ff]")), ("ko", re.compile(r"[\uac00-\ud7af]")),
           ("ru", re.compile(r"[\u0400-\u04ff]")), ("ar", re.compile(r"[\u0600-\u06ff]")), ("hi", re.compile(r"[\u0900-\u097f]")),
           ("he", re.compile(r"[\u0590-\u05ff]")), ("th", re.compile(r"[\u0e00-\u0e7f]"))]
LANG_NAMES = {"en": "English", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese", "it": "Italian", "zh": "Chinese",
              "ja": "Japanese", "ko": "Korean", "ru": "Russian", "ar": "Arabic", "hi": "Hindi", "he": "Hebrew", "th": "Thai"}

# structural signals: (name, regex, {task: log-odds boost}, human-readable reason)
SIGNALS = [
    ("code_block", re.compile(r"```|^\s{4,}\S", re.M), {"code_generation": 1.2, "debugging": 1.0, "code_review": 1.2}, "contains code"),
    ("code_syntax", re.compile(r"\b(def|class|import|return|function|const|let|var|public|private|fn|func|struct|#include|=>|console\.log|print\()\b|[{};]\s*$", re.M),
     {"code_generation": 1.0, "debugging": 0.8, "code_review": 0.9}, "code syntax"),
    ("stack_trace", re.compile(r"Traceback \(most recent call last\)|Exception|Error:|\bat [\w.$]+\(.*:\d+\)|line \d+, in|panicked at|segfault|stack trace", re.I),
     {"debugging": 2.5}, "error output / stack trace"),
    ("file_ext", re.compile(r"\b[\w/-]+\.(py|js|ts|tsx|jsx|go|rs|java|kt|rb|php|cs|cpp|c|h|swift|sql|yaml|yml|toml|json|sh)\b"),
     {"code_generation": 0.6, "debugging": 0.5, "code_review": 0.6, "agentic": 0.3}, "file names"),
    ("sql", re.compile(r"\b(select\s+.+\s+from|insert\s+into|group\s+by|left\s+join|create\s+table)\b", re.I | re.S), {"data_analysis": 2.0}, "SQL"),
    ("math", re.compile(r"\\(frac|int|sum|sqrt|lim|begin\{)|[∫∑√π∞≤≥≠]|\b\d+\s*[\^*/]\s*\d+|\b[xyn]\s*[\^=]\s*\d|\bprove\b|\btheorem\b|\blemma\b", re.I),
     {"math": 2.0}, "mathematical notation"),
    ("step_by_step", re.compile(r"step[- ]by[- ]step|think (carefully|hard)|reason (through|carefully)|chain of thought|show your work", re.I),
     {"logic": 1.0, "math": 0.6}, "asks for careful reasoning"),
    ("translate", re.compile(r"\btranslat\w*\b.{0,40}\b(into|to|from)\b|\b(in|into) (spanish|french|german|japanese|chinese|korean|italian|portuguese|russian|arabic|hindi)\b", re.I),
     {"translation": 3.0}, "translation request"),
    ("summarize", re.compile(r"\b(summari[sz]e|summary|tl;?dr|key takeaways|recap|condense)\b", re.I), {"summarization": 2.2}, "summarization request"),
    ("structured", re.compile(r"\b(json|schema|yaml|csv|extract|classify|label|categori[sz]e|sentiment)\b", re.I), {"extraction": 1.4}, "structured output"),
    ("agent", re.compile(r"\b(tool|tools|function call|browse|browser|click|navigate|terminal|shell|execute|run the|deploy|agent|automate|api call|mcp)\b", re.I),
     {"agentic": 1.3}, "tool use / actions"),
    ("creative", re.compile(r"\b(story|poem|lyrics|haiku|fiction|screenplay|roleplay|character|limerick)\b", re.I), {"creative": 2.0}, "creative writing"),
    # a few cross-language cues, since the bag-of-words model only knows English
    ("ml_summarize", re.compile(r"总结|摘要|要約|まとめ|요약|resum[eo]n?\b|résum|zusammenfass|riassun|резюм|кратко", re.I), {"summarization": 2.5}, "summarization request (non-English)"),
    ("ml_translate", re.compile(r"翻译|翻訳|번역|tradu[cz]|übersetz|tradurr|перевед", re.I), {"translation": 3.0}, "translation request (non-English)"),
    ("ml_code", re.compile(r"代码|程序|函数|コード|関数|코드|código|programa[rc]|programm|código|funci[oó]n|код|функци", re.I), {"code_generation": 1.2, "code_review": 0.4}, "code (non-English)"),
    ("ml_math", re.compile(r"证明|方程|数学|証明|数学|증명|demostr|ecuaci[oó]n|beweis|gleichung|уравнен|доказ", re.I), {"math": 2.0}, "math (non-English)"),
    ("question", re.compile(r"^\s*¿?(what|who|when|where|why|how|which|is|are|can|should|do|does|qu[eé]|c[oó]mo|cu[aá]l|pourquoi|comment|warum|wie|was)\b.{0,160}\?\s*$", re.I | re.S),
     {"conversation": 0.8}, "short question"),
]
COMPLEXITY_CUES = re.compile(r"\b(prove|derive|optimi[sz]e|architecture|design a|trade-?offs?|edge cases?|step by step|in depth|"
                             r"comprehensive|rigorous|multi-?step|refactor|end to end|production|scale|concurrency|benchmark)\b", re.I)
CONSTRAINTS = re.compile(r"^\s*([-*•]|\d+[.)])\s+|\b(must|should|ensure|make sure|do not|don't|never|always|at least|at most|exactly)\b", re.I | re.M)


def stem(w):
    for suf in ("ing", "edly", "ed", "ies", "es", "s", "ly"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def tokens(text):
    words = [stem(w.strip(".'-")) for w in WORD.findall(text.lower())]
    words = [w for w in words if w and w not in STOP and len(w) > 1]
    return words + [f"{a}_{b}" for a, b in zip(words, words[1:])]


class NaiveBayes:
    def __init__(self, examples, alpha=0.5):
        self.alpha = alpha
        self.counts, self.totals, self.vocab = {}, {}, set()
        n = sum(len(v) for v in examples.values())
        self.prior = {}
        for label, docs in examples.items():
            c = Counter()
            for d in docs:
                c.update(tokens(d))
            self.counts[label], self.totals[label] = c, sum(c.values())
            self.vocab |= set(c)
            self.prior[label] = math.log(len(docs) / n)

    def log_scores(self, toks):
        """Per-label log likelihood + prior, for tokens weighted by counts (a Counter of token -> weight)."""
        v = len(self.vocab)
        out = {}
        for label, c in self.counts.items():
            denom = math.log(self.totals[label] + self.alpha * v)
            s = self.prior[label]
            for t, w in toks.items():
                if t in self.vocab:  # unseen words carry no evidence for any label
                    s += w * (math.log(c.get(t, 0) + self.alpha) - denom)
            out[label] = s
        return out


MODEL = NaiveBayes(EXAMPLES)


def _softmax(d, temp=1.0):
    m = max(d.values())
    ex = {k: math.exp((v - m) / temp) for k, v in d.items()}
    z = sum(ex.values())
    return {k: v / z for k, v in ex.items()}


def _content_text(content):
    if isinstance(content, str):
        return content, 0
    text, images = [], 0
    for part in content or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") in ("image_url", "image", "input_image"):
            images += 1
        elif isinstance(part.get("text"), str):
            text.append(part["text"])
    return "\n".join(text), images


def gather(prompt=None, messages=None, task=None):
    """Weighted text sources: [(text, weight, source)], plus conversation facts."""
    srcs, images, tool_msgs, turns = [], 0, 0, 0
    if task:
        srcs.append((task, 2.0, "task"))
    if prompt:
        srcs.append((prompt, 1.5, "prompt"))
    user_idx = [i for i, m in enumerate(messages or []) if isinstance(m, dict) and m.get("role") == "user"]
    for i, m in enumerate(messages or []):
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        text, imgs = _content_text(m.get("content"))
        images += imgs
        if role == "tool" or m.get("tool_calls") or m.get("function_call"):
            tool_msgs += 1
        if role == "user":
            turns += 1
            w = 1.5 if user_idx and i == user_idx[-1] else 0.6
        elif role == "system":
            w = 0.8
        else:
            w = 0.3
        if text:
            srcs.append((text, w, role or "message"))
    return srcs, {"images": images, "tool_messages": tool_msgs, "user_turns": turns}


def estimate_tokens(text):
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u3040" <= ch <= "\u9fff" or "\uac00" <= ch <= "\ud7af")
    return int((len(text) - cjk) / 4 + cjk / 1.4) + 1


def detect_language(text):
    sample = text[:4000]
    letters = sum(1 for ch in sample if ch.isalpha()) or 1
    for code, rx in SCRIPTS:
        if len(rx.findall(sample)) / letters > 0.2:
            return code
    words = re.findall(r"[a-záéíóúñçàèìòùâêîôûäöüß]+", sample.lower())
    if len(words) < 3:
        return "en"
    scores = {code: sum(1 for w in words if w in sw) / len(words) for code, sw in LANGS.items()}
    en = sum(1 for w in words if w in STOP) / len(words)
    best = max(scores, key=scores.get)
    return best if scores[best] > max(en, 0.12) else "en"


def classify(prompt=None, messages=None, task=None, tools=None, response_format=None):
    """The triage result for one request (all inputs optional)."""
    srcs, conv = gather(prompt, messages, task)
    full = "\n".join(t for t, _, _ in srcs)
    weighted = Counter()
    for text, w, _ in srcs:
        for t in tokens(text[:20000]):
            weighted[t] += w
    # normalise evidence so long conversations don't drown the prior and signals
    total = sum(weighted.values())
    if total > 60:
        weighted = Counter({t: v * 60 / total for t, v in weighted.items()})
    logits = MODEL.log_scores(weighted) if weighted else {k: 0.0 for k in TASKS}
    fired = []
    for name, rx, boosts, why in SIGNALS:
        if rx.search(full):
            fired.append({"signal": name, "reason": why})
            for task_type, b in boosts.items():
                logits[task_type] += b
    if tools or conv["tool_messages"]:
        logits["agentic"] += 3.0
        fired.append({"signal": "tools", "reason": "tool definitions or tool calls in the request"})
    if response_format:
        logits["extraction"] += 1.0
        fired.append({"signal": "response_format", "reason": "structured output requested"})
    empty = not full.strip()
    probs = _softmax(logits, temp=1.0 if not empty else 50.0)
    top = sorted(probs.items(), key=lambda kv: -kv[1])
    mix = {d: 0.0 for d in DIMENSIONS}
    for task_type, p in probs.items():
        for d, w in TASKS[task_type][1].items():
            mix[d] += p * w
    z = sum(mix.values()) or 1
    mix = {d: round(v / z, 3) for d, v in mix.items()}

    n_tok = estimate_tokens(full)
    words = len(re.findall(r"\w+", full))
    cues = len(COMPLEXITY_CUES.findall(full))
    constraints = len(CONSTRAINTS.findall(full))
    hard = sum(probs.get(t, 0) for t in ("math", "logic", "debugging", "agentic", "research"))
    cscore = min(1.0, 0.15 * min(cues, 4) + 0.04 * min(constraints, 8) + 0.25 * min(words / 600, 1) + 0.35 * hard
                 + (0.1 if "code_block" in {f["signal"] for f in fired} else 0))
    complexity = "low" if cscore < 0.3 else "high" if cscore >= 0.6 else "medium"
    lang = detect_language(full) if full.strip() else "en"
    category = max(mix, key=mix.get)
    out_tokens = {"low": 300, "medium": 800, "high": 2000}[complexity]
    if top[0][0] in ("code_generation", "writing", "creative", "research"):
        out_tokens = int(out_tokens * 1.5)
    if top[0][0] in ("extraction", "translation", "conversation"):
        out_tokens = max(150, out_tokens // 2) if top[0][0] != "translation" else max(out_tokens, int(n_tok * 1.1))
    return {
        "category": category,
        "dimensions": mix,
        "task_types": [{"type": k, "label": TASKS[k][0], "p": round(v, 3)} for k, v in top[:3]],
        "confidence": round(top[0][1], 3) if not empty else 0.0,
        "complexity": complexity,
        "complexity_score": round(cscore, 2),
        "language": {"code": lang, "name": LANG_NAMES.get(lang, lang)},
        "needs": {"tools": bool(tools) or conv["tool_messages"] > 0, "vision": conv["images"] > 0,
                  "long_context": n_tok > 32000, "structured_output": bool(response_format) or "structured" in {f["signal"] for f in fired}},
        "signals": fired,
        "estimated_input_tokens": n_tok,
        "suggested_output_tokens": out_tokens,
        "conversation": {"user_turns": conv["user_turns"], "images": conv["images"], "tool_messages": conv["tool_messages"]},
        "empty": empty,
    }
