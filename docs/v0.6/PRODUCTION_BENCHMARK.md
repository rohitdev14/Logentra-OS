# Production ingress benchmark

**Result: FAIL at 50 and 100 users on the host TLS topology. Container benchmark BLOCKED.**

The workload ran through local nginx TLS ingress, FastAPI production authentication, and PostgreSQL 18.6 on the WSL2 host. The isolated database began with 5,000 assets, 500,000 readings and 2,000 work orders. Each simulated user had a distinct API key bound to the same technician role and identity. A closed-loop client rotated through five read endpoints and one reading write. It checked each successful write response and reconciled unique committed reading IDs after each run. These are host measurements and exclude container overhead.

Latency columns are P50 / P95 / P99 in ms. The harness is scripts/http_load.py. It requires a dedicated synthetic instance, a trusted certificate and one test credential per simulated user.

| App processes | Users | Duration s | Endpoint | P50 / P95 / P99 ms | Requests/s | Errors |
| ---: | ---: | ---: | --- | ---: | ---: | ---: |
| 1 | 10 | 12.05 | equipment | 104.40 / 122.73 / 143.19 | 15.11 | 0 |
| 1 | 10 | 12.05 | search | 108.03 / 126.38 / 152.47 | 15.11 | 0 |
| 1 | 10 | 12.05 | readings | 100.94 / 122.00 / 158.96 | 14.78 | 0 |
| 1 | 10 | 12.05 | events | 99.67 / 126.83 / 142.18 | 14.86 | 0 |
| 1 | 10 | 12.05 | work_orders | 99.60 / 119.71 / 138.62 | 14.94 | 0 |
| 1 | 10 | 12.05 | ingest | 147.49 / 174.95 / 190.40 | 14.94 | 0 |
| 1 | 50 | 12.37 | equipment | 548.06 / 873.02 / 1102.90 | 14.14 | 0 |
| 1 | 50 | 12.37 | search | 562.23 / 836.62 / 1223.31 | 14.06 | 0 |
| 1 | 50 | 12.37 | readings | 559.33 / 829.80 / 979.06 | 13.82 | 0 |
| 1 | 50 | 12.37 | events | 561.40 / 865.05 / 1101.13 | 13.98 | 0 |
| 1 | 50 | 12.37 | work_orders | 537.12 / 852.31 / 984.99 | 13.90 | 0 |
| 1 | 50 | 12.37 | ingest | 640.00 / 1071.84 / 1231.27 | 14.22 | 0 |
| 1 | 100 | 12.83 | equipment | 1187.43 / 1507.07 / 1667.02 | 13.57 | 0 |
| 1 | 100 | 12.83 | search | 1176.85 / 1492.84 / 1713.88 | 13.41 | 0 |
| 1 | 100 | 12.83 | readings | 1177.86 / 1496.41 / 1738.51 | 13.33 | 0 |
| 1 | 100 | 12.83 | events | 1178.35 / 1475.71 / 1607.31 | 13.41 | 0 |
| 1 | 100 | 12.83 | work_orders | 1175.40 / 1503.83 / 1807.50 | 13.96 | 0 |
| 1 | 100 | 12.83 | ingest | 1260.12 / 1610.53 / 1815.43 | 13.57 | 0 |
| 1 | 100 | 45.89 | equipment | 1244.53 / 1611.29 / 1809.72 | 12.97 | 0 |
| 1 | 100 | 45.89 | search | 1250.45 / 1648.10 / 1932.87 | 12.84 | 0 |
| 1 | 100 | 45.89 | readings | 1219.44 / 1662.58 / 1889.29 | 12.97 | 0 |
| 1 | 100 | 45.89 | events | 1214.15 / 1635.21 / 1878.63 | 13.01 | 0 |
| 1 | 100 | 45.89 | work_orders | 1234.40 / 1694.39 / 2056.41 | 12.92 | 0 |
| 1 | 100 | 45.89 | ingest | 1313.21 / 1692.11 / 1973.86 | 13.01 | 0 |
| 4 | 50 | 12.33 | equipment | 288.82 / 1270.23 / 1760.32 | 17.43 | 0 |
| 4 | 50 | 12.33 | search | 378.63 / 1416.69 / 1731.19 | 17.52 | 0 |
| 4 | 50 | 12.33 | readings | 336.89 / 1414.54 / 2019.35 | 17.68 | 0 |
| 4 | 50 | 12.33 | events | 311.20 / 1307.45 / 1805.40 | 17.52 | 0 |
| 4 | 50 | 12.33 | work_orders | 378.58 / 1321.63 / 1986.67 | 17.11 | 0 |
| 4 | 50 | 12.33 | ingest | 425.27 / 1426.74 / 2403.54 | 17.11 | 0 |
| 4 | 100 | 12.81 | equipment | 888.35 / 3354.77 / 5085.23 | 13.74 | 0 |
| 4 | 100 | 12.81 | search | 954.52 / 3351.66 / 5298.90 | 13.98 | 0 |
| 4 | 100 | 12.81 | readings | 878.30 / 3027.43 / 4015.86 | 14.44 | 0 |
| 4 | 100 | 12.81 | events | 883.38 / 3020.19 / 6094.46 | 14.21 | 0 |
| 4 | 100 | 12.81 | work_orders | 904.21 / 2388.62 / 3897.97 | 14.60 | 0 |
| 4 | 100 | 12.81 | ingest | 1004.56 / 3396.32 / 4406.46 | 14.21 | 0 |

| App processes | Users | Duration s | App CPU | Peak app RSS MiB | Peak DB connections / active / lock waits | Persisted writes |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 10 | 12.05 | 134.96% | 127.72 | 13 / 2 / 0 | 180 |
| 1 | 50 | 12.37 | 133.92% | 136.84 | 15 / 2 / 0 | 176 |
| 1 | 100 | 12.83 | 134.85% | 139.84 | 15 / 2 / 0 | 174 |
| 1 | 100 | 45.89 | 134.53% | 140.59 | 15 / 2 / 0 | 597 |
| 4 | 50 | 12.33 | 313.84% | 502.22 | 52 / 3 / 0 | 211 |
| 4 | 100 | 12.81 | 265.0% | 512.1 | 60 / 4 / 0 | 182 |

App CPU is relative to one logical core. Four-process RSS sums process RSS and can double-count shared pages. PostgreSQL samples excluded the sampler and were taken every 250 ms. Zero sampled lock waits does not rule out short waits. The four-process run overlapped final verification for part of the run. The two topologies were not measured under identical background load and this is not an isolated worker-count experiment.

One process sustained approximately 78 requests/s at 100 users for 45.89 seconds. Its five interactive P95 values ranged from 1,611 to 1,694 ms in that run and all 597 acknowledged writes persisted. Peak RSS stayed below 141 MiB in the single-process runs. The duration is insufficient to establish absence of leaks. The measured connection ceiling rose to 60 with four processes, but at most four connections were observed active. Pool size was not changed because there was no measured evidence that increasing it would help. Isolated pool queue time, transaction timing, serialization CPU and reverse-proxy overhead remain unmeasured.

Compared with the previous in-process PostgreSQL P95 ranges of 123–160, 788–967 and 1,456–2,186 ms at 10, 50 and 100 users, the single-process HTTPS runs measured 120–127, 830–873 and 1,476–1,507 ms. The workloads and transport differ. Four processes yielded worse observed tails and higher app RSS, so the default deployment remains one process. The results do not establish a minimum infrastructure specification. Qualify the intended CPU allocation, PostgreSQL host and container limits with a longer mixed run and a direct ingress-overhead profile before sizing production.

The separate local sustained delivery regression delivered 100 distinct webhook events in 0.555 seconds. Load generation used in-range readings and did not combine outbound event delivery with the HTTP workload.
