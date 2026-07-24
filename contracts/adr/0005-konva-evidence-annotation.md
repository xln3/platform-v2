# ADR-0005: Use Konva for evidence annotation

Status: Accepted · 2026-07-24

GEO Platform V2 uses Konva through `react-konva` for bounded screenshot evidence annotation. Authoritative annotations remain typed domain records in artifact coordinates rather than serialized canvas nodes. The canvas renders screenshots, bounding boxes, labels, handles and viewport transforms, is lazy-loaded, and always has a keyboard-accessible semantic text/coordinate alternative. Fabric.js was rejected because its general editor and object-serialization surface is unnecessary for this evidence-specific workflow. Annotation state must not contain credentials, browser profiles, authentication material or biometric data.
