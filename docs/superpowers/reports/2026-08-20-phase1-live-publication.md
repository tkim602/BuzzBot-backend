# Phase 1 Live Publication Report

## Bounded probe

- Requested unit: `202608 / CS / 7650`
- Status: `READY`
- Requests: 1
- Parsed sections: 3
- Required fields: present

## Bounded subject synchronization

- Requested unit: `202608 / CS`
- Requests: 2 (`probe=1`, `subject=1`)
- Result: `PARSE_FAILED / NO_SECTIONS`
- Published version: none
- Existing published data changed: no

The saved public response states that no classes matched because the subject URL sent an empty
`crse_in` value. The URL builder now sends Banner's `%` course wildcard and has a regression test.
The live subject request was not repeated, preserving the one-sync limit and no-retry policy.

## Fixture-backed verification

- Full suite: 135 passed, 6 skipped before the URL fix.
- PostgreSQL integration suite: 6 passed.
- Retrieval smoke: CS 7650 CRNs, instructors, timed/TBA meetings, source URL, `data_as_of`, version,
  and freshness returned from the latest `PUBLISHED` fixture version; `SUPERSEDED` data excluded.
