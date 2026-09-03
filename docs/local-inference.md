# Local inference footprint — manifest, not a deletion plan

Generated 2026-09-03 by `ops` Phase 6 step I. **Nothing here has been
deleted.** This exists so that reclaiming any of it later is a decision rather
than a risk: every store below is reproducible from the names recorded here.

## Status: wired, but dormant

- No inference process is running (`ollama`, `llama-server`, `mlx_lm`, `vllm`: none).
- `~/dev/agents/orchestrator/orchestrator.js` imports `LocalModelConnector`, which
  expects Ollama at `127.0.0.1:11434`. That daemon is not running.
- `orchestrator/state/router-stats.json` is 160 bytes and records no local arms,
  so the Thompson-sampling router has never accumulated evidence for them.
- Every other reference is in Codex shell snapshots and rollout logs, which are
  session noise, not workflows.

## Stores

| store | size | reproducible from | last touched |
|---|---|---|---|
| `~/.ollama` |  52G | 11 manifests, all `ollama pull <name>` | Oct 21 2025 |
| `~/.cache/huggingface` |  57G | 16 named repos, refetchable | 2026-08-28 |
| `~/Models/frontier-local` |  26G | qwen3.5-9b qwen3.6-27b  | 2026-04-29 |
| `~/Desktop/jr/jryear/ucie/eigen/models` |  22G | duplicate of the HF cache | 2025-08-29 |

## Ollama models (`ollama pull` restores each)
```
bge-m3:latest
chat:latest
code:latest
deepseek-r1:8b-llama-distill-q4_K_M
embed:latest
llama3.1:8b
mixtral:8x7b
qwen2.5-coder:7b
qwen2.5:14b
qwen2.5:7b
reason:latest
```

## Hugging Face repos (`huggingface-cli download` restores each)
```
google/embeddinggemma-300m
hexgrad/Kokoro-82M
microsoft/Phi-3.5-mini-instruct
mlx-community/embeddinggemma-300m-bf16
mlx-community/Qwen2-VL-2B-Instruct-4bit
mlx-community/Qwen2-VL-7B-bf16
mlx-community/Qwen2-VL-7B-Instruct-4bit
mlx-community/stablelm-2-1_6b-4bit
Qwen/Qwen2.5-Coder-1.5b-Instruct
Qwen/Qwen2.5-Math-1.5B-Instruct
rednote-hilab/dots.ocr
sentence-transformers/all-MiniLM-L6-v2
sentence-transformers/all-mpnet-base-v2
stabilityai/stablelm-2-1_6b-chat
Systran/faster-whisper-base
TinyLlama/TinyLlama-1.1B-Chat-v1.0
```

## Recommendation

There is no pressure: the machine has ~310 GB free. So this is a question about
intent, not space, and it splits cleanly in two.

**Reclaimable with no judgement call —** `ucie/eigen/models` duplicates the
Hugging Face cache byte for byte. It is 22 GB storing a second copy of files
already on this disk. That is not a local-inference decision at all.

**Everything else depends on one question:** do you intend to revive the
orchestrator's local arm? If yes, keep `~/.ollama` and the `embeddinggemma-300m`
repos — those are what the router would actually call. If no, all four stores are
refetchable from the names above, and `~/Models/frontier-local` (26 GB, untouched
since April) is the clearest candidate.

Deleting weights while the answer is "maybe" is the wrong trade: 157 GB is
recoverable in an afternoon of downloads, but only if this list survives.
That is what this file is for.
