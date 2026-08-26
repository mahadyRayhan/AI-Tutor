# Manual Verification: Authentication & Authorization Fix

How to re-run the reported attacks yourself and confirm they now fail.

**The reported finding:**

> There does not appear to be server-side authentication or authorization once a user
> is logged in. User credentials and account information are stored in the browser's
> local storage, which can be modified using the developer tools available in modern
> browsers. By modifying these values, I was able to access other students' accounts,
> as well as administrative functionality and the teacher's dashboard.

Every test below is the original attack, repeated. All should now fail.

> ⚠️ Run these against a **test deployment** if possible. They are read-only except
> where noted, but they do exercise real endpoints.

---

## Setup

You need two **real** student accounts and one teacher. List what exists:

```bash
python scripts/reset_password.py --list
```

Log in as one student in a normal browser window, open DevTools (F12), and paste this
into the Console so the rest of the tests have names to work with:

```js
const ME     = 'PathfindersChallenge';   // the account you are logged in as
const VICTIM = 'Mahady';                 // any OTHER real account
```

Substitute your own. The tests below fail open if you use a username that does not
exist — a 404 is not proof of anything.

---

## Test 1 — Editing localStorage no longer grants teacher access

**The original attack.**

1. Logged in as a student, open **Application → Local Storage** (Chrome) or
   **Storage → Local Storage** (Firefox)
2. Find the `c_tutor_user` key. It looks like:
   `{"username":"PathfindersChallenge","role":"student","name":"..."}`
3. Change `"role":"student"` to `"role":"teacher"` and save
4. Navigate to `/teacher_dashboard.html`

**PASS:** You are redirected to the home page. The dashboard never renders.
**FAIL:** The Teacher Command Center loads.

**Why:** the page is now gated server-side on a signed session cookie. localStorage is
a display cache with no authority.

---

## Test 2 — The teacher page is never sent to a student

Test 1 could in principle be satisfied by a client-side redirect that still downloads
the page first. Confirm the markup is not sent at all.

1. Still logged in as a student (with the tampered role from Test 1, if you like)
2. DevTools → **Network** tab
3. Navigate to `/teacher_dashboard.html`
4. Click the `teacher_dashboard.html` request → **Response** tab

**PASS:** Status is `303`, and the response body contains no dashboard HTML.
**FAIL:** Status `200` with the full page in the body.

---

## Test 3 — You cannot read another student's data

Run the whole sweep at once. It checks that the victim's data is refused **and** that
your own still loads — a guard that returns 403 for everything is broken, not secure.

```js
for (const p of [
  `/api/v1/analytics/student/${VICTIM}`,
  `/api/v1/analytics/report/${VICTIM}`,
  `/api/v1/skill-network/${VICTIM}`,
  `/api/v1/assignments/student/${VICTIM}`,
  `/api/v1/mastery/${VICTIM}/Variables`,
  `/api/v1/history/sessions?username=${VICTIM}`,
]) console.log('want 403 →', await fetch(p).then(r => r.status), p);

for (const p of [
  `/api/v1/analytics/student/${ME}`,
  `/api/v1/history/sessions?username=${ME}`,
]) console.log('want 200 →', await fetch(p).then(r => r.status), p);
```

**PASS:** the first six print `403`, the last two print `200`.
**FAIL:** any `403` line printing `200` — that response body contains the other
student's data.

> The `history/sessions` line is worth running deliberately. It passes `username`
> as a **query** parameter rather than in the path, which is how it escaped the
> first sweep — it returned another student's entire chat history, and the matching
> DELETE erased it.

---

## Test 4 — Administrative functionality is closed

The most severe one: this endpoint changes user roles. If it were open, a student
could promote themselves and everything else would follow legitimately.

```js
await fetch('/api/v1/admin/users/update', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({username: ME, role: 'teacher'})
}).then(r => r.status)
```

**PASS:** `403`
**FAIL:** `200` — then check whether your role actually changed.

---

## Test 5 — Spoofing the old header does nothing

The original check trusted an `X-User-Role` header.

```js
await fetch('/api/v1/analytics/teacher/detailed', {
  headers: {'X-User-Role': 'teacher'}
}).then(r => r.status)
```

**PASS:** `403` — the header is not read at all any more.
**FAIL:** `200`.

---

## Test 6 — You cannot forge a session cookie

1. DevTools → **Application → Cookies**
2. Find `sage_session`

**PASS:** the `HttpOnly` column is checked. You will find you **cannot** read its
value from `document.cookie` in the console:

```js
document.cookie          // sage_session is NOT listed
```

3. Try to overwrite it anyway:

```js
document.cookie = 'sage_session=anything; path=/'
await fetch('/api/v1/analytics/teacher/detailed').then(r => r.status)
```

**PASS:** `401` or `403`. A hand-made token has no valid signature.
**FAIL:** `200`.

---

## Test 7 — You cannot write to another account

The subtlest one. Several endpoints take a `username` in the request *body*.

```js
await fetch('/api/v1/user/goal', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({username: VICTIM, goal: 'TEST-IMPERSONATION'})
}).then(r => r.status)
```

**PASS:** `200` — **but the goal is written to YOUR account, not VICTIM's.**
Log in as VICTIM and confirm their goal is unchanged; check your own dashboard
and you will see `TEST-IMPERSONATION` there instead.

**FAIL:** VICTIM's goal changed.

> The 200 is intentional. The body's `username` is overwritten with the session's
> identity rather than rejected, so the request succeeds against the right account.

Same check for the destructive one. **Read this before pasting it.** The fix works by
redirecting the write to the caller, so passing `VICTIM` does not spare you — it
erases **your own** mastery data, the account you are logged in as. Log in as a
throwaway account first, or skip this one; the goal test above already proves the
same property harmlessly.

```js
await fetch('/api/v1/user/reset-knowledge', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({username: VICTIM})
}).then(r => r.status)
```

**PASS:** your own knowledge is reset; VICTIM's is untouched.

---

## Test 8 — Logging out actually ends the session

1. Click **Logout**
2. In the console:

   ```js
   await fetch('/api/v1/auth/me').then(r => r.status)
   ```

**PASS:** `401`.
**FAIL:** `200` with your user still returned.

---

## Quick checklist

```
[ ] 1. localStorage role edit → redirected, no teacher dashboard
[ ] 2. teacher_dashboard.html → 303, HTML not in response body
[ ] 3. other student's data → 403 (own data → 200)
[ ] 4. /admin/users/update → 403
[ ] 5. X-User-Role: teacher header → 403
[ ] 6. sage_session is HttpOnly; forged cookie → 401
[ ] 7. body username → writes to YOUR account, not theirs
[ ] 8. after logout, /auth/me → 401
```

---

## What is NOT fixed by this

**Traffic is still unencrypted (HTTP).** The first reported finding stands. Anyone
sniffing the same network can read your password at login *and copy your session
cookie* — and a copied cookie is a valid session. `HttpOnly` stops JavaScript from
reading the cookie; it does nothing against a packet capture.

This needs TLS, which on Lightsail needs a domain name. Once HTTPS is in front of the
app, set `SAGE_COOKIE_SECURE=true` in `.env` so the cookie is only ever sent over an
encrypted connection.

**Set a persistent signing key.** Without `SAGE_JWT_SECRET` in `.env`, a random key is
generated per process and every session is invalidated on restart:

```bash
python -c "import secrets; print('SAGE_JWT_SECRET=' + secrets.token_urlsafe(48))" >> .env
```

**Roles are still self-declared at signup** if your signup flow allows choosing one —
worth checking separately.
