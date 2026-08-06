declare module '*.css';
interface ImportMetaEnv {
  readonly DEV: boolean;
  readonly VITE_ALLOW_CONTRACT_FIXTURES?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
