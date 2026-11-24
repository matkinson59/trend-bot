# Bot 2 diagnostics

Chosen after seeing the registered results, so none of these is a trial and none can change the winner. They explain where the returns went.

## 1. Base rules: no leverage, no floor

| Candidate | Annual return | Volatility | Sharpe | Max drawdown | Average invested |
| --- | --- | --- | --- | --- | --- |
| B0: version 2, levered | 4.9% | 8.1% | 0.44 | -15.5% | 89% |
| B2: trend-timed S&P 500 | 5.1% | 8.8% | 0.44 | -15.4% | 58% |
| B3: sector momentum | 6.1% | 11.7% | 0.44 | -26.7% | 65% |
| B4: B0 plus index reversal | 4.4% | 8.1% | 0.39 | -15.8% | 85% |
| B5: equal mix of B0, B2 and B3 | 5.8% | 8.4% | 0.53 | -17.1% | 71% |

## 2. Chosen leverage with and without the floor rule

| Candidate | Leverage | With floor: return, max drawdown | Without floor: return, max drawdown |
| --- | --- | --- | --- |
| B0: version 2, levered | 1.5 | 4.2%, -16.8% | 5.7%, -23.3% |
| B2: trend-timed S&P 500 | 1.7 | 3.9%, -19.1% | 6.8%, -26.2% |
| B3: sector momentum | 1.3 | 4.0%, -22.0% | 7.1%, -33.8% |
| B4: B0 plus index reversal | 1.4 | 3.7%, -16.2% | 5.0%, -21.9% |
| B5: equal mix of B0, B2 and B3 | 1.9 | 5.0%, -21.5% | 8.3%, -31.5% |

## 3. Share of ten-year paths falling 25% or more, by bootstrap block length

Each cell: with the floor rule / without it, at the candidate's chosen leverage. 10,000 paths, seed 0.

| Candidate | Leverage | 2.9-day blocks (registered) | 21-day blocks | 63-day blocks |
| --- | --- | --- | --- | --- |
| B0: version 2, levered | 1.5 | 0.0% / 52.8% | 0.0% / 48.5% | 0.0% / 44.4% |
| B2: trend-timed S&P 500 | 1.7 | 0.0% / 74.3% | 0.0% / 74.7% | 0.0% / 73.7% |
| B3: sector momentum | 1.3 | 0.0% / 68.1% | 0.0% / 59.5% | 0.0% / 54.3% |
| B4: B0 plus index reversal | 1.4 | 0.0% / 46.8% | 0.0% / 40.5% | 0.0% / 36.1% |
| B5: equal mix of B0, B2 and B3 | 1.9 | 0.0% / 75.5% | 0.0% / 69.9% | 0.0% / 67.4% |
