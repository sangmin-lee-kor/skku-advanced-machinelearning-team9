import gradio as gr
import sys
import torch
import transformers
from peft import PeftModel
from transformers import GenerationConfig, LlamaForCausalLM, LlamaTokenizer, AutoModelForSequenceClassification
from tqdm import tqdm
import math
from datasets import load_dataset


if torch.cuda.is_available():
    device = "cuda"
else:
    device = "cpu"


def main(model_name="final_model", 
        lora_checkpoint="./mycheckpoint/checkpoint-12", 
        data_path="train_data_copy.json",
        temperature=0.1,
        top_p=0.75,
        top_k=40,
        num_beams=4,
        max_new_tokens=128
        ) :
    tokenizer = LlamaTokenizer.from_pretrained(model_name)
    if device == "cuda":
        model = LlamaForCausalLM.from_pretrained(
            model_name,
            torch_dtype=torch.float16
        )
        model = PeftModel.from_pretrained(
            model,
            lora_checkpoint,
            torch_dtype=torch.float16,
        )
    else:
        model = LlamaForCausalLM.from_pretrained(
            model_name, low_cpu_mem_usage=False
        )
        model = PeftModel.from_pretrained(
            model,
        )

    model.config.pad_token_id = tokenizer.pad_token_id = 0  # unk
    model.config.bos_token_id = 1
    model.config.eos_token_id = 2

    model.eval()
    model.to(device)
    if torch.__version__ >= "2" and sys.platform != "win32":
        model = torch.compile(model)

    torch.cuda.empty_cache()
    generation_config = GenerationConfig(
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            num_beams=num_beams,
            num_return_sequences=num_beams,
            do_sample=True
        )
    data = load_dataset("json", data_files=data_path)

    evaluation(data, model_name, tokenizer, model, generation_config)

def predict(
        instruction,
        model,
        tokenizer,
        input=None,
        temperature=0.1,
        top_p=0.75,
        top_k=40,
        num_beams=4,
        max_new_tokens=128,
        **kwargs,
    ):
    prompt = generate_prompt(instruction, input)
    inputs = tokenizer(prompt, return_tensors="pt")
    input_ids = inputs["input_ids"].to(device)
    generation_config = GenerationConfig(
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        num_beams=num_beams,
        num_return_sequences=num_beams,
        **kwargs,
    )
    with torch.no_grad():
        generation_output = model.generate(
            input_ids=input_ids,
            generation_config=generation_config,
            return_dict_in_generate=True,
            output_scores=True,
            max_new_tokens=max_new_tokens,
        )
    
    s = []
    
    for i in range(num_beams):
        temp = generation_output.sequences[i]
        s.append(tokenizer.decode(temp,skip_special_tokens=True))
    
    output = ''
    
    for cur in s:
        output += cur.split("### Response:")[1].strip() + '\n'

    return output


def generate_prompt(instruction, input=None):
    if input:
        return f"""Below is an instruction that describes a task, paired with an input that provides further context. Write a response that appropriately completes the request.  # noqa: E501

### Instruction:
{instruction}

### Input:
{input}

### Response:
"""
    else:
        return f"""Below is an instruction that describes a task. Write a response that appropriately completes the request.  # noqa: E501

### Instruction:
{instruction}

### Response:
"""
    

def evaluation(data, model_nm, tokenizer, model, generation_config) :
    hit5 = 0
    hit10 = 0
    ndcg5 = 0
    ndcg10 = 0
    total = 0
    res = []

    for i, cur in tqdm(enumerate(data['train'])):
        label = cur['output']
        inputs = generate_prompt({**cur, "output": ""})
        inputs = tokenizer(inputs, return_tensors="pt")
        input_ids = inputs['input_ids'].to('cuda:0')
        
        res = []
        with torch.no_grad():
            generation_output = model.generate(
                input_ids=input_ids,
                generation_config=generation_config,
                return_dict_in_generate=True,
                output_scores=False,#used to be True
                max_new_tokens=128,#used to be 128
            )

            num_sequences = len(generation_output.sequences)
            for j in range(num_sequences):
                temp = generation_output.sequences[j]
                cur = tokenizer.decode(temp,skip_special_tokens=True).split("### Response:")[1].strip()
                cur = cur.split("⁇")[0].strip()
                res.append(cur) 
        print(label)
        print(res)
                
        if label in res[:5]:
            hit5 += 1
            pos = res[:5].index(label)
            ndcg5 += 1.0 / (math.log(pos + 2) / math.log(2)) / 1.0
            print(res)
            print(label)

        if label in res:
            hit10 += 1
            pos = res.index(label)
            ndcg10 += 1.0 / (math.log(pos + 2) / math.log(2)) / 1.0
            print(res)
            print(label)

        total += 1

    eval_res = {
        "model_nm" : model_nm,
        "Hit@5" : hit5/total,
        "Hit@10" : hit10/total,
        "NDCG@5" : ndcg5/total,
        "NDCG@10" : ndcg10/total,
    }
    print(eval_res)

    return eval_res