# Rex web

Next.js 16 (App Router) + React 19 + Tailwind v4 + shadcn-style components + Recharts. Talks to the FastAPI backend at `NEXT_PUBLIC_API` (default `http://localhost:8000`).

    npm install
    npm run dev        # http://localhost:3000
    npm run lint       # tsc --noEmit
    npm run build && npm start

Pages: `/` home, `/build`, `/results`, `/refine`, `/journal`, `/check`, `/scout`, `/research`, `/memory`. Optional `POST /api/explain` (Groq or Gemini key in the Next server env) powers the "Explain" buttons; without a key they are hidden. No Google fonts are fetched, so the build works offline. Use the root `./start.sh` for the full stack.
