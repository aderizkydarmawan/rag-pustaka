"""Reranker Qwen3-Reranker-4B (4-bit, GPU): skor relevansi query–potongan = P("yes")."""
from __future__ import annotations

from . import MODELS_DIR
MODEL = str(MODELS_DIR / "Qwen__Qwen3-Reranker-4B")
RERANK_NAME = "Qwen3-Reranker-4B@22e6836 nf4/bf16 P(yes)"
INSTRUCT = "Given a research question, judge whether the passage from a book or paper helps answer it"
MAX_LEN = 768
BATCH = 4
PREFIX = ("<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the "
          "Instruct provided. Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n<|im_start|>user\n")
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
_m: dict = {}


def _load():
    if "m" not in _m:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        tok = AutoTokenizer.from_pretrained(MODEL, padding_side="left")
        model = AutoModelForCausalLM.from_pretrained(MODEL, quantization_config=BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16),
            dtype=torch.bfloat16, device_map="cuda").eval()
        _m["m"] = (tok, model, tok.convert_tokens_to_ids("yes"), tok.convert_tokens_to_ids("no"))
    return _m["m"]


def score(query: str, docs: list[str]) -> list[float]:
    import torch
    tok, model, yes, no = _load()
    pre = tok(PREFIX, add_special_tokens=False)["input_ids"]
    suf = tok(SUFFIX, add_special_tokens=False)["input_ids"]
    out: list[float] = []
    for i in range(0, len(docs), BATCH):
        ids = []
        for d in docs[i:i + BATCH]:
            body = tok(f"<Instruct>: {INSTRUCT}\n<Query>: {query}\n<Document>: {d}", add_special_tokens=False,
                       truncation=True, max_length=MAX_LEN - len(pre) - len(suf))["input_ids"]
            ids.append(pre + body + suf)
        enc = tok.pad({"input_ids": ids}, padding=True, return_tensors="pt").to("cuda")
        with torch.inference_mode():
            logits = model(**enc, logits_to_keep=1).logits[:, -1, :]
            p = torch.softmax(torch.stack([logits[:, no], logits[:, yes]], 1).float(), 1)[:, 1]
        out += p.cpu().tolist()
    return out
