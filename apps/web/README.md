## Next.js App

Scaffolded with Tailwind + shadcn/ui + Supabase Auth.

Local dev setup

1) Copy `.env.example` to `.env.local` and set:
   - `NEXT_PUBLIC_SUPABASE_URL`
   - `NEXT_PUBLIC_SUPABASE_ANON_KEY`
2) Run: `npm run dev`
3) Visit http://localhost:3000

Protected routes live under the `(app)` group; unauthenticated users are redirected to `/login` (magic link auth).
