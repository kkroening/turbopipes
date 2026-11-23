# Changelog

## [Unreleased]

## <span id="0-4-0" /> [0.4.0](https://github.com/kkroening/turbopipes/compare/0.3.0...0.4.0) - 2026-11-23

### Changes

* Add `aselect` for merging several async generators into a single stream ([#4](https://github.com/kkroening/turbopipes/pull/4))
* Decompose aselect into ataskify, atag and amerge building blocks ([#6](https://github.com/kkroening/turbopipes/pull/6))
* Add the first-principles deep-dive that derives `aparallel` and `aselect` ([#5](https://github.com/kkroening/turbopipes/pull/5))
* Keep a source cleanup failure reportable when `asettle` is cancelled mid-teardown ([#7](https://github.com/kkroening/turbopipes/pull/7))
* Claim only readiness ordering for `ataskify`'s wait, not backpressure ([#9](https://github.com/kkroening/turbopipes/pull/9))
* Derive the `ataskify` / `atag` / `amerge` building blocks in the first-principles guide ([#8](https://github.com/kkroening/turbopipes/pull/8))
* Derive `aselect` the way `aparallel` is derived, and split the parts by audience ([#10](https://github.com/kkroening/turbopipes/pull/10))
* Say that a source's own CancelledError comes out as a cancelled task ([#13](https://github.com/kkroening/turbopipes/pull/13))
* Yield aparallel's last tasks as they finish, not all at once unfinished ([#14](https://github.com/kkroening/turbopipes/pull/14))
* Add scripts for cutting releases ([#15](https://github.com/kkroening/turbopipes/pull/15))

## <span id="v0-3-0" /> [0.3.0](https://github.com/kkroening/turbopipes/releases/tag/0.3.0) - 2026-03-06

### Changes

*   Fix `aparallel` inadvertent peer cancellation on task failure ([#3](https://github.com/kkroening/turbopipes/pull/3))

## <span id="v0-2-0" /> [v0.2.0](https://github.com/kkroening/turbopipes/releases/tag/v0.2.0) - 2025-02-11

### Changes

*   Add `aiter_chunks` ([#1](https://github.com/kkroening/turbopipes/pull/1))
*   Relax version constraints ([#2](https://github.com/kkroening/turbopipes/pull/2))

## <span id="v0-1-0" /> [v0.1.0](https://github.com/kkroening/turbopipes/releases/tag/v0.1.0) - 2024-06-20

This is the very first release of `turbopipes`!

### Changes

*   Setup project structure, README, GHA definitions, etc.
*   Add `aparallel` function.
