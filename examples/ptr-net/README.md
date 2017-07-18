## Pointer Networks
- Approximate Planer Traveling Salesman Problem using Pointer Networks
- reference paper: https://arxiv.org/pdf/1506.03134.pdf

## How to run
1. Download TSP dataset `tsp_5_train.zip` and `tsp_10_train.zip` from [here](https://drive.google.com/drive/folders/0B2fg8yPGn2TCMzBtS0o4Q2RJaEU)
2. Unzip `tsp_5_train.zip` and `tsp_10_train.zip`
```
$ unzip '*.zip'
```
3. Run training script
```
$ python train.py --train_file tsp5.txt --test_file tsp5_test.txt
```
## Results
- prelim results on tsp5.txt

`iteration = 4000, train loss = 0.924373149872`

`iteration = 8000, train loss = 0.726571142673`

`iteration = 12000, train loss = 0.6722458601`

`iteration = 16000, train loss = 0.648913919926`

`iteration = 20000, train loss = 0.709739685059`

`iteration = 24000, train loss = 0.64789390564`

`iteration = 28000, train loss = 0.674442708492`

- prelim results on tsp10.txt

`iteration = 4000, train loss = 2.76649451256`

`iteration = 8000, train loss = 2.62935018539`

`iteration = 12000, train loss = 2.36450719833`

`iteration = 16000, train loss = 2.15519189835`

`iteration = 20000, train loss = 2.08786010742`

`iteration = 24000, train loss = 1.92875289917`

`iteration = 28000, train loss = 1.91526651382`

`iteration = 32000, train loss = 2.06947231293`

`iteration = 36000, train loss = 1.88495159149`

`iteration = 40000, train loss = 1.89364993572`
## To-dos
- [ ] change decoder input to coordinates for teacher forcing  
- [ ] ptr-net inference code
- [ ] ptr-net convergence issue (compare to [TF implementation](https://github.com/devsisters/pointer-network-tensorflow))
- [ ] add LSTM to rnn cell selection
- [ ] script to calculate travel distance and compare with original paper
- [ ] visualize TSP  
- [ ] python/shell script to download TSP data
