# BESS Sizing Platform

Multi-user BESS sizing tool. Formulas follow `BESS SIZING SOFTWARE.docx` (discharge, charge, DC-DC RTE, aux, FAT/SAT, augmentation, year-by-year MATCHED / NOT MATCHED).

- `backend/calc.py`: calculation engine (pure functions, no shared state)
- `backend/server.py`: REST API, auth, sqlite storage, serves the web app
- `backend/static/index.html`: the web app (Inputs, Years, Load, Design, Results, What-if, Ask, Account)
- `backend/test_bess.py`: 24 tests (formulas, API, auth, user isolation, concurrency)

The server recalculates everything itself; the browser copy is for instant feedback only. "Verify on server" in the Account tab checks they agree.

## Run locally
    export BESS_SECRET=$(python3 -c "import secrets;print(secrets.token_hex(32))")
    cd backend && python3 server.py        # open http://127.0.0.1:8000
    cd backend && python3 test_bess.py     # tests
Keep `BESS_SECRET` fixed between runs, otherwise everyone is logged out on restart. `pip install openpyxl` enables Excel export.

## Run with Docker
    cp .env.example .env     # set BESS_SECRET
    docker compose up -d --build     # http://SERVER-IP:8000, data kept in a volume

## Put it online (IP or domain)
Run it on a server or VPS, then put HTTPS in front (required, passwords and tokens travel over the network). Caddy example, with your domain's DNS pointing at the server:

    your-domain.com {
        reverse_proxy 127.0.0.1:8000
    }
Then open `https://your-domain.com`. For a quick test on the same network use `http://SERVER-IP:8000` (no HTTPS, do not use real passwords).

## Use it
1. Open the site, go to Account, Register, log in.
2. Enter inputs (or load a CSV in Load), check Results.
3. Account: "Save as new project". Later change something and "Save new version".
4. Compare a version with the previous one, verify on server, download CSV / Excel.

## Security summary
PBKDF2-SHA256 password hashing; signed 8-hour bearer tokens (header, not cookies, so CSRF does not apply); every query scoped to the logged-in user (others get 404); input range validation on the server; rate limiting; security headers; audit log table. Project names are escaped in the UI.

## Known limits
- Single-process SQLite: fine for a team, move to PostgreSQL for heavy use.
- Rate limiting and tokens are in-memory/stateless per process; run one process, or add shared storage.
- No password reset, email verification, or token revocation yet.
- No PDF report; load profiles are CSV or pasted text only.
- Formula assumptions are listed in the app notes: augmented containers use the same year's SOH/degradation, and the separate DC charging efficiency input is not used.

## Put this on GitHub
    cd bess-platform
    git init && git add . && git commit -m "BESS sizing platform"
    git branch -M main
    git remote add origin https://github.com/YOU/REPO.git
    git push -u origin main
Never commit `.env` or `*.db` (the `.gitignore` already blocks them).
