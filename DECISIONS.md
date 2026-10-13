# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?

   On employees, a unique `emp_code` blocks duplicates and speeds up lookups, `{department, emp_code}` serves the employee list, and `joined_on` finds who had joined by month end for the department summary. On attendance logs, the unique `{emp_code, date}` index keeps one record per person per day; PATCH and the monthly and department reports read through it. `{emp_code, punch_in}` finds the latest punch-in for punch-out, and `{date, emp_code}` matches the attendance list's sort.

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.

   Neither request checks first; both just insert. The unique `{emp_code, date}` index lets one through, and the other gets a duplicate key error, which I return as 409. Other writes follow the same idea: punch-out saves only if `punch_out` is still empty, and PATCH saves only if the record hasn't changed since it was read. Nothing is overwritten, no history is lost, and the loser gets 409.

3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?

4. **Headcount.** How does your department summary make sure employees with zero logs are counted?

   I start from the employees, not the logs. I pick everyone who joined on or before the last day of the month, then look up each person's logs for that month. Someone with no logs gets an empty list, so they add nothing to the totals but still add one to the headcount. People who join later are never picked.

5. **One thing you would change** if this had to serve 100x the data.
