# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?

   On `employees`: unique `emp_code` (lookups, and duplicate creates fail cleanly), `{department, emp_code}` for the filtered, sorted list, and `joined_on`, which will support headcount when no department is given. On `attendance_logs`: unique `{emp_code, date}` (one record per day, and the punch-in race guard); `{date: -1, emp_code}`, which will support the attendance list sort and month ranges; and `{emp_code, punch_in: -1}` for punch-out, which finds the latest record with `punch_in` at or before the punch-out time. I rejected a `status` index because the date index already handles status filters.

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.

   Each request is validated first. A missing `punched_at` means "now", as the contract says, but an explicit `null` is a 422 because the field isn't nullable. The time is cut to whole seconds before any maths, so 09:40:00.900 counts as 09:40:00. The date is the IST calendar date; on an overnight shift, a punch before `shift_end` belongs to the previous day. Late means more than 10 minutes after shift start, counted in whole minutes from the start itself. The record gets the contract defaults (null `punch_out` and `work_hours`, zero overtime, `half_day` false, empty history) and the response never includes `_id`.

   After the employee lookup (needed for the 404), both requests insert without first checking for an existing attendance record. The unique `{emp_code, date}` index lets one through; the other gets a `DuplicateKeyError`, which I return as 409. I rejected check-then-insert because both requests could pass the check before either inserts.

   For punch-out, a record that's already closed returns 409 before I check the time window, since the contract doesn't say which error wins. The 24-hour window uses whole-second times (R1), and its 422 uses FastAPI's list-shaped error body to match the contract's `ValidationError` schema.

3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?
4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
5. **One thing you would change** if this had to serve 100x the data.
