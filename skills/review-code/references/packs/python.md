# Pack: python

Applies to `*.py` and `pyproject.toml` changes. Read each check as a place to look. Report one only with the evidence its category needs.

## Checks

- A bare `except:` or `except Exception:` whose body discards the error (`pass`, `continue`, a default return) in changed code, where the caller loses a failure signal you can name. Quote the handler and say what the caller would have seen. (`bug`)
- A handler tuple that lists a class together with a base class it inherits from, such as `except (KeyError, Exception):`. The specific name adds no separate handling, so the intent behind it is lost. (`bug`)
- Serialisation with a catch-all fallback, such as `json.dumps(obj, default=str)`. Objects the encoder cannot represent turn into text, and the failure the encoder would raise never surfaces. (`bug`)
- A mutable default argument (`def f(x=[])`, `x={}`) that the function mutates or returns. The default is built once at definition time and shared by every call. Report only when no linter or type checker is configured. (`smell`)
- A wildcard import (`from m import *`) in a module the change adds or edits, when no linter or type checker is configured. Names from the module are no longer traceable to their source. (`smell`)

## Do not flag

- A bare or broad handler that logs with the traceback, re-raises, or converts to a more specific exception. The signal survives.
- A handler tuple, mutable default or wildcard import that a configured linter or type checker already reports. Check the repo config before assuming none exists.
- A mutable default that the function never mutates, returns or stores, and `__all__`-driven wildcard re-exports in a package `__init__.py`.
- `default=str` or similar in logging, debug dumps or tests, where a lossy rendering is the purpose and no later code reads the result back.
- Type-hint style, import order and naming nits that the repo's formatter or linter covers.

## Sources

- Python tutorial, Errors and Exceptions (handler matching and base classes), https://docs.python.org/3/tutorial/errors.html, read 2026.
- Python reference, function definitions (default values are evaluated once, at definition), https://docs.python.org/3/reference/compound_stmts.html, read 2026.
- Python library, json (the `default` hook), https://docs.python.org/3/library/json.html, read 2026.
- Review rules for this pack come from the project's own contract (docs/ccr-contract.md rows L51-L56), restated in own words.
