# Cooperative predictions

`cooperative.py` records what `cairn predict`, `cairn explain` and `cairn tune` say of the cooperative and tensor-core examples, beside what ptxas reports of the same kernels for sm_120. It wrote `evidence/v1_0/predict_cooperative/`. It needs `nvcc` for the ptxas reports; kernels are compiled and read, and none runs.

```sh
python3 bench/predict/cooperative.py --out results/predict_cooperative
```
