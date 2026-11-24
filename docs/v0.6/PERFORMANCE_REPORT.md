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
