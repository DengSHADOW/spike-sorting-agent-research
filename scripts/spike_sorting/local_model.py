"""Local text-only model for the spike curation pilot's multiple-choice policy."""
from __future__ import annotations

import json
from pathlib import Path
import select
from string import ascii_uppercase
import subprocess
import sys


class LocalModel:
    def __init__(self, python: Path, checkpoint: Path):
        self.command = [str(python), str(Path(__file__).resolve()), str(checkpoint.resolve())]

    def __enter__(self):
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        text=True, encoding="utf-8")
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.process.stdin.close()
        self.process.stdout.close()
        if exc_type is not None and self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()

    def select(self, prompt, choices):
        self.process.stdin.write(json.dumps(dict(prompt=prompt, choices=choices)) + "\n")
        self.process.stdin.flush()
        if not select.select([self.process.stdout], [], [], 180)[0]:
            raise TimeoutError("Local model did not return a choice within 180 seconds")
        response = self.process.stdout.readline()
        if not response:
            raise RuntimeError("Local model worker exited without a choice. See its stderr.")
        choice = json.loads(response)["choice"]
        return choices.index(choice)

    def choose(self, menu, mode, remaining, last_review, store):
        letters = ascii_uppercase[:len(menu)]
        choices = "\n".join(f"{letter}. {item.name} ({item.evidence})" for letter, item in zip(letters, menu))
        context = f"Latest review: {last_review or 'none'}\n" if store else ""
        index = self.select(
            "Curate extracellular spikes by choosing one next operation. Preserve plausible single units. "
            "Split heterogeneous clusters only with evidence. Merge only strong matches with little new ISI "
            "contamination. Small size alone is weak evidence for discarding. FINISH when no valuable edit remains. "
            "KEEP records that a cluster looks acceptable.\n"
            f"{context}{choices}\nChoose one letter for the next action.", letters,
        )
        item = menu[index]
        required = mode == "always" or (store and item.action == "DISCARD")
        if not remaining or item.action == "FINISH" or (not required and mode != "selective"):
            return index, False, "APPROVE"
        policy = store.profile.prompt("when_to_ask")
        prediction_choices = ("APPROVE", "REJECT", "ABSTAIN")
        query_choices = "\n".join(f"{letter}. Ask, expecting {label}"
                                  for letter, label in zip("BCD", prediction_choices))
        decision = self.select(
            f"{policy}\n{context}Proposed operation: {item.name}. Evidence: {item.evidence}.\n"
            f"{remaining} questions remain.\nA. Proceed without asking\n{query_choices}\n"
            "Choose one letter. APPROVE means the proposed operation is supported, REJECT means another "
            "decision is supported, and ABSTAIN means the expert records cannot resolve this state.", "BCD" if required else "ABCD",
        )
        if required:
            return index, False, prediction_choices[decision]
        return index, decision != 0, prediction_choices[max(0, decision - 1)]


def serve(checkpoint):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.set_num_threads(4)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    dtype = torch.float16 if device == "mps" else torch.float32
    tokenizer = AutoTokenizer.from_pretrained(checkpoint, local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(checkpoint, local_files_only=True, dtype=dtype)
    model = model.eval().to(device)
    for line in sys.stdin:
        request = json.loads(line)
        tokens = [tokenizer.encode(letter, add_special_tokens=False) for letter in request["choices"]]
        if any(len(token) != 1 for token in tokens):
            raise ValueError("Checkpoint tokenizer must encode option letters as single tokens")
        prompt = tokenizer.apply_chat_template([{"role": "user", "content": request["prompt"]}],
                                                tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(prompt, add_special_tokens=False, return_tensors="pt").to(device)
        with torch.inference_mode():
            logits = model(**inputs, use_cache=False, logits_to_keep=1).logits[0, -1]
        chosen = logits[[token[0] for token in tokens]].argmax().item()
        print(json.dumps({"choice": request["choices"][chosen]}), flush=True)


if __name__ == "__main__":
    serve(sys.argv[1])
