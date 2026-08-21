/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Overrides ONLY the backend port src/lib/api.ts talks to (default 8000).
   * Exists so an end-to-end run can drive a throwaway backend on its own
   * port with its own scratch database, instead of the instance a live demo
   * is already serving on 8000. Never set in normal dev or in a build.
   */
  readonly VITE_API_PORT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
