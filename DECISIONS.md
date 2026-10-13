# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?

   On employees, unique `emp_code` speeds up lookups and blocks duplicates, and `{department, emp_code}` serves the employee list. On attendance logs, the unique `{emp_code: 1, date: 1}` index keeps one record per person per day; PATCH uses it to find a record, and the monthly report uses it to read one employee's month. `{emp_code, punch_in: -1}` lets punch-out find the latest punch-in, and `{date: -1, emp_code}` matches the attendance list's sort. I also created `joined_on`, but nothing uses it yet.

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.

   Neither request checks first; both insert. The unique `{emp_code, date}` index accepts one, and the other gets a duplicate key error, returned as 409. Punch-out only saves if `punch_out` is still empty and `punch_in` hasn't changed, so only one punch-out wins and a PATCH can't leave old work hours behind. PATCH only saves if the status, punch times and history length still match what it read, then adds one history entry; otherwise it returns 409, so no history is lost. A PATCH must change the status or a punch time, the final punch-in must be on the record's date, and client-sent work hours or late minutes are ignored.

3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?

4. **Headcount.** How does your department summary make sure employees with zero logs are counted?

5. **One thing you would change** if this had to serve 100x the data.
