# Proposed ADR-0005: Use Konva for evidence annotation

Status: Accepted as `contracts/adr/0005-konva-evidence-annotation.md` · 2026-07-24  
Owner: S03  
Acceptance owner: S00/S04  
Decision scope: Customer Web evidence viewer and Report Studio evidence editor

## Context

GEO Platform V2 needs an interactive screenshot-annotation surface for evidence bounding boxes, text anchors,
selection handles, zoom and coordinate-preserving review. The surface is evidence-specific: it does not need a
general-purpose design-document model, arbitrary object authoring, filters or an extensible drawing editor.

The fixed frontend stack requires choosing either Konva or Fabric.js and recording the choice in an ADR. The
existing accepted ADR directory is S00-owned, so S03 records the complete proposed decision here for S04 to promote
without mutating an accepted shared contract.

Accessibility cannot rely on canvas content. Every annotation must retain a semantic text/coordinate representation,
keyboard-reachable controls and an equivalent non-canvas review path.

## Decision

Use **Konva through `react-konva`** for evidence screenshot annotation.

- Keep the authoritative annotation data as typed application/domain records, not serialized Konva nodes.
- Render only bounded evidence primitives: screenshot image, rectangles, labels, selection handles and transforms.
- Keep coordinates in the evidence artifact coordinate space and derive viewport transforms at render time.
- Lazy-load the canvas implementation so non-editor routes do not pay its bundle cost.
- Provide semantic annotation details and controls outside the canvas. A focusable scroll container exposes the
  canvas on constrained viewports; canvas is never the sole representation of evidence.
- Do not store secrets, browser profiles, raw authentication material or biological data in annotation state.

Fabric.js is not selected. Its object/editor model and broader manipulation surface add capabilities and state that
the bounded evidence workflow does not require.

## Consequences

### Positive

- React component integration and scene-graph primitives fit the existing Report Studio implementation.
- Evidence data remains library-independent and can be rendered in PDF/HTML/table alternatives.
- The bounded API reduces editor complexity and the amount of client state needing security review.
- Lazy loading contains the bundle impact.

### Costs and constraints

- Konva canvas output is not intrinsically accessible; the semantic alternative is mandatory and tested.
- Large images require explicit viewport sizing, local scrolling and coordinate transforms.
- Exported visual artifacts must be generated from immutable evidence data, not treated as authoritative because
  they were rendered by Konva.
- Replacing Konva later requires a renderer adapter, while the underlying annotation records remain stable.

## Rejected alternative

**Fabric.js** was considered for its general canvas-object editing capabilities. GEO evidence annotation does not
need its broader document/editor abstraction, serialization format or free-form manipulation features, so the
additional surface is not justified.

## Validation

- Report Studio component tests exercise evidence binding and annotation interaction.
- Playwright covers the editor at 1600×1100, 1024×768 and 390×844.
- Axe coverage validates the semantic alternative and focusable constrained canvas region.
- Visual regression covers the evidence editor at all three viewports and asserts no root overflow.

## Promotion

S04 promoted this decision to `contracts/adr/0005-konva-evidence-annotation.md`. This proposal is retained as the
full decision rationale and cross-link. Any later reversal must supersede the accepted ADR rather than edit it in
place.
