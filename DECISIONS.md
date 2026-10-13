# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?

   Employees: unique `emp_code` for lookups and duplicate creates, and `{department, emp_code}` for the filtered list sorted by code. Logs: unique `{emp_code, date}` for PATCH lookups and the punch-in race, `{emp_code, punch_in: -1}` so punch-out finds the latest punch-in, and `{date: -1, emp_code}`, which matches the list's sort so pages come straight off the index. `joined_on` exists but nothing uses it yet.

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.

   Both insert without checking first. The unique `{emp_code, date}` index takes one; the other gets `DuplicateKeyError`, returned as 409. Punch-out only writes if `punch_out` is still null and `punch_in` hasn't changed, so a parallel PATCH can't leave stale hours. PATCH writes only if status, punch times and history length still match what it read, then pushes one history entry; the loser gets 409, so no entry is lost. A PATCH must change status or a punch time, ignores client-sent derived fields, and its final `punch_in` must fall on the record's date.

3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?

4. **Headcount.** How does your department summary make sure employees with zero logs are counted?

5. **One thing you would change** if this had to serve 100x the data.
