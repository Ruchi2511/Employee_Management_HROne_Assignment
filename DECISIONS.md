# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?

   On employees, unique `emp_code` blocks duplicates, `{department, emp_code}` serves the employee list and trend, and `joined_on` serves the department summary. On logs, unique `{emp_code, date}` keeps one record per person per day and serves PATCH and the reports. `{emp_code, punch_in}` serves punch-out, and `{date, emp_code}` serves the attendance list and leaderboard.

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.

   Neither request checks first; both insert. The unique `{emp_code, date}` index lets one through, and the other gets a duplicate key error, which I return as 409. Punch-out and PATCH also only save if the record is unchanged.

3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?

   Both get the same rank and the next rank is skipped, like 1, 2, 2, 4. I rank first with `$rank`, then keep everyone whose rank is at most `limit`, so both come back, even past `limit`.

4. **Headcount.** How does your department summary make sure employees with zero logs are counted?

   I start from employees, not logs. I pick everyone who joined by the month's last day, then look up their logs for that month. Someone with no logs adds nothing to the totals but still counts in the headcount.

5. **One thing you would change** if this had to serve 100x the data.

   I'd keep a small summary per employee per month, updated on every punch and correction. Then the reports read a few thousand summary rows instead of every log in the month.
