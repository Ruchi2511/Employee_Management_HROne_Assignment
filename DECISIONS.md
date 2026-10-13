# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?

   On employees, unique `emp_code` blocks duplicates, `{department, emp_code}` serves the employee list and trend, and `joined_on` serves the department summary. On logs, unique `{emp_code, date}` keeps one record per person per day and serves PATCH and the reports. `{emp_code, punch_in}` serves punch-out; `{date, emp_code}` serves the attendance list and leaderboard.

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.

   Both requests find the employee and work out the same date. Neither checks for an existing record; both just insert. The unique `{emp_code, date}` index accepts the first, and the second gets a duplicate key error, which I return as 409.

3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?

   Both get the same rank and the next rank is skipped, like 1, 2, 2, 4. I rank with `$rank` first and apply `limit` after, so both come back. Cutting by position would drop one of two equal people.

4. **Headcount.** How does your department summary make sure employees with zero logs are counted?

   I start from employees, not logs. I pick everyone who joined by the month's last day and look up their logs. Someone with no logs gets an empty list, so they add nothing but still count toward headcount.

5. **One thing you would change** if this had to serve 100x the data.

   I'd keep a small summary per employee per month, updated on every punch and correction. Then the reports read a few thousand summary rows instead of every log in the month.
