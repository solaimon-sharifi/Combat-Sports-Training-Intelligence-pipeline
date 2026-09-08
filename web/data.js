// Snapshot of exports/*.csv (synthetic dataset, seed=42, 65 sessions).
// Regenerate with: python scripts/export_for_powerbi.py, then paste the
// refreshed rows in here. Kept as plain arrays so this page needs no build
// step — open index.html directly or serve the folder statically.

const STATS = {
  sessions: 65,
  exchanges: 683,
  weeks: 26,
  avgPerWeek: 2.5,
};

const TAKEDOWN_TREND = [
  { week: "2026-W10", start: "2026-03-10", attempts: 9,  successes: 3,  rate: 33.3 },
  { week: "2026-W11", start: "2026-03-17", attempts: 1,  successes: 0,  rate: 0.0 },
  { week: "2026-W12", start: "2026-03-27", attempts: 1,  successes: 0,  rate: 0.0 },
  { week: "2026-W13", start: "2026-03-30", attempts: 6,  successes: 2,  rate: 33.3 },
  { week: "2026-W15", start: "2026-04-13", attempts: 1,  successes: 0,  rate: 0.0 },
  { week: "2026-W17", start: "2026-04-28", attempts: 5,  successes: 3,  rate: 60.0 },
  { week: "2026-W18", start: "2026-05-04", attempts: 12, successes: 3,  rate: 25.0 },
  { week: "2026-W19", start: "2026-05-15", attempts: 3,  successes: 2,  rate: 66.7 },
  { week: "2026-W21", start: "2026-05-31", attempts: 2,  successes: 0,  rate: 0.0 },
  { week: "2026-W22", start: "2026-06-05", attempts: 1,  successes: 0,  rate: 0.0 },
  { week: "2026-W23", start: "2026-06-08", attempts: 11, successes: 5,  rate: 45.5 },
  { week: "2026-W24", start: "2026-06-15", attempts: 3,  successes: 1,  rate: 33.3 },
  { week: "2026-W25", start: "2026-06-22", attempts: 4,  successes: 2,  rate: 50.0 },
  { week: "2026-W26", start: "2026-07-01", attempts: 6,  successes: 2,  rate: 33.3 },
  { week: "2026-W28", start: "2026-07-13", attempts: 2,  successes: 2,  rate: 100.0 },
  { week: "2026-W29", start: "2026-07-20", attempts: 6,  successes: 3,  rate: 50.0 },
  { week: "2026-W30", start: "2026-07-27", attempts: 2,  successes: 1,  rate: 50.0 },
  { week: "2026-W31", start: "2026-08-03", attempts: 7,  successes: 4,  rate: 57.1 },
  { week: "2026-W32", start: "2026-08-10", attempts: 6,  successes: 2,  rate: 33.3 },
  { week: "2026-W33", start: "2026-08-21", attempts: 2,  successes: 2,  rate: 100.0 },
  { week: "2026-W34", start: "2026-08-24", attempts: 4,  successes: 3,  rate: 75.0 },
  { week: "2026-W35", start: "2026-09-04", attempts: 2,  successes: 1,  rate: 50.0 },
];

const POSITIONS = [
  { position: "back",          against: 53, finished: 27, finishRate: 50.9, share: 28.7 },
  { position: "mount",         against: 8,  finished: 5,  finishRate: 62.5, share: 5.3 },
  { position: "turtle",        against: 3,  finished: 3,  finishRate: 100.0, share: 3.2 },
  { position: "north_south",   against: 8,  finished: 3,  finishRate: 37.5, share: 3.2 },
  { position: "side_control",  against: 7,  finished: 2,  finishRate: 28.6, share: 2.1 },
  { position: "open_guard",    against: 3,  finished: 2,  finishRate: 66.7, share: 2.1 },
  { position: "half_guard",    against: 6,  finished: 1,  finishRate: 16.7, share: 1.1 },
  { position: "closed_guard",  against: 6,  finished: 1,  finishRate: 16.7, share: 1.1 },
];

const BELTS = [
  { belt: "white",  subAttempts: 14, subLanded: 7, tdAttempts: 16, tdLanded: 8, subRate: 50.0, tdRate: 50.0 },
  { belt: "blue",   subAttempts: 23, subLanded: 8, tdAttempts: 38, tdLanded: 18, subRate: 34.8, tdRate: 47.4 },
  { belt: "purple", subAttempts: 21, subLanded: 6, tdAttempts: 13, tdLanded: 4, subRate: 28.6, tdRate: 30.8 },
  { belt: "brown",  subAttempts: 23, subLanded: 6, tdAttempts: 21, tdLanded: 9, subRate: 26.1, tdRate: 42.9 },
  { belt: "black",  subAttempts: 5,  subLanded: 3, tdAttempts: 8,  tdLanded: 2, subRate: 60.0, tdRate: 25.0 },
];

const VOLUME = [
  { week: "2026-W10", start: "2026-03-10", sessions: 3, minutes: 165, avgDuration: 55.0, rounds: 18 },
  { week: "2026-W11", start: "2026-03-17", sessions: 1, minutes: 68,  avgDuration: 68.0, rounds: 6 },
  { week: "2026-W12", start: "2026-03-25", sessions: 2, minutes: 113, avgDuration: 56.5, rounds: 14 },
  { week: "2026-W13", start: "2026-03-30", sessions: 3, minutes: 204, avgDuration: 68.0, rounds: 18 },
  { week: "2026-W14", start: "2026-04-09", sessions: 1, minutes: 77,  avgDuration: 77.0, rounds: 6 },
  { week: "2026-W15", start: "2026-04-13", sessions: 1, minutes: 81,  avgDuration: 81.0, rounds: 6 },
  { week: "2026-W16", start: "2026-04-23", sessions: 2, minutes: 127, avgDuration: 63.5, rounds: 11 },
  { week: "2026-W17", start: "2026-04-28", sessions: 2, minutes: 161, avgDuration: 80.5, rounds: 10 },
  { week: "2026-W18", start: "2026-05-04", sessions: 5, minutes: 318, avgDuration: 63.6, rounds: 30 },
  { week: "2026-W19", start: "2026-05-15", sessions: 2, minutes: 167, avgDuration: 83.5, rounds: 12 },
  { week: "2026-W20", start: "2026-05-23", sessions: 2, minutes: 160, avgDuration: 80.0, rounds: 12 },
  { week: "2026-W21", start: "2026-05-30", sessions: 4, minutes: 309, avgDuration: 77.3, rounds: 25 },
  { week: "2026-W22", start: "2026-06-03", sessions: 2, minutes: 99,  avgDuration: 49.5, rounds: 13 },
  { week: "2026-W23", start: "2026-06-08", sessions: 4, minutes: 247, avgDuration: 61.8, rounds: 18 },
  { week: "2026-W24", start: "2026-06-15", sessions: 3, minutes: 212, avgDuration: 70.7, rounds: 19 },
  { week: "2026-W25", start: "2026-06-22", sessions: 3, minutes: 207, avgDuration: 69.0, rounds: 17 },
  { week: "2026-W26", start: "2026-06-29", sessions: 4, minutes: 247, avgDuration: 61.8, rounds: 21 },
  { week: "2026-W27", start: "2026-07-07", sessions: 2, minutes: 154, avgDuration: 77.0, rounds: 13 },
  { week: "2026-W28", start: "2026-07-13", sessions: 2, minutes: 138, avgDuration: 69.0, rounds: 9 },
  { week: "2026-W29", start: "2026-07-20", sessions: 3, minutes: 214, avgDuration: 71.3, rounds: 17 },
  { week: "2026-W30", start: "2026-07-27", sessions: 1, minutes: 82,  avgDuration: 82.0, rounds: 5 },
  { week: "2026-W31", start: "2026-08-03", sessions: 3, minutes: 159, avgDuration: 53.0, rounds: 14 },
  { week: "2026-W32", start: "2026-08-10", sessions: 3, minutes: 212, avgDuration: 70.7, rounds: 19 },
  { week: "2026-W33", start: "2026-08-18", sessions: 2, minutes: 141, avgDuration: 70.5, rounds: 16 },
  { week: "2026-W34", start: "2026-08-24", sessions: 3, minutes: 195, avgDuration: 65.0, rounds: 19 },
  { week: "2026-W35", start: "2026-09-04", sessions: 2, minutes: 135, avgDuration: 67.5, rounds: 13 },
];
