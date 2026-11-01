/** @type {import('next').NextConfig} */
// REX_STANDALONE=1 (set by web/Dockerfile) emits a self-contained server for the container image. Local `npm run build` is unchanged.
// "/" opens the AI research agent (/ai); the old landing page is still reachable at /home.
export default { reactStrictMode: true, async redirects() { return [{ source: "/", destination: "/ai", permanent: false }]; }, ...(process.env.REX_STANDALONE ? { output: "standalone" } : {}) };
