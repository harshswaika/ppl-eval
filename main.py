import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from datasets import load_dataset
import evaluate
from tqdm import tqdm
import numpy as np
import os
from torch.nn import CrossEntropyLoss
from evaluate import logging
import json

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

bnb_config = BitsAndBytesConfig(
load_in_4bit=True,
bnb_4bit_use_double_quant=True,
bnb_4bit_quant_type="nf4",
bnb_4bit_compute_dtype=torch.bfloat16
)

bnb_quantization_config = BitsAndBytesConfig(load_in_8bit=True, llm_int8_threshold = 6)

model_name = "google/gemma-2b"  

tokenizer = AutoTokenizer.from_pretrained(model_name)
# model = AutoModelForCausalLM.from_pretrained(model_name).to(device)
model = AutoModelForCausalLM.from_pretrained(model_name, quantization_config=bnb_quantization_config)
# model = AutoModelForCausalLM.from_pretrained(model_name, quantization_config=bnb_config)

# tokenizer = AutoTokenizer.from_pretrained("TinyLlama/TinyLlama_v1.1")
# model = AutoModelForCausalLM.from_pretrained("TinyLlama/TinyLlama_v1.1").to(device)
# model = AutoModelForCausalLM.from_pretrained("TinyLlama/TinyLlama_v1.1", load_in_8bit = True)
# model = AutoModelForCausalLM.from_pretrained("TinyLlama/TinyLlama_v1.1", quantization_config=bnb_config)

# tokenizer = AutoTokenizer.from_pretrained("microsoft/Phi-3-mini-4k-instruct", trust_remote_code=True)
# model = AutoModelForCausalLM.from_pretrained("microsoft/Phi-3-mini-4k-instruct", trust_remote_code=True, attn_implementation="flash_attention_2").to(device)

# access_token = "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"
# tokenizer = AutoTokenizer.from_pretrained("meta-llama/Llama-2-7b-hf", token=access_token)
# model = AutoModelForCausalLM.from_pretrained("meta-llama/Llama-2-7b-hf", token=access_token).to(device)
# model = AutoModelForCausalLM.from_pretrained("meta-llama/Llama-2-7b-hf", token=access_token, load_in_8bit = True)
# model = AutoModelForCausalLM.from_pretrained("meta-llama/Llama-2-7b-hf", token=access_token, quantization_config=bnb_config)

def compute(predictions, model, tokenizer, batch_size: int = 8, add_start_token: bool = True, device=None, max_length=None):

    if device is not None:
        assert device in ["gpu", "cpu", "cuda"], "device should be either gpu or cpu."
        if device == "gpu":
            device = "cuda"
    else:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    model = model#.to(device)

    tokenizer = tokenizer

    # if batch_size > 1 (which generally leads to padding being required), and
    # if there is not an already assigned pad_token, assign an existing
    # special token to also be the padding token
    if tokenizer.pad_token is None and batch_size > 1:
        existing_special_tokens = list(tokenizer.special_tokens_map_extended.values())
        # check that the model already has at least one special token defined
        assert (
            len(existing_special_tokens) > 0
        ), "If batch_size > 1, model must have at least one special token to use for padding. Please use a different model or set batch_size=1."
        # assign one of the special tokens to also be the pad token
        tokenizer.add_special_tokens({"pad_token": existing_special_tokens[0]})

    if add_start_token and max_length:
        assert (
            tokenizer.bos_token is not None
        ), "Input model must already have a BOS token if using add_start_token=True. Please use a different model, or set add_start_token=False"
        max_tokenized_len = max_length - 1
    else:
        max_tokenized_len = max_length

    encodings = tokenizer(
        predictions,
        add_special_tokens=False,
        padding=True,
        truncation=True if max_tokenized_len else False,
        max_length=max_tokenized_len,
        return_tensors="pt",
        return_attention_mask=True,
    ).to(device)

    encoded_texts = encodings["input_ids"]
    attn_masks = encodings["attention_mask"]

    if add_start_token:
        assert torch.all(torch.ge(attn_masks.sum(1), 1)), "Each input text must be at least one token long."
    else:
        assert torch.all(
            torch.ge(attn_masks.sum(1), 2)
        ), "When add_start_token=False, each input text must be at least two tokens long. Run with add_start_token=True if inputting strings of only one token, and remove all empty input strings."

    ppls = []
    loss_fct = CrossEntropyLoss(reduction="none")

    for start_index in logging.tqdm(range(0, len(encoded_texts), batch_size)):
        end_index = min(start_index + batch_size, len(encoded_texts))
        encoded_batch = encoded_texts[start_index:end_index]
        attn_mask = attn_masks[start_index:end_index]

        if add_start_token:
            bos_tokens_tensor = torch.tensor([[tokenizer.bos_token_id]] * encoded_batch.size(dim=0)).to(device)
            encoded_batch = torch.cat([bos_tokens_tensor, encoded_batch], dim=1)
            attn_mask = torch.cat(
                [torch.ones(bos_tokens_tensor.size(), dtype=torch.int64).to(device), attn_mask], dim=1
            )

        labels = encoded_batch

        with torch.no_grad():
            out_logits = model(encoded_batch, attention_mask=attn_mask).logits

        shift_logits = out_logits[..., :-1, :].contiguous()
        shift_labels = labels[..., 1:].contiguous()
        shift_attention_mask_batch = attn_mask[..., 1:].contiguous()

        perplexity_batch = torch.exp(
            (loss_fct(shift_logits.transpose(1, 2), shift_labels) * shift_attention_mask_batch).sum(1)
            / shift_attention_mask_batch.sum(1)
        )

        ppls += perplexity_batch.tolist()

    return {"mean_perplexity": np.mean(ppls), "perplexities": ppls}


#Perplexity calculation by varying stride length

# test = load_dataset('wikitext', 'wikitext-2-raw-v1', split='test')
# encodings = tokenizer('\n\n'.join(test['text']), return_tensors='pt')

# max_length = model.config.max_position_embeddings
# stride = 512

# lls = []
# for i in tqdm(range(0, encodings.input_ids.size(1), stride)):
#     begin_loc = max(i + stride - max_length, 0)
#     end_loc = min(i + stride, encodings.input_ids.size(1))
#     trg_len = end_loc - i    # may be different from stride on last loop
#     input_ids = encodings.input_ids[:,begin_loc:end_loc].to(device)
#     target_ids = input_ids.clone()
#     target_ids[:,:-trg_len] = -100

#     with torch.no_grad():
#         outputs = model(input_ids, labels=target_ids)
#         log_likelihood = outputs[0] * trg_len

#     lls.append(log_likelihood)

# ppl = torch.exp(torch.stack(lls).sum() / end_loc)


input_texts = load_dataset("wikitext", "wikitext-2-raw-v1", split="test")["text"]#[:10] 
input_texts = [s for s in input_texts if s!='']
results = compute(model = model, tokenizer = tokenizer, batch_size=8, predictions=input_texts)

with open("/home/test/harsh/llm/results/gemma_8.json", "w") as outfile:
    json.dump(results, outfile)
    
print(list(results.keys()))

print("Mean Perplexity = ", round(results["mean_perplexity"], 4))