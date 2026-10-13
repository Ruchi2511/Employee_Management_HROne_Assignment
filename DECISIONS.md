# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?

   On employees, a unique `emp_code` blocks duplicates, `{department, emp_code}` serves the employee list, and `joined_on` finds who had joined for the department summary. On attendance logs, the unique `{emp_code, date}` index keeps one record per person per day and serves PATCH and the monthly reports. `{emp_code, punch_in}` serves punch-out, and `{date, emp_code}` serves the attendance list and leaderboard.

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.

   Neither request checks first; both just insert. The unique `{emp_code, date}` index lets one through, and the other gets a duplicate key error, which I return as 409. Punch-out and PATCH also only save if the record hasn't changed since they read it.

3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?

   Both get the same rank, and the next rank is skipped, like 1, 2, 2, 4. I rank first with MongoDB's `$rank`, then keep everyone whose rank is at most `limit`, so both tied people come back even if that's more than `limit` rows. Tied rows are sorted by `emp_code`.

4. **Headcount.** How does your department summary make sure employees with zero logs are counted?

   I start from employees, not logs. I pick everyone who joined by the month's last day, then look up each person's logs for that month. Someone with no logs gets an empty list, so they add nothing to the totals but still count in the headcount.

5. **One thing you would change** if this had to serve 100x the data.
