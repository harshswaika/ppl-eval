I evaluated the perplexity on a number of different models like google/gemma-2b, TinyLlama/TinyLlama_v1.1, meta-llama/Llama-2-7b-hf and microsoft/Phi-3-mini-4k-instruct. Was getting similar numbers for the llama2 model as reported by some users on hf, but had surprising results for the other models which I am very curious to discuss about further. 

I used the standard perplexity function from the evaluate library as well as wrote down a couple of custom functions in which I experimented evaluating perplexity with varying strides of the context window.

I read up on some quantization techniques like GPTQ and a more recent one AQML which have show better performance than bnb in some cases but at the cost of increased time to quantize the models.

I tried running the TensorRT LLM on a remote GPU but was unable to proceed due to lack of admin priviledges.
