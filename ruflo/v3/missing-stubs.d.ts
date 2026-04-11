// Stub type declarations for optional WASM packages not published on npm.
// These modules are loaded via dynamic import with .catch(() => null) fallback,
// so they are safe to stub — the runtime gracefully degrades if absent.

declare module '@ruvector/hyperbolic-hnsw-wasm' {
  const _default: unknown;
  export default _default;
  export const HyperbolicHNSW: unknown;
}

declare module '@ruvector/learning-wasm' {
  const _default: unknown;
  export default _default;
  export const LearningWasm: unknown;
}

declare module '@ruvnet/bmssp' {
  export default function init(): Promise<void>;
  export const WasmNeuralBMSSP: unknown;
  export const WasmGraph: unknown;
}
