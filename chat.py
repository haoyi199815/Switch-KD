"""
chat.py
---------------
Single-round and multi-round chat with a MindVL model.

Usage
-----
# Describe an image
python chat.py --model HaoyiSun/Switch-KD-Qwen2.5-CLIP-1.8B --image view.jpg --question "Please describe this picture."

# Interactive multi-round chat
python chat.py --model HaoyiSun/Switch-KD-Qwen2.5-CLIP-1.8B --image view.jpg --interactive
"""

import argparse

import torch
from PIL import Image
from transformers import (
    AutoConfig,
    AutoModelForCausalLM,
    AutoProcessor,
    GenerationConfig,
)


# --------------------------------------------------------------------------- #
# Prompt helpers
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = "You are a helpful assistant."


def build_prompt(messages: list) -> str:
    """Build a ChatML prompt string from a list of {role, content} dicts."""
    text = f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
    for msg in messages:
        text += f"<|im_start|>{msg['role']}\n{msg['content']}<|im_end|>\n"
    text += "<|im_start|>assistant\n"
    return text


# --------------------------------------------------------------------------- #
# Inference
# --------------------------------------------------------------------------- #

@torch.no_grad()
def chat(model, processor, image, question: str, history: list,
         max_new_tokens: int = 512) -> str:
    """
    Args:
        history: list of previous {role, content} dicts (without the current question).
                 The image token is only inserted for the FIRST user turn.
    Returns:
        Assistant response string.
    """
    # Insert image token only in the first user turn
    if not history:
        content = question  # processor auto-prepends [[[IMAGE:modality]]]\n
    else:
        content = question  # no image token for follow-up turns

    messages = history + [{"role": "user", "content": content}]
    prompt = build_prompt(messages)

    # Processor handles image token insertion + expand2square + CLIP preprocess
    inputs = processor(
        text=prompt,
        images=image if not history else None,   # only pass image on first turn
        return_tensors="pt",
    )
    inputs = {k: v.to(model.device) for k, v in inputs.items()}

    # Cast pixel_values to model dtype
    if "pixel_values" in inputs:
        inputs["pixel_values"] = inputs["pixel_values"].to(
            next(model.visual_encoder.parameters()).dtype
        )

    gen_config = GenerationConfig(
        max_new_tokens=max_new_tokens,
        do_sample=False,
        repetition_penalty=1.05,
        eos_token_id=processor.tokenizer.eos_token_id,
        pad_token_id=processor.tokenizer.pad_token_id
        or processor.tokenizer.eos_token_id,
    )

    output_ids = model.generate(**inputs, generation_config=gen_config)

    # Decode only newly generated tokens
    response = processor.tokenizer.decode(
        output_ids[0], skip_special_tokens=True
    ).strip()

    # Strip echoed prompt (Qwen2 sometimes echoes the assistant prefix)
    if "assistant\n" in response:
        response = response.split("assistant\n")[-1].strip()

    return response


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model",       default="MindVL_hf",
                        help="Path to the saved MindVL checkpoint.")
    parser.add_argument("--image",       default=None,
                        help="Path to an image file.")
    parser.add_argument("--question",    default="Please describe this picture",
                        help="Question / instruction for the model.")
    parser.add_argument("--interactive", action="store_true",
                        help="Start multi-round interactive chat.")
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--torch-dtype", default="fp16",
                        choices=["fp16", "bf16", "fp32"])
    return parser.parse_args()


def main():
    args = parse_args()

    dtype_map = {"fp16": torch.float16, "bf16": torch.bfloat16, "fp32": torch.float32}
    dtype = dtype_map[args.torch_dtype]

    # ---- Load model -------------------------------------------------------
    print(f"Loading model from {args.model} ...")
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        trust_remote_code=True,
        torch_dtype=dtype,
        device_map="cuda",
    )
    model.eval()
    print(f"Model loaded on {next(model.parameters()).device}")

    # ---- Load processor ---------------------------------------------------
    processor = AutoProcessor.from_pretrained(args.model, trust_remote_code=True)

    # ---- Load image -------------------------------------------------------
    image = None
    if args.image:
        image = Image.open(args.image).convert("RGB")
        print(f"Image loaded: {args.image}  size={image.size}")

    # ---- Single-round or interactive chat --------------------------------
    if args.interactive:
        history = []
        print("\nMindVL interactive chat  (type 'exit' to quit)\n")
        while True:
            try:
                question = input("User: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if question.lower() in ("exit", "quit", "q"):
                break
            if not question:
                continue

            response = chat(model, processor, image, question, history,
                            max_new_tokens=args.max_new_tokens)
            print(f"Assistant: {response}\n")

            # Append to history (image only shown in first turn)
            history.append({"role": "user",      "content": question})
            history.append({"role": "assistant",  "content": response})
    else:
        print(f"\nQuestion: {args.question}")
        response = chat(model, processor, image, args.question, [],
                        max_new_tokens=args.max_new_tokens)
        print(f"Answer:   {response}")


if __name__ == "__main__":
    main()
