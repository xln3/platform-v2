# ADR-0001: Platform architecture

Status: Accepted · 2026-07-24

GEO Platform V2 is an independent pnpm/Turborepo monorepo. Four React 19 applications consume one generated OpenAPI client. A FastAPI modular monolith owns synchronous policy and transactional APIs; resource/risk-specific Temporal workers own durable execution. Domain code stays framework-free. This avoids premature microservices while preserving deployable worker boundaries.
