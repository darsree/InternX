# backend/app/services/layer2_scorer.py

import re
import math
import numpy as np
from functools import lru_cache

import torch
from transformers import AutoTokenizer, AutoModel

MODEL_NAME = "microsoft/codebert-base"

@lru_cache(maxsize=1)
def _load_model():
    print("[Layer2] Loading CodeBERT model...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModel.from_pretrained(MODEL_NAME)
    model.eval()
    print("[Layer2] CodeBERT ready.")
    return tokenizer, model

def get_embedding(code_text: str, max_length: int = 512) -> np.ndarray:
    tokenizer, model = _load_model()
    tokens = tokenizer(
        code_text,
        return_tensors="pt",
        truncation=True,
        max_length=max_length,
        padding="max_length",
    )
    with torch.no_grad():
        output = model(**tokens)
    return output.last_hidden_state[:, 0, :].squeeze().numpy()

def _parse_diff_stats(pr_diff: str) -> dict:
    return {
        "files_changed": len(re.findall(r"^diff --git", pr_diff, re.MULTILINE)),
        "lines_added":   len(re.findall(r"^\+[^+]", pr_diff, re.MULTILINE)),
        "lines_removed": len(re.findall(r"^-[^-]", pr_diff, re.MULTILINE)),
        "hunks":         len(re.findall(r"^@@", pr_diff, re.MULTILINE)),
        "churn":         len(re.findall(r"^\+[^+]", pr_diff, re.MULTILINE))
                       + len(re.findall(r"^-[^-]", pr_diff, re.MULTILINE)),
    }

def _classify_pr_type(pr_diff: str) -> str:
    d = pr_diff.lower()
    scores = {
        "bugfix":   sum(1 for w in ["fix","bug","patch","hotfix","revert","error","crash"] if w in d),
        "feature":  sum(1 for w in ["feat","add","new","implement","create","introduce"] if w in d),
        "refactor": sum(1 for w in ["refactor","cleanup","rename","restructure","simplify","extract"] if w in d),
    }
    return max(scores, key=scores.get)

def _predict_risk_score(embedding: np.ndarray, stats: dict) -> float:
    churn_risk   = min(stats["churn"] / 500, 1.0)
    files_risk   = min(stats["files_changed"] / 10, 1.0)
    hunks_risk   = min(stats["hunks"] / 20, 1.0)
    structural   = churn_risk * 0.5 + files_risk * 0.3 + hunks_risk * 0.2
    norm = np.linalg.norm(embedding)
    embedding_risk = 0.0
    if norm > 0:
        normalised = embedding / norm
        embedding_risk = float(np.clip(
            np.linalg.norm(normalised - 1.0 / math.sqrt(len(normalised))) / 10, 0, 1
        ))
    return round(structural * 0.7 + embedding_risk * 0.3, 3)

def _risk_label(score: float) -> str:
    if score >= 0.65: return "high"
    if score >= 0.35: return "medium"
    return "low"

def _complexity_label(stats: dict) -> str:
    score = stats["files_changed"] * 2 + stats["hunks"]
    if score >= 25: return "high"
    if score >= 10: return "medium"
    return "low"

async def run_layer2(pr_diff: str) -> dict:
    if not pr_diff or len(pr_diff.strip()) < 10:
        return _stub_result("empty diff")
    try:
        stats      = _parse_diff_stats(pr_diff)
        pr_type    = _classify_pr_type(pr_diff)
        embedding  = get_embedding(pr_diff[:2000])
        risk       = _predict_risk_score(embedding, stats)
        complexity = _complexity_label(stats)
        return {
            "risk_score":  risk,
            "risk_label":  _risk_label(risk),
            "pr_type":     pr_type,
            "complexity":  complexity,
            "diff_stats":  stats,
            "display": {
                "badge":      f"Risk Score: {int(risk * 100)}% ({_risk_label(risk).title()})",
                "pr_type":    f"PR Type: {pr_type.title()}",
                "complexity": f"Review Complexity: {complexity.title()}",
            },
        }
    except Exception as e:
        print(f"[Layer2] Error: {e} — returning stub")
        return _stub_result(str(e))

def _stub_result(reason: str = "") -> dict:
    return {
        "risk_score": 0.5, "risk_label": "medium",
        "pr_type": "unknown", "complexity": "medium",
        "diff_stats": {}, "error": reason,
        "display": {
            "badge":      "Risk Score: N/A",
            "pr_type":    "PR Type: Unknown",
            "complexity": "Review Complexity: Medium",
        },
    }