# Pack: concurrency

Applies to changes that use threads, async code, locks, queues, workers or multiple processes. Report a check only with the evidence its category needs.

## Checks

- State shared between threads, tasks or processes (a module-level dict, a counter, a cache, a file) that is written from more than one of them with no lock, queue or atomic operation. Name the writers and the interleaving that loses an update. (`race`)
- A check-then-act pair on shared state (`if key not in cache: cache[key] = ...`) where another writer can run between the check and the act. (`race`)
- Locks taken in different orders on two code paths in the change, so each path can hold one lock and wait for the other. Name both paths and the order. (`race`)
- A coroutine or promise called and never awaited, returned, or collected (`fetch(...)` or `task()` as a bare statement), so its result and its errors are lost. (`bug`)
- A blocking call (sleep, synchronous file or network I/O, a heavy loop) inside async code on the event loop, with no executor or async equivalent. (`bug`)

## Do not flag

- Single-threaded scripts, command-line tools and notebooks with no concurrency in the changed code.
- Test code, unless the test itself races (shared fixture written by parallel tests).
- Shared state that is read-only after start-up, thread-local, or guarded by a lock the diff shows on every writer.
- A fire-and-forget task that the code deliberately detaches and handles errors for (a callback, a `.catch`, a logging wrapper).
- A blocking call at start-up, in a worker thread or in a function never called from async code.

## Sources

- Python docs, asyncio development (a coroutine that is never awaited is lost; blocking calls stall the loop), https://docs.python.org/3/library/asyncio-dev.html, read 2026.
- Check shapes (lost update, check-then-act, lock-order inversion) are standard concurrency defects; the project contract (docs/ccr-contract.md L33, L38) supplies the unawaited-promise case.
