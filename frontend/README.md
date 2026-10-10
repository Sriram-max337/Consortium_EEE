# Magic Witch frontend

Vite + React + Tailwind frontend foundation for **Magic Witch — The Elixir of Eternal Life**.

## Local setup

1. Copy `.env.example` to `.env.local`.
2. Set `VITE_API_URL` to the FastAPI origin, `VITE_SUPABASE_URL` to the Supabase project URL, and `VITE_SUPABASE_ANON_KEY` to the project's public anon/publishable key.
3. From this directory, run:

   ```powershell
   npm install
   npm run dev
   ```

The app uses Supabase Auth for email/password sign-in and verifies the resulting session with `GET /auth/me`. The API determines whether the account is an active admin or organizer. Admins are routed to `/admin`; organizers are routed to their assigned Witch station at `/organizer`.

The admin overview reads `GET /admin/game/status`. The Organizers section manages Auth-backed organizer profiles and Witch assignments through admin-only FastAPI endpoints. Frontend route checks improve navigation only; FastAPI remains the authorization boundary.

Only the Supabase public anon/publishable key belongs in `VITE_*` variables. Never put the service-role key in this frontend.

## Checks

```powershell
npm run lint
npm run build
```
