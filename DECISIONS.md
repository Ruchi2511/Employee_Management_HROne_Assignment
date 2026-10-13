# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?

   Unique `emp_code` on employees for lookups and duplicate creates, plus `{department, emp_code}` for the filtered, sorted list. Unique `{emp_code, date}` on logs, which also guards punch-in, and `{emp_code, punch_in: -1}` so punch-out finds the latest record. `{date: -1, emp_code}` matches the attendance list's sort, so a page is read in index order instead of sorting the whole collection.

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.

   Both find the employee and try to insert, with no check first. The unique `{emp_code, date}` index lets one in; the other gets `DuplicateKeyError`, returned as 409. Punch-out only updates while `punch_out` is null, so one wins.

3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?

4. **Headcount.** How does your department summary make sure employees with zero logs are counted?

5. **One thing you would change** if this had to serve 100x the data.
