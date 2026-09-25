# Predictions

Two scripts record what `cairn predict` says of device code. The kernels are compiled and read by ptxas, and none of them runs. Both scripts need `nvcc` for the ptxas reports.

`cooperative.py` records what `cairn predict`, `cairn explain` and `cairn tune` say of the cooperative and tensor-core examples, beside what ptxas reports of the same kernels for sm_120. It wrote `evidence/v1_0/predict_cooperative/`.

`cards.py` records `cairn predict --card all --inspect` over two sets of kernels: the suite's kernels with their views placed on the device, and the cooperative and tensor-core examples. It gives a row for each packaged device card, priced from NVIDIA's published specification and compiled for the card's own target. It wrote `evidence/v1_1/catalog/`.

```sh
python3 bench/predict/cooperative.py --out results/predict_cooperative
python3 bench/predict/cards.py --out results/catalog
```
