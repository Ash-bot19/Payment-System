# Payment System

## Known Limitations / Future Work

- **Health endpoint confirms process liveness only** — does not validate Kafka partition assignment or poll loop activity. A production-grade implementation would expose consumer lag and assignment state.
