# Performance report

Compared checkpoint 111d2c2 with the hardening branch using the same harness and fresh SQLite files. Each seed contained 5,000 assets, 500,000 readings and 2,000 work orders. The host was AMD Ryzen AI 7 350 with 16 logical CPUs and 7.3 GiB RAM. Both runs used Ubuntu 26.04.1 under WSL2, CPython 3.14.4 and SQLite 3.46.1.

The harness uses FastAPI TestClient in one process. The sequential run took 30 measurements after three warmups per endpoint. Concurrent groups used 30, 150 and 300 samples per endpoint for 10, 50 and 100 client threads. They tested each endpoint in isolation, with the write workload last at each level. Other local verification overlapped portions of the runs. The results provide a comparison on this host with that limitation.

Reproduce with a fresh database per checkout.

    python scripts/load_test.py --db /tmp/logentra-fresh.db --out /tmp/logentra-results.json --concurrency 10,50,100

Run the same harness from a detached checkpoint worktree for v0.5.

## Sequential reads

Latency columns show P50 / P95 / P99 in milliseconds.

| Endpoint | v0.5 | Candidate |
| --- | ---: | ---: |
| Equipment page | 6.50 / 7.41 / 8.20 | 6.95 / 8.40 / 9.52 |
| Equipment search | 13.56 / 15.27 / 15.77 | 14.09 / 14.72 / 15.59 |
| Readings query | 6.45 / 7.34 / 7.62 | 6.73 / 7.47 / 7.70 |
| Out-of-range events | 8.98 / 13.78 / 25.91 | 10.41 / 12.81 / 26.72 |
| Work orders page | 9.49 / 10.23 / 10.28 | 10.42 / 12.01 / 12.53 |

## Concurrent reads and writes

Latency is P50 / P95 / P99 in ms. Throughput is requests per second. Every concurrent request succeeded under both versions.

| Users | Endpoint | v0.5 latency | Candidate latency | v0.5 req/s | Candidate req/s |
| ---: | --- | ---: | ---: | ---: | ---: |
| 10 | Equipment page | 604.56 / 660.31 / 675.70 | 688.74 / 783.13 / 797.69 | 17.09 | 14.28 |
| 10 | Equipment search | 182.05 / 288.82 / 301.65 | 238.32 / 343.15 / 346.99 | 52.34 | 40.95 |
| 10 | Readings query | 647.34 / 677.69 / 683.08 | 728.72 / 773.16 / 785.03 | 15.32 | 13.57 |
| 10 | Out-of-range events | 413.61 / 1330.34 / 1341.38 | 224.34 / 1598.45 / 1624.45 | 17.74 | 17.85 |
| 10 | Work orders page | 916.60 / 989.93 / 1000.60 | 1075.71 / 1155.28 / 1181.46 | 10.81 | 9.26 |
| 10 | Reading POST | 83.84 / 240.48 / 366.10 | 87.29 / 323.47 / 413.62 | 73.29 | 67.23 |
| 50 | Equipment page | 2971.86 / 4810.70 / 5811.57 | 3380.54 / 5467.57 / 6119.32 | 15.37 | 13.56 |
| 50 | Equipment search | 927.16 / 1230.20 / 1536.60 | 1227.56 / 1877.73 / 2094.08 | 48.52 | 36.40 |
| 50 | Readings query | 3905.19 / 6389.30 / 8455.09 | 4277.03 / 6708.89 / 7144.36 | 11.63 | 10.71 |
| 50 | Out-of-range events | 2646.92 / 4055.27 / 5040.56 | 2735.16 / 4231.95 / 4379.91 | 17.09 | 16.95 |
| 50 | Work orders page | 4391.93 / 7185.14 / 7277.32 | 4495.56 / 7296.01 / 7402.46 | 10.29 | 10.17 |
| 50 | Reading POST | 448.33 / 1121.12 / 1550.58 | 431.21 / 1033.81 / 1608.79 | 83.82 | 77.92 |
| 100 | Equipment page | 6423.24 / 8330.41 / 9145.54 | 5974.59 / 7785.10 / 9497.50 | 14.55 | 15.21 |
| 100 | Equipment search | 2218.14 / 2755.87 / 3015.65 | 2120.04 / 2620.27 / 2917.59 | 42.87 | 44.18 |
| 100 | Readings query | 12679.83 / 16277.73 / 18131.66 | 13329.45 / 16873.70 / 18366.62 | 7.32 | 7.05 |
| 100 | Out-of-range events | 5451.13 / 6142.86 / 7610.23 | 5428.56 / 5971.86 / 7187.48 | 18.12 | 17.75 |
| 100 | Work orders page | 10137.40 / 12960.48 / 14176.92 | 9094.92 / 11729.84 / 13122.99 | 9.37 | 10.19 |
| 100 | Reading POST | 907.48 / 1616.38 / 2092.35 | 923.29 / 1796.47 / 2791.99 | 86.43 | 86.37 |

## Resources and integration checks

| Users | v0.5 process CPU range | Candidate process CPU range | v0.5 peak RSS MiB | Candidate peak RSS MiB |
| ---: | ---: | ---: | ---: | ---: |
| 10 | 116–512% | 116–527% | 345.9 | 388.7 |
| 50 | 136–976% | 119–966% | 553.3 | 553.6 |
| 100 | 135–1037% | 132–1002% | 685.6 | 674.9 |

CPU is the process average relative to one logical core. RSS is the process high-water mark, including the harness. The raw SQLite time-series query used ix_readings_eq_param_ts in both runs, with P95 of 0.097 ms on v0.5 and 0.110 ms on the candidate.

The reading POST rows measure single-reading ingestion rate and concurrent write latency. A local webhook receiver returned HTTP 503 once and 204 on retry; it received the same delivery ID each time. Automated checks also verified transaction rollback and dead-letter behavior. MQTT ingested a reading before and after a live broker restart. Sustained webhook throughput and full-scale PostgreSQL and external HTTP measurements remain open.

All sequential interactive endpoints met the provisional 300 ms P95 target. Every candidate concurrent interactive scenario exceeded it. The 10-user out-of-range P95 rose from 1,330.34 to 1,598.45 ms. The 100-user readings query reached 16,873.70 ms on the candidate. These results require profiling with real HTTP and a deployed database before enterprise qualification.

## Final qualification comparison

The checkpoints used the same in-process HTTP harness, CPython 3.14.4 and fresh synthetic databases. Each run seeded 5,000 assets, 500,000 readings and 2,000 work orders. Hardware and benchmark conventions are unchanged from the earlier report. The SQLite candidate run preceded the final PostgreSQL metadata race fix. Some tests and benchmark processes overlapped, so latency differences smaller than ordinary run-to-run variation should be interpreted cautiously.

The PostgreSQL harness seeds a dedicated database specified by LOGENTRA_BENCH_POSTGRES with the --postgres flag. It runs ANALYZE and records EXPLAIN (ANALYZE, BUFFERS). Supply a new database for each run and use the same harness in the checkpoint worktree.

### SQLite 3.46.1

| Users | Endpoint | Checkpoint P50 / P95 / P99 ms | Qualified P50 / P95 / P99 ms | Req/s before / after | Errors before / after |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | Equipment page | 6.80 / 8.07 / 8.21 | 6.46 / 7.09 / 8.11 | — | 0 / 0 |
| 1 | Equipment search | 14.98 / 16.01 / 16.56 | 13.81 / 14.47 / 14.59 | — | 0 / 0 |
| 1 | Readings query | 7.61 / 9.22 / 9.59 | 6.68 / 7.69 / 8.84 | — | 0 / 0 |
| 1 | Out-of-range events | 10.79 / 13.58 / 28.26 | 6.08 / 7.94 / 23.88 | — | 0 / 0 |
| 1 | Work orders | 10.93 / 11.59 / 11.65 | 6.86 / 7.90 / 8.23 | — | 0 / 0 |
| 10 | Equipment page | 692.63 / 782.45 / 786.08 | 667.77 / 701.99 / 710.31 | 15.38 / 14.77 | 0 / 0 |
| 10 | Equipment search | 243.23 / 331.09 / 341.98 | 230.91 / 368.82 / 372.96 | 43.34 / 41.78 | 0 / 0 |
| 10 | Readings query | 723.15 / 767.47 / 794.35 | 699.83 / 736.20 / 747.37 | 13.58 / 14.07 | 0 / 0 |
| 10 | Out-of-range events | 473.08 / 1820.37 / 1853.85 | 550.03 / 598.90 / 602.66 | 15.94 / 17.89 | 0 / 0 |
| 10 | Work orders | 1002.67 / 1062.89 / 1072.45 | 800.12 / 858.51 / 868.89 | 10.19 / 12.52 | 0 / 0 |
| 10 | Reading POST | 92.09 / 215.09 / 264.64 | 61.60 / 304.38 / 385.59 | 85.30 / 70.95 | 0 / 0 |
| 50 | Equipment page | 3237.30 / 5316.58 / 5903.17 | 3136.91 / 4624.26 / 5118.41 | 14.00 / 14.58 | 0 / 0 |
| 50 | Equipment search | 1088.00 / 1603.00 / 1802.56 | 1257.89 / 1786.04 / 2102.80 | 41.73 / 35.55 | 0 / 0 |
| 50 | Readings query | 4585.83 / 7426.44 / 7854.41 | 4510.65 / 6985.95 / 8195.64 | 9.86 / 10.13 | 0 / 0 |
| 50 | Out-of-range events | 2628.06 / 4246.96 / 4479.39 | 2615.43 / 3658.25 / 5198.87 | 18.81 / 17.28 | 0 / 0 |
| 50 | Work orders | 4504.61 / 7384.14 / 7505.84 | 3620.21 / 5921.56 / 7162.16 | 10.03 / 12.60 | 0 / 0 |
| 50 | Reading POST | 423.81 / 1097.43 / 1530.74 | 441.60 / 1036.21 / 1568.40 | 81.90 / 85.47 | 0 / 0 |
| 100 | Equipment page | 7772.91 / 10004.88 / 10999.45 | 6370.42 / 8301.89 / 9168.79 | 12.33 / 14.47 | 0 / 0 |
| 100 | Equipment search | 3057.50 / 3413.14 / 4089.34 | 2597.06 / 3327.96 / 3665.62 | 31.35 / 36.34 | 0 / 0 |
| 100 | Readings query | 15678.97 / 20562.19 / 22486.07 | 13000.28 / 16805.38 / 18864.45 | 5.97 / 6.98 | 0 / 0 |
| 100 | Out-of-range events | 6684.06 / 8151.98 / 9010.58 | 6634.30 / 8463.08 / 9271.85 | 14.72 / 14.06 | 0 / 0 |
| 100 | Work orders | 11144.06 / 14593.08 / 16029.02 | 9556.16 / 11911.45 / 13396.33 | 8.34 / 10.10 | 0 / 0 |
| 100 | Reading POST | 977.39 / 1928.50 / 2608.68 | 1258.62 / 2131.49 / 2509.45 | 75.34 / 64.38 | 0 / 0 |

| Users | CPU before / after | Peak RSS MiB before / after |
| ---: | ---: | ---: |
| 10 | 135–477% / 106–502% | 390.1 / 148.0 |
| 50 | 121–927% / 128–944% | 566.0 / 187.4 |
| 100 | 125–873% / 130–1002% | 640.0 / 209.5 |

The indexed raw readings query measured P95 0.142 / 0.120 ms.

### PostgreSQL 18.6

| Users | Endpoint | Checkpoint P50 / P95 / P99 ms | Qualified P50 / P95 / P99 ms | Req/s before / after | Errors before / after |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | Equipment page | 12.18 / 13.50 / 13.79 | 13.05 / 14.48 / 14.73 | — | 0 / 0 |
| 1 | Equipment search | 19.47 / 21.12 / 21.73 | 19.66 / 22.24 / 23.04 | — | 0 / 0 |
| 1 | Readings query | 11.11 / 12.23 / 20.90 | 12.18 / 15.91 / 23.73 | — | 0 / 0 |
| 1 | Out-of-range events | 13.82 / 15.53 / 16.02 | 10.78 / 12.70 / 13.16 | — | 0 / 0 |
| 1 | Work orders | 30.16 / 31.64 / 32.59 | 11.33 / 12.78 / 54.59 | — | 0 / 0 |
| 10 | Equipment page | 133.72 / 158.65 / 165.28 | 133.28 / 145.92 / 148.48 | 69.30 / 71.32 | 0 / 0 |
| 10 | Equipment search | 125.23 / 152.71 / 163.84 | 115.99 / 143.98 / 148.71 | 73.37 / 80.21 | 0 / 0 |
| 10 | Readings query | 168.49 / 204.77 / 214.20 | 131.69 / 144.46 / 145.96 | 55.42 / 74.07 | 0 / 0 |
| 10 | Out-of-range events | 168.79 / 205.34 / 210.21 | 124.26 / 160.41 / 164.18 | 54.99 / 74.17 | 0 / 0 |
| 10 | Work orders | 483.66 / 557.59 / 589.92 | 114.90 / 123.25 / 124.48 | 19.91 / 84.07 | 0 / 0 |
| 10 | Reading POST | 148.70 / 194.31 / 197.46 | 159.74 / 180.09 / 195.26 | 60.11 / 60.18 | 6 / 0 |
| 50 | Equipment page | 747.30 / 1230.67 / 1736.20 | 610.31 / 920.18 / 1098.55 | 54.75 / 72.18 | 0 / 0 |
| 50 | Equipment search | 580.01 / 868.18 / 1232.18 | 565.45 / 933.68 / 1206.46 | 72.23 / 77.71 | 0 / 0 |
| 50 | Readings query | 675.37 / 1010.01 / 1249.44 | 671.29 / 966.74 / 1144.85 | 61.06 / 66.77 | 0 / 0 |
| 50 | Out-of-range events | 568.78 / 908.54 / 1098.11 | 534.53 / 788.25 / 896.84 | 74.45 / 83.42 | 0 / 0 |
| 50 | Work orders | 2329.15 / 3081.66 / 3978.14 | 533.08 / 788.66 / 915.26 | 20.11 / 84.45 | 0 / 0 |
| 50 | Reading POST | 604.49 / 1140.00 / 1460.03 | 661.87 / 1403.77 / 1794.17 | 63.29 / 58.58 | 0 / 0 |
| 100 | Equipment page | 1224.83 / 1838.96 / 1965.81 | 1267.15 / 1794.74 / 1912.37 | 69.84 / 69.41 | 0 / 0 |
| 100 | Equipment search | 1240.48 / 1730.92 / 1973.21 | 1184.01 / 1630.21 / 1900.00 | 72.51 / 72.49 | 0 / 0 |
| 100 | Readings query | 1416.14 / 1847.08 / 2117.28 | 1579.29 / 2185.96 / 2362.29 | 61.18 / 55.32 | 0 / 0 |
| 100 | Out-of-range events | 1041.02 / 1378.87 / 1819.88 | 1159.09 / 1627.26 / 1908.26 | 81.26 / 73.91 | 0 / 0 |
| 100 | Work orders | 4581.76 / 5218.19 / 6369.75 | 1095.37 / 1455.54 / 1744.22 | 20.70 / 80.60 | 0 / 0 |
| 100 | Reading POST | 1340.45 / 1962.32 / 2254.03 | 1482.95 / 2314.31 / 2861.91 | 64.19 / 57.57 | 0 / 0 |

| Users | CPU before / after | Peak RSS MiB before / after |
| ---: | ---: | ---: |
| 10 | 105–127% / 123–137% | 130.1 / 130.9 |
| 50 | 112–143% / 121–135% | 149.2 / 148.5 |
| 100 | 120–143% / 118–134% | 163.8 / 166.0 |

The indexed raw readings query measured P95 0.886 / 0.458 ms.

### Profiles and acceptance

- The PostgreSQL out-of-range query plan used an index-only scan on ix_readings_oor_recent. The limited 50-row plan took 0.172 ms and read three shared buffers in the sampled run. The predicate includes the legacy limit comparisons, preserving results for historical rows.
- Work-order listing replaced per-row equipment and user lookups with batched lookups. The qualified endpoint issued 3–4 SQL statements in the sequential open-mode sample. The authenticated sample issued 7–8 statements and moved median latency from 8.42 to 11.96 ms. At 50 users, the same profile measured 657.37 ms P95 open and 958.76 ms authenticated. The default pool size was five with overflow enabled. Pool queue time and serializer CPU were not isolated by this sample.
- Concurrent first readings on PostgreSQL raced to insert a shared timestamp row. The checkpoint had six IntegrityError responses at 10 threads; an intermediate candidate still showed eight. A conditional atomic upsert fixed the conflict in the final rerun. One PostgreSQL rerun initially stopped when the temporary filesystem filled. Releasing completed synthetic datasets and rerunning against a fresh database resolved it.
- PostgreSQL candidate interactive P95 was 123–160 ms at 10 users, 788–967 ms at 50, and 1,456–2,186 ms at 100. The 50- and 100-user targets remain unmet. This is an in-process qualification, and external HTTP load profiling of the production topology is still needed.

## Release completion update

The follow-up used authenticated real HTTPS traffic through local nginx. Results and the four-process experiment are in [the production benchmark](PRODUCTION_BENCHMARK.md).
