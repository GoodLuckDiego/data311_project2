Hi team,

Here is a summary of the notes/pseudo code we came up with together the other day in class.

General idea:
IF (a row is funny/has something wrong):
  set it aside for human review

For PersonID
- set aside for duplicate rows
- check for exactly 5 characters

For Role
- standardize capitilization
- check the value is in ('Student', 'Faculty', 'Staff')
- if 'Admin' change to 'Staff'

For current_city
- Standardize capitalization
- Do .unique() to look for and fix spelling errors

For Zip Code
- enforce 5 digits
- we decided not to care about the -XXXX suffix if it is there
- compare zip code to region, like 95521 for Arcata

For trips/week
- look at .unique()
- make sure it is a number

For percent columns
- make sure formatted as float
- make sure not negative

For distance_to_campus
- format as decimal or float
- set aside if value is over 100

We chose to discard the 'transit time' column

Latitude/Longitude
- set aside if outside of value range, like 39 to 41 or something
