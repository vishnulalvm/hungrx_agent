# Getting hungrX running — setup guide

This guide is written for someone with **no backend experience**. It
tells you exactly what to install, what values you need to fill in, and
where to fill them in. Follow the steps in order.

---

## 1. Install Docker Desktop

This project runs entirely inside Docker containers, so you don't need
to install Python, Node.js, or a database yourself.

- Download and install **Docker Desktop**: https://www.docker.com/products/docker-desktop/
- Open it once after installing, so it's running in the background.
- You can confirm it's working by opening a terminal and running:
  ```
  docker --version
  ```
  If that prints a version number, you're good.

---

## 2. Create your `.env` file

This project reads all of its settings (passwords, keys, URLs) from one
file called `.env` in the project's root folder. It is **not** checked
into git (so your real values never get shared), which is why you have
to create it yourself.

1. In the project root folder, find the file named `.env.example`.
2. Make a copy of it, and rename the copy to `.env` (remove the
   `.example` part).
   - On Mac/Linux, you can do this in a terminal from the project root:
     ```
     cp .env.example .env
     ```
3. Open `.env` in any text editor. You'll fill in a few values below —
   everything else can stay as-is for local use.

---

## 3. Values you MUST change

Open your new `.env` file and find these lines. Replace the placeholder
text with your own values.

### `OPENAI_API_KEY`

```
OPENAI_API_KEY=
```

This is currently empty. **Without this, the "AI reads the menu and
structures it" step of the pipeline will fail.** Everything else
(logging in, browsing the dashboard, crawling a site) still works
without it — you only need it once you actually try to ingest a
restaurant.

How to get one:
1. Go to https://platform.openai.com/api-keys
2. Sign in (or create an account).
3. Click "Create new secret key."
4. Copy the key (it starts with `sk-...`) and paste it after the `=`
   sign, e.g.:
   ```
   OPENAI_API_KEY=sk-your-actual-key-here
   ```

> This is a paid API — OpenAI will charge your account per request.
> Check their pricing page if you're unsure.

### `SEED_SUPER_ADMIN_EMAIL` and `SEED_SUPER_ADMIN_PASSWORD`

```
SEED_SUPER_ADMIN_EMAIL=admin@hungrx.local
SEED_SUPER_ADMIN_PASSWORD=change-me-immediately
```

This is the email/password you'll use to log into the admin dashboard
the very first time. Change the password to something real — the
default is intentionally obvious and insecure. Example:

```
SEED_SUPER_ADMIN_EMAIL=you@yourcompany.com
SEED_SUPER_ADMIN_PASSWORD=SomeStrongPasswordHere123!
```

(You'll use these values again in Step 5, to actually create the
account.)

### `API_SECRET_KEY`

```
API_SECRET_KEY=change-me
```

This is a private password the backend uses to sign your login
sessions. Anyone who knows this value could fake a login as any user, so
don't leave it as `change-me` — replace it with a long, random string.

An easy way to generate one: run this in a terminal —
```
openssl rand -hex 32
```
— and paste the result in, e.g.:
```
API_SECRET_KEY=af92e1d8b6c4f0...(long random string)
```

> **Note:** the app will actually refuse to start in a real production
> deployment if you leave this as `change-me` — it's a safety check, not
> just a suggestion. For local/dev use on your own machine, it's fine to
> leave it as-is, but if you're the only one who'll ever see this repo,
> it's still good practice to set a real value now.

---

## 4. Values you can leave alone (for local use)

Everything else in `.env` is already set up to work out of the box for
running the project on your own computer:

| Variable | What it is | Do I need to change it? |
|---|---|---|
| `DATABASE_URL`, `POSTGRES_*` | The database connection | No — Docker creates this database for you automatically |
| `REDIS_URL` | Background job queue connection | No |
| `CORS_ORIGINS` | Which websites are allowed to call the backend | No, unless you're hosting the dashboard somewhere other than `localhost:3000` |
| `API_INTERNAL_BASE_URL` | How the dashboard talks to the backend inside Docker | No |
| `PLAYWRIGHT_HEADLESS`, `CRAWLER_USER_AGENT` | Web-crawling behavior | No |
| `STORAGE_BACKEND`, `STORAGE_*` | Where crawled pages get saved | No — defaults to local disk inside the container |

---

## 5. Start everything

From the project root folder, in a terminal:

```
docker compose up -d
```

The first time you run this, it will download and build everything —
this can take several minutes. After that, starting/stopping is fast.

To check everything started correctly:
```
docker compose ps
```
You should see `api`, `worker`, `postgres`, `redis`, and
`admin-dashboard` all listed as `healthy` or `running`.

---

## 6. Create your admin login

The database starts out empty — there's no user account yet. Run this
one-time command to create yours, using the email/password you set in
Step 3:

```
docker compose exec api uv run python -m database.seed
```

You should see a message like `Created super admin: you@yourcompany.com`.

(If you ever need to run this again, it's safe — it won't create a
duplicate account.)

---

## 7. Open the dashboard

Go to: **http://localhost:3000**

Log in with the email and password you set in Step 3 / created in
Step 6.

The backend API itself (mostly useful for troubleshooting, not
day-to-day use) is at **http://localhost:8000/docs** — that's an
auto-generated page listing everything the backend can do.

---

## 8. Stopping the project

```
docker compose down
```

This stops everything but keeps your data (database, uploaded content)
saved for next time. Running `docker compose up -d` again will bring it
right back.

---

## Quick troubleshooting

- **"docker: command not found"** — Docker Desktop isn't installed or
  isn't running. Revisit Step 1.
- **Dashboard loads but login fails** — Did you run the seed command in
  Step 6, using the same email/password from your `.env` file?
- **Ingesting a restaurant fails at the AI step** — Check that
  `OPENAI_API_KEY` in your `.env` file is filled in and valid, then
  restart with `docker compose up -d --build api worker`.
- **Nothing loads at all** — Run `docker compose ps` to see which
  service isn't healthy, then `docker compose logs <service-name>`
  (e.g. `docker compose logs api`) to see the error.
- **Something changed in `.env` but the app doesn't seem to notice** —
  restart with `docker compose up -d --build` (a container only reads
  `.env` when it starts).
